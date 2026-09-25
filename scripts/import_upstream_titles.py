"""Import hand-written arc titles from the upstream fan project, cleaned.

Daily-Dose-of-TMOCK (MIT, (c) 2024 CodeMasterAbhishek) curated 672 arcs with human titles
such as "Mischievous Tapu" or "Champaklal's Arrival". Where one of those arcs covers most
of one of ours, its title is a far better name than anything we can derive -- especially
for the arcs whose episodes carry no usable title of their own.

But the titles cannot be taken as-is. 113 of the 672 are placeholders ("Episodes 511-516",
"Ep 1186", "Introduction"), and others are raw YouTube titles full of the show's name and
upload dates. Each title is run through the same boilerplate stripper the pipeline uses
for episode titles, and anything left generic or empty is dropped. Borrowing a placeholder
would only swap one "Episodes a-b" for another.

Writes data/upstream_arc_titles.json and carries the MIT licence notice alongside it, as
the licence requires for copied material.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from config import DATA_DIR
from embed import strip_title_boilerplate

UPSTREAM = Path(r"D:\dev\Daily-Dose-of-TMOCK")
OUT = DATA_DIR / "upstream_arc_titles.json"
LICENSE_OUT = DATA_DIR / "UPSTREAM_LICENSE.txt"

GENERIC = re.compile(
    r"^(?:(?:episodes?|eps?)\.?\s*\d+(?:\s*[-–]\s*\d+)?|introduction|intro|arc\s*\d*|part\s*\d+"
    r"|full\s*episode|special|\d+)$",
    re.I,
)


# Only raw YouTube-style titles get the boilerplate stripper. Run on a clean human title it
# does damage: it removed the year from "Diwali (2008)" -- the very thing that separates one
# Diwali arc from another -- and reduced "GPL 1" (Gokuldham Premier League) to "GPL".
RAW_YT = re.compile(
    r"ta+ra+k\s*mehta|तारक|full\s*episode|एपिसोड|\bep(?:isode)?s?\.?\s*#?\d"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+[a-z]{3,9},?\s+\d{4}|\|",
    re.I,
)


def clean(title: str) -> str | None:
    t = (title or "").strip()
    if RAW_YT.search(t):
        # Keep a "Part N" suffix: the stripper treats the number as boilerplate, which
        # turned "... | FULL MOVIE | Part 2" into "... FULL MOVIE Part".
        part = re.search(r"\bpart\s*(\d+)\b", t, re.I)
        t = strip_title_boilerplate(t)
        t = re.sub(r"\bfull\s*movie\b|\bpart\b\s*$|\bFE\b\s*$", "", t, flags=re.I)  # FE = "full episode"
        if part:
            t = t.strip(" -–|:,.") + f" (Part {part.group(1)})"
    # An "Ep 1956 - " prefix in front of a real name ("Ep 1956 - Bagha Bawri Ki
    # Engagement?!") is stripped from every title, not only the raw YouTube-style ones.
    t = re.sub(r"^\s*(?:ep(?:isode)?s?\.?\s*#?\d+(?:\s*[-–]\s*\d+)?)\s*[-–:|]\s*", "", t, flags=re.I)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–|:,.")
    if len(t) < 3 or GENERIC.match(t):
        return None
    return t


def main() -> int:
    src = UPSTREAM / "data" / "storylines.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    kept, dropped = [], []
    for a in raw:
        t = clean(a.get("title", ""))
        rec = {"start_ep": int(a["startEp"]), "end_ep": int(a["endEp"])}
        if t:
            kept.append({**rec, "title": t, "original": a.get("title", "")})
        else:
            dropped.append(a.get("title", ""))

    OUT.write_text(json.dumps({
        "source": "Daily-Dose-of-TMOCK data/storylines.json",
        "license": "MIT, (c) 2024 CodeMasterAbhishek -- see UPSTREAM_LICENSE.txt",
        "note": "titles cleaned of show-name/date boilerplate; placeholders dropped",
        "n_upstream": len(raw), "n_kept": len(kept), "n_dropped": len(dropped),
        "arcs": kept,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copyfile(UPSTREAM / "LICENSE", LICENSE_OUT)

    changed = [k for k in kept if k["title"] != k["original"].strip()]
    print(f"upstream arcs   {len(raw)}")
    print(f"kept            {len(kept)}  ({len(changed)} cleaned of boilerplate)")
    print(f"dropped         {len(dropped)}  e.g. {dropped[:6]}")
    for k in changed[:4]:
        print(f"  cleaned: {k['original'][:70]!r}\n        -> {k['title']!r}")
    print(f"wrote {OUT} and {LICENSE_OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
