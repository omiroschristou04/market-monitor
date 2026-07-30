"""HTML report generation for the market monitor.

Renders an institutional-grade "Morning Market Briefing" styled after a
trading-desk terminal page: a deep-navy masthead with a market-regime badge
and live clock, a Goldman-style analyst note, a markets snapshot, a set of
interview-ready analytics cards (yield curve, drawdown tracker, S&P key
levels, cross-asset correlation), a GS-style handwritten notebook, an analyst
decision framework, 30-day trend charts, and a news section with Open Graph
thumbnails.

Design language
---------------
* **Type** — Libre Baskerville (serif) for headings, Inter (grotesque) for
  body copy, IBM Plex Mono for *every* number, price and percentage, and
  Caveat for the handwritten notebook. Numbers use ``tabular-nums`` so
  columns align to a common baseline.
* **Colour** — a layered navy (page off-white, cards pure white, headers deep
  navy with subtle radial depth) plus exactly three signal colours: green,
  red and amber, used sparingly.
* **Space** — an 8px spacing rhythm, hairline dividers, soft shadows, and
  small uppercase micro-labels above key numbers.

The stylesheet lives in :data:`_STYLES` (a static string, so no brace
escaping is needed) and per-element inline styles are kept *consistent* with
it, so the page degrades gracefully in clients that strip ``<head>`` CSS.
"""

import base64
import html
import io
import os
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # headless backend, no display required
import matplotlib.pyplot as plt  # noqa: E402

from . import analysis  # noqa: E402
from .config import ASSET_CLASS_ORDER, REPORTS_DIR  # noqa: E402

# Asset classes for which a 30-day trend chart is drawn.
CHART_CLASSES = ["Equity", "FX", "Commodity"]

# --------------------------------------------------------------------------- #
# Colour language
#
# Three signal colours only — green, red, amber — plus a neutral slate. Each
# has an on-dark counterpart so the same semantic reads correctly against the
# navy masthead and briefing card.
# --------------------------------------------------------------------------- #
POS = "#0f7f5b"      # signal green — positive
NEG = "#c0392f"      # signal red   — negative
AMBER = "#b0761c"    # signal amber — caution
NEUTRAL = "#5b6b7f"  # slate        — neutral

POS_DK = "#34d399"     # on-navy variants
NEG_DK = "#f87171"
AMBER_DK = "#fbbf24"
NEUTRAL_DK = "#94a3b8"

NAVY = "#0a1628"
NAVY_MID = "#16304d"

# Map semantic tones from analysis -> colours (light and on-dark contexts).
TONE_COLOURS = {"pos": POS, "neg": NEG, "caution": AMBER, "neutral": NEUTRAL}
TONE_COLOURS_DK = {"pos": POS_DK, "neg": NEG_DK,
                   "caution": AMBER_DK, "neutral": NEUTRAL_DK}

# Asset-class tag colours.
ASSET_COLOURS = {
    "Equity":     "#1d4ed8",
    "Volatility": "#7c3aed",
    "Rate":       "#0f766e",
    "FX":         "#c2410c",
    "Macro":      "#334155",   # dark grey — macro indicator (US Dollar Index)
    "Commodity":  "#a16207",
    "Crypto":     "#ea580c",   # orange — crypto (Bitcoin)
}

# News source tag colours.
SOURCE_COLOURS = {
    "Reuters":      "#d97706",
    "BBC Business": "#b91c1c",
}

# Restrained institutional line colours for the trend charts.
CHART_COLOURS = ["#16304d", "#2b6ea8", "#0f766e", "#a16207", "#c0392f", "#7c3aed"]


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def vix_regime(vix_value):
    """Map a VIX level to a (label, colour) volatility-regime pair.

    The colour is the on-dark variant: this pair is rendered in the navy
    masthead, where the light-context signal colours would be too dim.
    """
    if vix_value is None:
        return ("UNKNOWN", NEUTRAL_DK)
    if vix_value < 15:
        return ("LOW VOL", POS_DK)
    if vix_value < 20:
        return ("NORMAL", POS_DK)
    if vix_value < 30:
        return ("ELEVATED", AMBER_DK)
    return ("STRESS", NEG_DK)


def _move_colour(value):
    if value is None or value == 0:
        return NEUTRAL
    return POS if value > 0 else NEG


def _darken(hex_colour, factor=0.72):
    """Return a darker shade of a #rrggbb colour (factor < 1 = darker)."""
    hex_colour = hex_colour.lstrip("#")
    if len(hex_colour) != 6:
        return "#" + hex_colour
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, int(c * factor))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgba(hex_colour, alpha):
    """Return an ``rgba(...)`` string for a #rrggbb colour at *alpha*."""
    hex_colour = hex_colour.lstrip("#")
    if len(hex_colour) != 6:
        return hex_colour
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _fmt_pct(value):
    """A signed percentage in monospace, tinted by direction."""
    if value is None:
        return '<span class="num muted-cell">&mdash;</span>'
    arrow = ('<span class="arw">&#9650;</span>' if value > 0 else
             '<span class="arw">&#9660;</span>' if value < 0 else "")
    return (f'<span class="num" style="color:{_move_colour(value)};'
            f'font-weight:600">{arrow}{value:+.2f}%</span>')


def _fmt_price(value, dp=2):
    if value is None:
        return "&mdash;"
    return f"{value:,.{dp}f}"


def _count_price(value, dp=2):
    """A price cell that animates (counts up) on first load via JS."""
    if value is None:
        return "&mdash;"
    return (f'<span class="num count" data-target="{value:.6f}" data-dp="{dp}">'
            f'{_fmt_price(value, dp)}</span>')


