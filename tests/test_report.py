"""Tests for the HTML report renderer.

Guards the Morning Briefing bullet layout — the coloured dot used to be
absolutely positioned while an inline ``padding`` on the ``<li>`` overrode the
left padding that made room for it, so the dot landed on top of the first
letter of each label ("*entiment" instead of "* Sentiment"). The dot is now a
non-shrinking flex item with a gap, and these tests assert that the class
rules *and* the inline fallback styles both stay that way.

Run with:  python tests/test_report.py
(No pytest dependency — the project ships without a test runner.)
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import report  # noqa: E402
from tests.test_analysis import make_rows  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not condition else ""))


def _padding_left(style):
    """Resolve the left component of an inline ``padding`` shorthand.

    1 value -> all sides; 2 -> (v, h); 3 -> (t, h, b); 4 -> (t, r, b, l).
    Returns "0" when no padding is declared.
    """
    m = re.search(r"(?:^|;)\s*padding:([^;]*)", style)
    if not m:
        return "0"
    parts = m.group(1).split()
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 4:
        return parts[3]
    return parts[1]          # 2- and 3-value forms share the horizontal slot


HEADLINES = [
    {"source": "Reuters", "title": "Yields steady as traders weigh the Fed",
     "url": "https://example.com/a", "relative": "2h ago",
     "published_str": "07:14 BST", "image": None},
]


def render():
    """Render a full report from the standard fixture and return its HTML."""
    rows = make_rows()
    for r in rows:                      # give the trend charts something to draw
        r["history"] = [(f"2026-07-{d:02d}", (r["price"] or 100.0) * (1 + d / 500.0))
                        for d in range(1, 29)]
    with tempfile.TemporaryDirectory() as tmp:
        path = report.generate_report(rows, headlines=HEADLINES, reports_dir=tmp)
        with open(path, encoding="utf-8") as f:
            return f.read()


HTML = render()

# Every <li> in the briefing list, with its inline style attribute.
BULLETS = re.findall(r'<li class="brief-li" style="([^"]*)"(.*?)</li>', HTML, re.S)


# --------------------------------------------------------------------------- #
# 1. The bug: the dot must never sit on top of the label
# --------------------------------------------------------------------------- #
def test_bullet_dot_layout():
    print("\n1. Morning Briefing bullets - dot sits left of the label")

    check("briefing renders bullets", len(BULLETS) > 0, f"found {len(BULLETS)}")

    # The <li> is a flex row in both the stylesheet and the inline fallback.
    check("class rule makes the li a flex row",
          re.search(r"\.brief-list li \{[^}]*display: flex", report._STYLES) is not None)
    check("every li is inline-styled display:flex",
          all("display:flex" in style for style, _ in BULLETS))
    check("every li declares a gap",
          all("gap:12px" in style for style, _ in BULLETS))

    # The dot is a non-shrinking flex item — never absolutely positioned.
    dots = re.findall(r'<span class="dot" style="([^"]*)"', HTML)
    check("a dot per bullet", len(dots) == len(BULLETS), f"{len(dots)} vs {len(BULLETS)}")
    check("no dot is absolutely positioned",
          all("position:absolute" not in d for d in dots))
    check("every dot sets flex-shrink:0",
          all("flex-shrink:0" in d for d in dots))
    check("no dot uses a negative margin",
          all("margin:-" not in d and "margin-left:-" not in d for d in dots))

    # The old bug was an inline padding that killed the left padding reserving
    # space for an absolutely positioned dot. Neither may come back.
    check("stylesheet .dot rule is not absolute",
          re.search(r"\.brief-list \.dot \{[^}]*position: absolute", report._STYLES) is None)
    lefts = [_padding_left(s) for s, _ in BULLETS]
    check("no li reserves left padding for an absolute dot",
          all(p == "0" for p in lefts),
          f"left paddings {lefts} imply the old absolute-dot layout")


# --------------------------------------------------------------------------- #
# 2. Label and text stay separate, escaped elements
# --------------------------------------------------------------------------- #
def test_bullet_content():
    print("\n2. Bullet content - label and text are distinct elements")

    labels = re.findall(r'<span class="b-label"[^>]*>([^<]*)</span>', HTML)
    check("a label per bullet", len(labels) == len(BULLETS),
          f"{len(labels)} labels vs {len(BULLETS)} bullets")
    check("labels are non-empty", all(lbl.strip() for lbl in labels))
    check("no label starts mid-word (dot did not eat a letter)",
          all(lbl[0].isupper() for lbl in labels if lbl),
          f"labels: {labels}")
    check("label and body are separate spans",
          all('class="brief-body"' in body for _, body in BULLETS))


# --------------------------------------------------------------------------- #
# 3. The institutional design system is wired up
# --------------------------------------------------------------------------- #
def test_design_system():
    print("\n3. Design system - fonts, tokens, structure")

    for family in ("Libre+Baskerville", "Inter", "IBM+Plex+Mono", "Caveat"):
        check(f"loads {family.replace('+', ' ')}", family in HTML)

    check("declares a serif token", "--serif:" in HTML)
    check("declares a mono token", "--mono:" in HTML)
    check("numbers use tabular figures", "font-variant-numeric: tabular-nums" in HTML)
    check("has a thin top accent bar", 'class="topbar"' in HTML)
    check("section headers carry micro-labels", 'class="sec-eyebrow"' in HTML)
    check("has a refined footer", 'class="site-footer"' in HTML)
    check("footer carries a timestamp", "Generated" in HTML)
    check("footer carries data attribution", "Yahoo Finance" in HTML)
    check("rows have hover states", "tbody tr:hover" in HTML)
    check("tables can scroll on mobile", 'class="table-wrap"' in HTML)
    check("has a mobile breakpoint", "@media (max-width: 760px)" in HTML)
    check("respects reduced motion", "prefers-reduced-motion" in HTML)
    check("content survives without JS", "<noscript>" in HTML)


# --------------------------------------------------------------------------- #
# 4. Every section still renders
# --------------------------------------------------------------------------- #
def test_sections_present():
    print("\n4. All sections still render")

    for title in ("Morning Briefing", "Markets Snapshot", "Yield Curve",
                  "Drawdown Tracker", "Key Levels to Watch", "Correlation Note",
                  "What a GS Analyst Writes", "Analyst Decision Framework",
                  "30-Day Trends", "Headlines"):
        check(f"section: {title}", title in HTML)

    check("snapshot lists instruments", "S&amp;P 500" in HTML)
    check("charts embedded as data URIs", "data:image/png;base64," in HTML)
    check("scroll animations retained",
          re.search(r'class="[^"]*\breveal\b', HTML) is not None
          and ".reveal.visible" in HTML)
    check("count-up animation retained", 'class="num count"' in HTML)


def main():
    print("=" * 70)
    print(" REPORT RENDERER TESTS")
    print("=" * 70)
    test_bullet_dot_layout()
    test_bullet_content()
    test_design_system()
    test_sections_present()

    print("\n" + "=" * 70)
    print(f" {len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"   FAILED: {name}")
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
