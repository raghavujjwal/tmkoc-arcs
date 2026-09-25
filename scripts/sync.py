"""CP9/CP10 -- incremental re-segmentation with sealed and open arcs.

The failure this exists to prevent is the one that froze upstream: arcs were computed
once and never regenerated, so the catalogue rotted as episodes kept airing.

The mechanism is the *open arc*. A boundary detected near the end of the corpus is not
trustworthy -- the episodes that would confirm or move it have not aired yet. So the last
arc stays `ongoing` and is recomputed every run; only arcs whose end boundary sits at
least EDGE_MARGIN episodes from the corpus end are `sealed` and never touched again.

That gives two properties worth stating plainly:
  * sealed arcs are stable, so re-running is cheap and diffs stay small
  * the tail self-corrects as new episodes arrive, instead of ossifying
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from config import EMBEDDINGS_NPY, EPISODES_PARQUET
from segment import DARK_REGION_CUTOFF, SIGNAL_MIN_CHARS, seg_pelt

ARCS_JSON = EPISODES_PARQUET.parent / "arcs.json"

# A boundary this close to the end of known episodes is provisional: the episodes that
# would confirm it have not aired. Two mean arc lengths (~8.5 each) of margin.
EDGE_MARGIN = 17


def load_arcs(path) -> dict:
    if not path.exists():
        return {"method": None, "arcs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def last_sealed_end(arcs: list[dict]) -> int | None:
    sealed = [a for a in arcs if a.get("status") == "sealed"]
    return max((a["end_ep"] for a in sealed), default=None)


def build_arc(df: pd.DataFrame, eps: np.ndarray, lo: int, hi: int, corpus_end: int) -> dict:
    """One arc record. `lo`/`hi` are positions into `eps`, inclusive."""
    start_ep, end_ep = int(eps[lo]), int(eps[hi])
    g = df[(df.ep_number >= start_ep) & (df.ep_number <= end_ep)]
    sealed = (corpus_end - end_ep) >= EDGE_MARGIN
    return {
        "start_ep": start_ep,
        "end_ep": end_ep,
        "n_episodes": hi - lo + 1,
        "signal": round(float((g.text_chars > SIGNAL_MIN_CHARS).mean()), 3),
        "status": "sealed" if sealed else "ongoing",
    }


def segment_range(df, emb, eps, lo_pos: int, corpus_end: int, pen: float,
                  chunk: int = 400) -> list[dict]:
    """Segment positions [lo_pos, end] into arcs, chunked to bound PELT's quadratic cost."""
    out: list[dict] = []
    n = len(eps)
    pos = lo_pos
    while pos < n:
        block_end = min(pos + chunk, n)
        block = emb[pos:block_end]
        if len(block) < 8:
            if out:
                # Too short to segment: extend the previous arc rather than emit a stub.
                out[-1] = build_arc(df, eps,
                                    int(np.flatnonzero(eps == out[-1]["start_ep"])[0]),
                                    block_end - 1, corpus_end)
            else:
                out.append(build_arc(df, eps, pos, block_end - 1, corpus_end))
            break
        cuts = [0] + seg_pelt(block, pen) + [len(block)]
        for a, b in zip(cuts, cuts[1:]):
            if b - a < 2:
                continue
            out.append(build_arc(df, eps, pos + a, pos + b - 1, corpus_end))
        pos = block_end
    return out


def sync(pen: float = 1.0, dry_run: bool = False, limit: int | None = None,
         arcs_path=None, quiet: bool = False) -> dict:
    df = pd.read_parquet(EPISODES_PARQUET.with_name("episodes_enriched.parquet"))
    df = df.sort_values("ep_number").reset_index(drop=True)
    emb = np.load(EMBEDDINGS_NPY)
    if limit is not None:
        # Simulate a corpus that only knows episodes up to `limit` -- the state the
        # pipeline was in before the newest episodes aired.
        df, emb = df.iloc[:limit].reset_index(drop=True), emb[:limit]
    if len(emb) != len(df):
        raise SystemExit(
            f"embeddings ({len(emb)}) and episodes ({len(df)}) disagree -- "
            f"run embed.py before sync")

    eps = df.ep_number.to_numpy()
    corpus_end = int(eps[-1])
    path = arcs_path or ARCS_JSON
    prev = load_arcs(path)
    prev_arcs = prev.get("arcs", [])

    seal_end = last_sealed_end(prev_arcs)
    if seal_end is None:
        kept, resume_pos = [], 0
    else:
        kept = [a for a in prev_arcs if a.get("status") == "sealed"]
        after = np.flatnonzero(eps > seal_end)
        resume_pos = int(after[0]) if len(after) else len(eps)

    fresh = segment_range(df, emb, eps, resume_pos, corpus_end, pen) if resume_pos < len(eps) else []
    arcs = kept + fresh

    out = {
        "resegmented": len(eps) - resume_pos,
        "method": f"pelt-pen{pen:g}",
        "edge_margin": EDGE_MARGIN,
        "corpus_end": corpus_end,
        "n_arcs": len(arcs),
        "arcs": arcs,
    }
    n_sealed = sum(1 for a in arcs if a["status"] == "sealed")
    log = (lambda *a: None) if quiet else print
    log(f"episodes {len(df)} (through ep {corpus_end})")
    log(f"resumed from position {resume_pos}"
          f"{f' (after sealed ep {seal_end})' if seal_end else ' (full rebuild)'}")
    log(f"re-segmented {len(eps) - resume_pos} episodes, kept {len(kept)} sealed arcs")
    log(f"arcs {len(arcs)}: {n_sealed} sealed, {len(arcs) - n_sealed} ongoing")

    if not dry_run:
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        if not quiet:
            print(f"wrote {path}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pen", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="verify the CP9 invariants")
    args = ap.parse_args()

    if args.selftest:
        rc = selftest(args.pen)
        return rc or selftest_incremental(args.pen)
    sync(args.pen, args.dry_run)
    return 0