def _last_n(history, n=30):
    if not history:
        return []
    return history[-n:]


def _badge(label, colour):
    """A restrained tag: hairline border, faint tint, small signal dot.

    Text is darkened for contrast against the tint while the dot keeps the
    full-strength colour, so the tag reads as a quiet label rather than a
    heavy pill.
    """
    text = _darken(colour, 0.78)
    return (
        f'<span class="tag" style="color:{text};'
        f'background:{_rgba(colour, 0.07)};'
        f'border:1px solid {_rgba(colour, 0.24)}">'
        f'<span class="tag-dot" style="background:{colour}"></span>'
        f'{html.escape(label)}</span>'
    )


def _tone_badge(label, tone):
    return _badge(label, TONE_COLOURS.get(tone, NEUTRAL))


def _section_head(eyebrow, title, meta=""):
    """A section header: micro uppercase label, serif title, hairline rule."""
    meta_html = f'<div class="sec-meta">{meta}</div>' if meta else ""
    return f"""
        <header class="sec-head">
            <div>
                <div class="sec-eyebrow">{html.escape(eyebrow)}</div>
                <h2 class="sec-title">{title}</h2>
            </div>
            {meta_html}
        </header>"""


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def _chart_base64(series):
    """Render a multi-line 30-day trend chart and return a base64 PNG src.

    The chart carries no title: the card supplies one as a ``<figcaption>``
    micro-label above the image, which keeps the plot area clean and lets the
    legend sit at the top without colliding with anything.
    """
    fig, ax = plt.subplots(figsize=(8.0, 2.9), dpi=160)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    for idx, (label, points) in enumerate(series):
        if not points:
            continue
        dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in points]
        values = [v for _, v in points]
        base = values[0]
        if base:
            values = [(v / base - 1.0) * 100.0 for v in values]
        ax.plot(dates, values, linewidth=1.5, solid_capstyle="round",
                color=CHART_COLOURS[idx % len(CHART_COLOURS)], label=label)

    ax.axhline(0, color="#cbd5e1", linewidth=0.9)
    ax.set_ylabel("% vs 30d ago", fontsize=7.5, color="#8a99ab", labelpad=8)
    ax.legend(fontsize=7.5, loc="lower left", frameon=False, ncol=6,
              handlelength=1.6, columnspacing=1.6, labelcolor="#33445a",
              borderaxespad=0, bbox_to_anchor=(0, 1.02))
    ax.grid(True, axis="y", color="#eef1f5", linewidth=0.9)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=7.5, colors="#8a99ab", length=0, pad=6)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#e4e8ee")
        ax.spines[spine].set_linewidth(0.9)
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _build_charts(rows):
    charts = []
    for asset_class in CHART_CLASSES:
        series = [
            (r["name"], _last_n(r.get("history"), 30))
            for r in rows
            if r["asset_class"] == asset_class and r.get("history")
        ]
        if not series:
            continue
        charts.append((asset_class, _chart_base64(series)))
    return charts


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #
def _briefing_section(brief):
    """The navy Goldman-style morning note: lead paragraph + signal bullets.

    Each bullet is a flex row — ``[dot] [label: text]`` — with the dot as a
    non-shrinking flex item and a gap. Nothing is absolutely positioned, so
    the dot can never sit on top of the label.

    Every element also carries inline styles that *mirror* the stylesheet, so
    the card still renders correctly in clients that strip the ``<head>``
    block. Keeping the two in sync is what prevents the dot/label collision.
    """
    bullets = []
    for i, b in enumerate(brief["bullets"]):
        dot = TONE_COLOURS_DK.get(b["tone"], NEUTRAL_DK)
        first = i == 0
        padding = "0 0 12px" if first else "12px 0"
        border = "" if first else "border-top:1px solid rgba(255,255,255,0.07);"
        bullets.append(
            f'<li class="brief-li" style="list-style:none;display:flex;'
            f'align-items:flex-start;gap:12px;{border}'
            f'padding:{padding};margin:0;font-size:13.5px;color:#b6c6d8;'
            f'line-height:1.6;">'
            f'<span class="dot" style="display:block;flex:0 0 auto;'
            f'flex-shrink:0;width:8px;height:8px;border-radius:50%;'
            f'margin-top:7px;background:{dot};"></span>'
            f'<span class="brief-body" style="flex:1 1 auto;min-width:0;">'
            f'<span class="b-label" style="color:#ffffff;font-weight:600;">'
            f'{html.escape(b["label"])}</span>'
            f'<span class="b-sep" style="color:#54687f;">&nbsp;&mdash;&nbsp;</span>'
            f'<span class="b-text" style="color:#b6c6d8;">'
            f'{html.escape(b["text"])}</span></span></li>'
        )
    return f"""
    <section class="card brief reveal">
        {_section_head("Desk Note", "Morning Briefing", "Analyst Commentary")}
        <p class="brief-lead">{html.escape(brief["paragraph"])}</p>
        <ul class="brief-list" style="list-style:none;margin:0;padding:0;">
            {''.join(bullets)}</ul>
    </section>"""


