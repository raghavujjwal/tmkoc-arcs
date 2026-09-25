# TMKOC Arc Segmenter & Summarizer — Design

**Status:** design only, no code yet
**Location:** `D:\dev\tmkoc-arcs`
**Prior-art reference clone (read-only):** `D:\dev\Daily-Dose-of-TMOCK`
**Date:** 2026-09-25

---

## 1. Problem

*Taarak Mehta Ka Ooltah Chashmah* has 4,800+ episodes spanning 16+ years. Official
YouTube playlists are fragmented, incomplete, and ordered unhelpfully. What viewers
actually want is to browse by **story arc** — "the Hong Kong Disneyland trip",
"the 10 Crore Ruby", "Janmashtami 2008" — not by episode number.

Existing arc lists share three failures:

1. They are **static** — hand-curated once, never regenerated.
2. They are **undescribed** — a title and an episode range, nothing more.
3. They **rot** — the show airs daily; any frozen list is stale within a week.

This project builds a *reproducible* arc segmenter and summarizer that fixes all three.

---

## 2. Prior art: Daily-Dose-of-TMOCK

Audited at commit `c1632a1`. Repo: https://github.com/CodeMasterAbhishek/Daily-Dose-of-TMOCK

### What it does well (worth borrowing)

- **`scrapetube` instead of the YouTube Data API** — sidesteps quota limits entirely.
  Polls 6 official channels every 6h (`scripts/config.py:8`).
- **3-tier link cascade** — primary / fallback / 10-minute short, written per episode
  (`scripts/update_website.py:405`). A real answer to YouTube takedowns.
- **Client-side geo-block probing** — a hidden 200x200 iframe pre-loads queued video IDs,
  catches YouTube error 150/101, caches the verdict in `localStorage`, and invalidates it
  via the ipify API when the user's IP changes. Genuinely good engineering.
- **Zero-cost architecture** — GitHub Actions as backend, Pages as CDN, flat CSV as DB.

### Where the segregation layer fails

| # | Gap | Evidence |
|---|---|---|
| 1 | **No summarizer exists.** All 672 arcs have `"description": ""`. The UI dutifully renders an empty `<p>` on every card. | `data/storylines.json`; `js/ui.js:1409` |
| 2 | **No generator script.** Nothing in `scripts/` produces `storylines.json`. Committed once, out-of-band, not reproducible. | `grep -rn storylines --include=*.py` returns nothing |
| 3 | **Frozen and rotting.** The sync workflow stages only `episodes.csv`, `state.json`, `activity_logs.md` — `storylines.json` is never re-committed. Arcs stop at ep 4824 while state already reads `last_episode: 4825`. Every new episode belongs to no arc. | `.github/workflows/daily_sync.yml` git-add line |
| 4 | **10 off-by-one boundary collisions** where `endEp[i] == startEp[i+1]`, and the filter is inclusive on both ends — so those episodes render inside two arcs. | `js/app.js:114` |
| 5 | **Mixed, unreconciled provenance.** The `tagline` field leaks it: 196 arcs are *"Official Wikipedia Ground-Truth Arc"*, 476 are *"Episode Guide Arc"*. Two segmentation philosophies concatenated with no reconciliation pass. | `data/storylines.json` taglines |
| 6 | **`category` is an era band, not a genre.** Classic/Golden/Modern/Recent are chronological buckets. No Festival / Trip / Mystery / Guest-Star tagging — which is how fans actually browse. | `data/storylines.json` |
| 7 | **The richest signal is fetched, then discarded.** `extract_description_text()` is defined and called during every sync, but `writerow` never persists the description. | defined `update_website.py:91`, called `:173`/`:231`, dropped at `:405` |

Gap 7 is the single most important finding: the input that makes summarization work is
*already in memory* on every sync and thrown away.

---

## 3. Data audit (measured, not estimated)

```
arcs                672
descriptions        0 non-empty  /  672 empty
coverage            eps 1-4824, 0 gaps
overlaps            10 pairs (endEp == startEp)
arc length          min 1, max 51, mean 7.19
length mode         5 episodes (190 arcs)
category split      Recent 274 | Modern 166 | Golden 131 | Classic 101
provenance split    Episode Guide 476 | Wikipedia ground-truth 196
episodes.csv        4,820 rows, 1.06 MB
```

### Sample overlap collisions

```
  96-100  'Jethalal Fitness'   OVERLAPS  100-106  'Donations leads to Jail'
1447-1465 '10 Crore Ruby'      OVERLAPS 1465-1478 'Hongkong-Disneyland trip'
3369-3373 'The Second Deal'    OVERLAPS 3373-3384 'Party Sharty'
```

### Data hygiene bugs to avoid inheriting

- CSV header declares **4 columns** (`Episode,Title,URL,Status`) while rows carry **8**.
- Ep 4825 is `PRECAP 4825`, duration `0:42` — a promo clip treated as a real episode.
- Hardcoded duration fallback `realEpNum === 4778 ? '09:48' : '21:45'` (`js/api.js:107`).

---

## 4. Core insight

**Arc detection is linear text segmentation, not clustering.**

