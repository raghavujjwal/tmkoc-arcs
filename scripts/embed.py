"""CP5 -- multilingual embeddings for every episode, cached to disk.

Text is built as title + synopsis where a synopsis exists, title alone otherwise. The
corpus is code-mixed Hinglish and Devanagari ("Iyer-Babita ne Court mein diya Tapu ke
khilaaf bayaan"), so an English-only encoder would collapse exactly the distinctions we
need. e5 models expect a "query: " / "passage: " prefix; we use "passage: ".

Titles carry heavy fixed boilerplate -- the show name appears in nearly every one -- and
that shared text inflates cosine similarity between unrelated episodes, flattening the
very boundaries we are hunting for. strip_title_boilerplate removes it before encoding.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import numpy as np
import pandas as pd

from config import DESC_CACHE, EMBED_MODEL, EMBEDDINGS_NPY, EPISODES_PARQUET
from textclean import clean_description

# Applied in order. Each pattern removes text that appears across unrelated episodes and
# therefore cannot help distinguish one arc from another.
_MONTHS = (
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
    r"|january|february|march|april|june|july|august|september|october|november|december"
    r"|जनवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|सितम्बर|अक्टूबर|नवंबर|नवम्बर|दिसंबर|दिसम्बर"
)

TITLE_STRIP_PATTERNS = [
    # Show name, Latin and Devanagari, including the many misspellings in the wild.
    re.compile(r"ta+ra+k\s*mehta\s*ka\s*ooltah?\s*chash?ma?h?", re.I),
    re.compile(r"तारक\s*मेहता(?:\s*का\s*उल्टा\s*चश्?मा?ह?)?"),
    # Branding sometimes abbreviates the show to just "Taarak Mehta" as its own trailing
    # segment ("... | Full Episode | Taarak Mehta"), which the full-name pattern above
    # misses. Measured impact is small -- 21 episodes, 19 of which have no synopsis and so
    # become correctly dark rather than carrying the show's name as their only "content".
    # Anchored to a whole segment so the CHARACTER Taarak Mehta survives mid-sentence.
    re.compile(r"(?:^|(?<=[|•\-–—]))\s*ta+ra+k\s*mehta\s*(?=[|•\-–—]|$)", re.I),
    # Episode markers, Latin and Devanagari.
    re.compile(r"\b(?:full\s*)?ep(?:isode)?s?\.?\s*#?\s*\d{1,4}\b", re.I),
    re.compile(r"एपिसोड\s*\d{1,4}"),
    re.compile(r"\b(?:full\s*)?episodes?\b", re.I),
    # Dates: "24th September 2020", "6 Feb 2024", "15 अगस्त, 2016", "12th March, 2018".
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s*(?:{_MONTHS})\.?,?\s*\d{{4}}\b", re.I),
    re.compile(rf"\b(?:{_MONTHS})\.?\s*\d{{1,2}}(?:st|nd|rd|th)?,?\s*\d{{4}}\b", re.I),
    # Channel and promo furniture.
    re.compile(r"\b(?:sab\s*tv|sony\s*sab|sony\s*pal|liv\s*comedy|new!?|watch\s*now"
               r"|hindi\s*(?:tv\s*)?serial|comedy\s*show|horror\s*comedy)\b", re.I),
]

# After the above, any run of digits left over is an episode/date fragment, not content.
_LEFTOVER_NUM_RE = re.compile(r"\b\d{1,4}(?:st|nd|rd|th)?\b", re.I)
_SEPARATOR_RE = re.compile(r"[|•\-–—:,]{1,}")


def strip_title_boilerplate(title: str) -> str:
    """Reduce a title to the part that actually describes this episode's story."""
    s = title or ""
    for pat in TITLE_STRIP_PATTERNS:
        s = pat.sub(" ", s)
    s = _LEFTOVER_NUM_RE.sub(" ", s)
    s = _SEPARATOR_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -–—|:.!")


def load_descriptions() -> dict[str, str]:
    out: dict[str, str] = {}
    if DESC_CACHE.exists():
        for line in DESC_CACHE.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("description"):
                out[rec["video_id"]] = rec["description"]
    return out


def build_texts(df: pd.DataFrame) -> pd.DataFrame:
    descs = load_descriptions()
    df = df.copy()
    df["raw_description"] = df.video_id.map(descs)
    df["synopsis"] = df.raw_description.map(clean_description)
    df["clean_title"] = df.title.map(strip_title_boilerplate)

    def combine(row) -> str:
        parts = [row.clean_title]
        if row.synopsis:
            parts.append(row.synopsis)
        return "passage: " + " . ".join(p for p in parts if p).strip()

    df["embed_text"] = df.apply(combine, axis=1)
    df["has_synopsis"] = df.synopsis.str.len() > 0
    df["text_chars"] = df.embed_text.str.len()
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=64)
    args = ap.parse_args()

    df = pd.read_parquet(EPISODES_PARQUET)
    df = build_texts(df).sort_values("ep_number").reset_index(drop=True)

    print(f"episodes          {len(df)}")
    print(f"with synopsis     {int(df.has_synopsis.sum())} ({df.has_synopsis.mean()*100:.1f}%)")
    print(f"median text chars {int(df.text_chars.median())}")
    print(f"dead titles       {int((df.clean_title.str.len() == 0).sum())} (no story content; separate from synopsis coverage above)")
    print(f"\nloading {EMBED_MODEL} ...")
    sys.stdout.flush()

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBED_MODEL)
    emb = model.encode(
        df.embed_text.tolist(),
        batch_size=args.batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
        convert_to_numpy=True,
    )

    np.save(EMBEDDINGS_NPY, emb)
    df.drop(columns=["raw_description"]).to_parquet(
        EPISODES_PARQUET.with_name("episodes_enriched.parquet"), index=False
    )

    print(f"\nembeddings {emb.shape} -> {EMBEDDINGS_NPY}")

    # Sanity: neighbouring episodes should look more alike than random pairs.
    rng = np.random.default_rng(0)
    adj = float(np.mean([emb[i] @ emb[i + 1] for i in range(len(emb) - 1)]))
    idx = rng.integers(0, len(emb), size=(2000, 2))
    rnd = float(np.mean([emb[a] @ emb[b] for a, b in idx if a != b]))
    print(f"mean cosine, adjacent episodes {adj:.4f}")
    print(f"mean cosine, random pairs      {rnd:.4f}")
    print(f"separation                     {adj - rnd:+.4f}")

    passed = emb.shape[0] == len(df) and adj > rnd
    print("CP5", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
