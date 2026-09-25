"""Paths, constants and credential loading. No secrets are ever printed."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = BASE_DIR / "cache"
REPORT_DIR = BASE_DIR / "reports"

for _d in (DATA_DIR, CACHE_DIR, REPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Upstream reference clone -- read-only seed source. Never written to.
UPSTREAM_DIR = Path(r"D:\dev\Daily-Dose-of-TMOCK")
UPSTREAM_CSV = UPSTREAM_DIR / "data" / "episodes.csv"
UPSTREAM_STORYLINES = UPSTREAM_DIR / "data" / "storylines.json"

EPISODES_PARQUET = DATA_DIR / "episodes.parquet"
DESC_CACHE = CACHE_DIR / "descriptions.jsonl"
GROUND_TRUTH_ARCS = DATA_DIR / "ground_truth_arcs.json"
EMBEDDINGS_NPY = DATA_DIR / "embeddings.npy"

# An episode shorter than this is a promo/precap, not a real episode.
PRECAP_MAX_SECONDS = 5 * 60
PRECAP_TITLE_RE = r"\b(precap|promo|teaser|trailer|highlights?)\b"

EMBED_MODEL = "intfloat/multilingual-e5-base"
GEMINI_MODEL = "gemini-2.5-flash-lite"

# Keep HF model downloads off the nearly-full C: drive.
os.environ.setdefault("HF_HOME", r"D:\dev\.hfcache")

# Where a GOOGLE_API_KEY may live, in priority order.
_ENV_CANDIDATES = [
    BASE_DIR / ".env",
    Path(r"C:\Users\ujjwal\OneDrive\Desktop\Files\paper-finder\.env"),
]


def load_api_key() -> str | None:
    """Return the Gemini key from the environment or a known .env, or None.

    The value is returned for direct use by the caller and is never logged.
    """
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        val = os.getenv(var)
        if val:
            return val.strip()

    for env_path in _ENV_CANDIDATES:
        if not env_path.is_file():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
                    return val.strip().strip("'\"")
        except OSError:
            continue
    return None
