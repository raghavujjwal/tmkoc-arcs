"""CP7/CP8 -- generate a structured synopsis for each arc with Gemini 2.5 Flash-Lite.

Every result is cached under a hash of the arc's *content* (its episode ids plus the
text fed in), so a rerun costs zero API calls and a re-segmentation only re-summarises
the arcs whose membership actually changed. That is what makes Phase 3's incremental
daily run affordable.

The factuality check matters more than it might look. An LLM handed 8 episode titles
will cheerfully invent a character who is not in them; since these summaries are the
product's entire value over a bare title list, a fabricated one is worse than none.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time

import pandas as pd

from config import CACHE_DIR, DATA_DIR, EPISODES_PARQUET, GEMINI_MODEL, load_api_key

SUMMARY_CACHE = CACHE_DIR / "summaries.jsonl"

# Recurring cast, used to check that named characters are actually grounded in the source.
KNOWN_CHARACTERS = [
    "Jethalal", "Daya", "Tapu", "Champaklal", "Taarak Mehta", "Anjali", "Bhide",
    "Madhavi", "Sonu", "Popatlal", "Iyer", "Babita", "Sodhi", "Roshan", "Abdul",
    "Bagha", "Natu Kaka", "Bapuji", "Komal", "Pinku", "Goli", "Gogi", "Sundar",
    "Hathi", "Komal Hathi", "Tanmay", "Dr Hathi",
]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "synopsis": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "string"}},
        "tags": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["Festival", "Trip", "Mystery", "Romance", "Business",
                         "School", "Guest Star", "Competition", "Health", "Society",
                         "Prank", "Crime"],
            },
        },
        "best_episode": {"type": "integer"},
    },
    "required": ["title", "logline", "synopsis", "characters", "tags"],
}

PROMPT = """You are cataloguing story arcs of the Indian sitcom Taarak Mehta Ka Ooltah Chashmah.

Below are the episodes of ONE story arc, in broadcast order. Some entries have only a
title; others include a synopsis. Titles mix Hindi, English and Devanagari.

Write a catalogue entry for this arc.

Rules:
- Use ONLY what appears in the source text. Never invent plot points or characters.
- CRITICAL: every name in `characters` must appear VERBATIM in the episode text above.
  Do not add regular cast members because you expect them to be present, do not infer a
  name from context, and do not transliterate a name into a different spelling. If the
  text names nobody, return an empty list. A single invented name invalidates the entry.
- If the source is too thin to tell what happens, say so plainly in the synopsis rather
  than guessing. A short honest entry beats a confident wrong one.
- `synopsis`: 3-4 sentences, present tense, SPOILER-FREE -- set up the premise and the
  comic problem, do not reveal how it resolves.
- `characters`: only those actually named in the source text.
- `title`: a short evocative arc name, not a summary sentence.

