"""Strip Sony SAB boilerplate from a YouTube description, leaving the episode synopsis.

Descriptions are era-dependent. SAB-era uploads open with a real synopsis
("Ep 1401 - ...: Jethalal and Sundar arrive in election commission office..."), while
TMKOC-Official-era uploads are pure promo furniture with no synopsis at all. This module
isolates the genuine prose so downstream code can tell the two apart instead of
embedding subscribe links.
"""
from __future__ import annotations

import re

# Everything from here on is the same generic show blurb on every upload.
CUT_MARKERS = (
    "about taarak mehta ka ooltah chashmah",
    "the show is inspired from the famous humorous column",
    "the show is inspired by the famous humorous column",
)

JUNK_LINE_RE = re.compile(
    r"(subscribe|click here|click to watch|playlist\?list=|youtube\.com|youtu\.be"
    r"|sonyliv|sonypal|share this episode|watch more|follow us|download the app"
    r"|we bring to you the best videos|get your daily dose|watch the full episodes"
    r"|^-{5,}$|^\W*$)",
    re.I,
)

# A line that is only "Episode 1001" / "Ep 1001" carries no information.
BARE_EP_RE = re.compile(r"^\s*(?:full\s+)?ep(?:isode)?\.?\s*#?\s*\d{1,4}\s*$", re.I)

# "Ep 1401 - Taarak Mehta Ka Ooltah Chashmah:" / "Episode 1401 -" etc.
EP_PREFIX_RE = re.compile(
    r"^\s*(?:full\s+)?ep(?:isode)?\.?\s*#?\s*\d{1,4}\s*[-:|]?\s*"
    r"(?:taarak mehta ka ooltah chashmah\s*[-:|]?\s*)?",
    re.I,
)

TRAILING_PROMO_RE = re.compile(
    r"watch\s+(?:latest\s+)?(?:videos|full episodes?)\s+of\s+ta?arak\s+mehta.*$", re.I
)

MIN_SYNOPSIS_CHARS = 60


def clean_description(raw: str | None) -> str:
    """Return the episode-specific synopsis, or '' when the upload carries none."""
    # pandas hands us NaN (a float) for missing cells, and NaN is truthy.
    if not isinstance(raw, str) or not raw.strip():
        return ""

    text = raw.replace("\r\n", "\n")

    # 1. Truncate at the generic "About the show" section.
    low = text.lower()
    cut = len(text)
    for marker in CUT_MARKERS:
        idx = low.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    text = text[:cut]

    # 2. Drop promo furniture line by line.
    kept = [
        ln.strip()
        for ln in text.split("\n")
        if ln.strip() and not JUNK_LINE_RE.search(ln) and not BARE_EP_RE.match(ln)
    ]
    body = " ".join(kept)

    # 3. Strip the "Ep NNNN - <show>:" prefix and any trailing promo sentence.
    body = EP_PREFIX_RE.sub("", body)
    body = TRAILING_PROMO_RE.sub("", body)

    # 4. Normalise whitespace and stray quoting.
    body = re.sub(r'""+', '"', body)
    body = re.sub(r"\s+", " ", body).strip(" \"'-:| ")

    return body if len(body) >= MIN_SYNOPSIS_CHARS else ""


def has_synopsis(raw: str | None) -> bool:
    return bool(clean_description(raw))