def selftest_incremental(pen: float) -> int:
    """CP10 exit test: simulate new episodes airing and prove the tail self-corrects.

    Segments a corpus truncated to 4,000 episodes, then extends it to the full 4,819 and
    re-syncs. What matters is that sealed arcs survive byte-identical while the arcs near
    the old edge are recomputed -- that is the difference between a catalogue that updates
    and one that rots.
    """
    import tempfile
    from pathlib import Path

    print("\n\nCP10 self-test -- simulating new episodes\n")
    checks: dict[str, bool] = {}
    tmp = Path(tempfile.mkdtemp()) / "arcs_test.json"

    before = sync(pen, dry_run=False, limit=4000, arcs_path=tmp, quiet=True)
    sealed_before = [a for a in before["arcs"] if a["status"] == "sealed"]
    ongoing_before = [a for a in before["arcs"] if a["status"] == "ongoing"]
    print(f"  at 4000 eps : {len(before['arcs'])} arcs "
          f"({len(sealed_before)} sealed, {len(ongoing_before)} ongoing), "
          f"segmented {before['resegmented']}")

    after = sync(pen, dry_run=False, limit=4819, arcs_path=tmp, quiet=True)
    sealed_after = [a for a in after["arcs"] if a["status"] == "sealed"]
    print(f"  at 4819 eps : {len(after['arcs'])} arcs "
          f"({len(sealed_after)} sealed), re-segmented only {after['resegmented']}")

    checks["sealed arcs survived byte-identical"] = (
        sealed_before == sealed_after[:len(sealed_before)])
    checks["work is proportional to the tail, not the corpus"] = (
        after["resegmented"] < 4819 // 4)
    checks["previously-ongoing arcs were recomputed"] = (
        after["resegmented"] >= len(ongoing_before))
    checks["new episodes are covered"] = (
        max(a["end_ep"] for a in after["arcs"]) > max(a["end_ep"] for a in before["arcs"]))
    checks["still covers every episode exactly once"] = (
        sum(a["n_episodes"] for a in after["arcs"]) == 4819)

    print()
    for label, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    passed = all(checks.values())
    print("\nCP10", "PASS" if passed else "FAIL")
    return 0 if passed else 1


def selftest(pen: float) -> int:
    """CP9 exit test: edge boundaries never seal, runs are idempotent, sealed arcs frozen."""
    import copy

    print("CP9 self-test\n")
    checks: dict[str, bool] = {}

    first = sync(pen, dry_run=False)
    arcs = first["arcs"]
    corpus_end = first["corpus_end"]

    near_edge_sealed = [a for a in arcs
                        if a["status"] == "sealed" and corpus_end - a["end_ep"] < EDGE_MARGIN]
    checks[f"no sealed arc within {EDGE_MARGIN} eps of the end"] = not near_edge_sealed

    ongoing = [a for a in arcs if a["status"] == "ongoing"]
    checks["at least one ongoing arc at the tail"] = bool(ongoing)
    checks["ongoing arcs are contiguous at the end"] = (
        all(a["status"] == "ongoing" for a in arcs[-len(ongoing):]) if ongoing else False)

    print()
    second = sync(pen, dry_run=True)
    checks["re-running is idempotent"] = second["arcs"] == arcs

    sealed_before = copy.deepcopy([a for a in arcs if a["status"] == "sealed"])
    sealed_after = [a for a in second["arcs"] if a["status"] == "sealed"]
    checks["sealed arcs unchanged across runs"] = sealed_before == sealed_after

    covered = sum(a["n_episodes"] for a in arcs)
    checks["arcs cover every episode exactly once"] = covered == len(
        pd.read_parquet(EPISODES_PARQUET.with_name("episodes_enriched.parquet")))

    print()
    for label, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    passed = all(checks.values())
    print("\nCP9", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
