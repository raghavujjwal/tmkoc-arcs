"""CP0 exit test: dependencies, credentials and disk are all usable."""
from __future__ import annotations

import importlib
import shutil
import sys

from config import BASE_DIR, UPSTREAM_CSV, UPSTREAM_STORYLINES, load_api_key

REQUIRED = ["pandas", "numpy", "ruptures", "yt_dlp", "sentence_transformers", "google.genai", "tqdm"]


def main() -> int:
    ok = True

    print("dependencies")
    for mod in REQUIRED:
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "")
            print(f"  [ok]   {mod} {ver}")
        except Exception:
            print(f"  [FAIL] {mod} not importable")
            ok = False

    print("\nseed data")
    for path in (UPSTREAM_CSV, UPSTREAM_STORYLINES):
        if path.is_file():
            print(f"  [ok]   {path.name} ({path.stat().st_size / 1e6:.2f} MB)")
        else:
            print(f"  [FAIL] missing {path}")
            ok = False

    print("\ncredentials")
    if load_api_key():
        print("  [ok]   Gemini API key found (value not shown)")
    else:
        print("  [warn] no Gemini key; needed only from CP7 onward")

    print("\ndisk")
    for label, path in (("D:", BASE_DIR), ("C:", "C:\\")):
        free_gb = shutil.disk_usage(path).free / 1e9
        flag = "ok" if free_gb > 5 else "warn"
        print(f"  [{flag}]   {label} {free_gb:.2f} GB free")

    print("\nCP0", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
