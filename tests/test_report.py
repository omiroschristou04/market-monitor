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
from datetime import datetime, timezone

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


def _dated(hour, minute, source, title):
    """A headline carrying a real UTC publish time, as the feeds produce."""
    published = datetime(2026, 7, 31, hour, minute, tzinfo=timezone.utc)
    return {"source": source, "title": title, "url": "https://example.com/x",
            "published": published,
            "published_str": published.strftime("%H:%M UTC"),
            # Deliberately stale/wrong — the renderer must recompute these.
            "relative": "99m ago", "image": None}


# The ten items the live report published, in the order it published them:
# five Reuters, then five BBC, with two BBC items newer than Reuters ones above.
DATED_HEADLINES = [
    _dated(14, 4, "Reuters", "Reuters 14:04"),
    _dated(13, 42, "Reuters", "Reuters 13:42"),
    _dated(13, 41, "Reuters", "Reuters 13:41"),
    _dated(12, 57, "Reuters", "Reuters 12:57"),
    _dated(12, 51, "Reuters", "Reuters 12:51"),
    _dated(13, 27, "BBC Business", "BBC 13:27"),
    _dated(13, 16, "BBC Business", "BBC 13:16"),
    _dated(12, 42, "BBC Business", "BBC 12:42"),
    _dated(12, 19, "BBC Business", "BBC 12:19"),
    _dated(11, 19, "BBC Business", "BBC 11:19"),
]


def render(headlines=HEADLINES, rows=None):
    """Render a full report from the standard fixture and return its HTML."""
    rows = rows if rows is not None else make_rows()
    for r in rows:                      # give the trend charts something to draw
        r["history"] = [(f"2026-07-{d:02d}", (r["price"] or 100.0) * (1 + d / 500.0))
                        for d in range(1, 29)]
    with tempfile.TemporaryDirectory() as tmp:
        path = report.generate_report(rows, headlines=headlines, reports_dir=tmp)
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