def _snapshot_section(rows):
    by_class = {}
    for r in rows:
        by_class.setdefault(r["asset_class"], []).append(r)

    ordered = ASSET_CLASS_ORDER + [c for c in by_class if c not in ASSET_CLASS_ORDER]
    blocks = []
    for asset_class in ordered:
        items = by_class.get(asset_class)
        if not items:
            continue
        colour = ASSET_COLOURS.get(asset_class, NEUTRAL)
        count = f"{len(items)} instrument{'s' if len(items) != 1 else ''}"
        rows_html = []
        for r in items:
            high = r.get("52w_high") or r.get("high_52w")
            low = r.get("52w_low") or r.get("low_52w")
            rows_html.append(
                "<tr>"
                f'<td class="name">{html.escape(r["name"])}'
                f'<span class="ticker">{html.escape(r["ticker"])}</span></td>'
                f'<td class="price">{_count_price(r.get("price"))}</td>'
                f'<td>{_fmt_pct(r.get("change_pct"))}</td>'
                f'<td>{_fmt_pct(r.get("week_change_pct"))}</td>'
                f'<td>{_fmt_pct(r.get("month_change_pct"))}</td>'
                f'<td>{_fmt_pct(r.get("ytd_change_pct"))}</td>'
                f'<td class="range">{_fmt_price(low)}&nbsp;&ndash;&nbsp;'
                f'{_fmt_price(high)}</td>'
                "</tr>"
            )
        blocks.append(f"""
        <div class="asset-group">
            <div class="asset-head">
                {_badge(asset_class, colour)}
                <span class="rule"></span>
                <span class="asset-count">{count}</span>
            </div>
            <div class="table-wrap">
                <table class="snap">
                    <thead><tr>
                        <th class="left">Instrument</th>
                        <th>Last</th><th>1D</th><th>1W</th>
                        <th>1M</th><th>YTD</th><th>52W Range</th>
                    </tr></thead>
                    <tbody>{''.join(rows_html)}</tbody>
                </table>
            </div>
        </div>""")
    total = len(rows)
    return f"""
    <section class="card reveal">
        {_section_head("Cross-Asset", "Markets Snapshot", f"{total} Instruments")}
        {''.join(blocks)}
    </section>"""


def _yield_curve_section(curve):
    spread_txt = (f"{curve['spread_bps']:+.0f}" if curve["spread_bps"] is not None
                  else "&mdash;")
    spread_unit = ('<span class="unit">bps</span>'
                   if curve["spread_bps"] is not None else "")
    return f"""
    <section class="card reveal">
        {_section_head("Rates", "Yield Curve &middot; 2s10s", "US Treasuries")}
        <div class="kpi-row">
            <div class="kpi">
                <div class="kpi-label">2Y &middot; 2YY=F</div>
                <div class="kpi-value">{_fmt_price(curve['two_y'])}<span class="unit">%</span></div>
            </div>
            <div class="kpi">
                <div class="kpi-label">10Y &middot; ^TNX</div>
                <div class="kpi-value">{_fmt_price(curve['ten_y'])}<span class="unit">%</span></div>
            </div>
            <div class="kpi">
                <div class="kpi-label">10Y &minus; 2Y Spread</div>
                <div class="kpi-value">{spread_txt}{spread_unit}</div>
            </div>
            <div class="kpi kpi-badge">
                <div class="kpi-label">Curve Shape</div>
                <div class="kpi-tag">{_tone_badge(curve['label'], curve['tone'])}</div>
            </div>
        </div>
        <p class="explain">{html.escape(curve['explanation'])}</p>
    </section>"""


def _drawdown_section(items):
    if not items:
        return ""
    rows_html = []
    for d in items:
        rows_html.append(
            "<tr>"
            f'<td class="name">{html.escape(d["name"])}'
            f'<span class="ticker">{html.escape(d["ticker"])}</span></td>'
            f'<td class="price">{_fmt_price(d["price"])}</td>'
            f'<td class="range">{_fmt_price(d["high"])}</td>'
            f'<td><span class="num" style="color:{_move_colour(d["pct_below"])};'
            f'font-weight:600">{d["pct_below"]:+.2f}%</span></td>'
            f'<td class="tag-cell">{_tone_badge(d["label"], d["tone"])}</td>'
            "</tr>"
        )
    return f"""
    <section class="card reveal">
        {_section_head("Risk", "Drawdown Tracker", "Equities vs 52-Week High")}
        <div class="table-wrap">
            <table class="snap">
                <thead><tr>
                    <th class="left">Index</th><th>Last</th>
                    <th>52W High</th><th>From High</th>
                    <th class="right-tag">Status</th>
                </tr></thead>
                <tbody>{''.join(rows_html)}</tbody>
            </table>
        </div>
    </section>"""


def _key_levels_section(kl):
    if not kl:
        return ""
    high_flag = ('<span class="flag flag-pos">within 3% of high</span>'
                 if kl["near_high"] else "")
    low_flag = ('<span class="flag flag-neg">within 3% of low</span>'
                if kl["near_low"] else "")
    return f"""
    <section class="card reveal">
        {_section_head("Technicals", "Key Levels to Watch", "S&amp;P 500")}
        <div class="kpi-row">
            <div class="kpi">
                <div class="kpi-label">Current</div>
                <div class="kpi-value">{_fmt_price(kl['price'], 0)}</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">52-Week High</div>
                <div class="kpi-value">{_fmt_price(kl['high'], 0)}</div>
                <div class="kpi-sub" style="color:{_move_colour(kl['pct_from_high'])}">
                    {kl['pct_from_high']:+.2f}% {high_flag}</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">52-Week Low</div>
                <div class="kpi-value">{_fmt_price(kl['low'], 0)}</div>
                <div class="kpi-sub" style="color:{_move_colour(kl['pct_from_low'])}">
                    {kl['pct_from_low']:+.2f}% {low_flag}</div>
            </div>
        </div>
        <p class="explain">{html.escape(kl['message'])}</p>
    </section>"""


def _correlation_section(note):
    return f"""
    <section class="card reveal">
        {_section_head("Cross-Asset", "Correlation Note", "Regime Read")}
        <p class="explain big">{html.escape(note)}</p>
    </section>"""


