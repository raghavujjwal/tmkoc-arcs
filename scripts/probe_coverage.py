"""Estimate what fraction of episodes actually carry a usable synopsis, by era.

This is the real Phase 0 gate. If synopsis coverage is high, descriptions become the
primary segmentation signal. If it is low, titles stay primary and descriptions are a
bonus feature -- and Phase 1 must be designed around titles.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from bench_fetch import via_watchpage
from config import EPISODES_PARQUET
from textclean import clean_description

N = 120


def fetch(video_id: str) -> str | None:
    try:
        return via_watchpage(video_id, requests.Session())
    except Exception:
        return None


def main() -> None:
    df = pd.read_parquet(EPISODES_PARQUET)
    real = df[~df.is_precap].dropna(subset=["video_id"]).sort_values("ep_number")
    sample = real.iloc[:: max(1, len(real) // N)].head(N).copy()

    with ThreadPoolExecutor(max_workers=10) as ex:
        sample["raw"] = list(ex.map(fetch, sample.video_id))

    sample["synopsis"] = sample.raw.map(clean_description)
    sample["ok"] = sample.synopsis.str.len() > 0

    fetched = sample.raw.notna().sum()
    print(f"sampled {len(sample)} episodes across eps {sample.ep_number.min()}-{sample.ep_number.max()}")
    print(f"fetched successfully   {fetched}/{len(sample)}")
    print(f"with usable synopsis   {int(sample.ok.sum())}/{fetched} "
          f"({sample.ok.sum() / max(1, fetched) * 100:.0f}%)")
    print(f"median synopsis length {int(sample[sample.ok].synopsis.str.len().median() or 0)} chars")
    print(f"median title length    {int(sample.title.str.len().median())} chars")

    print("\ncoverage by era (1000-episode bands):")
    sample["band"] = (sample.ep_number // 1000) * 1000
    for band, grp in sample.groupby("band"):
        got = grp.raw.notna().sum()
        print(f"  ep {band:>4}-{band + 999:<4}  {int(grp.ok.sum()):>3}/{got:<3} "
              f"({grp.ok.sum() / max(1, got) * 100:>3.0f}%)")

    print("\nexamples of extracted synopses:")
    for _, r in sample[sample.ok].head(5).iterrows():
        print(f"  ep {r.ep_number}: {r.synopsis[:150]}")

    print("\nexamples with NO synopsis (title is all we get):")
    for _, r in sample[~sample.ok & sample.raw.notna()].head(3).iterrows():
        print(f"  ep {r.ep_number}: {r.title[:110]}")

    rate = sample.ok.sum() / max(1, fetched)
    print("\nGATE:", end=" ")
    if rate >= 0.6:
        print(f"{rate*100:.0f}% coverage -- descriptions are the primary signal. Proceed as planned.")
    elif rate >= 0.25:
        print(f"{rate*100:.0f}% coverage -- PARTIAL. Titles stay primary; descriptions augment.")
    else:
        print(f"{rate*100:.0f}% coverage -- descriptions are NOT a usable signal. Replan Phase 1.")


if __name__ == "__main__":
    sys.exit(main())
