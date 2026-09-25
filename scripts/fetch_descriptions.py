"""CP2 -- backfill YouTube descriptions for every episode. Resumable and concurrent.

Writes one JSON object per line to cache/descriptions.jsonl the moment each fetch
returns, so a kill at any point loses at most the in-flight requests. Re-running skips
everything already cached.

Chose the watch-page extractor over yt-dlp: benchmarked 0.13 s/video at 8 workers vs
1.21 s/video for yt-dlp, with identical output on 8/8 sampled videos (see bench_fetch.py).
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

from bench_fetch import SHORT_DESC_RE
from config import DESC_CACHE, EPISODES_PARQUET

_lock = threading.Lock()
_local = threading.local()

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}


def session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update(HEADERS)
    return _local.s


def load_cache() -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if DESC_CACHE.exists():
        with DESC_CACHE.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cache[rec["video_id"]] = rec
    return cache


def append(rec: dict) -> None:
    with _lock:
        with DESC_CACHE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fetch_one(video_id: str, attempts: int = 3, delay: float = 0.0) -> dict:
    for attempt in range(attempts):
        try:
            if delay:
                # Jitter so N workers do not fire in lockstep -- synchronised bursts are
                # what tripped the rate limiter on the first attempt.
                time.sleep(delay * random.uniform(0.6, 1.4))
            r = session().get(
                f"https://www.youtube.com/watch?v={video_id}", timeout=25
            )
            if r.status_code == 429 or "/sorry/" in r.url:
                # Exponential backoff; a CAPTCHA interstitial means back all the way off.
                time.sleep(min(120, 15 * (2 ** attempt)))
                continue
            r.raise_for_status()
            m = SHORT_DESC_RE.search(r.text)
            if not m:
                return {"video_id": video_id, "description": None, "error": "no_match"}
            desc = json.loads(f'"{m.group(1)}"')
            return {"video_id": video_id, "description": desc, "error": None}
        except Exception as exc:
            if attempt == attempts - 1:
                return {"video_id": video_id, "description": None, "error": type(exc).__name__}
            time.sleep(1.5 * (attempt + 1))
    return {"video_id": video_id, "description": None, "error": "exhausted"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2,
                    help="keep low; 10 workers triggered a CAPTCHA block")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="per-request delay in seconds, jittered +/-40%%")
    ap.add_argument("--abort-after", type=int, default=25,
                    help="give up if this many consecutive fetches fail")
    args = ap.parse_args()

    df = pd.read_parquet(EPISODES_PARQUET)
    wanted = df.video_id.dropna().unique().tolist()

    cache = load_cache()
    todo = [v for v in wanted if v not in cache]
    if args.limit:
        todo = todo[: args.limit]

    print(f"episodes with a video id : {len(wanted)}")
    print(f"already cached           : {len(cache)}")
    print(f"to fetch                 : {len(todo)}")
    sys.stdout.flush()

    if not todo:
        print("nothing to do")
        return 0

    print(f"workers {args.workers}, delay {args.delay}s jittered "
          f"(~{args.workers / max(args.delay, 0.01):.1f} req/s)")
    sys.stdout.flush()

    start = time.time()
    done = ok = consecutive_fail = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_one, v, 3, args.delay): v for v in todo}
        for fut in as_completed(futures):
            rec = fut.result()
            append(rec)
            done += 1
            if rec["description"] is not None:
                ok += 1
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                if consecutive_fail >= args.abort_after:
                    print(f"\nABORT: {consecutive_fail} consecutive failures -- "
                          f"still blocked. {ok} fetched this run; rerun later to resume.",
                          flush=True)
                    for f in futures:
                        f.cancel()
                    break
            if done % 100 == 0 or done == len(todo):
                rate = done / max(0.1, time.time() - start)
                eta = (len(todo) - done) / max(0.01, rate) / 60
                print(
                    f"  {done}/{len(todo)}  ok={ok}  {rate:.1f}/s  eta {eta:.1f} min",
                    flush=True,
                )

    print(f"\nfetched {ok}/{len(todo)} in {(time.time() - start) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
