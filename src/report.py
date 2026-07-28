"""HTML report generation for the market monitor.

Renders a premium, Bloomberg-style "Morning Market Briefing": a dark-navy
masthead with a market-regime badge and live clock, a Goldman-style analyst
note, a markets snapshot, a set of interview-ready analytics cards (yield
curve, drawdown tracker, S&P key levels, cross-asset correlation), a GS-style
handwritten notebook, an analyst decision framework, 30-day trend charts, and
a news section with Open Graph thumbnails.

The page is enriched with Google Fonts (Georgia/Inter/Caveat), a gradient
accent bar, gradient asset-class badges, and smooth, professional scroll and
load animations (Intersection Observer reveals, count-up numbers, gradient
masthead, pulsing regime badge, thumbnail hover zoom).
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

# Core colour language.
POS = "#16a34a"      # green  — positive
NEG = "#dc2626"      # red    — negative
AMBER = "#d97706"    # amber  — caution
NEUTRAL = "#475569"  # slate  — neutral
NAVY = "#0a1628"

# Map semantic tones from analysis -> colours.
TONE_COLOURS = {"pos": POS, "neg": NEG, "caution": AMBER, "neutral": NEUTRAL}

# Asset-class badge colours.
ASSET_COLOURS = {
    "Equity":     "#2563eb",
    "Volatility": "#9333ea",
    "Rate":       "#0d9488",
    "FX":         "#ea580c",
    "Macro":      "#334155",   # dark grey — macro indicator (US Dollar Index)
    "Commodity":  "#ca8a04",
    "Crypto":     "#f97316",   # orange — crypto (Bitcoin)
}

# News source badge colours.
SOURCE_COLOURS = {
    "Reuters":      "#ff8000",
    "BBC Business": "#bb1919",
}


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def vix_regime(vix_value):
    """Map a VIX level to a (label, colour) volatility-regime pair."""
    if vix_value is None:
        return ("UNKNOWN", NEUTRAL)
    if vix_value < 15:
        return ("LOW VOL", POS)
    if vix_value < 20:
        return ("NORMAL", "#22c55e")
    if vix_value < 30:
        return ("ELEVATED", AMBER)
    return ("STRESS", NEG)


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


def _fmt_pct(value):
    if value is None:
        return f'<span style="color:{NEUTRAL}">&mdash;</span>'
    arrow = "&#9650;" if value > 0 else "&#9660;" if value < 0 else ""
    return (f'<span style="color:{_move_colour(value)};font-weight:600">'
            f'{arrow} {value:+.2f}%</span>')


def _fmt_price(value, dp=2):
    if value is None:
        return "&mdash;"
    return f"{value:,.{dp}f}"


def _count_price(value, dp=2):
    """A price cell that animates (counts up) on first load via JS."""
    if value is None:
        return "&mdash;"
    return (f'<span class="count" data-target="{value:.6f}" data-dp="{dp}">'
            f'{_fmt_price(value, dp)}</span>')


def _last_n(history, n=30):
    if not history:
        return []
    return history[-n:]


def _badge(label, colour):
    """A gradient pill badge (subtle 135deg gradient from the base colour)."""
    return (f'<span class="badge" style="background:linear-gradient(135deg,'
            f'{colour},{_darken(colour)})">{html.escape(label)}</span>')


def _tone_badge(label, tone):
    return _badge(label, TONE_COLOURS.get(tone, NEUTRAL))


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def _chart_base64(series, title):
    """Render a multi-line 30-day trend chart and return a base64 PNG src."""
    fig, ax = plt.subplots(figsize=(7.6, 3.0), dpi=120)
    fig.patch.set_facecolor("white")

    for label, points in series:
        if not points:
            continue
        dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in points]
        values = [v for _, v in points]
        base = values[0]
        if base:
            values = [(v / base - 1.0) * 100.0 for v in values]
        ax.plot(dates, values, linewidth=1.8, label=label)

    ax.set_title(title, fontsize=11, fontweight="bold", loc="left", color=NAVY)
    ax.axhline(0, color="#cbd5e1", linewidth=0.8, linestyle="--")
    ax.set_ylabel("% vs 30d ago", fontsize=8, color="#64748b")
    ax.legend(fontsize=8, loc="best", frameon=False)
    ax.grid(True, alpha=0.2)
    ax.tick_params(labelsize=8, colors="#64748b")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
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
        charts.append((asset_class, _chart_base64(series, f"{asset_class} — 30-day trend")))
    return charts


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #
def _briefing_section(brief):
    """The navy Goldman-style morning note: paragraph + bullets.

    Every element carries inline styles in addition to its class. The class
    styling drives the polished browser view; the inline styles guarantee the
    coloured dots, bold labels and navy card still render in email clients
    (e.g. Gmail) that strip the <head> <style> block and ``position:absolute``.
    """
    bullets = []
    for i, b in enumerate(brief["bullets"]):
        dot = TONE_COLOURS.get(b["tone"], NEUTRAL)
        border = "" if i == 0 else "border-top:1px solid #16243b;"
        bullets.append(
            f'<li class="brief-li" style="list-style:none;{border}'
            f'padding:8px 0;margin:0;font-size:14px;color:#cbd5e1;'
            f'line-height:1.5;">'
            f'<span class="dot" style="display:inline-block;width:9px;'
            f'height:9px;border-radius:50%;background:{dot};margin-right:10px;'
            f'vertical-align:middle;"></span>'
            f'<span class="b-label" style="color:#ffffff;font-weight:700;">'
            f'{html.escape(b["label"])}:</span> '
            f'<span style="color:#cbd5e1;">{html.escape(b["text"])}</span></li>'
        )
    return f"""
    <section class="card brief reveal" style="background:{NAVY};border:none;
        border-radius:12px;padding:22px 24px;margin-bottom:22px;color:#e2e8f0;">
        <h2 class="brief-h" style="color:#93c5fd;border-bottom:1px solid #1e3a5f;
            margin:0 0 16px;font-size:13px;font-weight:700;
            text-transform:uppercase;letter-spacing:1.5px;padding-bottom:12px;">
            Morning Briefing</h2>
        <p class="brief-lead" style="font-size:16px;line-height:1.6;
            color:#f1f5f9;margin:0 0 18px;font-family:Georgia,'Times New Roman',serif;">
            {html.escape(brief["paragraph"])}</p>
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
        colour = ASSET_COLOURS.get(asset_class, "#475569")
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
                f'<td class="range">{_fmt_price(low)} &ndash; {_fmt_price(high)}</td>'
                "</tr>"
            )
        blocks.append(f"""
        <div class="asset-group">
            <div class="asset-head">{_badge(asset_class, colour)}</div>
            <table class="snap">
                <thead><tr>
                    <th class="left">Instrument</th>
                    <th>Last</th><th>1D</th><th>1W</th>
                    <th>1M</th><th>YTD</th><th>52W Range</th>
                </tr></thead>
                <tbody>{''.join(rows_html)}</tbody>
            </table>
        </div>""")
    return f"""
    <section class="card reveal">
        <h2>Markets Snapshot</h2>
        {''.join(blocks)}
    </section>"""


