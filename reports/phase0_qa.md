# Phase 0 dataset QA

Generated 2026-09-25 17:16 from `data/episodes.parquet`.

## Totals

| metric | value |
|---|---|
| rows | 4819 |
| real episodes | 4817 |
| precaps flagged | 2 |
| episode range | 1–4825 |
| gaps in range | 6 |
| null video_id | 0 |
| has fallback link | 3762 (78%) |
| has short link | 1265 (26%) |
| descriptions cached | 4818 |
| usable synopsis | 3089 (64.1%) |

## Signal coverage by era

The two signals are complementary **only from ep 2000 onward**, where the era with
the worst titles (92% dead in 2000-2999) has the best synopses (93%) and the two
together cover 99%. Before ep 2000 they fail *together*: eps 0-999 are 92% dead
titles AND 22% synopsis coverage, leaving 29% of episodes with any text at all.
Descriptions are now 100% fetched, so that gap is permanent -- those uploads carry
promo boilerplate and no plot. It is a property of the source, not of our fetching.

| episode band | n | dead titles | synopsis coverage | either signal |
|---|---|---|---|---|
| 0–999 | 999 | 926 (93%) | 224 (22%) | 29% |
| 1000–1999 | 1000 | 593 (59%) | 208 (21%) | 60% |
| 2000–2999 | 996 | 913 (92%) | 931 (93%) | 99% |
| 3000–3999 | 998 | 264 (26%) | 933 (93%) | 95% |
| 4000–4999 | 824 | 3 (0%) | 792 (96%) | 100% |

## Known defects, inherited and fixed

| defect | upstream | here |
|---|---|---|
| CSV header declares 4 columns, rows carry 8 | present | fixed: typed schema |
| air date is really the YouTube upload date | conflated | separated as `youtube_upload_date` + `date_source` |
| dates synthesised from `epNum * 1.378` when missing | `js/api.js:18` | never fabricated; missing stays null |
| precaps stored as real episodes | present | flagged via `is_precap` |
| arc boundary overlaps (`endEp == startEp`) | 10 pairs | reconciled in ground truth |

## Duration sanity

- median 1233s, p05 1096s, p95 1328s
- suspiciously short (<10 min) but not flagged precap: 2
- missing duration: 0

## Date provenance

- `csv`: 4818
- `unknown`: 1

These are upload dates, so they do not order the episodes chronologically by
broadcast — ep 1 was uploaded in 2017, long after ep 1000. Do not sort by them.
