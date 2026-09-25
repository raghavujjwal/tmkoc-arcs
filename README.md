# tmkoc-arcs

A reproducible **story-arc segmenter and summarizer** for *Taarak Mehta Ka Ooltah Chashmah*.

The show has 4,800+ episodes. Existing arc lists are hand-curated once, carry no
descriptions, and go stale within days because the show airs daily. This project treats
arc detection as **linear text segmentation** over the episode sequence — which makes it
both automatable and measurable (Pk / WindowDiff against a Wikipedia ground-truth set) —
then generates a real synopsis, character list, and semantic tags for every arc.

**Status:** Phases 0, 1, 3 and 4 complete and passing their exit tests. Phase 2
(summaries) is rate-limited — see *Known limits* below.

| checkpoint | what it proves | status |
|---|---|---|
| CP0–CP3 | clean ingest, 100% of descriptions fetched, dataset QA | PASS |
| CP4–CP6 | ground truth, embeddings, segmenter beats baseline | PASS |
| CP7–CP8 | arc summaries | blocked on API quota |
| CP9–CP10 | open-arc sealing, incremental re-sync | PASS |
| CP11–CP12 | site data, browsable frontend | PASS |

Headline result: on the regions that carry usable text, `pelt-pen1` reaches
**Pk 0.298 / F1 0.721** against a fixed-width baseline's 0.541 / 0.388, predicting 30
boundaries where 29 exist.

## Run it

```bash
python scripts/doctor.py             # CP0  deps, API key, disk
python scripts/ingest.py             # CP1  episodes.parquet
python scripts/fetch_descriptions.py # CP2  resumable; --workers 5 --delay 0.4
python scripts/qa_report.py          # CP3  reports/phase0_qa.md
python scripts/embed.py              # CP5  embeddings.npy (~70 min, CPU)
python scripts/segment.py            # CP6  evaluate against ground truth
python scripts/sync.py               # CP9/CP10  incremental arcs.json
python scripts/summarize.py --limit 60   # CP7/CP8  summaries (quota-limited)
python scripts/build_site.py         # CP11 site/arcs.json
python scripts/check_site.py         # CP12 frontend checks
```

`sync.py` replaces a full re-segmentation on every run: sealed arcs are kept and only the
tail past the last sealed boundary is recomputed.

## The site

<https://raghavujjwal.github.io/tmkoc-arcs/>

A live field of all 419 arcs in broadcast order — one band per era, one soft body per arc
(size = episodes), a thread running through them in sequence. The pointer is a magnetic
lens: the arc under it swells and blooms into its episodes while its neighbours part to
make room, then spring back as you move on. Arrow keys step the lens through the run;
clicking opens a drawer with the synopsis, every episode link, and previous/next arc.
Search and filters dim non-matching arcs live. Below it, the same arcs as a list.

Geometry is tested at phone, tablet and desktop widths (`node scripts/check_field.js`):
no arcs overlap, every arc is pickable including the 106 spanning 2–4 episodes, the
hovered arc stays under the pointer, and broadcast order runs continuously.

## Episode links

All **4,819** episode links verified against YouTube: **100% resolve, 0 broken**, and
**419/419 arcs** have every one of their episodes linked (`python scripts/verify_links.py`,
audit written to `reports/link_audit.json`).

Three episodes (2022, and two of 3859/3860/3862) are mapped by upstream onto another
episode's video. The links work but play the wrong episode, so they are flagged in the
data and labelled in the site rather than silently corrected.

## Known limits

- **Summaries are 2.1% complete (9 of 419 arcs).** The Gemini free tier allows
  **20 requests per day, per model**; rotating three models gives ~60/day. This is a
  request cap, not a token cap — throttling does not help. Enabling billing would finish
  the run in ~45 minutes for roughly $0.15.
- **Episodes 1–1999 are largely untextable.** Even with 100% of descriptions fetched,
  only 29% of eps 0–999 carry any story text; the rest are promo boilerplate. Arc
  detection there is weak and honestly labelled as such (`signal` per arc).
- **The eval set sits where the data is darkest.** The Wikipedia ground truth covers the
  early episodes (39% text coverage) while the usable text is in eps 2000+ (98%). Only
  4 of 13 ground-truth regions are evaluable; the other 9 are reported as dark rather
  than averaged into the score.

## Read this first

[`docs/DESIGN.md`](docs/DESIGN.md) — the full design: an audit of the prior art
([Daily-Dose-of-TMOCK](https://github.com/CodeMasterAbhishek/Daily-Dose-of-TMOCK)),
measured data findings, the five-phase build plan, and the evaluation strategy.

## Stack

Python 3.11+ · `sentence-transformers` (multilingual — the corpus is Hinglish/Devanagari
mixed) · `ruptures` for change-point detection · `yt-dlp` · `scrapetube` ·
Gemini 2.5 Flash-Lite for summarization · vanilla JS frontend.

## Related

Reference clone of the prior-art repo lives at `D:\dev\Daily-Dose-of-TMOCK` (read-only).