# --------------------------------------------------------------------------- #
# 5. Dark terminal theme + WCAG AA contrast
# --------------------------------------------------------------------------- #
def _srgb(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hexc):
    hexc = hexc.lstrip("#")
    r, g, b = (int(hexc[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def contrast(fg, bg):
    a, b = _lum(fg), _lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _over(fg, bg, alpha):
    """Composite fg over bg at *alpha* — badge tints are translucent."""
    f, b = fg.lstrip("#"), bg.lstrip("#")
    return "#" + "".join(
        f"{round(int(f[i:i + 2], 16) * alpha + int(b[i:i + 2], 16) * (1 - alpha)):02x}"
        for i in (0, 2, 4))


AA = 4.5


def test_dark_theme():
    print("\n5. Dark terminal theme")

    check("declares a dark colour-scheme",
          '<meta name="color-scheme" content="dark">' in HTML)
    check("page background is a vertical gradient between the dark stops",
          "linear-gradient(180deg," in HTML
          and report.PAGE_TOP in HTML and report.PAGE_BOT in HTML)
    check("page background is deep navy-grey, not white",
          _lum(report.PAGE_TOP) < 0.05, f"luminance {_lum(report.PAGE_TOP):.3f}")
    check("cards are dark, not light on dark",
          _lum(report.SURFACE) < 0.05, f"luminance {_lum(report.SURFACE):.3f}")
    check("cards sit above the page, not below it",
          _lum(report.SURFACE) > _lum(report.PAGE_TOP))
    check("card text is light, not dark",
          _lum(report.INK) > 0.7, f"luminance {_lum(report.INK):.3f}")
    check("charts render on the card colour, not white",
          report.CHART_BG == report.SURFACE)
    check("no white page background survives in the stylesheet",
          "background: var(--bg); color: var(--ink);" not in report._STYLES)

    # Every text/background pair on the page must clear AA.
    surfaces = [("page top", report.PAGE_TOP), ("page bottom", report.PAGE_BOT),
                ("card", report.SURFACE), ("kpi tile", report.SURFACE_2),
                ("masthead", report.NAVY)]
    inks = [("ink", report.INK), ("body", report.INK_2),
            ("muted", report.MUTED), ("micro-label", report.MUTED_2)]
    signals = [("pos", report.POS), ("neg", report.NEG),
               ("caution", report.AMBER), ("neutral", report.NEUTRAL)]

    worst = (999.0, "")
    for s_name, surface in surfaces:
        for t_name, colour in inks + signals:
            r = contrast(colour, surface)
            if r < worst[0]:
                worst = (r, f"{t_name} on {s_name}")
            check(f"AA: {t_name} on {s_name}", r >= AA, f"{r:.2f}:1")

    # Brand tags are lightened for the dark card; check them over their tint.
    for name, colour in list(report.ASSET_COLOURS.items()) + \
            list(report.SOURCE_COLOURS.items()):
        tint = _over(colour, report.SURFACE, 0.12)
        r = contrast(report._lighten(colour, 0.55), tint)
        if r < worst[0]:
            worst = (r, f"badge {name}")
        check(f"AA: badge '{name}' on its tint", r >= AA, f"{r:.2f}:1")

    # The notebook is the one deliberate light surface.
    check("AA: notebook ink on cream paper (deliberate exception)",
          contrast("#1f3a5f", "#fbf8f0") >= AA)
    check("notebook keeps its cream paper", "#fbf8f0" in HTML)
    check("notebook re-declares its own ink so it cannot inherit light text",
          re.search(r"\.notebook \{[^}]*color: #33455f", report._STYLES) is not None)

    print(f"      lowest contrast on the page: {worst[0]:.2f}:1 ({worst[1]})")
    check("every pair clears AA with margin", worst[0] >= AA, f"{worst[0]:.2f}:1")


# --------------------------------------------------------------------------- #
# 6. News thumbnails and the placeholder tile
# --------------------------------------------------------------------------- #
def test_news_placeholder():
    print("\n6. News placeholder tile")

    check("a placeholder renders when no image is reachable",
          'class="news-thumb news-thumb-ph"' in HTML)
    check("the placeholder carries the source name",
          re.search(r'news-thumb-ph"[^>]*>\s*<span>Reuters</span>', HTML) is not None)

    # The old tile was a flat block of per-source brand colour, built inline.
    check("no inline per-source gradient on the tile",
          re.search(r'news-thumb-ph"\s+style=', HTML) is None)
    check("the tile is not a flat orange block",
          "#d97706" not in re.search(
              r'\.news-thumb-ph \{[^}]*\}', report._STYLES).group(0))

    rule = re.search(r"\.news-thumb-ph \{[^}]*\}", report._STYLES).group(0)
    check("tile uses a subtle dark gradient", "linear-gradient" in rule)
    check("tile has a thin border", "border: 1px solid" in rule)
    span = re.search(r"\.news-thumb-ph span \{[^}]*\}", report._STYLES).group(0)
    check("source name is small", re.search(r"font-size: 9(\.\d+)?px", span) is not None)
    check("source name is refined uppercase",
          "text-transform: uppercase" in span and "letter-spacing" in span)
    check("tile styling is source-independent (one treatment for all)",
          "Reuters" not in rule and "BBC" not in rule)


# --------------------------------------------------------------------------- #
# 7. Headlines render strictly newest-first, with labels that reconcile
# --------------------------------------------------------------------------- #
def _rendered_news(html_text):
    """Every headline's (relative label, UTC stamp, title) in render order."""
    out = []
    for block in re.finditer(
            r'news-time">(.*?)</span>.*?news-title"[^>]*>(.*?)</a>',
            html_text, re.S):
        meta = " ".join(block.group(1).split())
        label, _, stamp = meta.partition("&middot;")
        out.append((label.strip(), stamp.strip(), block.group(2).strip()))
    return out


def test_news_ordering_and_times():
    print("\n7. Headlines: cross-source ordering and UTC labels")

    html_text = render(headlines=DATED_HEADLINES)
    items = _rendered_news(html_text)
    for label, stamp, title in items:
        print(f"      {stamp:<12} {label:<10} {title}")

    check("every headline renders", len(items) == len(DATED_HEADLINES),
          f"{len(items)} of {len(DATED_HEADLINES)}")

    stamps = [stamp for _, stamp, _ in items]
    check("stamps are strictly newest-first",
          stamps == sorted(stamps, reverse=True), str(stamps))
    check("the 13:27 BBC item renders above the 12:57 Reuters one",
          stamps.index("13:27 UTC") < stamps.index("12:57 UTC"), str(stamps))
    check("the 13:16 BBC item renders above the 12:57 Reuters one",
          stamps.index("13:16 UTC") < stamps.index("12:57 UTC"), str(stamps))
    check("sources are not grouped into blocks",
          sum(1 for i in range(len(items) - 1)
              if items[i][2].split()[0] != items[i + 1][2].split()[0]) > 1)

    # The renderer must overwrite the stale labels the fixture carries.
    check("stale fetch-time labels are recomputed",
          not any(label == "99m ago" for label, _, _ in items),
          str([l for l, _, _ in items]))

    # Every label must reconcile with its stamp and the page's own clock.
    generated = re.search(r"Generated <span class=\"num\">([^<]*)</span>",
                          html_text).group(1)
    print(f"      page generated: {generated}")
    check("the generation stamp is labelled UTC", generated.endswith(" UTC"),
          generated)
    rendered_at = datetime.strptime(generated, "%Y-%m-%d %H:%M:%S UTC").replace(
        tzinfo=timezone.utc)

    bad = []
    for (label, stamp, title), source in zip(items, report.news.sort_by_published(
            DATED_HEADLINES)):
        expected = report.news._relative_time(source["published"], rendered_at)
        if label != expected:
            bad.append(f"{stamp}: {label} != {expected}")
        if stamp != source["published"].strftime("%H:%M UTC"):
            bad.append(f"stamp mismatch at {title}")
    check("every label reconciles with its stamp and the page clock",
          not bad, "; ".join(bad))

    # The specific contradiction from the live report: a 14:04 UTC story cannot
    # read "25m ago" on a page generated at 17:30 with no timezone on it.
    offset = (rendered_at - DATED_HEADLINES[0]["published"]).total_seconds() / 60
    check("the newest label matches the real elapsed UTC minutes",
          items[0][0] == report.news._relative_time(
              DATED_HEADLINES[0]["published"], rendered_at),
          f"{items[0][0]} vs {offset:.0f} minutes elapsed")


# --------------------------------------------------------------------------- #
# 8. One clock for the whole page, and it says UTC
# --------------------------------------------------------------------------- #
def test_utc_timestamps():
    print("\n8. Page timestamps are UTC and labelled")

    masthead = re.search(r'class="date">([^<]*)</div>', HTML).group(1)
    clock = re.search(r'id="clock">([^<]*)</span>', HTML).group(1)
    generated = re.search(r"Generated <span class=\"num\">([^<]*)</span>",
                          HTML).group(1)
    print(f"      masthead:  {masthead}")
    print(f"      clock:     {clock}")
    print(f"      generated: {generated}")

    check("the masthead time is labelled UTC", masthead.rstrip().endswith("UTC"),
          masthead)
    check("the live clock is labelled UTC", clock.endswith(" UTC"), clock)
    check("the footer stamp is labelled UTC", generated.endswith(" UTC"), generated)

    # All three must be the same instant, not three different clocks.
    stamp = datetime.strptime(generated, "%Y-%m-%d %H:%M:%S UTC")
    check("the masthead and footer agree to the minute",
          stamp.strftime("%H:%M UTC") in masthead,
          f"{masthead} vs {generated}")
    check("the live clock and footer agree to the second",
          clock == stamp.strftime("%H:%M:%S UTC"), f"{clock} vs {generated}")

    # And the page must be rendering in UTC, not local wall time.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    check("the stamp is the current UTC time, not local wall time",
          abs((now - stamp).total_seconds()) < 120,
          f"{stamp} vs UTC now {now}")

    # The ticking clock must not fall back to the viewer's local timezone.
    check("the clock script reads UTC, not toLocaleTimeString",
          "toLocaleTimeString" not in HTML and "toISOString" in HTML)


# --------------------------------------------------------------------------- #
# 9. An uncomputable horizon renders as n/a, never as +0.00%
# --------------------------------------------------------------------------- #
def test_na_cells():
    print("\n9. Unavailable horizons render as n/a")

    check("a None percentage renders n/a", "n/a" in report._fmt_pct(None),
          report._fmt_pct(None))
    check("n/a is not styled as a movement",
          "color:" not in report._fmt_pct(None) and "muted-cell" in report._fmt_pct(None))
    check("n/a carries no direction arrow", "9650" not in report._fmt_pct(None)
          and "9660" not in report._fmt_pct(None))
    check("a real zero is still shown as +0.00%", "+0.00%" in report._fmt_pct(0.0))
    check("n/a is not an em-dash", "&mdash;" not in report._fmt_pct(None))

    # A 2Y row whose 1-month horizon could not be computed, as the guard in
    # fetch now returns it.
    rows = make_rows()
    for r in rows:
        if r["ticker"] == "2YY=F":
            r["price"] = 4.130
            r["change_pct"] = 1.08
            r["week_change_pct"] = 2.35
            r["month_change_pct"] = None      # the stale reference bar
            r["ytd_change_pct"] = 22.23
            r["52w_high"], r["52w_low"] = 4.130, 3.376
    html_text = render(rows=rows)

    row = re.search(r"<tr>(?:(?!</tr>).)*2YY=F.*?</tr>", html_text, re.S).group(0)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    values = [" ".join(re.sub(r"<[^>]+>", " ", c).split()) for c in cells]
    print(f"      2Y row: {values}")

    check("the 2Y row shows n/a for the month it cannot compute",
          any(v == "n/a" for v in values), str(values))
    check("the 2Y row no longer prints a misleading +0.00%",
          not any("0.00%" in v for v in values), str(values))
    check("the horizons that are measurable are still shown",
          any("1.08%" in v for v in values) and any("2.35%" in v for v in values)
          and any("22.23%" in v for v in values), str(values))
    check("only the uncomputable horizon is blanked",
          sum(1 for v in values if v == "n/a") == 1, str(values))


# --------------------------------------------------------------------------- #
# 10. One VIX rounding on the page, masthead included
# --------------------------------------------------------------------------- #
def test_masthead_vix():
    print("\n10. The masthead quotes VIX like every other section")

    html_text = render(rows=make_rows(vix=18.52))
    strip = re.search(r'class="mast-vix">(.*?)</div>', html_text, re.S).group(1)
    strip = " ".join(re.sub(r"<[^>]+>", " ", strip).split())
    print(f"      masthead: {strip}")

    check("the masthead quotes one decimal place", "18.5" in strip, strip)
    check("the masthead does not quote two decimals", "18.52" not in strip, strip)
    check("the masthead still carries the volatility regime",
          "NORMAL" in strip, strip)

    # No VIX level anywhere on the page may use a different rounding.
    levels = set(re.findall(r"VIX(?:\s+Regime\s+\w+\s+&middot;)?\s*(?:at\s*)?"
                            r"(\d+\.\d+)", re.sub(r"<[^>]+>", " ", html_text)))
    check("every VIX level on the page reads 18.5", levels == {"18.5"}, str(levels))
    check("the report and the analysis agree on the format",
          report.analysis._vix_str(18.52) == "18.5")


def main():
    print("=" * 70)
    print(" REPORT RENDERER TESTS")
    print("=" * 70)
    test_bullet_dot_layout()
    test_bullet_content()
    test_design_system()
    test_sections_present()
    test_dark_theme()
    test_news_placeholder()
    test_news_ordering_and_times()
    test_utc_timestamps()
    test_na_cells()
    test_masthead_vix()

    print("\n" + "=" * 70)
    print(f" {len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"   FAILED: {name}")
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
