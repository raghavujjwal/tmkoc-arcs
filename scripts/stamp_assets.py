"""Fingerprint every asset URL in site/index.html with a hash of the file's contents.

GitHub Pages serves every file with `Cache-Control: max-age=600`. After a deploy, a
browser could hold the new index.html but a cached old layout.js -- the new page then
called functions the old script did not have, and every hover and click failed. With
`layout.js?v=<hash>` the new page always requests the new file, and an unchanged file
keeps its URL, so it stays cached.

Run after anything that changes a site asset; build_site.py calls it automatically.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
ASSETS = ["arcs.js", "layout.js", "logo.png", "bg.jpg", "bg-kim.webp"]


def digest(name: str) -> str:
    return hashlib.sha256((SITE / name).read_bytes()).hexdigest()[:10]


def stamp() -> dict[str, str]:
    page = SITE / "index.html"
    html = page.read_text(encoding="utf-8")
    stamped = {}
    for name in ASSETS:
        if not (SITE / name).exists():
            continue
        v = digest(name)
        pattern = re.compile(r'(?<![\w.-])' + re.escape(name) + r'(?:\?v=[0-9a-f]+)?(?=["\')])')
        html, n = pattern.subn(f"{name}?v={v}", html)
        if n:
            stamped[name] = v
    page.write_text(html, encoding="utf-8")
    return stamped


def check() -> list[str]:
    """Asset references whose fingerprint does not match the file on disk."""
    html = (SITE / "index.html").read_text(encoding="utf-8")
    bad = []
    for name in ASSETS:
        if not (SITE / name).exists():
            continue
        for m in re.finditer(r'(?<![\w.-])' + re.escape(name) + r'(\?v=([0-9a-f]+))?(?=["\')])', html):
            if m.group(2) != digest(name):
                bad.append(f"{name} referenced as {m.group(0)!r}, expected v={digest(name)}")
    return bad


if __name__ == "__main__":
    for k, v in stamp().items():
        print(f"  {k:<12} ?v={v}")
    problems = check()
    print("stale references:", problems or "none")
    sys.exit(1 if problems else 0)
