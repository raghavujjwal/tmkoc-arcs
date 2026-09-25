"""CP4 -- build a held-out ground-truth arc set from upstream's Wikipedia-sourced arcs.

Upstream's storylines.json mixes two provenances in one list, distinguishable only by a
string in the `tagline` field: 196 arcs are "Official Wikipedia Ground-Truth Arc" and 476
are "Episode Guide Arc". Only the former are trustworthy enough to score against.

Pk and WindowDiff compare two segmentations of one continuous sequence, so isolated arcs
are useless -- a boundary is only "missed" if we know the region around it is fully
labelled. We therefore keep contiguous *runs* of wiki arcs and evaluate inside those
regions only.

Upstream also has 10 boundary collisions where endEp[i] == startEp[i+1], which put one
episode in two arcs. We reconcile to half-open convention: an episode belongs to exactly
one arc.
"""
from __future__ import annotations

import json
import sys

from config import GROUND_TRUTH_ARCS, UPSTREAM_STORYLINES

MIN_RUN_ARCS = 5


def load_wiki_arcs() -> list[dict]:
    arcs = json.loads(UPSTREAM_STORYLINES.read_text(encoding="utf-8"))
    wiki = [a for a in arcs if "Wikipedia" in a.get("tagline", "")]
    wiki.sort(key=lambda a: (a["startEp"], a["endEp"]))
    return wiki


def reconcile(arcs: list[dict]) -> tuple[list[dict], int]:
    """Force non-overlap by truncating the earlier arc. Returns (arcs, n_fixed)."""
    fixed = 0
    out = [dict(a) for a in arcs]
    for i in range(len(out) - 1):
        if out[i]["endEp"] >= out[i + 1]["startEp"]:
            out[i]["endEp"] = out[i + 1]["startEp"] - 1
            fixed += 1
    return [a for a in out if a["endEp"] >= a["startEp"]], fixed


def find_runs(arcs: list[dict]) -> list[list[dict]]:
    """Group arcs into maximal runs where each arc starts right after the previous ends."""
    runs: list[list[dict]] = []
    cur = [arcs[0]]
    for a in arcs[1:]:
        if a["startEp"] == cur[-1]["endEp"] + 1:
            cur.append(a)
        else:
            runs.append(cur)
            cur = [a]
    runs.append(cur)
    return runs


def main() -> int:
    wiki = load_wiki_arcs()
    reconciled, fixed = reconcile(wiki)
    runs = [r for r in find_runs(reconciled) if len(r) >= MIN_RUN_ARCS]

    regions = []
    for run in runs:
        regions.append(
            {
                "start_ep": run[0]["startEp"],
                "end_ep": run[-1]["endEp"],
                "n_arcs": len(run),
                "n_episodes": run[-1]["endEp"] - run[0]["startEp"] + 1,
                # Boundaries are the first episode of each arc after the first.
                "boundaries": [a["startEp"] for a in run[1:]],
                "arcs": [
                    {"title": a["title"], "start_ep": a["startEp"], "end_ep": a["endEp"]}
                    for a in run
                ],
            }
        )

    total_eps = sum(r["n_episodes"] for r in regions)
    total_arcs = sum(r["n_arcs"] for r in regions)

    print(f"wiki arcs found        {len(wiki)}")
    print(f"overlaps reconciled    {fixed}")
    print(f"eval regions (>={MIN_RUN_ARCS} arcs) {len(regions)}")
    print(f"arcs in eval set       {total_arcs}")
    print(f"episodes in eval set   {total_eps}")
    print(f"mean arc length        {total_eps / total_arcs:.1f}")

    print("\nregions:")
    for r in regions:
        print(f"  eps {r['start_ep']:>4}-{r['end_ep']:<4} {r['n_arcs']:>3} arcs "
              f"{r['n_episodes']:>4} eps")

    payload = {
        "source": "upstream storylines.json, tagline contains 'Wikipedia'",
        "convention": "inclusive [start_ep, end_ep], non-overlapping after reconciliation",
        "min_run_arcs": MIN_RUN_ARCS,
        "n_regions": len(regions),
        "n_arcs": total_arcs,
        "n_episodes": total_eps,
        "regions": regions,
    }
    GROUND_TRUTH_ARCS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {GROUND_TRUTH_ARCS}")

    passed = len(regions) >= 8 and total_arcs >= 80 and all(
        r["boundaries"] == sorted(set(r["boundaries"])) for r in regions
    )
    print("CP4", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
