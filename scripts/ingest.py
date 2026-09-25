"""CP1 -- seed the episode table from upstream's CSV into a clean, typed schema.

Decision: hybrid seed. The episode -> video-id mapping upstream curated over months is
expensive to rebuild and already correct, so we take it. Everything else is re-derived
here, because upstream's schema has known defects:

  * the header declares 4 columns while every row carries 8
  * promos/precaps are stored as if they were real episodes
  * the date column is the YouTube *upload* date, not the original broadcast date
    (ep 1 aired July 2008; the CSV says 12 Jun 2017) -- upstream's frontend compounds
    this by synthesising a fake date from `epNum * 1.378` when the cell is empty
    (js/api.js:18). We never fabricate; missing stays missing.

We do NOT re-scrape the six channel listings: that rebuilds something already correct
and risks a worse result.
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import date, datetime

import pandas as pd

from config import EPISODES_PARQUET, PRECAP_MAX_SECONDS, PRECAP_TITLE_RE, UPSTREAM_CSV

COLUMNS = ["ep", "title", "url", "status", "date", "duration", "fallback_url", "short_url"]

VIDEO_ID_RE = re.compile(r"(?:v=|/embed/|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})(?:[&?]|$)")
# Mirrors upstream's extractRealEpNumber (js/api.js:26) -- the CSV's own ep cell is a
# positional counter, while the title often carries the true broadcast number.
TITLE_EP_RE = re.compile(r"(?:full\s+)?(?:ep|episode|ep\.|एपिसोड)\s*#?\s*(\d{1,4})", re.I)
PRECAP_RE = re.compile(PRECAP_TITLE_RE, re.I)


def extract_video_id(url: str) -> str | None:
    if not url:
        return None
    m = VIDEO_ID_RE.search(url.strip())
    return m.group(1) if m else None


def extract_ep_number(title: str, csv_ep: int) -> int:
    """Prefer the episode number written in the title; fall back to the CSV counter."""
    if title:
        m = TITLE_EP_RE.search(title)
        if m:
            n = int(m.group(1))
            if 0 < n <= 4999:
                return n
    return csv_ep


def parse_duration(text: str) -> int | None:
    """'21:45' -> 1305 seconds. Returns None on anything unparseable."""
    if not text or ":" not in text:
        return None
    try:
        parts = [int(p) for p in text.strip().split(":")]
    except ValueError:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def parse_upload_date(text: str) -> date | None:
    """'12 Jun 2017' -> date. This is the YouTube upload date, not the air date."""
    if not text:
        return None
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def is_precap(title: str, duration_sec: int | None) -> bool:
    if PRECAP_RE.search(title or ""):
        return True
    return duration_sec is not None and duration_sec < PRECAP_MAX_SECONDS


def build() -> pd.DataFrame:
    with UPSTREAM_CSV.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))

    records = []
    for row in rows[1:]:  # header declares the wrong arity; skip it positionally
        if len(row) < 4 or not row[0].strip().isdigit():
            continue
        row = row + [""] * (len(COLUMNS) - len(row))
        csv_ep = int(row[0])
        title = row[1].strip()
        duration_text = row[5].strip()
        duration_sec = parse_duration(duration_text)
        upload_date = parse_upload_date(row[4])

        records.append(
            {
                "ep_number": extract_ep_number(title, csv_ep),
                "csv_ep_number": csv_ep,
                "title": title,
                "url": row[2].strip(),
                "video_id": extract_video_id(row[2]),
                "fallback_id": extract_video_id(row[6]),
                "short_id": extract_video_id(row[7]),
                "duration_text": duration_text or None,
                "duration_sec": duration_sec,
                "youtube_upload_date": upload_date,
                "date_source": "csv" if upload_date else "unknown",
                "is_precap": is_precap(title, duration_sec),
                "status": row[3].strip() or None,
                "description": None,
                "desc_fetched_at": None,
            }
        )

    df = pd.DataFrame.from_records(records)
    df = df.sort_values("ep_number", kind="stable").reset_index(drop=True)
    df["duration_sec"] = df["duration_sec"].astype("Int64")
    df["is_precap"] = df["is_precap"].astype(bool)
    return df


def main() -> int:
    df = build()

    collisions = df[df.duplicated("ep_number", keep=False)]
    real = df[~df.is_precap]

    print(f"rows ingested       {len(df)}")
    print(f"real episodes       {len(real)}")
    print(f"precaps flagged     {int(df.is_precap.sum())}")
    print(f"ep_number range     {df.ep_number.min()} - {df.ep_number.max()}")
    print(f"null video_id       {int(df.video_id.isna().sum())}")
    print(f"has fallback        {int(df.fallback_id.notna().sum())}")
    print(f"has short           {int(df.short_id.notna().sum())}")
    print(f"dated               {int(df.youtube_upload_date.notna().sum())}")
    print(f"renumbered by title {int((df.ep_number != df.csv_ep_number).sum())}")

    if not collisions.empty:
        print(f"\nep_number collisions after title-renumbering: {len(collisions)}")
        for _, r in collisions.head(10).iterrows():
            print(f"  ep {r.ep_number:<5} (csv {r.csv_ep_number:<5}) {r.title[:70]}")

    df.to_parquet(EPISODES_PARQUET, index=False)
    print(f"\nwrote {EPISODES_PARQUET} ({EPISODES_PARQUET.stat().st_size / 1e6:.2f} MB)")

    # CP1 exit test
    passed = (
        len(df) >= 4700
        and int(df.video_id.isna().sum()) == 0
        and collisions.empty
    )
    print("CP1", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