def _notebook_section(notes):
    """GS-analyst handwritten notebook: lined cream paper, Caveat font."""
    if not notes:
        notes = ["No notes today — data was thin."]
    lines = "".join(
        f'<li class="nb-line">{html.escape(n)}</li>' for n in notes
    )
    return f"""
    <section class="card notebook reveal">
        {_section_head("Desk Notes",
                       "What a GS Analyst Writes This Morning",
                       "Longhand")}
        <ul class="nb-paper">{lines}</ul>
    </section>"""


def _framework_section(points):
    """Analyst decision framework: numbered navy tiles + icon + explanation."""
    if not points:
        return ""
    items = []
    for i, p in enumerate(points, start=1):
        items.append(f"""
        <li class="tp-item">
            <span class="tp-num">{i:02d}</span>
            <div class="tp-body">
                <div class="tp-title"><span class="tp-icon">{p['icon']}</span>
                    {html.escape(p['title'])}</div>
                <div class="tp-text">{html.escape(p['text'])}</div>
            </div>
        </li>""")
    return f"""
    <section class="card framework reveal">
        {_section_head("Process", "Analyst Decision Framework",
                       f"{len(points)} Steps")}
        <ol class="tp-list">{''.join(items)}</ol>
    </section>"""


def _charts_section(charts):
    if not charts:
        body = '<p class="empty">No chart data available.</p>'
    else:
        body = "".join(
            f'<figure class="chart">'
            f'<figcaption class="chart-label">{html.escape(name)} '
            f'&middot; Indexed to 30 Days Ago</figcaption>'
            f'<img alt="{html.escape(name)} trend chart" src="{src}">'
            f'</figure>'
            for name, src in charts
        )
    return f"""
    <section class="card reveal">
        {_section_head("Performance", "30-Day Trends", "Indexed &middot; % Change")}
        {body}
    </section>"""


def _news_section(headlines):
    if not headlines:
        body = '<p class="empty">No headlines available in the last 24 hours.</p>'
    else:
        items = []
        for h in headlines:
            colour = SOURCE_COLOURS.get(h["source"], NEUTRAL)
            title = html.escape(h["title"])
            url = html.escape(h["url"], quote=True)
            image = h.get("image")
            if image:
                thumb = (f'<div class="news-thumb">'
                         f'<img loading="lazy" alt="" '
                         f'src="{html.escape(image, quote=True)}"></div>')
            else:
                # Muted placeholder carrying the source name.
                thumb = (f'<div class="news-thumb news-thumb-ph" '
                         f'style="background:linear-gradient(155deg,'
                         f'{_darken(colour, 0.92)},{_darken(colour, 0.62)})">'
                         f'<span>{html.escape(h["source"])}</span></div>')
            items.append(f"""
            <div class="news-item">
                {thumb}
                <div class="news-content">
                    <div class="news-meta">
                        {_badge(h["source"], colour)}
                        <span class="news-time">{html.escape(h.get("relative", ""))}
                            &middot; {html.escape(h.get("published_str", ""))}</span>
                    </div>
                    <a class="news-title" href="{url}" target="_blank" rel="noopener">{title}</a>
                </div>
            </div>""")
        body = "\n".join(items)
    count = f"{len(headlines)} Stories" if headlines else "No Stories"
    return f"""
    <section class="card reveal">
        {_section_head("Newsflow", "Headlines", count)}
        {body}
    </section>"""


def _find_vix(rows):
    for r in rows:
        if r["ticker"] == "^VIX":
            return r.get("price")
    return None


# --------------------------------------------------------------------------- #
# Stylesheet
#
# Split in two: a small interpolated block for the design tokens, and a large
# raw string for the rules themselves (raw so CSS escapes like \2022 survive,
# plain so no brace doubling is needed).
# --------------------------------------------------------------------------- #
_TOKENS = f"""
    :root {{
        --navy-900:#050d1a; --navy-800:{NAVY}; --navy-700:#0f2137;
        --navy-600:{NAVY_MID};
        --ink:#0d1826; --ink-2:#33445a; --muted:#64748b; --muted-2:#8a99ab;
        --line:#e4e8ee; --hairline:#eef1f5;
        --bg:#f4f6f9; --surface:#ffffff; --surface-2:#f8fafc;
        --pos:{POS}; --neg:{NEG}; --amber:{AMBER};
        --serif:"Libre Baskerville",Georgia,"Times New Roman",serif;
        --sans:"Inter","Segoe UI",-apple-system,Roboto,Helvetica,Arial,sans-serif;
        --mono:"IBM Plex Mono",ui-monospace,"SFMono-Regular",Consolas,
               "Liberation Mono",monospace;
        --script:"Caveat","Segoe Script",cursive;
    }}
"""

