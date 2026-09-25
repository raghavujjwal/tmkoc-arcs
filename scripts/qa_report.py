"""CP3 -- validate the Phase 0 dataset and write reports/phase0_qa.md.

The point is to make the dataset's weaknesses explicit and quantified before any of it
is used for segmentation, so a bad downstream result can be traced to its cause instead
of guessed at.
"""
from __future__ import annotations

import sys
from datetime import datetime

import pandas as pd

from config import EPISODES_PARQUET, REPORT_DIR
from embed import build_texts, strip_title_boilerplate

BANDS = [(0, 999), (1000, 1999), (2000, 2999), (3000, 3999), (4000, 4999)]


def informative_len(title: str) -> int:
    return len(strip_title_boilerplate(title))


def main() -> int:
    df = pd.read_parquet(EPISODES_PARQUET)
    df = build_texts(df).sort_values("ep_number").reset_index(drop=True)
    df["title_info_len"] = df.title.map(informative_len)
    real = df[~df.is_precap]

    lines: list[str] = []
    w = lines.append

    w("# Phase 0 dataset QA")
    w("")
    w(f"Generated {datetime.now():%Y-%m-%d %H:%M} from `data/episodes.parquet`.")
    w("")
    w("## Totals")
    w("")
    w("| metric | value |")
    w("|---|---|")
    w(f"| rows | {len(df)} |")
    w(f"| real episodes | {len(real)} |")
    w(f"| precaps flagged | {int(df.is_precap.sum())} |")
    w(f"| episode range | {df.ep_number.min()}–{df.ep_number.max()} |")
    w(f"| gaps in range | {len(set(range(df.ep_number.min(), df.ep_number.max() + 1)) - set(df.ep_number))} |")
    w(f"| null video_id | {int(df.video_id.isna().sum())} |")
    w(f"| has fallback link | {int(df.fallback_id.notna().sum())} ({df.fallback_id.notna().mean()*100:.0f}%) |")
    w(f"| has short link | {int(df.short_id.notna().sum())} ({df.short_id.notna().mean()*100:.0f}%) |")
    w(f"| descriptions cached | {int(df.raw_description.notna().sum())} |")
    w(f"| usable synopsis | {int(df.has_synopsis.sum())} ({df.has_synopsis.mean()*100:.1f}%) |")
    w("")

    w("## Signal coverage by era")
    w("")
    w("The two signals are complementary **only from ep 2000 onward**, where the era with")
    w("the worst titles (92% dead in 2000-2999) has the best synopses (93%) and the two")
    w("together cover 99%. Before ep 2000 they fail *together*: eps 0-999 are 92% dead")
    w("titles AND 22% synopsis coverage, leaving 29% of episodes with any text at all.")
    w("Descriptions are now 100% fetched, so that gap is permanent -- those uploads carry")
    w("promo boilerplate and no plot. It is a property of the source, not of our fetching.")
    w("")
    w("| episode band | n | dead titles | synopsis coverage | either signal |")
    w("|---|---|---|---|---|")
    for lo, hi in BANDS:
        g = real[(real.ep_number >= lo) & (real.ep_number <= hi)]
        if g.empty:
            continue
        dead = (g.title_info_len < 10)
        syn = g.has_synopsis
        either = (~dead) | syn
        w(f"| {lo}–{hi} | {len(g)} | {int(dead.sum())} ({dead.mean()*100:.0f}%) "
          f"| {int(syn.sum())} ({syn.mean()*100:.0f}%) | {either.mean()*100:.0f}% |")
    w("")

    w("## Known defects, inherited and fixed")
    w("")
    w("| defect | upstream | here |")
    w("|---|---|---|")
    w("| CSV header declares 4 columns, rows carry 8 | present | fixed: typed schema |")
    w("| air date is really the YouTube upload date | conflated | separated as `youtube_upload_date` + `date_source` |")
    w("| dates synthesised from `epNum * 1.378` when missing | `js/api.js:18` | never fabricated; missing stays null |")
    w("| precaps stored as real episodes | present | flagged via `is_precap` |")
    w("| arc boundary overlaps (`endEp == startEp`) | 10 pairs | reconciled in ground truth |")
    w("")

    dur = real.duration_sec.dropna()
    w("## Duration sanity")
    w("")
    w(f"- median {int(dur.median())}s, p05 {int(dur.quantile(0.05))}s, p95 {int(dur.quantile(0.95))}s")
    w(f"- suspiciously short (<10 min) but not flagged precap: "
      f"{int((dur < 600).sum())}")
    w(f"- missing duration: {int(real.duration_sec.isna().sum())}")
    w("")

    w("## Date provenance")
    w("")
    for src, n in df.date_source.value_counts().items():
        w(f"- `{src}`: {n}")
    w("")
    w("These are upload dates, so they do not order the episodes chronologically by")
    w("broadcast — ep 1 was uploaded in 2017, long after ep 1000. Do not sort by them.")
    w("")

    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / "phase0_qa.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwrote {out}")

    passed = (
        int(df.video_id.isna().sum()) == 0
        and not df.ep_number.duplicated().any()
        and len(real) > 4700
    )
    print("CP3", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
