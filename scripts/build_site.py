"""CP11 -- emit a self-contained JSON the frontend loads with no backend.

The upstream defect this is written against: the site rendered an empty `<p>` where a
synopsis should be, because arcs had no description text and nothing said so. Here an arc
without a summary is explicitly flagged `has_summary: false` and the frontend shows what
it does know (titles, episode range) instead of an empty shell pretending to be content.
"""
from __future__ import annotations

import json
import sys

import pandas as pd

from config import CACHE_DIR, EPISODES_PARQUET

SITE_DIR = EPISODES_PARQUET.parent.parent / "site"
ARCS_JSON = EPISODES_PARQUET.parent / "arcs.json"

# Neutral, checkable era bands. Deliberately not fan-canon names ("Golden era" etc.):
# those are contested and we have no source for them in the data.
ERAS = [
    (1, 999, "Early"),
    (1000, 1999, "Classic"),
    (2000, 2999, "Middle"),
    (3000, 3999, "Late"),
    (4000, 9999, "Current"),
]


def era_for(ep: int) -> str:
    for lo, hi, name in ERAS:
        if lo <= ep <= hi:
            return name
    return "Unknown"


def load_summaries() -> dict[tuple[int, int], dict]:
    path = CACHE_DIR / "summaries.jsonl"
    out: dict[tuple[int, int], dict] = {}
    if not path.exists():
        return out
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Only surface summaries that passed the factuality gate. A summary naming a
        # character who is not in the source is worse than no summary at all.
        if not rec.get("grounding", {}).get("grounded", False):
            continue
        out[(rec["start_ep"], rec["end_ep"])] = rec["summary"]
    return out


def main() -> int:
    df = pd.read_parquet(EPISODES_PARQUET.with_name("episodes_enriched.parquet"))
    df = df.sort_values("ep_number").reset_index(drop=True)
    arcs_doc = json.loads(ARCS_JSON.read_text(encoding="utf-8"))
    summaries = load_summaries()

    by_ep = {int(r.ep_number): r for r in df.itertuples()}

    # Upstream's CSV maps a handful of episodes onto another episode's video. The links
    # resolve, so a link check alone would call them healthy -- but they play the wrong
    # episode, which is worse than a dead link because nothing signals the error. Mark
    # every episode whose video id is claimed by more than one episode.
    vid_users: dict[str, list[int]] = {}
    for n, r in by_ep.items():
        if isinstance(r.video_id, str):
            vid_users.setdefault(r.video_id, []).append(n)
    shared = {v: eps for v, eps in vid_users.items() if len(eps) > 1}
    out_arcs = []
    for arc in arcs_doc["arcs"]:
        a, b = arc["start_ep"], arc["end_ep"]
        eps = []
        for n in range(a, b + 1):
            r = by_ep.get(n)
            if r is None:
                continue
            vid = r.video_id if isinstance(r.video_id, str) else None
            item = {
                "ep": n,
                "title": (r.clean_title or "").strip(),
                "vid": vid,
            }
            if vid in shared:
                item["shared_with"] = [e for e in shared[vid] if e != n]
            eps.append(item)
        s = summaries.get((a, b))
        out_arcs.append({
            "start_ep": a,
            "end_ep": b,
            "n": arc["n_episodes"],
            "status": arc.get("status", "sealed"),
            "signal": arc.get("signal", 0.0),
            "era": era_for(a),
            "has_summary": s is not None,
            "title": (s or {}).get("title"),
            "logline": (s or {}).get("logline"),
            "synopsis": (s or {}).get("synopsis"),
            "characters": (s or {}).get("characters") or [],
            "tags": (s or {}).get("tags") or [],
            "episodes": eps,
        })

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_from": {
            "method": arcs_doc.get("method"),
            "corpus_end": arcs_doc.get("corpus_end"),
            "edge_margin": arcs_doc.get("edge_margin"),
        },
        "n_arcs": len(out_arcs),
        "n_summarised": sum(1 for a in out_arcs if a["has_summary"]),
        "shared_video_ids": {v: eps for v, eps in shared.items()},
        "eras": [e[2] for e in ERAS],
        "arcs": out_arcs,
    }
    dest = SITE_DIR / "arcs.json"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    dest.write_text(body, encoding="utf-8")
    # Also emit as a script that assigns a global. A published artifact serves scripts
    # unconditionally, so this sidesteps any question about fetching JSON from the page.
    (SITE_DIR / "arcs.js").write_text(f"window.ARC_DATA={body};", encoding="utf-8")

    # CP11 exit test.
    checks = {
        "every arc has a span and episode list":
            all(a["start_ep"] <= a["end_ep"] and a["episodes"] for a in out_arcs),
        "episode count matches the declared span":
            all(len(a["episodes"]) == a["n"] for a in out_arcs),
        "arcs without a summary are flagged, not blank":
            all(a["has_summary"] or a["synopsis"] is None for a in out_arcs),
        "every episode has a watch link":
            all(e["vid"] for a in out_arcs for e in a["episodes"]),
        "episodes sharing a video are flagged, not silently wrong":
            all(("shared_with" in e) == (e["vid"] in shared)
                for a in out_arcs for e in a["episodes"]),
        "file loads standalone":
            json.loads(dest.read_text(encoding="utf-8"))["n_arcs"] == len(out_arcs),
    }
    total_eps = sum(len(a["episodes"]) for a in out_arcs)
    print(f"wrote {dest}  ({dest.stat().st_size / 1024:.0f} KB)")
    if shared:
        print(f"  {sum(len(v) for v in shared.values())} episodes share "
              f"{len(shared)} video ids -- flagged in the output")
    print(f"  {len(out_arcs)} arcs, {total_eps} episodes, "
          f"{payload['n_summarised']} summarised "
          f"({payload['n_summarised'] / len(out_arcs) * 100:.1f}%)")
    print()
    for label, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    passed = all(checks.values())
    print("\nCP11", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