_RULES = r"""
    *,*::before,*::after { box-sizing: border-box; }
    html { -webkit-text-size-adjust: 100%; }
    body {
        margin: 0; background: var(--bg); color: var(--ink);
        font-family: var(--sans); font-size: 14px; line-height: 1.55;
        -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
    }
    /* Every number, price and percentage renders in monospace, tabular. */
    .num, .mono, .kpi-value, .kpi-sub, table.snap td, table.snap th {
        font-family: var(--mono);
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }
    .arw { font-size: 8px; margin-right: 3px; vertical-align: 1px; }
    .unit { font-size: .58em; margin-left: 3px; color: var(--muted-2);
        letter-spacing: .08em; text-transform: uppercase; font-weight: 500; }
    .muted-cell { color: var(--muted-2); }

    /* ---------- thin top accent bar ---------- */
    .topbar {
        position: sticky; top: 0; z-index: 60; height: 3px; width: 100%;
        background: linear-gradient(90deg,#0a1628 0%,#123457 34%,#2b6ea8 62%,
            #2f7d8c 82%,#c2a24c 100%);
    }

    /* ---------- masthead ---------- */
    .masthead {
        position: relative; color: #fff;
        background:
            radial-gradient(900px 320px at 12% -30%, rgba(43,110,168,.30), transparent 62%),
            radial-gradient(700px 260px at 92% 0%, rgba(47,125,140,.16), transparent 60%),
            linear-gradient(180deg,#0c1e35 0%,#0a1628 62%,#081221 100%);
        box-shadow: inset 0 -1px 0 rgba(255,255,255,.09);
        animation: fadeIn .7s ease both;
    }
    @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
    .masthead .inner {
        max-width: 1080px; margin: 0 auto; padding: 40px 24px 32px;
        display: flex; justify-content: space-between;
        align-items: flex-end; gap: 24px; flex-wrap: wrap;
    }
    .eyebrow, .sec-eyebrow, .sec-meta, .asset-count, .kpi-label,
    .chart-label, .news-time, .foot-row {
        font-family: var(--mono); font-size: 10px; font-weight: 500;
        text-transform: uppercase; letter-spacing: .2em;
    }
    .masthead .eyebrow { color: #7f9bbd; }
    .masthead h1 {
        margin: 10px 0 0; font-family: var(--serif); font-size: 34px;
        font-weight: 700; line-height: 1.16; letter-spacing: -.02em; color: #fff;
    }
    .masthead .date { margin-top: 14px; font-family: var(--mono); font-size: 11px;
        text-transform: uppercase; letter-spacing: .16em; color: #9fb6d1; }
    .masthead .clock { margin-top: 6px; font-family: var(--mono); font-size: 11px;
        color: #6f89a8; letter-spacing: .06em; }
    .masthead .clock #clock { color: #dce7f3; font-weight: 500; }
    .mast-right { display: flex; flex-direction: column; align-items: flex-end;
        text-align: right; }
    .regime-badge {
        display: inline-flex; align-items: center; gap: 9px; margin-top: 10px;
        padding: 9px 16px; border-radius: 6px; border: 1px solid;
        font-family: var(--mono); font-size: 13px; font-weight: 600;
        letter-spacing: .1em; text-transform: uppercase; line-height: 1;
    }
    .regime-badge .rb-dot { width: 7px; height: 7px; border-radius: 50%;
        flex: 0 0 auto; }
    .mast-vix { margin-top: 14px; font-family: var(--mono); font-size: 11px;
        color: #7f96b2; letter-spacing: .1em; text-transform: uppercase; }
    .mast-vix b { font-weight: 600; }

    /* ---------- layout ---------- */
    .wrap { max-width: 1080px; margin: 0 auto; padding: 40px 24px 64px; }

    /* ---------- cards ---------- */
    .card {
        background: var(--surface); border: 1px solid var(--line);
        border-radius: 10px; padding: 28px 32px; margin-bottom: 24px;
        box-shadow: 0 1px 2px rgba(13,24,38,.04),
                    0 14px 30px -20px rgba(13,24,38,.30);
    }
    .card:last-of-type { margin-bottom: 0; }
    .reveal {
        opacity: 0; transform: translateY(14px);
        transition: opacity .55s cubic-bezier(.16,1,.3,1),
                    transform .55s cubic-bezier(.16,1,.3,1);
    }
    .reveal.visible { opacity: 1; transform: none; }

    /* ---------- section headers ---------- */
    .sec-head {
        display: flex; justify-content: space-between; align-items: flex-end;
        gap: 16px; padding-bottom: 16px; margin-bottom: 24px;
        border-bottom: 1px solid var(--line);
    }
    .sec-eyebrow { color: var(--muted-2); }
    .sec-title {
        margin: 8px 0 0; font-family: var(--serif); font-size: 19px;
        font-weight: 700; letter-spacing: -.01em; line-height: 1.3;
        color: var(--ink);
    }
    .sec-meta { color: var(--muted-2); white-space: nowrap; padding-bottom: 2px; }

    /* ---------- briefing (navy hero card) ---------- */
    .brief {
        background:
            radial-gradient(700px 280px at 6% -50%, rgba(43,110,168,.22), transparent 62%),
            linear-gradient(180deg,#0c1e35 0%,#0a1628 100%);
        border-color: #16304d; color: #b6c6d8;
        box-shadow: 0 1px 2px rgba(5,13,26,.20),
                    0 18px 36px -22px rgba(5,13,26,.70);
    }
    .brief .sec-head { border-bottom-color: rgba(255,255,255,.10); }
    .brief .sec-eyebrow { color: #7f9bbd; }
    .brief .sec-title { color: #fff; }
    .brief .sec-meta { color: #6f89a8; }
    .brief-lead {
        margin: 0 0 24px; font-family: var(--serif); font-size: 16px;
        line-height: 1.75; color: #e8eef6; letter-spacing: -.005em;
    }
    .brief-list { list-style: none; margin: 0; padding: 0; }
    /* Dot is a non-shrinking flex item with a gap — never overlaps the label. */
    .brief-list li {
        display: flex; align-items: flex-start; gap: 12px;
        padding: 12px 0; margin: 0; font-size: 13.5px; line-height: 1.6;
        color: #b6c6d8; border-top: 1px solid rgba(255,255,255,.07);
    }
    .brief-list li:first-child { border-top: none; padding-top: 0; }
    .brief-list li:last-child { padding-bottom: 0; }
    .brief-list .dot {
        display: block; flex: 0 0 auto; flex-shrink: 0;
        width: 8px; height: 8px; margin-top: 7px; border-radius: 50%;
    }
    .brief-list .brief-body { flex: 1 1 auto; min-width: 0; }
    .brief-list .b-label { color: #fff; font-weight: 600; }
    .brief-list .b-sep { color: #54687f; }

    /* ---------- tags ---------- */
    .tag {
        display: inline-flex; align-items: center; gap: 6px; line-height: 1;
        padding: 5px 9px; border-radius: 4px; white-space: nowrap;
        font-family: var(--mono); font-size: 10px; font-weight: 600;
        text-transform: uppercase; letter-spacing: .14em;
    }
    .tag-dot { width: 5px; height: 5px; border-radius: 50%; flex: 0 0 auto; }

    /* ---------- snapshot tables ---------- */
    .asset-group { margin-bottom: 32px; }
    .asset-group:last-child { margin-bottom: 0; }
    .asset-head { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }
    .asset-head .rule { flex: 1 1 auto; height: 1px; background: var(--hairline); }
    .asset-count { color: var(--muted-2); }
    .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
    table.snap { width: 100%; border-collapse: collapse; font-size: 13px; }
    table.snap th {
        padding: 0 12px 10px; text-align: right; font-size: 10px; font-weight: 500;
        color: var(--muted-2); border-bottom: 1px solid var(--line);
        white-space: nowrap; text-transform: uppercase; letter-spacing: .14em;
    }
    table.snap td {
        padding: 12px; text-align: right; white-space: nowrap;
        border-bottom: 1px solid var(--hairline);
    }
    table.snap th:first-child, table.snap td:first-child { padding-left: 0; }
    table.snap th:last-child, table.snap td:last-child { padding-right: 0; }
    table.snap th.left, table.snap td.name { text-align: left; }
    table.snap th.right-tag, table.snap td.tag-cell { text-align: right; }
    table.snap tbody tr { transition: background-color .18s ease; }
    table.snap tbody tr:hover { background: #f7f9fc; }
    table.snap tbody tr:last-child td { border-bottom: none; }
    td.name {
        font-family: var(--sans); font-size: 13.5px; font-weight: 600;
        color: var(--ink); white-space: normal; line-height: 1.35;
    }
    td.name .ticker {
        display: block; margin-top: 4px; font-family: var(--mono);
        font-size: 10px; font-weight: 500; letter-spacing: .12em;
        text-transform: uppercase; color: var(--muted-2);
    }
    td.price { font-size: 14px; font-weight: 600; color: var(--ink); }
    td.range { font-size: 12px; color: var(--muted); }

    /* ---------- KPI blocks ---------- */
    .kpi-row { display: flex; flex-wrap: wrap; gap: 16px; }
    .kpi {
        flex: 1 1 180px; min-width: 0; padding: 16px 18px;
        background: var(--surface-2); border: 1px solid var(--line);
        border-top: 2px solid var(--navy-600); border-radius: 0 0 6px 6px;
    }
    .kpi-label { color: var(--muted-2); }
    .kpi-value {
        margin-top: 10px; font-size: 26px; font-weight: 600; line-height: 1.1;
        letter-spacing: -.02em; color: var(--ink);
    }
    .kpi-sub { margin-top: 8px; font-size: 11.5px; font-weight: 600; }
    .kpi-badge .kpi-tag { margin-top: 12px; }
    .flag {
        display: inline-block; margin-left: 8px; padding: 3px 7px;
        border-radius: 3px; font-family: var(--mono); font-size: 9.5px;
        font-weight: 600; text-transform: uppercase; letter-spacing: .1em;
    }
    .flag-pos { background: #e6f4ee; color: #0b5f44; }
    .flag-neg { background: #fbeceb; color: #8f2a22; }

    /* ---------- explanatory copy ---------- */
    .explain {
        margin: 24px 0 0; padding-top: 20px; font-size: 13.5px; line-height: 1.7;
        color: var(--ink-2); border-top: 1px solid var(--hairline);
    }
    .explain.big {
        margin: 0; padding: 0; border: none; font-family: var(--serif);
        font-size: 16px; line-height: 1.8; color: var(--ink);
    }

    /* ---------- GS notebook — lined cream paper, red margin rule ---------- */
    .notebook {
        background-color: #fbf8f0; border-color: #e8dfc9;
        background-image:
            linear-gradient(90deg, transparent 56px, #e8c4c4 56px, #e8c4c4 57px, transparent 57px),
            repeating-linear-gradient(#fbf8f0, #fbf8f0 33px, #e3e8ef 34px);
    }
    .notebook .sec-head { border-bottom-color: #e8dfc9; padding-left: 72px; }
    .notebook .sec-eyebrow { color: #a2946e; }
    .notebook .sec-title { color: #6f6242; }
    .notebook .sec-meta { color: #a2946e; }
    .nb-paper { list-style: none; margin: 0; padding: 0 8px 8px 72px; }
    .nb-line {
        font-family: var(--script); font-size: 25px; line-height: 34px;
        color: #1f3a5f; letter-spacing: .2px;
    }
    .nb-line::before { content: "\2022  "; color: #b08968; }

    /* ---------- decision framework ---------- */
    .tp-list { list-style: none; margin: 0; padding: 0; }
    .tp-item { display: flex; gap: 18px; padding: 20px 0;
        border-bottom: 1px solid var(--hairline); }
    .tp-item:first-child { padding-top: 0; }
    .tp-item:last-child { border-bottom: none; padding-bottom: 0; }
    .tp-num {
        flex: 0 0 auto; width: 30px; height: 30px; border-radius: 5px;
        background: linear-gradient(160deg,#17324f,#0a1628); color: #dce7f3;
        font-family: var(--mono); font-size: 11px; font-weight: 600;
        letter-spacing: .04em; display: flex; align-items: center;
        justify-content: center; box-shadow: 0 2px 8px -3px rgba(10,22,40,.6);
    }
    .tp-body { flex: 1 1 auto; min-width: 0; }
    .tp-title { font-size: 14.5px; font-weight: 600; color: var(--navy-800);
        letter-spacing: -.005em; }
    .tp-icon { margin-right: 8px; }
    .tp-text { margin-top: 6px; font-size: 13.5px; line-height: 1.7;
        color: var(--ink-2); }

    /* ---------- charts ---------- */
    .chart { margin: 0 0 28px; }
    .chart:last-child { margin-bottom: 0; }
    .chart-label { color: var(--muted-2); margin-bottom: 10px; }
    .chart img { display: block; width: 100%; height: auto;
        border: 1px solid var(--hairline); border-radius: 6px; }

    /* ---------- news ---------- */
    .news-item {
        display: flex; gap: 18px; align-items: flex-start;
        margin: 0 -8px; padding: 18px 8px; border-radius: 6px;
        border-bottom: 1px solid var(--hairline);
        transition: background-color .18s ease;
    }
    .news-item:hover { background: #f7f9fc; }
    .news-item:last-child { border-bottom: none; }
    .news-thumb {
        flex: 0 0 auto; width: 116px; height: 78px; border-radius: 6px;
        overflow: hidden; background: #e8edf3; border: 1px solid var(--hairline);
    }
    .news-thumb img { width: 100%; height: 100%; object-fit: cover; display: block;
        transition: transform .5s cubic-bezier(.16,1,.3,1); }
    .news-item:hover .news-thumb img { transform: scale(1.05); }
    .news-thumb-ph { display: flex; align-items: center; justify-content: center;
        padding: 8px; text-align: center; border: none; }
    .news-thumb-ph span {
        color: #fff; font-family: var(--mono); font-size: 10px; font-weight: 600;
        text-transform: uppercase; letter-spacing: .12em; line-height: 1.35;
    }
    .news-content { flex: 1 1 auto; min-width: 0; }
    .news-meta { display: flex; align-items: center; gap: 12px; margin-bottom: 8px;
        flex-wrap: wrap; }
    .news-time { color: var(--muted-2); }
    .news-title {
        display: block; color: var(--ink); text-decoration: none; font-size: 15px;
        font-weight: 600; line-height: 1.45; letter-spacing: -.005em;
    }
    .news-title:hover { color: #1d4ed8; text-decoration: underline;
        text-underline-offset: 3px; }

    .empty { color: var(--muted); font-style: italic; margin: 8px 0; }

    /* ---------- footer ---------- */
    .site-footer { margin-top: 40px; padding-top: 24px;
        border-top: 1px solid var(--line); }
    .foot-row { display: flex; justify-content: space-between; gap: 16px;
        flex-wrap: wrap; color: var(--muted-2); }
    .foot-row .foot-brand { color: var(--muted); font-weight: 600; }
    .foot-note { margin-top: 16px; font-size: 11.5px; line-height: 1.7;
        color: #9aa8b8; }

    /* ---------- responsive ---------- */
    @media (max-width: 760px) {
        .masthead .inner { padding: 32px 20px 26px; flex-direction: column;
            align-items: flex-start; gap: 20px; }
        .masthead h1 { font-size: 26px; }
        .mast-right { align-items: flex-start; text-align: left; }
        .wrap { padding: 28px 16px 48px; }
        .card { padding: 22px 18px; border-radius: 8px; }
        .sec-head { flex-direction: column; align-items: flex-start; gap: 8px; }
        .sec-meta { padding-bottom: 0; }
        .kpi { flex: 1 1 100%; }
        .kpi-value { font-size: 22px; }
        .news-thumb { width: 88px; height: 62px; }
        .news-item { gap: 14px; }
        .notebook { background-image:
            repeating-linear-gradient(#fbf8f0, #fbf8f0 33px, #e3e8ef 34px); }
        .notebook .sec-head, .nb-paper { padding-left: 0; }
        .nb-line { font-size: 22px; }
        .tp-item { gap: 14px; }
        .foot-row { flex-direction: column; gap: 8px; }
    }
    @media (prefers-reduced-motion: reduce) {
        .reveal { opacity: 1; transform: none; transition: none; }
        .masthead { animation: none; }
        .news-item, .news-thumb img, table.snap tbody tr { transition: none; }
    }
    @media print {
        body { background: #fff; }
        .topbar { display: none; }
        .card { box-shadow: none; break-inside: avoid; page-break-inside: avoid; }
        .reveal { opacity: 1; transform: none; }
    }
"""