Episodes:
{episodes}
"""


def arc_hash(arc: dict, text: str) -> str:
    """Keyed on arc span, source text AND prompt.

    The prompt belongs in the key: editing it changes the output, so without it a cache
    hit would silently serve results from the previous prompt and any measurement of the
    new one would be of stale data.
    """
    prompt_v = hashlib.sha256(PROMPT.encode()).hexdigest()[:8]
    key = (f"{arc['start_ep']}-{arc['end_ep']}"
           f"|{hashlib.sha256(text.encode()).hexdigest()}|{prompt_v}")
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def load_cache() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if SUMMARY_CACHE.exists():
        for line in SUMMARY_CACHE.open(encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                    out[rec["hash"]] = rec
                except json.JSONDecodeError:
                    continue
    return out


def build_episode_text(df: pd.DataFrame, start_ep: int, end_ep: int) -> str:
    """Feed the model the CLEANED title, never the raw one.

    A raw title is mostly branding ("Episode 98 - Taarak Mehta Ka Ooltah Chashmah | Full
    Episode"). Handing that to the model tells it which show this is and invites it to
    supply the regular cast from memory: eps 98-99, whose raw titles contain no character
    at all, came back naming Jethalal. Cleaned, those episodes contribute nothing, which
    is the honest input and lets `has_content` skip them.
    """
    rows = df[(df.ep_number >= start_ep) & (df.ep_number <= end_ep)]
    parts = []
    for _, r in rows.iterrows():
        title = (getattr(r, "clean_title", "") or "").strip()
        syn = (getattr(r, "synopsis", "") or "").strip()
        if not title and not syn:
            continue
        line = f"Ep {r.ep_number}: {title}" if title else f"Ep {r.ep_number}:"
        if syn:
            line += f"\n    {syn[:400]}"
        parts.append(line)
    return "\n".join(parts)


def has_content(source: str) -> bool:
    """True when the arc has any text worth summarising at all."""
    return bool(source.strip())


# TMKOC names appear in several forms across Hindi/English/Devanagari titles. A plain
# substring test produced BOTH error directions on the pilot: it flagged "Jethalal" as
# invented when the source said "Jetha" (a false alarm), and it would accept "Taarak
# Mehta" as grounded purely because the show's own name contains it (a miss). Each
# character maps to every spelling that counts as a real mention of them.
NAME_VARIANTS: dict[str, tuple[str, ...]] = {
    "jethalal": ("jethalal", "jetha", "जेठालाल", "जेठा"),
    "daya": ("daya", "dayaben", "दया"),
    "taarak mehta": ("taarak mehta", "tarak mehta", "तारक मेहता"),
    "champaklal": ("champaklal", "champak", "bapuji", "चंपकलाल", "बापूजी"),
    "bhide": ("bhide", "aatmaram", "भिड़े", "भिडे"),
    "madhavi": ("madhavi", "माधवी"),
    "popatlal": ("popatlal", "popat", "पोपटलाल"),
    "iyer": ("iyer", "krishnan", "अय्यर"),
    "babita": ("babita", "बबीता"),
    "sodhi": ("sodhi", "roshan singh", "सोढ़ी", "सोढी"),
    "hathi": ("hathi", "hansraj", "हाथी"),
    "komal": ("komal", "कोमल"),
    "tapu": ("tapu", "टप्पू", "टपु"),
    "sonu": ("sonu", "सोनू"),
    "abdul": ("abdul", "अब्दुल"),
    "bagha": ("bagha", "बाघा"),
    "natu kaka": ("natu kaka", "natu", "नट्टू"),
    "anjali": ("anjali", "अंजली"),
}


def check_grounding(summary: dict, source: str) -> dict:
    """Flag characters the model named that do not appear in the source text.

    The show's own name is stripped first, so "Taarak Mehta Ka Ooltah Chashmah" in a title
    never counts as a mention of the character Taarak Mehta.
    """
    from embed import TITLE_STRIP_PATTERNS

    low = source.lower()
    for pat in TITLE_STRIP_PATTERNS[:2]:  # the two show-name patterns
        low = pat.sub(" ", low)

    named = summary.get("characters") or []
    ungrounded = []
    for c in named:
        key = c.lower().strip()
        variants = NAME_VARIANTS.get(key, ())
        if not variants:
            # Unknown name: fall back to matching its first token.
            variants = (key.split()[0],) if key.split() else ()
        if not any(v in low for v in variants):
            ungrounded.append(c)
    return {
        "n_characters": len(named),
        "ungrounded": ungrounded,
        "grounded": not ungrounded,
    }


# Free-tier quota is per DAY and per MODEL (GenerateRequestsPerDayPerProjectPerModel,
# value 20), so rotating models multiplies the daily budget rather than merely spreading
# load. Ordered cheapest/fastest first; a model is retired for the run once it reports its
# daily quota exhausted.
# Ordered by observed remaining quota rather than preference: a model whose daily
# allowance is already spent costs a wasted request to rediscover.
MODEL_POOL = ("gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.5-flash")

_last_call = [0.0]


def arc_score(arc: dict) -> tuple:
    """Rank arcs by how much a reader would get from a summary of them.

    Signal dominates: an arc with no text cannot produce a real entry however long it is.
    Among arcs that do have text, prefer substantial ones, but treat anything past ~25
    episodes as a probable under-segmentation rather than an epic worth featuring.
    """
    n = arc["n_episodes"]
    length_value = n if n <= 25 else max(1, 50 - n)
    return (round(arc.get("signal", 0.0), 2), length_value, -arc["start_ep"])


def is_daily_quota_error(exc: Exception) -> bool:
    return "PerDay" in str(exc)


def _throttle(min_interval: float) -> None:
    """Free-tier flash-lite allows 10 requests/minute; exceeding it returns hard 429s."""
    wait = min_interval - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _retry_delay(exc: Exception, attempt: int = 0) -> float | None:
    """How long to wait before retrying, or None if the error is not retryable.

    429s carry an explicit `retryDelay`. 503 ("this model is currently experiencing high
    demand") carries none, so the first version treated it as fatal and failed all 14
    requests of a run inside 130 s. Transient overload now gets explicit backoff.
    """
    s = str(exc)
    m = re.search(r"retryDelay['\"]?:\s*['\"](\d+(?:\.\d+)?)s", s)
    if m:
        return float(m.group(1)) + 1.0
    m = re.search(r"retry in (\d+(?:\.\d+)?)s", s)
    if m:
        return float(m.group(1)) + 1.0
    if any(t in s for t in ("503", "UNAVAILABLE", "500", "INTERNAL", "overloaded")):
        return min(60.0, 5.0 * (2 ** attempt))
    return None


def summarize_arc(client, arc: dict, source: str, min_interval: float = 6.5,
                  attempts: int = 3, models: list[str] | None = None) -> tuple[dict, str]:
    """Returns (summary, model_used). Rotates models as each daily quota runs out."""
    from google.genai import types

    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        temperature=0.3,
    )
    pool = models if models is not None else [GEMINI_MODEL]
    for attempt in range(max(attempts, 6)):
        if not pool:
            raise RuntimeError("all models exhausted their daily quota")
        model = pool[0]
        _throttle(min_interval)
        try:
            resp = client.models.generate_content(
                model=model, contents=PROMPT.format(episodes=source), config=cfg)
            return json.loads(resp.text), model
        except Exception as exc:
            if is_daily_quota_error(exc):
                # Per-day quota: no delay will clear it today, so retire this model for
                # the whole run instead of burning retries against it.
                print(f"    {model}: daily quota exhausted, rotating", flush=True)
                pool.pop(0)
                continue
            delay = _retry_delay(exc, attempt)
            if delay is None or attempt == attempts - 1:
                raise
            time.sleep(delay)
    raise RuntimeError("retries exhausted")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arcs", default=str(DATA_DIR / "arcs.json"))
    ap.add_argument("--limit", type=int, default=20, help="0 = all (CP8)")
    ap.add_argument("--min-interval", type=float, default=6.5,
                    help="seconds between API calls; free tier allows 10/min")
    ap.add_argument("--min-signal", type=float, default=0.5,
                    help="skip arcs whose episodes carry no text")
    args = ap.parse_args()

    key = load_api_key()
    if not key:
        print("no GOOGLE_API_KEY available")
        return 1

    from google import genai

    client = genai.Client(api_key=key)

    enriched = EPISODES_PARQUET.with_name("episodes_enriched.parquet")
    df = pd.read_parquet(enriched if enriched.exists() else EPISODES_PARQUET)

    arcs = json.loads(open(args.arcs, encoding="utf-8").read())
    if isinstance(arcs, dict):
        arcs = arcs.get("arcs", [])

    # Arcs are in episode order, so a naive head-slice pilots on the earliest episodes --
    # exactly the dark era with no text. That would measure the corpus's gaps, not the
    # summariser. Filter to arcs that actually carry text first.
    n_all = len(arcs)
    arcs = [a for a in arcs if a.get("signal", 1.0) >= args.min_signal]
    # Rank by value, not episode order, so a capped run spends its quota on the arcs a
    # reader would actually want rather than on whatever happens to come first.
    arcs.sort(key=arc_score, reverse=True)
    print(f"arcs {n_all} total, {len(arcs)} with signal >= {args.min_signal}")
    todo = arcs[: args.limit] if args.limit else arcs
    if todo:
        print(f"selected {len(todo)} best by signal x length: "
              f"signal {min(a.get('signal', 0) for a in todo):.2f}-"
              f"{max(a.get('signal', 0) for a in todo):.2f}, "
              f"{min(a['n_episodes'] for a in todo)}-"
              f"{max(a['n_episodes'] for a in todo)} eps")

    pool = list(MODEL_POOL)
    cache = load_cache()
    results, grounded_ok, cached_hits = [], 0, 0
    start = time.time()

    skipped_empty = 0
    for i, arc in enumerate(todo, 1):
        source = build_episode_text(df, arc["start_ep"], arc["end_ep"])
        if not has_content(source):
            # No text at all once branding is stripped. Asking the model anyway is how
            # eps 98-99 got a confidently invented cast.
            skipped_empty += 1
            continue
        h = arc_hash(arc, source)
        if h in cache:
            rec = cache[h]
            cached_hits += 1
        else:
            try:
                summary, used = summarize_arc(
                    client, arc, source, args.min_interval, models=pool)
            except Exception as exc:
                msg = str(exc).split(chr(10))[0][:110]
                print(f"  ep {arc['start_ep']}-{arc['end_ep']}: FAILED {type(exc).__name__}: {msg}")
                if not pool:
                    print("  all models out of daily quota -- stopping; rerun tomorrow "
                          "to resume from cache", flush=True)
                    break
                continue
            rec = {
                "hash": h,
                "start_ep": arc["start_ep"],
                "end_ep": arc["end_ep"],
                "summary": summary,
                "model": used,
                "grounding": check_grounding(summary, source),
            }
            with SUMMARY_CACHE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        results.append(rec)
        grounded_ok += rec["grounding"]["grounded"]
        if i % 5 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} ({cached_hits} cached)", flush=True)

    print(f"\nsummarised {len(results)}/{len(todo)} in {time.time() - start:.0f}s")
    print(f"served from cache   {cached_hits}")
    print(f"skipped, no text    {skipped_empty}")
    print(f"grounded summaries  {grounded_ok}/{len(results)}")

    for rec in results[:3]:
        s = rec["summary"]
        print(f"\n  eps {rec['start_ep']}-{rec['end_ep']}: {s['title']}")
        print(f"    {s['logline']}")
        print(f"    tags={s.get('tags')} chars={s.get('characters')}")
        if rec["grounding"]["ungrounded"]:
            print(f"    UNGROUNDED: {rec['grounding']['ungrounded']}")

    if not results:
        # Distinguish "the gate rejected the output" from "no output was produced".
        # Reporting an empty run as a factuality failure is its own kind of wrong answer.
        print("\nCP7 INCONCLUSIVE -- no summaries produced (see API errors above); "
              "the grounding gate never ran")
        return 2
    passed = grounded_ok == len(results)
    print("\nCP7", "PASS" if passed else
          f"FAIL -- {len(results) - grounded_ok}/{len(results)} summaries name a "
          f"character absent from their source text")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
