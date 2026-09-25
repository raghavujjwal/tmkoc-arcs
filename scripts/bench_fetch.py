"""Benchmark description-fetch strategies on a small sample before the full run.

4,817 episodes at 1.5 s each is ~2 hours single-threaded, so it is worth 60 seconds to
find out which extractor is actually fastest and whether it survives concurrency.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import yt_dlp

from config import EPISODES_PARQUET

SAMPLE = 8
SHORT_DESC_RE = re.compile(r'"shortDescription":"((?:[^"\\]|\\.)*)"')

YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": False,
    # Skip the expensive signature-JS download and manifest parsing; we only want metadata.
    "extractor_args": {"youtube": {"player_skip": ["js"], "skip": ["hls", "dash"]}},
}


def via_ytdlp(video_id: str) -> str | None:
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return (info or {}).get("description")


def via_watchpage(video_id: str, session: requests.Session) -> str | None:
    """Pull shortDescription straight out of the watch page's embedded JSON."""
    r = session.get(
        f"https://www.youtube.com/watch?v={video_id}",
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
        timeout=20,
    )
    r.raise_for_status()
    m = SHORT_DESC_RE.search(r.text)
    if not m:
        return None
    return json.loads(f'"{m.group(1)}"')


def timed(label, fn, ids):
    start = time.perf_counter()
    results = [fn(v) for v in ids]
    elapsed = time.perf_counter() - start
    hits = sum(1 for r in results if r)
    per = elapsed / len(ids)
    total_est = per * 4817 / 60
    print(f"{label:<28} {hits}/{len(ids)} hits  {per:.2f}s/video  ~{total_est:.0f} min serial")
    return results


def main() -> None:
    df = pd.read_parquet(EPISODES_PARQUET)
    ids = df[~df.is_precap].video_id.dropna().sample(SAMPLE, random_state=0).tolist()

    session = requests.Session()
    a = timed("yt-dlp (serial)", via_ytdlp, ids)
    b = timed("watchpage (serial)", lambda v: via_watchpage(v, session), ids)

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as ex:
        c = list(ex.map(lambda v: via_watchpage(v, requests.Session()), ids))
    elapsed = time.perf_counter() - start
    hits = sum(1 for r in c if r)
    print(
        f"{'watchpage (8 workers)':<28} {hits}/{len(ids)} hits  "
        f"{elapsed / len(ids):.2f}s/video  ~{elapsed / len(ids) * 4817 / 60:.0f} min wall"
    )

    agree = sum(1 for x, y in zip(a, b) if x and y and x.strip() == y.strip())
    print(f"\nyt-dlp vs watchpage agreement: {agree}/{SAMPLE}")
    for r in b:
        if r:
            print(f"\nsample description ({len(r)} chars):\n{r[:400]}")
            break


if __name__ == "__main__":
    main()
