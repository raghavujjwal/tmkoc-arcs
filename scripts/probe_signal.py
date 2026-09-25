"""Is the YouTube description actually episode-specific, or is it shared boilerplate?

This gates Phase 0. The whole plan assumed descriptions are the rich signal that titles
lack. If every episode ships the same Sony SAB template, persisting them buys nothing and
would dilute the embedding, and the plan must change.
"""
from __future__ import annotations

import difflib
import re
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from bench_fetch import via_watchpage
from config import EPISODES_PARQUET

N = 24


def norm_lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def main() -> None:
    df = pd.read_parquet(EPISODES_PARQUET)
    real = df[~df.is_precap].dropna(subset=["video_id"])
    # Spread the sample across the run so we see early, middle and late-era templates.
    sample = real.iloc[:: max(1, len(real) // N)].head(N)

    with ThreadPoolExecutor(max_workers=8) as ex:
        descs = list(ex.map(lambda v: via_watchpage(v, requests.Session()), sample.video_id))

    got = [(ep, t, d) for (ep, t), d in zip(zip(sample.ep_number, sample.title), descs) if d]
    print(f"fetched {len(got)}/{len(sample)} descriptions\n")

    lengths = [len(d) for _, _, d in got]
    print(f"length: min {min(lengths)} / median {sorted(lengths)[len(lengths)//2]} / max {max(lengths)}")

    # Lines appearing in >60% of descriptions are template, not content.
    counts: dict[str, int] = {}
    for _, _, d in got:
        for ln in set(norm_lines(d)):
            counts[ln] = counts.get(ln, 0) + 1
    shared = {ln for ln, c in counts.items() if c > len(got) * 0.6}

    print(f"\nlines shared by >60% of episodes: {len(shared)}")
    for ln in list(shared)[:6]:
        print(f"  | {ln[:100]}")

    print("\nper-episode residue after stripping shared lines:")
    residues = []
    for ep, title, d in got[:12]:
        uniq = [ln for ln in norm_lines(d) if ln not in shared]
        residue = " ".join(uniq)
        residues.append(residue)
        print(f"  ep {ep:<5} {len(residue):>5} chars | {residue[:110]}")

    # How similar are the residues to each other? High similarity == still boilerplate.
    pairs = [
        difflib.SequenceMatcher(None, residues[i], residues[i + 1]).ratio()
        for i in range(len(residues) - 1)
        if residues[i] and residues[i + 1]
    ]
    if pairs:
        print(f"\nmean pairwise similarity of residues: {sum(pairs)/len(pairs):.2f}")

    title_lens = [len(t) for _, t, _ in got]
    print(f"mean title length:   {sum(title_lens)/len(title_lens):.0f} chars")
    if residues:
        print(f"mean residue length: {sum(len(r) for r in residues)/len(residues):.0f} chars")

    print("\nVERDICT:", end=" ")
    mean_res = sum(len(r) for r in residues) / max(1, len(residues))
    if mean_res < 40:
        print("descriptions are BOILERPLATE -- titles remain the primary signal.")
    elif pairs and sum(pairs) / len(pairs) > 0.7:
        print("residues are near-identical -- low marginal signal.")
    else:
        print("descriptions carry episode-specific content -- worth persisting.")


if __name__ == "__main__":
    main()