def _yield_curve_section(curve):
    spread_txt = (f"{curve['spread_bps']:+.0f} bps" if curve["spread_bps"] is not None
                  else "&mdash;")
    return f"""
    <section class="card reveal">
        <h2>Yield Curve &middot; 2s10s</h2>
        <div class="kpi-row">
            <div class="kpi">
                <div class="kpi-label">2Y (2YY=F)</div>
                <div class="kpi-value">{_fmt_price(curve['two_y'])}%</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">10Y (^TNX)</div>
                <div class="kpi-value">{_fmt_price(curve['ten_y'])}%</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">10Y &minus; 2Y Spread</div>
                <div class="kpi-value">{spread_txt}</div>
            </div>
            <div class="kpi kpi-badge">
                <div class="kpi-label">Shape</div>
                <div>{_tone_badge(curve['label'], curve['tone'])}</div>
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
            f'<td style="color:{_move_colour(d["pct_below"])};font-weight:600">'
            f'{d["pct_below"]:+.2f}%</td>'
            f'<td>{_tone_badge(d["label"], d["tone"])}</td>'
            "</tr>"
        )
    return f"""
    <section class="card reveal">
        <h2>Drawdown Tracker &middot; Equities vs 52-Week High</h2>
        <table class="snap">
            <thead><tr>
                <th class="left">Index</th><th>Last</th>
                <th>52W High</th><th>From High</th><th>Status</th>
            </tr></thead>
            <tbody>{''.join(rows_html)}</tbody>
        </table>
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
        <h2>Key Levels to Watch &middot; S&amp;P 500</h2>
        <div class="kpi-row">
            <div class="kpi">
                <div class="kpi-label">Current</div>
                <div class="kpi-value">{_fmt_price(kl['price'], 0)}</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">52W High</div>
                <div class="kpi-value">{_fmt_price(kl['high'], 0)}</div>
                <div class="kpi-sub" style="color:{_move_colour(kl['pct_from_high'])}">
                    {kl['pct_from_high']:+.2f}% {high_flag}</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">52W Low</div>
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
        <h2>Cross-Asset Correlation Note</h2>
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
        <h2>What a GS Analyst Writes in Their Notebook This Morning</h2>
        <ul class="nb-paper">{lines}</ul>
    </section>"""


def _framework_section(points):
    """Analyst decision framework: numbered navy circles + icon + explanation."""
    if not points:
        return ""
    items = []
    for i, p in enumerate(points, start=1):
        items.append(f"""
        <li class="tp-item">
            <span class="tp-num">{i}</span>
            <div class="tp-body">
                <div class="tp-title"><span class="tp-icon">{p['icon']}</span>
                    {html.escape(p['title'])}</div>
                <div class="tp-text">{html.escape(p['text'])}</div>
            </div>
        </li>""")
    return f"""
    <section class="card framework reveal">
        <h2>Analyst Decision Framework</h2>
        <ol class="tp-list">{''.join(items)}</ol>
    </section>"""


def _charts_section(charts_html):
    return f"""
    <section class="card reveal">
        <h2>30-Day Trends</h2>
        {charts_html}
    </section>"""


def _news_section(headlines):
    if not headlines:
        body = '<p class="empty">No headlines available in the last 24 hours.</p>'
    else:
        items = []
        for h in headlines:
            colour = SOURCE_COLOURS.get(h["source"], "#475569")
            title = html.escape(h["title"])
            url = html.escape(h["url"], quote=True)
            image = h.get("image")
            if image:
                thumb = (f'<div class="news-thumb">'
                         f'<img loading="lazy" alt="" '
                         f'src="{html.escape(image, quote=True)}"></div>')
            else:
                # Coloured placeholder carrying the source name.
                thumb = (f'<div class="news-thumb news-thumb-ph" '
                         f'style="background:linear-gradient(135deg,{colour},'
                         f'{_darken(colour)})"><span>{html.escape(h["source"])}'
                         f'</span></div>')
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
    return f"""
    <section class="card reveal">
        <h2>Headlines</h2>
        {body}
    </section>"""


def _find_vix(rows):
    for r in rows:
        if r["ticker"] == "^VIX":
            return r.get("price")
    return None


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

    // 2. Reveal cards on scroll via Intersection Observer.
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
        }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
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
        var dur = 900, start = null;
        function step(ts) {
            if (start === null) start = ts;
            var p = Math.min((ts - start) / dur, 1);
            var eased = 1 - Math.pow(1 - p, 3);   // easeOutCubic
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
    regime_badge_colour = TONE_COLOURS.get(regime["tone"], NEUTRAL)

    charts = _build_charts(rows)
    charts_html = "".join(
        f'<figure class="chart"><img alt="{html.escape(name)} trend chart" '
        f'src="{src}"></figure>'
        for name, src in charts
    ) or '<p class="empty">No chart data available.</p>'

    # --- Assemble sections ------------------------------------------------- #
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Morning Market Briefing &middot; {long_date}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Caveat:wght@500;600;700&display=swap" rel="stylesheet">
<style>
    :root {{
        --navy: {NAVY}; --pos: {POS}; --neg: {NEG}; --amber: {AMBER};
        --ink: #0f172a; --muted: #64748b; --line: #e2e8f0; --bg: #eef1f6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
        margin: 0; background: var(--bg); color: var(--ink);
        font-family: "Inter", "Segoe UI", -apple-system, Roboto, Helvetica, Arial, sans-serif;
        -webkit-font-smoothing: antialiased; line-height: 1.45;
    }}

    /* Thin gradient accent bar pinned to the very top of the page. */
    .topbar {{
        height: 4px; width: 100%;
        background: linear-gradient(90deg, #0a1628 0%, #2563eb 50%, #14b8a6 100%);
        position: sticky; top: 0; z-index: 50;
    }}

    /* Masthead with a subtle animated gradient on load. */
    .masthead {{
        color: #fff; padding: 30px 0 26px; border-bottom: 3px solid #1e3a5f;
        background: linear-gradient(120deg, #0a1628, #112844, #0a1628, #0d2137);
        background-size: 300% 300%;
        animation: mastShift 18s ease infinite, mastFade 0.9s ease both;
    }}
    @keyframes mastShift {{
        0% {{ background-position: 0% 50%; }}
        50% {{ background-position: 100% 50%; }}
        100% {{ background-position: 0% 50%; }}
    }}
    @keyframes mastFade {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
    .masthead .inner {{
        max-width: 980px; margin: 0 auto; padding: 0 24px;
        display: flex; justify-content: space-between;
        align-items: flex-end; flex-wrap: wrap; gap: 16px;
    }}
    .masthead h1 {{
        margin: 0; font-size: 31px; font-weight: 700; letter-spacing: -0.5px;
        font-family: Georgia, "Times New Roman", serif;
    }}
    .masthead .date {{ color: #93c5fd; font-size: 13px; margin-top: 6px;
        text-transform: uppercase; letter-spacing: 1.4px; }}
    .masthead .clock {{ color: #cbd5e1; font-size: 12px; margin-top: 6px;
        font-variant-numeric: tabular-nums; }}
    .masthead .clock #clock {{ color: #fff; font-weight: 600; }}
    .masthead .right {{ text-align: right; }}
    .masthead .regime-lbl {{ font-size: 11px; color: #94a3b8;
        text-transform: uppercase; letter-spacing: 1px; }}
    .masthead .regime-badge {{
        display: inline-block; margin-top: 6px; padding: 8px 18px;
        border-radius: 999px; font-weight: 700; font-size: 16px; color: #fff;
        background: {regime_badge_colour}; letter-spacing: .5px;
        animation: regimePulse 2.4s ease-in-out 3;
    }}
    @keyframes regimePulse {{
        0%, 100% {{ box-shadow: 0 0 0 0 rgba(255,255,255,0.0); transform: scale(1); }}
        50% {{ box-shadow: 0 0 0 8px rgba(255,255,255,0.10); transform: scale(1.035); }}
    }}
    .masthead .vix {{ color: #cbd5e1; font-size: 13px; margin-top: 8px; }}
    .masthead .vix b {{ color: {regime_colour}; }}

    .wrap {{ max-width: 980px; margin: 0 auto; padding: 26px 24px 64px; }}

    /* Cards (with scroll-reveal animation). */
    .card {{
        background: #fff; border: 1px solid var(--line); border-radius: 12px;
        padding: 22px 24px; margin-bottom: 22px;
        box-shadow: 0 1px 2px rgba(15,23,42,.04), 0 4px 12px rgba(15,23,42,.05);
    }}
    .reveal {{
        opacity: 0; transform: translateY(26px);
        transition: opacity .6s ease, transform .6s cubic-bezier(.22,.61,.36,1);
    }}
    .reveal.visible {{ opacity: 1; transform: translateY(0); }}
    @media (prefers-reduced-motion: reduce) {{
        .reveal {{ opacity: 1; transform: none; transition: none; }}
        .masthead {{ animation: none; }}
        .masthead .regime-badge {{ animation: none; }}
    }}
    .card > h2 {{
        margin: 0 0 18px; font-size: 13px; font-weight: 700; color: var(--muted);
        text-transform: uppercase; letter-spacing: 1.5px;
        padding-bottom: 12px; border-bottom: 1px solid var(--line);
    }}

    /* Navy briefing card */
    .brief {{ background: var(--navy); border: none; color: #e2e8f0; }}
    .brief .brief-h {{
        color: #93c5fd; border-bottom: 1px solid #1e3a5f; margin: 0 0 16px;
        font-size: 13px; font-weight: 700; text-transform: uppercase;
        letter-spacing: 1.5px; padding-bottom: 12px;
    }}
    .brief-lead {{
        font-size: 16px; line-height: 1.6; color: #f1f5f9; margin: 0 0 18px;
        font-family: Georgia, "Times New Roman", serif;
    }}
    .brief-list {{ list-style: none; margin: 0; padding: 0; }}
    .brief-list li {{
        position: relative; padding: 7px 0 7px 22px; font-size: 14px;
        color: #cbd5e1; border-top: 1px solid #16243b;
    }}
    .brief-list .dot {{
        position: absolute; left: 0; top: 13px; width: 9px; height: 9px;
        border-radius: 50%;
    }}
    .brief-list .b-label {{ color: #fff; font-weight: 700; }}

    /* Badges (gradient pills) */
    .badge {{
        display: inline-block; padding: 4px 11px; border-radius: 6px;
        color: #fff; font-size: 11px; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.6px;
        box-shadow: 0 1px 2px rgba(15,23,42,.18);
    }}

    /* Snapshot tables */
    .asset-group {{ margin-bottom: 22px; }}
    .asset-group:last-child {{ margin-bottom: 0; }}
    .asset-head {{ margin-bottom: 8px; }}
    table.snap {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    table.snap th, table.snap td {{
        padding: 10px 10px; text-align: right; border-bottom: 1px solid var(--line);
    }}
    table.snap th {{ color: var(--muted); font-size: 11px; font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.5px; }}
    table.snap th.left, table.snap td.name {{ text-align: left; }}
    table.snap tbody tr:last-child td {{ border-bottom: none; }}
    td.name {{ font-weight: 600; }}
    td.name .ticker {{ color: #94a3b8; font-size: 11px; font-weight: 400; margin-left: 8px; }}
    td.price {{ font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; }}
    td.range {{ color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }}

    /* KPI rows (yield curve, key levels) */
    .kpi-row {{ display: flex; flex-wrap: wrap; gap: 14px; }}
    .kpi {{
        flex: 1 1 150px; background: #f8fafc; border: 1px solid var(--line);
        border-radius: 10px; padding: 14px 16px;
    }}
    .kpi-label {{ font-size: 11px; color: var(--muted); text-transform: uppercase;
        letter-spacing: 0.6px; }}
    .kpi-value {{ font-size: 24px; font-weight: 700; margin-top: 6px;
        font-variant-numeric: tabular-nums; }}
    .kpi-sub {{ font-size: 13px; margin-top: 4px; font-weight: 600; }}
    .kpi-badge {{ display: flex; flex-direction: column; justify-content: center; }}
    .kpi-badge > div:last-child {{ margin-top: 8px; }}
    .flag {{ display: inline-block; margin-left: 6px; padding: 2px 8px;
        border-radius: 5px; font-size: 11px; font-weight: 700; }}
    .flag-pos {{ background: #dcfce7; color: #166534; }}
    .flag-neg {{ background: #fee2e2; color: #991b1b; }}

    .explain {{ color: var(--muted); font-size: 14px; margin: 16px 0 0;
        padding-top: 14px; border-top: 1px dashed var(--line); }}
    .explain.big {{ font-size: 16px; color: var(--ink); border: none;
        padding: 0; margin: 0; line-height: 1.6; }}

    /* GS notebook — lined cream paper with a red margin line. */
    .notebook {{
        background: #fdf9ec;
        background-image:
            linear-gradient(90deg, transparent 46px, #f4b8b8 46px, #f4b8b8 48px, transparent 48px),
            repeating-linear-gradient(#fdf9ec, #fdf9ec 33px, #d9e3ef 34px);
        border: 1px solid #e6dcc0;
    }}
    .notebook > h2 {{ color: #8a7b53; border-bottom-color: #e6dcc0;
        padding-left: 60px; }}
    .nb-paper {{ list-style: none; margin: 0; padding: 0 8px 6px 60px; }}
    .nb-line {{
        font-family: "Caveat", "Segoe Script", cursive; font-size: 26px;
        line-height: 34px; color: #1f3a5f; padding: 0; letter-spacing: .2px;
    }}
    .nb-line::before {{ content: "\\2022  "; color: #b08968; }}

    /* Analyst decision framework — numbered navy circles + icons. */
    .tp-list {{ list-style: none; margin: 0; padding: 0; counter-reset: tp; }}
    .tp-item {{ display: flex; gap: 16px; padding: 16px 0;
        border-bottom: 1px solid var(--line); }}
    .tp-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
    .tp-num {{
        flex: 0 0 auto; width: 30px; height: 30px; border-radius: 50%;
        background: linear-gradient(135deg, #1e3a5f, var(--navy)); color: #fff;
        font-weight: 700; font-size: 14px; display: flex; align-items: center;
        justify-content: center; box-shadow: 0 2px 6px rgba(10,22,40,.30);
    }}
    .tp-body {{ flex: 1 1 auto; }}
    .tp-title {{ font-weight: 700; font-size: 15px; color: var(--navy);
        margin-bottom: 3px; }}
    .tp-icon {{ margin-right: 7px; }}
    .tp-text {{ font-size: 14px; color: #334155; line-height: 1.55; }}

    /* Charts */
    .chart {{ margin: 0 0 14px; text-align: center; }}
    .chart img {{ max-width: 100%; height: auto; }}

    /* News (thumbnail + content row, hover-zoom on the image) */
    .news-item {{ display: flex; gap: 14px; align-items: flex-start;
        padding: 14px 0; border-bottom: 1px solid var(--line); }}
    .news-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
    .news-thumb {{
        flex: 0 0 auto; width: 120px; height: 80px; border-radius: 8px;
        overflow: hidden; background: #e2e8f0;
    }}
    .news-thumb img {{ width: 100%; height: 100%; object-fit: cover;
        display: block; transition: transform .45s ease; }}
    .news-thumb:hover img {{ transform: scale(1.10); }}
    .news-thumb-ph {{ display: flex; align-items: center; justify-content: center;
        padding: 6px; text-align: center; }}
    .news-thumb-ph span {{ color: #fff; font-size: 12px; font-weight: 700;
        text-transform: uppercase; letter-spacing: .5px; line-height: 1.25;
        text-shadow: 0 1px 2px rgba(0,0,0,.25); }}
    .news-content {{ flex: 1 1 auto; min-width: 0; }}
    .news-meta {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
    .news-time {{ color: var(--muted); font-size: 12px; }}
    .news-title {{ color: var(--ink); text-decoration: none; font-size: 15px;
        font-weight: 600; display: block; }}
    .news-title:hover {{ color: #2563eb; text-decoration: underline; }}

    .empty {{ color: var(--muted); font-style: italic; margin: 6px 0; }}
    footer {{ text-align: center; color: #94a3b8; font-size: 12px; margin-top: 8px;
        line-height: 1.6; }}
</style>
</head>
<body>
    <div class="topbar"></div>
    <div class="masthead">
        <div class="inner">
            <div>
                <h1>Morning Market Briefing</h1>
                <div class="date">{long_date} &middot; {stamp}</div>
                <div class="clock">Last updated <span id="clock">{stamp}</span></div>
            </div>
            <div class="right">
                <div class="regime-lbl">Market Regime</div>
                <div class="regime-badge">{html.escape(regime['label'])}</div>
                <div class="vix">VIX Regime: <b>{regime_label}</b>
                    ({_fmt_price(vix_value)})</div>
            </div>
        </div>
    </div>

    <div class="wrap">
        {_briefing_section(brief)}
        {_snapshot_section(rows)}
        {_yield_curve_section(curve)}
        {_drawdown_section(dd)}
        {_key_levels_section(kl)}
        {_correlation_section(corr)}
        {_notebook_section(notebook)}
        {_framework_section(framework)}
        {_charts_section(charts_html)}
        {_news_section(headlines or [])}

        <footer>
            Generated {full_stamp} &middot; Market data via Yahoo Finance &middot;
            Headlines via Reuters &amp; BBC RSS<br>
            For informational purposes only — not investment advice.
        </footer>
    </div>
    {_SCRIPTS}
</body>
</html>
"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)

    return out_path