_STYLES = _TOKENS + _RULES


# --------------------------------------------------------------------------- #
# Scripts (animations) — kept in one place for clarity
# --------------------------------------------------------------------------- #
_SCRIPTS = """
<script>
(function () {
    "use strict";

    // 1. Live clock in the masthead.
    function tick() {
        var el = document.getElementById("clock");
        if (!el) return;
        var now = new Date();
        el.textContent = now.toLocaleTimeString([], {
            hour: "2-digit", minute: "2-digit", second: "2-digit"
        });
    }
    tick();
    setInterval(tick, 1000);

    var reduce = window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // 2. Reveal cards on scroll via Intersection Observer (subtle, once each).
    var cards = Array.prototype.slice.call(document.querySelectorAll(".reveal"));
    if (reduce || !("IntersectionObserver" in window)) {
        cards.forEach(function (c) { c.classList.add("visible"); });
    } else {
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (e) {
                if (e.isIntersecting) {
                    e.target.classList.add("visible");
                    io.unobserve(e.target);
                }
            });
        // Positive bottom margin: cards begin revealing just *before* they
        // scroll into view, so fast scrolling never lands on a blank frame.
        }, { threshold: 0, rootMargin: "0px 0px 20% 0px" });
        cards.forEach(function (c) { io.observe(c); });
    }

    // 3. Count-up animation for snapshot numbers (runs once, on first reveal).
    function animateCount(el) {
        var target = parseFloat(el.getAttribute("data-target"));
        var dp = parseInt(el.getAttribute("data-dp") || "2", 10);
        if (isNaN(target)) return;
        if (reduce) {
            el.textContent = target.toLocaleString(undefined,
                { minimumFractionDigits: dp, maximumFractionDigits: dp });
            return;
        }
        var dur = 750, start = null;
        function step(ts) {
            if (start === null) start = ts;
            var p = Math.min((ts - start) / dur, 1);
            var eased = 1 - Math.pow(1 - p, 4);   // easeOutQuart
            var val = target * eased;
            el.textContent = val.toLocaleString(undefined,
                { minimumFractionDigits: dp, maximumFractionDigits: dp });
            if (p < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }
    var counters = Array.prototype.slice.call(document.querySelectorAll(".count"));
    if (!("IntersectionObserver" in window)) {
        counters.forEach(animateCount);
    } else {
        var co = new IntersectionObserver(function (entries) {
            entries.forEach(function (e) {
                if (e.isIntersecting) {
                    animateCount(e.target);
                    co.unobserve(e.target);
                }
            });
        }, { threshold: 0.5 });
        counters.forEach(function (c) { co.observe(c); });
    }
})();
</script>
"""

