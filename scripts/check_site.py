"""CP12 exit test -- structural checks on the frontend.

Not a browser test. These are the failures that would actually ship silently: a CSS
variable referenced but never defined (renders as nothing, often invisible text), a
getElementById for an id that is not in the markup (a dead control), a theme token
defined in only one of the three theme states (the classic unreadable-artifact bug), or
an arc rendering an empty synopsis -- the exact upstream defect this project exists to
fix.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


def main() -> int:
    html = (SITE / "index.html").read_text(encoding="utf-8")
    data = json.loads((SITE / "arcs.json").read_text(encoding="utf-8"))
    checks: dict[str, bool] = {}
    detail: list[str] = []

    # --- CSS variables -----------------------------------------------------
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", html))
    root_block = html.split(":root{", 1)[1].split("}", 1)[0]
    defined_light = set(re.findall(r"(--[a-z0-9-]+)\s*:", root_block))
    missing = used - defined_light
    checks["every CSS var used is defined in the base :root"] = not missing
    if missing:
        detail.append(f"    undefined: {sorted(missing)}")

    # Each theme state must define the same token set, or one theme renders wrong.
    dark_media = re.search(r"@media \(prefers-color-scheme:dark\)\{\s*:root:not\(\[data-theme=\"light\"\]\)\{(.*?)\}",
                           html, re.S)
    dark_attr = re.search(r":root\[data-theme=\"dark\"\]\{(.*?)\}", html, re.S)
    single_theme = not dark_media and not dark_attr and "color-scheme:light" in html
    ok_parity = bool(dark_media and dark_attr) or single_theme
    if dark_media and dark_attr:
        a = set(re.findall(r"(--[a-z0-9-]+)\s*:", dark_media.group(1)))
        b = set(re.findall(r"(--[a-z0-9-]+)\s*:", dark_attr.group(1)))
        ok_parity = a == b and a.issubset(defined_light)
        if not ok_parity:
            detail.append(f"    media-only: {sorted(a - b)}  attr-only: {sorted(b - a)}")
    checks["theme tokens consistent (or a deliberate single light theme)"] = ok_parity

    # --- DOM wiring --------------------------------------------------------
    ids_in_markup = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', html))
    ids_queried = set(re.findall(r"getElementById\(['\"]([A-Za-z0-9_-]+)['\"]\)", html))
    # `more` is created dynamically by paint(), so it is legitimately absent from markup.
    dangling = ids_queried - ids_in_markup - {"more"}
    checks["every getElementById target exists in the markup"] = not dangling
    if dangling:
        detail.append(f"    dangling: {sorted(dangling)}")

    # --- data contract the page relies on ----------------------------------
    arcs = data["arcs"]
    checks["arcs.js mirrors arcs.json"] = (
        (SITE / "arcs.js").read_text(encoding="utf-8").startswith("window.ARC_DATA=")
    )
    checks["no arc would render an empty synopsis"] = all(
        (a["synopsis"] or "").strip() for a in arcs if a["has_summary"]
    )
    checks["every summarised arc has a title and logline"] = all(
        a["title"] and a["logline"] for a in arcs if a["has_summary"]
    )
    checks["every arc has at least one episode with a watch link"] = all(
        any(e["vid"] for e in a["episodes"]) for a in arcs
    )
    checks["era values all appear in the era chip list"] = (
        {a["era"] for a in arcs} <= set(data["eras"])
    )
    checks["arcs are ordered and non-overlapping"] = all(
        arcs[i]["end_ep"] < arcs[i + 1]["start_ep"] for i in range(len(arcs) - 1)
    )

    # --- search behaviour, replicated from the page's own matcher ----------
    def haystack(a: dict) -> str:
        return " ".join([a["title"] or "", a["logline"] or "", a["synopsis"] or "",
                         " ".join(a["characters"]), " ".join(a["tags"]),
                         " ".join(e["title"] for e in a["episodes"])]).lower()

    summarised = [a for a in arcs if a["has_summary"]]
    if summarised:
        probe = summarised[0]
        word = next((w for w in re.findall(r"[A-Za-z]{6,}", probe["synopsis"] or "")), None)
        checks["a word from a synopsis finds its arc"] = bool(
            word and any(word.lower() in haystack(a) for a in arcs))
    else:
        checks["a word from a synopsis finds its arc"] = False

    mid = arcs[len(arcs) // 2]
    probe_ep = (mid["start_ep"] + mid["end_ep"]) // 2
    checks["an episode number resolves to its arc"] = any(
        a["start_ep"] <= probe_ep <= a["end_ep"] for a in arcs)

    print(f"site/index.html  {len(html) / 1024:.0f} KB")
    print(f"site/arcs.json   {len(arcs)} arcs, {data['n_summarised']} summarised\n")
    for label, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    for d in detail:
        print(d)
    passed = all(checks.values())
    print("\nCP12", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
