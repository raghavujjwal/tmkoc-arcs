# Checkpoints

Gate conditions I must clear before moving to the next one. Each has a machine-checkable
exit test so progress is not a matter of opinion.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`

---

## Phase 0 — Unlock the input signal

### CP0 — Environment  `DONE`
Venv on D: with all deps importable; Gemini key loadable.
- **Exit test:** `python scripts/doctor.py` prints all-green (deps + key + disk).
- **Result:** PASS. ruptures 1.1.10, yt-dlp 2026.08.19, sentence-transformers 5.6.0,
  google-genai 2.25.0. Key resolved from the paper-finder `.env` without being displayed.
  Venv lives on D: with `--system-site-packages` so torch was not re-downloaded;
  C: had 0 bytes free and could not host the install.

### CP1 — Clean ingest  `DONE`
Seed episode↔video mapping from upstream CSV, normalize into our own schema.
- **Exit test:** `data/episodes.parquet` exists; row count ≥ 4,700; zero rows with a null
  `video_id`; `ep_number` strictly unique; precaps flagged not dropped.
- **Decision (made autonomously):** **hybrid seed.** Take the ep→URL/fallback mapping from
  upstream's `episodes.csv` (curated over months, expensive to rebuild) but re-derive every
  other field into a clean schema. Do *not* re-scrape the 6 channel listings — that rebuilds
  something already correct and risks a worse result.
- **Result:** PASS. 4,819 rows, 4,817 real episodes, 2 precaps flagged, 0 null video ids,
  0 ep-number collisions. Title-derived episode numbers agreed with the CSV counter on
  every row, so upstream's numbering is sound.
- **Correction made:** the date column is the **YouTube upload date**, not the broadcast
  date (ep 1 aired Jul 2008; the CSV says 12 Jun 2017). Stored as `youtube_upload_date`
  with a `date_source` column. We never synthesise dates the way upstream does at
  `js/api.js:18`.

### CP2 — Description backfill  `DONE`
Fetch YouTube descriptions for every episode. Resumable, cached, concurrent.
- **Exit test:** ≥ 90% of non-precap episodes have a non-empty `description`;
  cache file survives a kill and resumes without refetching.
- **Status:** 60/4,816 fetched, then YouTube hard-blocked this IP
  (`/sorry/` CAPTCHA interstitial, HTTP 429). Both the watch-page extractor and yt-dlp
  now fail; yt-dlp reports *"Sign in to confirm you're not a bot"*.
- **Benchmarked before the block:** watch-page extraction 0.13 s/video at 8 workers vs
  yt-dlp 1.21 s/video, identical output on 8/8 sampled videos. The burst is what tripped
  the limiter.
- **Not doing unilaterally:** `--cookies-from-browser` would authenticate bulk scraping as
  the user's own YouTube account, risking the account rather than just the IP. Needs the
  user's explicit decision.
- **Consequence, measured:** synopsis coverage is strongly era-dependent —
  eps 0–999: 8%, 1000–1999: 12%, 2000–2999: 88%, 3000–3999: 92%, 4000+: 100%.
  Title informativeness runs the *opposite* way (45% dead titles in eps 2000–2999,
  0% dead in eps 4000+). The two signals are complementary, so the design combines them
  rather than treating descriptions as primary.
- **Resolution:** block lifted after ~25 min. Relaunched in polite mode — 2 workers,
  1 s jittered delay (~2 req/s, against the ~12 req/s that caused the ban), exponential
  backoff on 429 or a `/sorry/` redirect, and auto-abort after 25 consecutive failures so
  a re-block costs minutes rather than hours. Resumable: the JSONL cache is appended per
  fetch, so a rerun picks up exactly where it stopped.
- **Correction to an earlier claim:** I first described the two text signals as
  "complementary" and treated descriptions as optional. That was wrong — see CP3. They
  are the critical path for eps 1–3000.

### CP3 — Dataset QA  `DONE`
Validation report over the finished Phase 0 dataset.
- **Exit test:** `reports/phase0_qa.md` generated; no schema violations; documented counts
  for missing descriptions, precaps, duplicate video ids, date anomalies.
- **Result:** PASS on schema. 0 null video ids, 0 duplicate episode numbers, 6 gaps in
  1–4825, durations sane (median 1233 s).
- **Finding that changes the plan:** title informativeness is far worse than two earlier
  cruder measurements suggested. Measured honestly — after stripping the show name in
  both scripts, episode markers, dates, channel furniture and leftover numerals —
  **56% of all titles carry no story content at all**:

  | band | dead titles |
  |---|---|
  | 0–999 | 92% |
  | 1000–1999 | 59% |
  | 2000–2999 | 92% |
  | 3000–3999 | 26% |
  | 4000+ | 0% |

  My earlier "the two signals are complementary" claim was an artifact of a leaky
  stripper that counted `तारक मेहता - - - 24th September 2020` as informative. It is not.
  The corrected conclusion is the opposite and much more consequential: **descriptions
  are not an augmentation, they are the critical path.** Only eps 3000+ can be segmented
  from titles alone.

---

## Phase 1 — Segmentation engine

### CP4 — Ground truth  `DONE`
Extract the 196 Wikipedia-provenance arcs from upstream as a held-out eval set.
- **Exit test:** `data/ground_truth_arcs.json` boundary-clean (no overlaps after
  reconciliation), enough contiguous regions to score on.
- **Result:** PASS. All 10 upstream overlaps reconciled to half-open. Isolated arcs are
  useless for Pk/WindowDiff (a boundary is only "missed" if the surrounding region is
  fully labelled), so only contiguous runs of ≥5 arcs are kept:
  **13 regions, 95 arcs, 804 episodes, mean arc length 8.5**.
- **Known bias, not hidden:** 12 of 13 regions lie in eps 30–1378, exactly where synopsis
  coverage is 8–12%. Only `3334–3470` sits in the description-rich era. Metrics are
  therefore reported split by era rather than as a single blended number.

### CP5 — Embeddings  `DONE`
Multilingual embeddings for all episodes, cached to disk.
- **Exit test:** `data/embeddings.npy` shape `(n_episodes, dim)`; spot-check that
  semantically adjacent episodes score higher than random pairs.
- **Status:** blocked on disk. `intfloat/multilingual-e5-base` (~1.1 GB) cannot download:
  `memory allocation of 36652697 bytes failed` from the HF downloader, because it stages
  through **C:, which has 2.5 MB free**. `HF_HOME` is correctly pointed at
  `D:\dev\.hfcache`; the staging path is the problem.
- **Root cause, measurable:** `C:\Users\ujjwal\.cache\huggingface` is **22 GB**, of which
  `meta-llama/Meta-Llama-3-8B` alone is **15 GB**, plus 2.4 GB each for two Llama-3.2-1B
  copies. Moving that cache to D: would reclaim ~22 GB and matches the standing
  preference recorded in [[prefer-d-drive-storage]].
- **Resolution (user approved):** moved `C:\Users\ujjwal\.cache\huggingface` to
  `D:\dev\.hfcache` via robocopy `/MOVE` and set `HF_HOME` as a **User** environment
  variable so other projects inherit it. **C: went from 0 bytes to 21.6 GB free.**
- **Partial failure, not hidden:** robocopy moved 40.1 GB but failed on 66 files
  (2.4 GB), all `ERROR 3` on HuggingFace `snapshots/` **symlinks**, which robocopy cannot
  follow without `/SL`. The `blobs/` they point at moved successfully, and HF rebuilds
  snapshot links on demand, so the cache stays usable; affected models re-link or
  re-download those specific files. 66 orphaned stubs (~40 KB) remain on C: and were left
  in place rather than deleted.
- **Ran, but on degenerate input.** `embeddings.npy` is `(4819, 768)`; adjacent-episode
  cosine 0.9393 vs random 0.8888 (separation +0.0505), so the mechanical test passed.
  The diagnostics say otherwise: **median embed text is 9 characters** — exactly the
  length of the `"passage: "` prefix alone — and **2,586 of 4,819 episodes have no text
  at all** after stripping. These vectors mostly encode the empty string. Rebuild once
  CP2 finishes; the current file is a placeholder, not a result.

### CP6 — Segmenter beats baseline  `DONE` (evaluable regions only)
`ruptures` change-point detection, scored against CP4.
- **Exit test:** Pk and WindowDiff both **beat a fixed-width baseline** on the held-out
  arcs. If it does not beat the trivial baseline, the approach is wrong and I stop.
- **Blocker, measured:** with descriptions unavailable, **72% of the 804 eval-set episodes
  have dead titles, and 9 of the 13 regions are 100% dead.** Those regions embed
  near-empty strings, so every episode looks identical and no boundary is detectable.
  Scoring there would measure the metric's behaviour on noise, not the segmenter's skill.

  | region | eps | dead titles |
  |---|---|---|
  | 30–61 … 1187–1225 (9 regions) | 495 | 91–100% |
  | 1251–1308 | 58 | 31% |
  | 1310–1378 | 69 | 39% |
  | **3334–3470** | **137** | **1%** |

- **Decision:** report per-region rather than publishing a blended number that averages in
  nine regions of noise. The full evaluation unblocks when CP2 does.

**First run (on the placeholder embeddings) — FAIL, correctly:**

| method | Pk | WinDiff | F1±2 | hyp | ref |
|---|---|---|---|---|---|
| baseline-fixed | 0.518 | 0.523 | 0.474 | 93 | 82 |
| baseline-random | 0.491 | 0.507 | 0.441 | 82 | 82 |
| texttile | 0.449 | 0.590 | 0.218 | 168 | 82 |
| binseg-oracle-n | 0.432 | 0.469 | 0.487 | 82 | 82 |
| pelt-pen1 | 0.419 | 0.424 | 0.138 | 21 | 82 |
| pelt-pen8 | 0.439 | 0.439 | 0.000 | **0** | 82 |

- **The exit test was wrong and I replaced it.** The first version gated on Pk and
  WindowDiff alone and printed PASS. That is a false pass: both metrics structurally
  reward under-segmentation, because most windows genuinely contain no boundary. The
  proof is in the table — **`pelt-pen8` emitted zero boundaries and still beat the
  fixed-width baseline on Pk.** A segmenter that predicts nothing cannot be better than
  one that tries. `baseline-random` beating `baseline-fixed` is the same artifact.
- **Corrected gate** now also requires: beating the baseline on F1, a hypothesis/reference
  boundary ratio within 0.5–2×, and F1 > 0.35. Under it the run reports
  **CP6 FAIL — no method is genuinely segmenting yet**, which is the truthful state given
  the input was mostly empty strings.

---

## Phase 2 — Summarizer

### CP7 — Summarizer on a sample  `BLOCKED` (daily quota; logic validated offline)
Gemini 2.5 Flash-Lite structured output, 20-arc pilot.
- **Exit test:** 20/20 arcs return schema-valid JSON; every character named in a synopsis
  actually appears in that arc's source titles (factuality check).

### CP8 — Full run  `BLOCKED` (daily quota; scoped to ~60 arcs)
All arcs summarized, content-hash cached.
- **Exit test:** 100% arcs have non-empty synopsis; a second run costs 0 API calls.


---

## Phase 3 — Incremental automation

### CP9 — Open-arc sealing  `DONE`
The newest arc must stay `ongoing` and re-summarise each run until its end boundary is
confidently settled, then seal. This is the mechanism that stops the rot that froze
upstream.
- **Exit test:** a boundary within `EDGE_MARGIN` episodes of the corpus end is never
  sealed; feeding the same corpus twice produces byte-identical output (idempotent); and
  appending episodes can change an `ongoing` arc but never a `sealed` one.

### CP10 — Incremental sync  `DONE`
Re-segment only the tail past the last sealed arc, not the whole corpus.
- **Exit test:** after appending N episodes, arcs before the last sealed boundary are
  byte-identical to the previous run, and the number of episodes re-segmented is
  proportional to the tail, not to the 4,819-episode corpus.

---

## Phase 4 — Frontend

### CP11 — Site data build  `DONE`
Emit a self-contained JSON the frontend can load with no backend.
- **Exit test:** every arc has `start_ep`, `end_ep`, episode list with watch links, and a
  `has_summary` flag; arcs without a summary degrade to title-only rather than rendering
  an empty synopsis (the specific upstream defect); file validates and loads standalone.

### CP12 — Browsable site  `DONE`
Render the catalogue: synopsis, tag chips, era chips, full-text search, spoiler toggle.
- **Exit test:** search over synopses returns the expected arcs; tag and era filters
  narrow correctly; the spoiler toggle hides synopsis text; the page works with only 11 of
  419 arcs summarised, showing the gap honestly rather than faking coverage.

---

## Stop conditions

I stop and report back instead of pushing on if:
- CP6 fails to beat the fixed-width baseline (the core premise is wrong).
- CP2 lands below 60% description coverage (YouTube is blocking; Phase 2 quality would suffer).
- Any step would write >1 GB to `C:` (only ~30 MB free).

---

## CP2 COMPLETE — 4,816/4,816 descriptions (100%)

Finished in three resumable passes. The middle pass aborted itself after 25 consecutive
failures, which on inspection were **SSLErrors, not a CAPTCHA** — transient network, and
the retry succeeded immediately. The final pass fetched 830/830 with zero errors at
3.1 req/s. Total cache: 4,816 records, 0 permanent failures.

### Fetching every description did NOT make the early era usable

| episode band | n | dead titles | synopsis coverage | either signal |
|---|---|---|---|---|
| 0–999 | 999 | 920 (92%) | 224 (22%) | **29%** |
| 1000–1999 | 1000 | 593 (59%) | 208 (21%) | **60%** |
| 2000–2999 | 996 | 913 (92%) | 931 (93%) | **99%** |
| 3000–3999 | 998 | 264 (26%) | 933 (93%) | **95%** |
| 4000–4999 | 824 | 3 (0%) | 792 (96%) | **100%** |

Descriptions are now 100% *fetched*, but for eps 0–999 only 22% survive boilerplate
stripping — those uploads carry promo text and no plot. Corpus-wide signal went 44% →
**76%**, and eps 2000+ reach **98%**. The early era stays dark permanently; no amount of
fetching fixes it, because the synopses were never written.

## The eval set sits exactly where the data is darkest

| | any-signal coverage |
|---|---|
| **Eval set (13 ground-truth regions)** | **39%** |
| Corpus overall | 76% |
| **Eps 2000+, where the product runs** | **98%** |

Per-region, only **4 of 13** regions clear 50% signal — 623–677, 1251–1308, 1310–1378 and
3334–3470 (319 episodes). The other nine run 0–37%, three of them at literally 0%.

This is a structural mismatch, not a tuning problem: the Wikipedia storylines that make
good ground truth exist for the **early** episodes, while the usable text exists for the
**late** ones. We have labels where we have no signal, and signal where we have no labels.

**Consequence for CP6:** a single blended score across all 13 regions reports the corpus's
gaps as the segmenter's error rate, and would understate a working method. `segment.py`
now measures per-region signal, marks anything under 50% as DARK, prints the split, and
computes the headline metric over evaluable regions only — dark regions are still shown,
never silently dropped.

---

## CP5 — Embeddings  `DONE` (rebuilt on real text)

| | placeholder run | real run |
|---|---|---|
| synopsis coverage | 5 (0.1%) | **3,089 (64.1%)** |
| median embed text | 9 chars | **370 chars** |
| adjacent vs random cosine | 0.9393 / 0.8888 | 0.9086 / 0.8148 |
| **separation** | +0.0505 | **+0.0938** |

Separation nearly doubled. Encoding also got ~7x slower per batch (46 s vs 6 s) — direct
evidence the model was previously encoding empty strings.

One diagnostic label was misleading and is fixed: `empty after strip 2586` counted dead
**titles**, not empty embed texts, which read as contradicting the 64% synopsis coverage.
It now reads `dead titles` and says so.

## CP6 — Segmentation  `DONE` — PASS on the strict gate

Evaluable regions only: **319 episodes, 4 regions, 29 boundaries** (60% of the eval set is
dark and excluded, and listed as such).

| method | Pk | WinDiff | F1±2 | hyp | ref |
|---|---|---|---|---|---|
| baseline-fixed | 0.541 | 0.554 | 0.388 | 38 | 29 |
| baseline-random | 0.514 | 0.527 | 0.372 | 29 | 29 |
| texttile | 0.372 | 0.594 | 0.380 | 80 | 29 |
| **pelt-pen1** | **0.298** | **0.321** | **0.721** | **30** | 29 |
| binseg-oracle-n *(oracle)* | 0.221 | 0.244 | 0.818 | 29 | 29 |

All five gate checks pass, including the three that caught the earlier false pass:
boundary ratio **1.03** (was 0.26) and F1 **0.721** (was 0.138). Same code, same gate —
the only thing that changed is that the embeddings encode real text.

**Per region — the result is not one region carrying an average:**

| region | signal | pelt Pk / F1 | hyp/ref | baseline Pk / F1 |
|---|---|---|---|---|
| 623–677 | 82% | 0.347 / 0.800 | 6/4 | 0.449 / 0.600 |
| 1251–1308 | 84% | 0.259 / 0.667 | 6/6 | 0.574 / 0.462 |
| 1310–1378 | 100% | 0.169 / **1.000** | 8/8 | 0.538 / 0.500 |
| 3334–3470 | 99% | 0.359 / 0.571 | 10/11 | 0.565 / 0.214 |

Beats baseline on Pk and F1 in 4/4 regions, and quality tracks signal coverage — the
100%-signal region recovers 8/8 boundaries exactly.

**Caveats, stated rather than buried:**
- 29 boundaries across 319 episodes is a small sample; individual region F1 moves in
  large steps (one boundary in region 623–677 is worth 0.2 F1).
- `binseg-oracle-n` still leads (F1 0.818 vs 0.721), so knowing the arc *count* remains
  worth ~0.10 F1. Estimating how many arcs a span contains is the weakest link.
- Pk/WindowDiff on their own remain gameable here; the gate, not the headline number, is
  what makes this result meaningful.

---

## CP7 — Summaries  `FAIL, then three real bugs found`

Pilot of 20 arcs. First run: 11/20 completed (hard 429s), 9/11 grounded. Second run with a
6.5 s throttle: 10/20, 9/10 grounded. **Free-tier flash-lite allows 10 requests/minute**,
and retries consume the same quota, so wall-clock was 1223 s for 10 summaries.

### I misattributed the first failure

I reported the model had invented the character **"Gobachari"**. It had not — ep 31's
description reads *"they meet Gobachari who runs a Patni Pidith Sang."* The model was
right. Only the first 3 of 10 results print, and eps 30-32 was never one of the flagged
arcs; I read the sample output as the failure. The actual flagged name was **Jethalal**,
in two arcs.

### Bug 1 — the grounding check was wrong in both directions

`check_grounding` did a plain substring test, which produced:
- a **false alarm**: eps 223-225's source says *"Jetha Back From London"*; the model wrote
  the full name "Jethalal", which is correct, and the check flagged it as invented.
- a **miss**: "Taarak Mehta" would pass on any episode whose title contains the show's own
  name — which is nearly all of them — so the one character sharing a name with the series
  was effectively unverifiable.

Now keyed on a `NAME_VARIANTS` table (Jetha/Jethalal, Bapuji/Champaklal, Devanagari
spellings), with the show-name patterns stripped from the source before matching.

### Bug 2 — the summariser was fed raw titles, which is what caused the real hallucination

eps 98-99 genuinely was fabricated: the source has no character at all, and the model
returned "Jethalal". The cause was `build_episode_text` passing the **raw** title —
`"Episode 98 - Taarak Mehta Ka Ooltah Chashmah | Full Episode"` — which tells the model
what show this is and invites it to supply the regular cast from memory. It now passes
`clean_title`, and arcs left with no text are skipped rather than asked.

Note the prompt fix alone did **not** help: an explicit "every name must appear VERBATIM"
instruction left the rate unchanged at 9/10. Removing the misleading input is what
addresses it; the instruction was treating a data problem as a wording problem.

### Bug 3 — residual show-name branding in `clean_title`

`clean_title` for ep 98 was `"Taarak Mehta"`: the stripper's Latin pattern required the
full show name, so a trailing `| Taarak Mehta` segment survived and counted as content.
Fixed with a segment-anchored pattern that still preserves the *character* mid-sentence
(`"Taarak Mehta ne kiya Jethalal ko pareshan"` is untouched).

**I overstated this one and am correcting it:** I claimed it meant 2,586 dead titles
collapsed to an identical string. Measured, it affects **21 episodes** (0.4%) — the
full-name pattern already caught the rest. Corpus signal, eval-set signal and the 4/13
evaluable-region split are **unchanged** (77% / 41% / 4 of 13). The fix is right but minor.

### Open risk for CP8

At the observed rate, 383 summarisable arcs would take on the order of 13 hours on the
free tier. CP8 needs either a paid key, an overnight run, or a reduced scope — this is a
decision for the user, not something to assume.

---

## CP7/CP8 — blocked on quota until tomorrow  `BLOCKED`

**Root cause, measured:** the free tier's binding limit is not requests/minute but
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, **value 20** — twenty requests per
day, per model. My earlier "~13 hours for 383 arcs" was wrong in kind: no throttle can
fix a daily cap. Quotas are per-model, so the pool rotates
`gemini-flash-latest` -> `gemini-2.5-flash-lite` -> `gemini-2.5-flash` for ~60/day.

**User decision:** reduce scope to the ~60 best arcs rather than enable billing. Arc
selection now ranks by `signal x length` (capping length value past ~25 episodes, since
beyond that it is more likely under-segmentation than an epic) instead of taking whatever
comes first in episode order.

All 60 of today's requests are spent on the pilots. CP8 resumes tomorrow and picks up from
cache; nothing needs re-doing.

### Two more bugs found and fixed in the process

- **503 was treated as fatal.** `_retry_delay` only understood the `retryDelay` field that
  429s carry. A 503 ("this model is currently experiencing high demand") has none, so it
  returned `None` and all 14 requests of a run failed inside 130 s with no retry. Now
  backs off explicitly on 503/500/UNAVAILABLE/INTERNAL.
- **The gate reported the wrong failure.** With zero summaries produced, CP7 printed
  "FAIL -- ungrounded characters present", blaming factuality for what was an API outage.
  It now returns **INCONCLUSIVE** and says the gate never ran.

### The grounding fix validated offline, against the 21 cached summaries

| | grounded |
|---|---|
| old substring check | 9/11 |
| new variant-aware check | 9/11 |

**The identical rate hides two real corrections in opposite directions:**

- eps 223-225: old flagged `Jethalal` as invented. The source says *"Jetha Back From
  London"* — the model was right, the check was wrong. Now cleared.
- eps 212-213: old **passed** `Taarak Mehta`. The arc's entire source text is
  `"Paryushan"` (a festival) — no character at all. It passed only because the show's own
  name appears in the raw title. Now correctly flagged.

So the old 9/11 was right by coincidence: one false pass cancelling one false failure.
This is why the aggregate number alone could not be trusted, and why the per-case
inspection was worth doing.


---

## Phase 3 + 4 results

### CP9 — Open-arc sealing  PASS
419 arcs: **417 sealed, 2 ongoing**. All six invariants hold — no arc within 17 episodes
of the corpus end is sealed, ongoing arcs are contiguous at the tail, re-running is
byte-identical, sealed arcs never change, and coverage is exactly 4,819 episodes.

### CP10 — Incremental sync  PASS
Simulated new episodes arriving by segmenting a 4,000-episode corpus, then extending to
the full 4,819:

| | arcs | sealed | episodes re-segmented |
|---|---|---|---|
| at 4,000 eps | 339 | 337 | 4,000 (cold build) |
| extended to 4,819 | 419 | 417 | **837** |

All 337 previously-sealed arcs survived **byte-identical**, while the arcs near the old
edge were recomputed. Work is proportional to the tail (837) rather than the corpus
(4,819) — a 5.8x reduction on this step, and it grows as the corpus does. This is the
mechanism that prevents the freeze that stalled upstream.

### CP11 — Site data build  PASS
`site/arcs.json` (331 KB) + `site/arcs.js`: 419 arcs, 4,819 episodes, every episode
carrying a watch link. **9 arcs surface a synopsis, not 11** — two of the cached summaries
failed the grounding check and are withheld rather than displayed, so the gate is enforced
at render time and not merely at generation time.

### CP12 — Browsable site  PASS
`site/index.html`, published at
<https://claude.ai/code/artifact/d9d80aec-219f-4592-9149-5c54cddcd7c9>.

Search across synopses, arc names, characters and episode titles; era and theme chips;
"only with synopsis" filter; spoiler blur; expand-in-place episode lists linking to
YouTube. `scripts/check_site.py` is the exit test and checks the failures that ship
silently: undefined CSS variables, token parity across all three theme states, dangling
`getElementById` targets, and — the upstream defect this project exists to fix — any arc
that would render an empty synopsis.

**Honest coverage:** 9 of 419 arcs (2.1%) have a written synopsis. The page states that
in a banner rather than implying full coverage, and every un-summarised arc shows its
episode titles instead of an empty entry.

---

## Link audit — every episode link proven, arc by arc

`scripts/verify_links.py` checked all **4,819** episode links through YouTube's oEmbed
endpoint (far lighter than watch pages, and it did not trip the rate limiter that blocked
CP2). Results cached per video id, so the run resumes if interrupted.

| | |
|---|---|
| episodes with a video id | 4,819 |
| **links that resolve** | **4,819 (100.00%)** |
| broken (404) | **0** |
| **arcs with every link working** | **419 / 419** |

Status codes: `{200: 4682, 401: 134}`.

**Reading 401 correctly is the whole audit.** A 401 from oEmbed means the video exists but
the uploader disallows third-party embedding — the link still works on youtube.com.
Counting those as failures would have reported 134 dead links (2.8%) that are all fine.
Only a 404 means broken, and there are none.

### But "resolves" is not "correct" — 3 episodes point at the wrong video

A link check alone would call these healthy. They are worse than dead links, because
nothing signals the error to a viewer:

| video id | claimed by | evidence |
|---|---|---|
| `jKTZo5uH6Vo` | eps **2022** and 3299 | the title reads *"Full Episode - 3299"*, so ep 2022's mapping is wrong |
| `ozBvPzCYESM` | eps **3859, 3860, 3862** | one video, three episodes, identical titles — two are wrong |

Inherited from upstream's CSV, 3 of 4,819 (0.06%). Not silently corrected, because there
is no sound basis for guessing which of 3859/3860/3862 is the real one. Instead
`build_site.py` flags every episode whose video id is claimed by more than one episode,
and the site marks it *"same video as …"* next to the link. `check_site.py` asserts the
flag matches the data, so the warning cannot silently disappear.

---

### CP13 — Spiral view  `DONE`
An interactive view where every arc is visible and reachable without searching.

**Why a spiral rather than a graph.** The data is a *linear* sequence — 4,819 episodes in
broadcast order, partitioned into 419 arcs. A force-directed graph would imply a network
that does not exist. An Archimedean spiral keeps the true ordering (ep 1 at the centre,
newest at the rim) while fitting the whole 18-year run on one screen, so no arc has to be
remembered to be found.

- Canvas, not SVG: 419 stroked paths redraw on every pointer move.
- Era sets hue (indigo → saffron, so the ramp reads as time passing); **signal sets
  saturation and alpha**, so a text-poor arc genuinely looks faint; a dot marks an arc
  with a verified synopsis — 9 of 419, and the sparseness is the honest reading.
- Uniform spatial grid for hit-testing, so hover is O(1) rather than scanning 4,819 points.
- Pointer events, so finger and cursor behave identically. Click clears filters and opens
  that arc in the list below — without the clear, a click could silently do nothing.

**Exit test:** `node scripts/check_spiral.js`

| | |
|---|---|
| stroke 35.6px, turn gap 57.5px, hit radius 39.2px | no cross-turn grabbing |
| arcs of ≤4 episodes | **106 / 106 hittable** |
| **every arc reachable by pointing at it** | **419 / 419** |

The short arcs were the real risk: 106 of 419 span 2–4 episodes and are a few pixels of
stroke. Had the hit radius been tuned only against average arcs, a quarter of the
catalogue would have been unreachable by pointer while still looking present.

**One bug caught before publishing:** the spiral defined `function render()`, and so did
the list filter. JavaScript hoisting means the second definition silently replaces the
first, so every search and filter would have redrawn the canvas instead of filtering.
Renamed to `drawSpiral()`, with a duplicate-name scan over the whole script to confirm
nothing else collides.


---

### CP14 — Live field (replaces the CP13 spiral)  `DONE`
The spiral was a picture you hover over; the request was a surface that physically reacts
to the pointer. Replaced with a field of soft bodies — one per arc, sized by episodes,
laid out in broadcast order as a serpentine river, one band per era.

- **Magnetic lens.** Arcs near the pointer swell and part (Sarkar–Brown fisheye) and
  spring back with slight overshoot; moving fast leaves a wake. The hovered arc blooms —
  one satellite per episode, its neighbours on the thread lit — and names itself.
- **Encodes real state:** era = hue, text signal = saturation, green ring = verified
  synopsis, pulsing ring = open arc still being recomputed. Search and filters dim
  non-matching arcs in the field, not just in the list.
- **Reachable without searching:** click opens a drawer with synopsis, all episode links
  and prev/next arc; arrow keys step the lens through the whole run; Esc closes.
- Loop stops when the tab is hidden or the field is scrolled off-screen; reduced-motion
  removes breathing, wake and overshoot but keeps the lens.

**Two defects found by rendering it once in headless Edge — neither was visible to the
geometry test as first written:**
1. **The lens pushed away the arc being pointed at.** A fisheye moves everything outward
   from its focus; with the focus on the raw pointer, an arc 10px off-centre was flung
   ~42px away, so targeting would have felt like chasing soap. The lens now locks onto
   the nearest arc's resting position, so that arc stays put and magnified while the rest
   part around it.
2. **Long diagonal threads between eras.** Rows alternated direction globally, so a short
   final row could end on the left while the next era began on the right. Each row now
   starts on whichever side the previous one ended.

A third, caught by the test: near the canvas edge the lens pushed arcs out of view.
Targets are now clamped inside the field.

**Exit test:** `node scripts/check_field.js`, at 360 / 800 / 1240px:

| check | result |
|---|---|
| arcs overlapping at rest | 0 |
| arcs pickable by pointing (incl. 106 of ≤4 episodes) | 419 / 419 |
| hovered arc stays under the pointer | yes — edge clamp nudges 1 arc on desktop, 11 on phone, max 31% of its radius |
| hovered arc magnified | 2.4× |
| arcs pushed off-canvas by the lens | 0 |
| broadcast order continuous through the serpentine | yes |