Arcs are *contiguous* episode ranges. An arc can never be `{12, 47, 203}`. That constraint
is what makes the problem both tractable and — crucially — quantitatively evaluable: it
reduces to 1-D change-point detection over an ordered sequence, which has standard
algorithms and standard metrics (Pk, WindowDiff).

Treating it as clustering would discard the ordering that carries most of the signal.

---

## 5. Architecture

### Phase 0 — Unlock the input signal

- Extend the episode schema with `description`, `channel`, `videoId`, `isPrecap`.
  Fix the 4-vs-8 column header mismatch while touching it.
- One-time backfill of descriptions for eps 1-4824 via
  `yt-dlp --skip-download --print description`.
  (yt-dlp is already a dependency upstream and already used at `scripts/update_dates.py:38`.)
- Persist `description` on every ongoing sync — a one-line change to `writerow`.
- Filter precaps/promos: duration < 5 min, or title matching `PRECAP|Promo|Teaser`.

**Why first:** titles alone are thin. Titles + descriptions is what makes both
segmentation *and* summarization actually work.

### Phase 1 — Segmentation engine

- Embed each episode's `title + description` with a **multilingual** model. The corpus is
  Hinglish and Devanagari mixed — e.g.
  `Iyer-Babita ने Court में दिया Tapu के खिलाफ बयान`.
  Candidates: `intfloat/multilingual-e5-base`, `paraphrase-multilingual-MiniLM-L12-v2`.
- Change-point detection over the 1-D episode sequence using `ruptures` (`Pelt` + `rbf`
  kernel), penalty tuned so mean segment length lands ~6-8
  (upstream's mean is 7.2, a reasonable prior).
- **Reconciliation pass** enforcing three invariants:
  `endEp[i] < startEp[i+1]` (no overlap), no gaps, full coverage.
  This permanently kills the 10 collisions.
- Pin the 196 Wikipedia arcs as **anchors** where they exist; let the detector fill the rest.

### Phase 2 — Summarizer

- Per arc, feed concatenated episode titles + descriptions to **Gemini 2.5 Flash-Lite**
  with structured output:

```json
{
  "title": "...",
  "logline": "one sentence",
  "synopsis": "3-4 sentences, spoiler-free",
  "characters": ["Jethalal", "Bhide"],
  "tags": ["Festival", "Mystery"],
  "bestEpisode": 1452
}
```

- Scale: 419 detected arcs (381 with usable text) x ~2k tokens = **~800k tokens**.
- **The "negligible on the free tier" assumption was wrong; corrected after measurement.**
  The free tier is metered in *requests*, not tokens:
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier` = **20 per day, per model**. Token
  volume is irrelevant here; request count is the entire constraint. Rotating three models
  yields ~60/day, making a full 381-arc run a ~6-day job on the free tier — or a single
  ~45-minute pass with billing enabled (~$0.15).
- **Content-hash cache** keyed on the arc's episode-ID set, so re-runs cost nothing and
  only new or re-segmented arcs hit the API.
- Optional second tier: one-line per-episode blurbs, generated lazily for arcs users open.

### Phase 3 — Incremental automation

- After each episode sync, segment + summarize **only the tail** past the last sealed arc.
- **"Open arc" concept:** the newest arc stays `"status": "ongoing"` and re-summarizes each
  run until a boundary is confidently detected, then seals.
  *This is the mechanism that stops the rot.*
- Commit the regenerated arc file — the specific omission that froze upstream.

### Phase 4 — Frontend

- Render the synopsis (the empty `<p>` finally has content).
- **Tag chips** alongside era chips — browse by *Festival* or *Trip*, not just *Golden*.
- Full-text search across arc synopses, which only becomes possible once descriptions exist.
- Spoiler toggle; exclusive-boundary filtering.

---

## 6. Evaluation

Hold out the **196 Wikipedia-sourced arcs** as ground truth and score segmentation with
**Pk** and **WindowDiff**, the standard text-segmentation metrics.

This is what turns "do these arcs look right?" into a number you can tune the `ruptures`
penalty against. Without it, boundary quality is pure vibes.

Summary quality is harder to score automatically; plan a small human spot-check set
(~20 arcs) plus a factuality check that every named character actually appears in the
arc's source titles.

---

## 7. Highest-leverage path

**Phase 0 → Phase 2.** Descriptions are already being fetched and discarded upstream.
Once persisted, all 672 summaries generate in one cheap batch — which alone transforms a
Storylines tab of bare titles into something genuinely browsable. Phase 1 makes the
*boundaries* good; Phase 0+2 makes the product useful. Do them in that order.

---

## 8. Open questions

- Seed from upstream's `episodes.csv`, or re-scrape independently for a clean schema?
- How to handle episodes that legitimately span two arcs (festival specials that interleave
  with an ongoing plot)? The current model is strictly non-overlapping.
- Spoiler policy: is `bestEpisode` itself a spoiler? Should synopses be gated by a toggle?
- Keep the era `category`, or replace it entirely with semantic tags?

---

## 9. Stack

Python 3.11+ · `sentence-transformers` · `ruptures` · `yt-dlp` · `scrapetube` ·
Gemini 2.5 Flash-Lite (`google-genai`) · vanilla JS frontend