_FONTS = (
    "https://fonts.googleapis.com/css2"
    "?family=Libre+Baskerville:wght@400;700"
    "&family=Inter:wght@400;500;600;700"
    "&family=IBM+Plex+Mono:wght@400;500;600"
    "&family=Caveat:wght@500;600;700"
    "&display=swap"
)


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def generate_report(rows, headlines=None, reports_dir=REPORTS_DIR):
    """Generate the Morning Market Briefing HTML report.

    *rows* are the metric dicts from fetch; *headlines* (optional) are dicts
    from news.fetch_headlines. Returns the written file path.
    """
    os.makedirs(reports_dir, exist_ok=True)
    now = datetime.now()
    long_date = now.strftime("%A, %d %B %Y")
    stamp = now.strftime("%H:%M")
    full_stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    filename = f"market_report_{now.strftime('%Y%m%d')}.html"
    out_path = os.path.join(reports_dir, filename)

    # --- Analytics --------------------------------------------------------- #
    vix_value = _find_vix(rows)
    regime = analysis.market_regime(rows)
    curve = analysis.yield_curve(rows)
    dd = analysis.drawdowns(rows)
    kl = analysis.key_levels(rows)
    corr = analysis.correlation_note(rows)
    brief = analysis.morning_briefing(rows)
    notebook = analysis.analyst_notebook(rows)
    framework = analysis.decision_framework(rows)

    regime_label, regime_colour = vix_regime(vix_value)
    regime_dk = TONE_COLOURS_DK.get(regime["tone"], NEUTRAL_DK)

    charts = _build_charts(rows)

    # --- Assemble sections ------------------------------------------------- #
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Morning Market Briefing &middot; {long_date}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{_FONTS}" rel="stylesheet">
<style>
{_STYLES}
</style>
<noscript><style>.reveal {{ opacity: 1; transform: none; }}</style></noscript>
</head>
<body>
    <div class="topbar"></div>
    <header class="masthead">
        <div class="inner">
            <div class="mast-left">
                <div class="eyebrow">Market Intelligence &middot; Daily</div>
                <h1>Morning Market Briefing</h1>
                <div class="date">{long_date} &middot; {stamp}</div>
                <div class="clock">Last updated <span id="clock">{stamp}</span></div>
            </div>
            <div class="mast-right">
                <div class="eyebrow">Market Regime</div>
                <div class="regime-badge" style="color:{regime_dk};
                    background:{_rgba(regime_dk, 0.12)};
                    border-color:{_rgba(regime_dk, 0.38)}">
                    <span class="rb-dot" style="background:{regime_dk}"></span>
                    {html.escape(regime['label'])}</div>
                <div class="mast-vix">VIX Regime
                    <b style="color:{regime_colour}">{regime_label}</b>
                    &middot; <span class="num">{_fmt_price(vix_value)}</span></div>
            </div>
        </div>
    </header>

    <main class="wrap">
        {_briefing_section(brief)}
        {_snapshot_section(rows)}
        {_yield_curve_section(curve)}
        {_drawdown_section(dd)}
        {_key_levels_section(kl)}
        {_correlation_section(corr)}
        {_notebook_section(notebook)}
        {_framework_section(framework)}
        {_charts_section(charts)}
        {_news_section(headlines or [])}

        <footer class="site-footer">
            <div class="foot-row">
                <span class="foot-brand">Morning Market Briefing</span>
                <span>Generated <span class="num">{full_stamp}</span></span>
            </div>
            <div class="foot-row" style="margin-top:8px">
                <span>Market data &middot; Yahoo Finance</span>
                <span>Headlines &middot; Reuters &amp; BBC RSS</span>
            </div>
            <p class="foot-note">
                Prices are end-of-session or latest available and may be delayed.
                For informational purposes only &mdash; not investment advice.
            </p>
        </footer>
    </main>
    {_SCRIPTS}
</body>
</html>
"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)

    return out_path
