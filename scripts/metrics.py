"""Pk and WindowDiff -- the standard text-segmentation metrics. Lower is better.

Both compare a hypothesised segmentation against a reference by sliding a window across
the sequence and asking whether the two disagree about how many boundaries fall inside.
Plain boundary precision/recall is too harsh: it scores a boundary placed one episode off
the same as one placed fifty episodes off. Pk and WindowDiff degrade gracefully with
distance, which is what we want when arc edges are inherently a little fuzzy.

Segmentations are represented as a sorted list of boundary positions, where a position
b means "a new segment starts at index b" for 0 < b < n.
"""
from __future__ import annotations


def _boundary_set(boundaries) -> set[int]:
    return set(int(b) for b in boundaries)


def default_k(n: int, n_segments: int) -> int:
    """Half the mean reference segment length, per Beeferman et al."""
    if n_segments <= 0:
        return max(2, n // 4)
    return max(2, int(round(n / (2 * n_segments))))


def pk(ref, hyp, n: int, k: int | None = None) -> float:
    """Beeferman's Pk: P(the two segmentations disagree on a randomly placed window)."""
    ref_b, hyp_b = _boundary_set(ref), _boundary_set(hyp)
    if k is None:
        k = default_k(n, len(ref_b) + 1)
    if n <= k:
        return 0.0

    disagreements = 0
    trials = 0
    for i in range(n - k):
        lo, hi = i + 1, i + k  # boundary positions strictly inside the window
        ref_same = not any(lo <= b <= hi for b in ref_b)
        hyp_same = not any(lo <= b <= hi for b in hyp_b)
        disagreements += ref_same != hyp_same
        trials += 1
    return disagreements / trials if trials else 0.0


def window_diff(ref, hyp, n: int, k: int | None = None) -> float:
    """Pevzner & Hearst's WindowDiff: penalises differing boundary *counts* per window.

    Stricter than Pk -- it catches a hypothesis that puts the right number of boundaries
    in the sequence but clusters them wrongly.
    """
    ref_b, hyp_b = _boundary_set(ref), _boundary_set(hyp)
    if k is None:
        k = default_k(n, len(ref_b) + 1)
    if n <= k:
        return 0.0

    errors = 0
    trials = 0
    for i in range(n - k):
        lo, hi = i + 1, i + k
        r = sum(1 for b in ref_b if lo <= b <= hi)
        h = sum(1 for b in hyp_b if lo <= b <= hi)
        errors += r != h
        trials += 1
    return errors / trials if trials else 0.0


def boundary_f1(ref, hyp, tolerance: int = 2) -> dict:
    """Exact-ish boundary agreement, allowing +/- `tolerance` episodes of slack."""
    ref_b, hyp_b = sorted(_boundary_set(ref)), sorted(_boundary_set(hyp))
    if not ref_b and not hyp_b:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tolerance": tolerance}

    unmatched = set(ref_b)
    hits = 0
    for h in hyp_b:
        match = next((r for r in sorted(unmatched, key=lambda r: abs(r - h))
                      if abs(r - h) <= tolerance), None)
        if match is not None:
            unmatched.discard(match)
            hits += 1

    precision = hits / len(hyp_b) if hyp_b else 0.0
    recall = hits / len(ref_b) if ref_b else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tolerance": tolerance}


def fixed_width_baseline(n: int, width: int) -> list[int]:
    """The trivial segmenter: a boundary every `width` items. CP6 must beat this."""
    return list(range(width, n, width))
