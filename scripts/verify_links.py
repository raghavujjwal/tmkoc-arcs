"""Prove every episode link resolves, and report the result arc by arc.

Uses YouTube's oEmbed endpoint rather than fetching watch pages: it is a small JSON
response, far lighter on both ends, and it is the endpoint least likely to trip the rate
limiter that blocked the description backfill.

Reading the status codes correctly matters more than it looks:

  200  video exists and is embeddable                      -> link works
  401  video exists but the uploader disallows embedding   -> link still works
  403  same, region- or policy-restricted embedding        -> link still works
  404  no such video: deleted, private, or a bad id        -> link is BROKEN

Treating 401/403 as failures would report a large fraction of a working catalogue as
dead, because many official uploads disable third-party embedding while remaining
perfectly watchable on youtube.com. Only 404 means the link is broken.

Results are cached per video id, so a run that is interrupted or throttled resumes
without refetching.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from config import CACHE_DIR, EPISODES_PARQUET

LINK_CACHE = CACHE_DIR / "link_status.jsonl"
OEMBED = "https://www.youtube.com/oembed"

_local = threading.local()
_lock = threading.Lock()


def session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
        })
        _local.s = s
    return s


def load_cache() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if LINK_CACHE.exists():
        for line in LINK_CACHE.open(encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    out[r["vid"]] = r
                except json.JSONDecodeError:
                    continue
    return out


def append(rec: dict) -> None:
    with _lock, LINK_CACHE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def check(vid: str, delay: float, attempts: int = 3) -> dict:
    url = f"https://www.youtube.com/watch?v={vid}"
    for attempt in range(attempts):
        try:
            if delay:
                time.sleep(delay * random.uniform(0.6, 1.4))
            r = session().get(OEMBED, params={"url": url, "format": "json"}, timeout=20)
            code = r.status_code
            if code == 429 or "/sorry/" in r.url:
                time.sleep(min(90, 15 * (2 ** attempt)))
                continue
            title = None
            if code == 200:
                try:
                    title = r.json().get("title")
                except ValueError:
                    code = -1
            return {"vid": vid, "code": code, "ok": code in (200, 401, 403),
                    "title": title}
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return {"vid": vid, "code": None, "ok": False, "title": None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--abort-after", type=int, default=40)
    args = ap.parse_args()

    df = pd.read_parquet(EPISODES_PARQUET.with_name("episodes_enriched.parquet"))
    vids = [v for v in df.video_id.dropna().unique().tolist() if isinstance(v, str)]
    cache = load_cache()
    todo = [v for v in vids if v not in cache]
    if args.limit:
        todo = todo[: args.limit]

    print(f"distinct video ids : {len(vids)}")
    print(f"already checked    : {len(vids) - len([v for v in vids if v not in cache])}")
    print(f"to check           : {len(todo)}")
    sys.stdout.flush()

    start = time.time()
    done = ok = consecutive_fail = 0
    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(check, v, args.delay): v for v in todo}
            for fut in as_completed(futs):
                rec = fut.result()
                append(rec)
                done += 1
                if rec["code"] is not None:
                    ok += rec["ok"]
                    consecutive_fail = 0
                else:
                    consecutive_fail += 1
                    if consecutive_fail >= args.abort_after:
                        print(f"\nABORT after {consecutive_fail} consecutive network "
                              f"failures; rerun to resume", flush=True)
                        break
                if done % 250 == 0 or done == len(todo):
                    el = time.time() - start
                    print(f"  {done}/{len(todo)}  ok={ok}  {done / el:.1f}/s  "
                          f"eta {(len(todo) - done) / max(done / el, .01) / 60:.1f} min",
                          flush=True)
    return report()


def report() -> int:
    """Arc-wise proof: for every arc, how many of its episode links resolve."""
    df = pd.read_parquet(EPISODES_PARQUET.with_name("episodes_enriched.parquet"))
    arcs = json.loads((EPISODES_PARQUET.parent / "arcs.json").read_text(encoding="utf-8"))
    cache = load_cache()

    vid_of = {int(r.ep_number): r.video_id for r in df.itertuples()}
    rows, unchecked, broken = [], 0, []
    for a in arcs["arcs"]:
        n = good = bad = miss = 0
        for ep in range(a["start_ep"], a["end_ep"] + 1):
            v = vid_of.get(ep)
            if not isinstance(v, str):
                continue
            n += 1
            rec = cache.get(v)
            if rec is None:
                miss += 1
            elif rec["ok"]:
                good += 1
            else:
                bad += 1
                broken.append((ep, v, rec.get("code")))
        unchecked += miss
        rows.append({"start_ep": a["start_ep"], "end_ep": a["end_ep"],
                     "n": n, "ok": good, "broken": bad, "unchecked": miss})

    total = sum(r["n"] for r in rows)
    tot_ok = sum(r["ok"] for r in rows)
    tot_bad = sum(r["broken"] for r in rows)
    codes: dict[str, int] = {}
    for r in cache.values():
        codes[str(r.get("code"))] = codes.get(str(r.get("code")), 0) + 1

    out = EPISODES_PARQUET.parent.parent / "reports" / "link_audit.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "episodes": total, "ok": tot_ok, "broken": tot_bad, "unchecked": unchecked,
        "status_codes": codes,
        "broken_episodes": [{"ep": e, "vid": v, "code": c} for e, v, c in broken],
        "arcs": rows,
    }, indent=2), encoding="utf-8")

    print(f"\n{'':-<58}")
    print(f"episodes with a video id : {total}")
    print(f"links verified working   : {tot_ok} ({tot_ok / max(total, 1) * 100:.2f}%)")
    print(f"links broken (404)       : {tot_bad}")
    print(f"not yet checked          : {unchecked}")
    print(f"status codes             : {dict(sorted(codes.items()))}")

    full = [r for r in rows if r["n"] and r["ok"] == r["n"]]
    print(f"\narcs with every link working : {len(full)}/{len(rows)}")
    partial = [r for r in rows if r["broken"]]
    if partial:
        print(f"arcs containing a broken link: {len(partial)}")
        for r in partial[:12]:
            print(f"  eps {r['start_ep']}-{r['end_ep']}: {r['broken']} of {r['n']} broken")
    if broken:
        print(f"\nfirst broken episodes: "
              f"{', '.join(f'ep{e}({c})' for e, _, c in broken[:10])}")
    print(f"\nwrote {out}")

    passed = unchecked == 0 and tot_bad == 0
    print("\nLINK AUDIT", "PASS -- every episode link resolves" if passed else
          ("INCOMPLETE -- rerun to finish" if unchecked else
           f"FAIL -- {tot_bad} broken links"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
