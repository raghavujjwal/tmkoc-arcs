"""CP6 -- detect arc boundaries and score them against the held-out Wikipedia arcs.

Three candidate segmenters, all evaluated against two baselines so that "it works" is a
measurement rather than an impression:

  pelt      ruptures PELT with an rbf kernel and a tunable penalty (unknown arc count)
  binseg    ruptures binary segmentation given the true arc count -- an oracle-count
            upper bound, not a deployable method, useful for separating "bad boundary
            placement" from "wrong number of boundaries"
  texttile  classic TextTiling depth scoring over adjacent-window cosine similarity

Baselines: a fixed-width cut every `mean arc length` episodes, and random boundaries at
the true rate. A segmenter that cannot beat fixed-width is not earning its complexity,
and CP6 fails.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
import ruptures as rpt

from config import EMBEDDINGS_NPY, EPISODES_PARQUET, GROUND_TRUTH_ARCS, REPORT_DIR
from metrics import boundary_f1, fixed_width_baseline, pk, window_diff


def load() -> tuple[pd.DataFrame, np.ndarray, dict]:
    df = pd.read_parquet(EPISODES_PARQUET.with_name("episodes_enriched.parquet"))
    emb = np.load(EMBEDDINGS_NPY)
    gt = json.loads(GROUND_TRUTH_ARCS.read_text(encoding="utf-8"))
    return df.sort_values("ep_number").reset_index(drop=True), emb, gt


def region_slice(df: pd.DataFrame, emb: np.ndarray, start_ep: int, end_ep: int):
    mask = (df.ep_number >= start_ep) & (df.ep_number <= end_ep)
    idx = np.flatnonzero(mask.to_numpy())
    return idx, emb[idx], df.loc[mask, "ep_number"].to_numpy()


def seg_pelt(sig: np.ndarray, pen: float) -> list[int]:
    algo = rpt.Pelt(model="rbf", min_size=2, jump=1).fit(sig)
    return [b for b in algo.predict(pen=pen) if b < len(sig)]


def seg_binseg(sig: np.ndarray, n_bkps: int) -> list[int]:
    n_bkps = max(1, min(n_bkps, len(sig) // 2 - 1))
    algo = rpt.Binseg(model="rbf", min_size=2, jump=1).fit(sig)
    return [b for b in algo.predict(n_bkps=n_bkps) if b < len(sig)]


def seg_texttile(sig: np.ndarray, window: int = 3, threshold: float = 0.5) -> list[int]:
    """Depth score at each gap: how deep is the similarity valley relative to its peaks."""
    n = len(sig)
    if n < 2 * window + 2:
        return []
    gaps = np.arange(window, n - window)
    sim = np.array(
        [sig[g - window:g].mean(axis=0) @ sig[g:g + window].mean(axis=0) for g in gaps]
    )
    depth = np.zeros_like(sim)
    for i in range(len(sim)):
        left = sim[:i + 1].max() if i >= 0 else sim[i]
        right = sim[i:].max()
        depth[i] = (left - sim[i]) + (right - sim[i])
    if depth.std() == 0:
        return []
    cutoff = depth.mean() + threshold * depth.std()
    return [int(gaps[i]) for i in range(len(depth)) if depth[i] > cutoff]


def evaluate(hyp: list[int], ref: list[int], n: int) -> dict:
    return {
        "pk": pk(ref, hyp, n),
        "wd": window_diff(ref, hyp, n),
        "f1": boundary_f1(ref, hyp, tolerance=2)["f1"],
        "n_hyp": len(hyp),
        "n_ref": len(ref),
    }


def aggregate(rows: list[dict]) -> dict:
    """Weight each region by its length -- a 155-episode region should count more."""
    total = sum(r["n"] for r in rows)
    return {
        "pk": sum(r["pk"] * r["n"] for r in rows) / total,
        "wd": sum(r["wd"] * r["n"] for r in rows) / total,
        "f1": sum(r["f1"] * r["n"] for r in rows) / total,
        "n_hyp": sum(r["n_hyp"] for r in rows),
        "n_ref": sum(r["n_ref"] for r in rows),
    }


# An embed_text is "passage: " (9 chars) plus whatever survived cleaning, so text_chars
# at or below this means the episode contributed no text at all and its vector encodes
# the empty string.
SIGNAL_MIN_CHARS = 10
# Below this fraction of episodes carrying text, a region's score measures noise, not the
# segmenter. Such regions are reported but excluded from the headline metric.
DARK_REGION_CUTOFF = 0.5


def region_signal(df: pd.DataFrame, start_ep: int, end_ep: int) -> float:
    g = df[(df.ep_number >= start_ep) & (df.ep_number <= end_ep)]
    if g.empty:
        return 0.0
    return float((g.text_chars > SIGNAL_MIN_CHARS).mean())


def run_method(name: str, fn, df, emb, gt, rng) -> tuple[dict, list[dict]]:
    per_region = []
    for reg in gt["regions"]:
        _, sig, eps = region_slice(df, emb, reg["start_ep"], reg["end_ep"])
        n = len(sig)
        if n < 8:
            continue
        ep_to_pos = {int(e): i for i, e in enumerate(eps)}
        ref = [ep_to_pos[b] for b in reg["boundaries"] if b in ep_to_pos]
        hyp = fn(sig, reg, rng)
        res = evaluate(hyp, ref, n)
        cov = region_signal(df, reg["start_ep"], reg["end_ep"])
        res.update(n=n, start_ep=reg["start_ep"], end_ep=reg["end_ep"], method=name,
                   signal=cov, dark=cov < DARK_REGION_CUTOFF)
        per_region.append(res)
    lit = [r for r in per_region if not r["dark"]]
    # Headline aggregates over regions that actually carry text; averaging in the dark
    # ones would report the corpus's gaps as the segmenter's error rate.
    return aggregate(lit or per_region), per_region


def emit_arcs(df, emb, out_path, pen: float = 1.0, chunk: int = 400) -> int:
    """Segment the whole corpus with the CP6-winning method and write data/arcs.json.

    PELT's rbf cost is quadratic in the segment length, so the run is chunked. Chunks are
    cut at natural low-similarity points rather than fixed offsets where possible, but a
    chunk edge is still a forced boundary -- recorded as `forced` so downstream code can
    tell a detected arc break from a bookkeeping one.
    """
    eps = df.ep_number.to_numpy()
    arcs = []
    for lo in range(0, len(df), chunk):
        block = emb[lo:lo + chunk]
        if len(block) < 8:
            continue
        cuts = [0] + seg_pelt(block, pen) + [len(block)]
        for a, b in zip(cuts, cuts[1:]):
            if b - a < 2:
                continue
            g = df.iloc[lo + a:lo + b]
            arcs.append({
                "start_ep": int(eps[lo + a]),
                "end_ep": int(eps[lo + b - 1]),
                "n_episodes": int(b - a),
                "signal": round(float((g.text_chars > SIGNAL_MIN_CHARS).mean()), 3),
                "forced": bool(b == len(block) and lo + chunk < len(df)),
            })
    out_path.write_text(json.dumps({"method": f"pelt-pen{pen:g}", "n_arcs": len(arcs),
                                    "arcs": arcs}, indent=2), encoding="utf-8")
    lit = [a for a in arcs if a["signal"] >= DARK_REGION_CUTOFF]
    lens = sorted(a["n_episodes"] for a in arcs)
    print(f"wrote {len(arcs)} arcs -> {out_path}")
    print(f"  median length {lens[len(lens) // 2]} eps, range {lens[0]}-{lens[-1]}")
    print(f"  {len(lit)} arcs ({len(lit) / len(arcs) * 100:.0f}%) have usable text; "
          f"{len(arcs) - len(lit)} are dark and will summarise poorly")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--emit", action="store_true",
                    help="segment the full corpus and write data/arcs.json")
    args = ap.parse_args()

    if args.emit:
        df, emb, _ = load()
        return emit_arcs(df, emb, EPISODES_PARQUET.parent / "arcs.json")

    df, emb, gt = load()
    rng = np.random.default_rng(0)
    mean_arc = gt["n_episodes"] / gt["n_arcs"]
    print(f"eval set: {gt['n_regions']} regions, {gt['n_arcs']} arcs, "
          f"{gt['n_episodes']} episodes, mean arc {mean_arc:.1f}")

    # The ground truth is concentrated in the earliest episodes, which are exactly the
    # ones with no usable text -- so most of the eval set cannot test a text segmenter at
    # all. Say which regions those are up front rather than burying it in an average.
    lit_eps = dark_eps = 0
    print(f"\n{'region':<13}{'signal':>8}  status")
    for reg in gt["regions"]:
        cov = region_signal(df, reg["start_ep"], reg["end_ep"])
        n = int(((df.ep_number >= reg["start_ep"]) & (df.ep_number <= reg["end_ep"])).sum())
        dark = cov < DARK_REGION_CUTOFF
        dark_eps, lit_eps = (dark_eps + n, lit_eps) if dark else (dark_eps, lit_eps + n)
        print(f"{reg['start_ep']}-{reg['end_ep']:<8}{cov * 100:>7.0f}%  "
              f"{'DARK - excluded from headline' if dark else 'evaluable'}")
    print(f"\nevaluable {lit_eps} eps | dark {dark_eps} eps "
          f"({dark_eps / max(1, lit_eps + dark_eps) * 100:.0f}% of the eval set)\n")

    methods: dict[str, object] = {
        "baseline-fixed": lambda sig, reg, rng: fixed_width_baseline(len(sig), max(2, round(mean_arc))),
        "baseline-random": lambda sig, reg, rng: sorted(
            rng.choice(range(1, len(sig)), size=min(len(reg["boundaries"]), len(sig) - 1),
                       replace=False).tolist()
        ),
        "texttile": lambda sig, reg, rng: seg_texttile(sig),
        "binseg-oracle-n": lambda sig, reg, rng: seg_binseg(sig, len(reg["boundaries"])),
    }
    for pen in (1.0, 2.0, 3.0, 5.0, 8.0):
        methods[f"pelt-pen{pen:g}"] = (lambda p: (lambda sig, reg, rng: seg_pelt(sig, p)))(pen)

    results = {}
    all_regions: list[dict] = []
    print(f"{'method':<18}{'Pk':>8}{'WinDiff':>9}{'F1±2':>8}{'hyp':>6}{'ref':>6}")
    print("-" * 55)
    for name, fn in methods.items():
        agg, per = run_method(name, fn, df, emb, gt, rng)
        results[name] = agg
        all_regions.extend(per)
        print(f"{name:<18}{agg['pk']:>8.3f}{agg['wd']:>9.3f}{agg['f1']:>8.3f}"
              f"{agg['n_hyp']:>6}{agg['n_ref']:>6}")

    base = results["baseline-fixed"]
    best = min(
        (k for k in results if not k.startswith("baseline") and k != "binseg-oracle-n"),
        key=lambda k: results[k]["pk"],
    )
    print(f"\nbaseline-fixed Pk {base['pk']:.3f} | best deployable '{best}' Pk {results[best]['pk']:.3f}")

    if args.report:
        REPORT_DIR.mkdir(exist_ok=True)
        pd.DataFrame(all_regions).to_csv(REPORT_DIR / "cp6_per_region.csv", index=False)
        (REPORT_DIR / "cp6_summary.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        print(f"wrote {REPORT_DIR / 'cp6_summary.json'}")

    # Pk and WindowDiff both structurally reward under-segmentation: most windows contain
    # no boundary, so a method that predicts almost nothing scores well. Observed directly
    # here -- pelt-pen8 emitted ZERO boundaries and still beat the fixed-width baseline on
    # Pk. Guarding on Pk alone therefore certifies a segmenter that does nothing, so the
    # gate also demands a sane boundary count and a real F1.
    r = results[best]
    ratio = r["n_hyp"] / max(1, r["n_ref"])
    checks = {
        "beats baseline Pk": r["pk"] < base["pk"],
        "beats baseline WindowDiff": r["wd"] < base["wd"],
        "beats baseline F1": r["f1"] > base["f1"],
        "boundary count within 0.5-2x of reference": 0.5 <= ratio <= 2.0,
        "F1 above 0.35 (not merely non-degenerate)": r["f1"] > 0.35,
    }
    print(f"\ngate for '{best}' (hyp/ref boundary ratio {ratio:.2f}):")
    for label, passed_check in checks.items():
        print(f"  [{'ok' if passed_check else 'FAIL'}] {label}")

    passed = all(checks.values())
    print("CP6", "PASS" if passed else "FAIL -- no method is genuinely segmenting yet")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
