"""Derived market analytics for the morning briefing.

Turns the raw metric dicts from fetch into the higher-level, plain-English
signals shown in the report: the market-regime label, the Goldman-style
opening note and bullets, the yield-curve read, equity drawdowns, the
cross-asset correlation note, and S&P key levels.

Every function is defensive about missing values (None) so a single failed
ticker never breaks the briefing. Functions return semantic "tone" strings
("pos" / "neg" / "caution" / "neutral") rather than colours, so the report
layer keeps full control of the visual language.

Two rules hold throughout this module:

    1. The regime label is a *weighted score* across five components (VIX
       level, 2s10s curve, equity direction, gold, dollar). No single
       component — and no single index — can decide it on its own.
    2. Narrative text is derived from values, never from the label. If a
       sentence claims a direction, the number behind it must agree. The
       ``consistency_check`` at the bottom of this file enforces that and
       reports violations to the console.
"""

import re
from statistics import mean


# --------------------------------------------------------------------------- #
# Small accessors
# --------------------------------------------------------------------------- #
def _get(rows, ticker):
    for r in rows:
        if r["ticker"] == ticker:
            return r
    return None


def _field(rows, ticker, field):
    r = _get(rows, ticker)
    return r.get(field) if r else None


def _equities(rows):
    return [r for r in rows if r["asset_class"] == "Equity"]


def _avg(values):
    vals = [v for v in values if v is not None]
    return mean(vals) if vals else None


def _sign(value):
    """Return -1 / 0 / +1 for a number, or 0 for None."""
    if value is None or value == 0:
        return 0
    return 1 if value > 0 else -1


def _pct(value, dp=2):
    return f"{value:+.{dp}f}%" if value is not None else "n/a"


def _clip(value, lo=-1.0, hi=1.0):
    return max(lo, min(hi, value))


def _dir(value, flat=None):
    """Direction of a daily % move with a dead-band: -1 / 0 / +1.

    Unlike ``_sign`` this treats anything inside +/- ``flat`` as genuinely
    flat, so a -0.03% average never reads as "lower".
    """
    if value is None:
        return 0
    band = FLAT_PCT if flat is None else flat
    if value > band:
        return 1
    if value < -band:
        return -1
    return 0


# --------------------------------------------------------------------------- #
# Regime scoring — tunables
# --------------------------------------------------------------------------- #
# A daily move smaller than this is flat, not directional.
FLAT_PCT = 0.10

# No single index may push the equity component by more than this, so one
# outlier (a -4% Nikkei while Europe rallies) cannot flip the whole regime.
EQ_WINSOR_PCT = 1.50

# Moves that saturate a component at full -1 / +1.
EQ_FULL_PCT = 0.75
GOLD_FULL_PCT = 1.50

# Relative component weights. Renormalised over whichever components have
# data, so a missing ticker rescales the rest instead of skewing the score.
REGIME_WEIGHTS = {
    "vix":      0.30,
    "curve":    0.20,
    "equities": 0.25,
    "gold":     0.15,
    "dollar":   0.10,
}

# Score thresholds. Deliberately symmetric — the old gates required VIX < 18
# for Risk-On but only VIX >= 20 for Risk-Off, which made the 18-20 band
# structurally incapable of printing Risk-On.
RISK_ON_AT = 0.30
RISK_OFF_AT = -0.30

# A component must clear this on its OWN score before the Sentiment line may
# name it as leaning risk-on or risk-off. Selecting on *contribution* alone was
# not enough: VIX 19.5 scores only -0.13, but at a 0.30 weight that cleared the
# old 0.02 contribution gate, so the briefing printed "gold +2.51% and vix 19.5
# lean risk-off" on the same page where the Volatility line called the identical
# level "a calm, risk-tolerant tape". Inside this band a component is neutral
# and is described as neutral — never as a lean in either direction.
COMPONENT_LEAN_AT = 0.20

# A 10Y move smaller than this is flat, not "rising" or "easing". ^TNX
# change_pct is the percentage change in the yield itself, so +0.03% on a 4.6%
# yield is a fraction of a basis point and must not read as rising yields.
YIELD_FLAT_PCT = 0.05

FX_PAIRS = ("EURUSD=X", "GBPUSD=X", "JPY=X")


def _equity_stats(rows):
    """Breadth and central-tendency stats for the equity complex.

    ``mean`` is the plain average shown in the report. ``winsor_mean`` caps
    each index at +/- EQ_WINSOR_PCT and is what feeds the regime score, so a
    single outsized decliner cannot dominate the classification.
    """
    eqs = [r for r in _equities(rows) if r.get("change_pct") is not None]
    if not eqs:
        return None
    changes = [r["change_pct"] for r in eqs]
    up = sum(1 for c in changes if c > FLAT_PCT)
    down = sum(1 for c in changes if c < -FLAT_PCT)
    return {
        "rows": eqs,
        "changes": changes,
        "n": len(changes),
        "mean": mean(changes),
        "winsor_mean": mean(_clip(c, -EQ_WINSOR_PCT, EQ_WINSOR_PCT)
                            for c in changes),
        "up": up,
        "down": down,
        "flat": len(changes) - up - down,
        "breadth": (up - down) / len(changes),
        "best": max(eqs, key=lambda r: r["change_pct"]),
        "worst": min(eqs, key=lambda r: r["change_pct"]),
    }


# Each component scores to [-1, +1] where POSITIVE means risk-on.
def _vix_component(vix):
    """12 or below -> +1; 18 -> neutral; 30 or above -> -1."""
    if vix is None:
        return None
    if vix <= 12:
        return 1.0
    if vix <= 18:
        return (18.0 - vix) / 6.0
    if vix <= 30:
        return -(vix - 18.0) / 12.0
    return -1.0


def _curve_component(spread_bps):
    """+100bps or steeper -> +1; flat -> 0; -50bps or deeper -> -1."""
    if spread_bps is None:
        return None
    if spread_bps >= 0:
        return _clip(spread_bps / 100.0)
    return _clip(spread_bps / 50.0)


def _equity_component(stats):
    """Winsorised average blended with breadth (share of indices higher)."""
    if stats is None:
        return None
    return _clip(0.6 * (stats["winsor_mean"] / EQ_FULL_PCT)
                 + 0.4 * stats["breadth"])


def _gold_component(gold_chg):
    """Gold bid = haven demand = risk-negative, so the sign is inverted."""
    if gold_chg is None:
        return None
    return _clip(-gold_chg / GOLD_FULL_PCT)


def _dollar_component(rows, usd_raw):
    """A firmer dollar is a mild risk headwind. None if no FX data at all."""
    if not any(_field(rows, t, "change_pct") is not None for t in FX_PAIRS):
        return None
    return _clip(-usd_raw / 3.0)


def _equity_phrase(stats):
    """Describe the equity tape from breadth AND the average together.

    When the two disagree — four indices higher but one outlier dragging the
    mean negative — neither "extend gains" nor "trade lower" is honest, so the
    phrase says mixed and names the index responsible. Returns
    (phrase, shape) where shape is 'up' / 'down' / 'flat' / 'mixed'.
    """
    if stats is None:
        return "equity direction is unclear", "flat"
    avg = stats["mean"]
    avg_dir = _dir(avg)
    breadth_dir = _dir(stats["breadth"], 0.0)
    if avg_dir != 0 and breadth_dir != 0 and avg_dir != breadth_dir:
        outlier = stats["worst"] if avg_dir < 0 else stats["best"]
        return (f"equities are mixed ({stats['up']} of {stats['n']} higher, but "
                f"{outlier['name']} {_pct(outlier['change_pct'])} pulls the "
                f"average to {_pct(avg)})"), "mixed"
    if avg_dir > 0:
        return f"equities extend gains (avg {_pct(avg)})", "up"
    if avg_dir < 0:
        return f"equities trade lower (avg {_pct(avg)})", "down"
    return f"equities trade broadly flat (avg {_pct(avg)})", "flat"


def _join_phrases(items):
    """'a', 'a and b', 'a, b and c' from component text fragments."""
    texts = [c["text"] for c in items]
    if len(texts) == 1:
        return texts[0]
    return ", ".join(texts[:-1]) + " and " + texts[-1]


# --------------------------------------------------------------------------- #
# Market regime
# --------------------------------------------------------------------------- #
def market_regime(rows):
    """Classify the environment: Risk-On / Risk-Off / Transitional / Stress.

    Five components — VIX level, the 2s10s curve, equity direction, gold and
    the dollar — are each scored to [-1, +1] (positive = risk-on) and combined
    with the weights in REGIME_WEIGHTS, renormalised over whichever components
    actually have data. The label then falls out of symmetric thresholds on
    that single score, so no individual signal (and, thanks to winsorising, no
    individual index) can carry the classification on its own.

    Returns {label, tone, detail, score, components}, where ``components`` is
    a list of {key, label, weight, score, contribution, text}. ``detail`` is
    built from the components that actually moved the score — it is never a
    canned string attached to the label.
    """
    vix = _field(rows, "^VIX", "price")
    curve = yield_curve(rows)
    stats = _equity_stats(rows)
    gold_chg = _field(rows, "GC=F", "change_pct")
    usd_raw, usd_label = _usd_read(rows)

    specs = [
        ("vix", "VIX", _vix_component(vix),
         f"VIX {vix:.1f}" if vix is not None else "VIX n/a"),
        ("curve", "2s10s", _curve_component(curve["spread_bps"]),
         f"2s10s {curve['spread_bps']:+.0f}bps"
         if curve["spread_bps"] is not None else "curve n/a"),
        ("equities", "Equities", _equity_component(stats),
         f"{stats['up']} of {stats['n']} indices higher (avg {_pct(stats['mean'])})"
         if stats else "equity data n/a"),
        ("gold", "Gold", _gold_component(gold_chg),
         f"gold {_pct(gold_chg)}" if gold_chg is not None else "gold n/a"),
        ("dollar", "Dollar", _dollar_component(rows, usd_raw),
         f"dollar {usd_label}"),
    ]
    available = [s for s in specs if s[2] is not None]

    # Need at least the volatility or the equity leg to say anything useful.
    if not available or (vix is None and stats is None):
        return {"label": "Transitional", "tone": "caution",
                "detail": "Insufficient data to classify the regime.",
                "score": None, "components": []}

    total_weight = sum(REGIME_WEIGHTS[key] for key, _, _, _ in available)
    components, score = [], 0.0
    for key, label, component_score, text in available:
        weight = REGIME_WEIGHTS[key] / total_weight
        contribution = weight * component_score
        score += contribution
        components.append({"key": key, "label": label, "weight": weight,
                           "score": component_score,
                           "contribution": contribution, "text": text})

    # Stress still overrides, but now needs either an outright VIX spike or a
    # high VIX confirmed by a broadly negative cross-asset score.
    if vix is not None and (vix >= 30 or (vix >= 25 and score <= -0.50)):
        label, tone = "Stress", "neg"
    elif score >= RISK_ON_AT:
        label, tone = "Risk-On", "pos"
    elif score <= RISK_OFF_AT:
        label, tone = "Risk-Off", "neg"
    else:
        label, tone = "Transitional", "caution"

    # Detail is assembled from the components that moved the number. Group
    # membership keys off each component's OWN score against COMPONENT_LEAN_AT,
    # not off its weighted contribution: a heavily-weighted component sitting
    # in its neutral band still has nothing directional to say, and listing it
    # as a lean contradicts the bullet that describes that same reading.
    supports = sorted((c for c in components if c["score"] >= COMPONENT_LEAN_AT),
                      key=lambda c: -c["contribution"])
    drags = sorted((c for c in components if c["score"] <= -COMPONENT_LEAN_AT),
                   key=lambda c: c["contribution"])
    neutrals = [c for c in components if abs(c["score"]) < COMPONENT_LEAN_AT]

    parts = [f"cross-asset score {score:+.2f}"]
    if supports:
        verb = "leans" if len(supports) == 1 else "lean"
        parts.append(f"{_join_phrases(supports)} {verb} risk-on")
    if drags:
        verb = "leans" if len(drags) == 1 else "lean"
        parts.append(f"{_join_phrases(drags)} {verb} risk-off")
    if not supports and not drags:
        parts.append("every component reads neutral")
    elif neutrals:
        verb = "reads" if len(neutrals) == 1 else "read"
        parts.append(f"{_join_phrases(neutrals)} {verb} neutral")
    # Uppercase only the first letter — str.capitalize() would lowercase the
    # rest of the sentence and turn "VIX 19.5" into "vix 19.5".
    detail = "; ".join(parts)
    detail = detail[0].upper() + detail[1:] + "."

    return {"label": label, "tone": tone, "detail": detail,
            "score": score, "components": components}


# --------------------------------------------------------------------------- #
# Yield curve
# --------------------------------------------------------------------------- #
def yield_curve(rows):
    """2s10s curve read. Returns {two_y, ten_y, spread, spread_bps,
    label, tone, explanation} (values may be None if data is missing)."""
    two_y = _field(rows, "2YY=F", "price")
    ten_y = _field(rows, "^TNX", "price")

    if two_y is None or ten_y is None:
        return {"two_y": two_y, "ten_y": ten_y, "spread": None,
                "spread_bps": None, "label": "Unknown", "tone": "neutral",
                "explanation": "Curve data unavailable."}

    spread = ten_y - two_y
    spread_bps = spread * 100.0

    if spread < 0:
        label, tone = "INVERTED", "neg"
        explanation = ("Short rates sit above long rates — historically a "
                       "recession warning and a sign the market expects rate cuts.")
    elif spread < 0.5:
        label, tone = "Flattening", "caution"
        explanation = ("The curve is flat — the market is pricing limited growth "
                       "and inflation premium, a cautious late-cycle signal.")
    else:
        label, tone = "Normal", "pos"
        explanation = ("Upward-sloping curve with long yields above short — "
                       "consistent with a healthy growth and inflation outlook.")

    return {"two_y": two_y, "ten_y": ten_y, "spread": spread,
            "spread_bps": spread_bps, "label": label, "tone": tone,
            "explanation": explanation}


# --------------------------------------------------------------------------- #
# Drawdowns
# --------------------------------------------------------------------------- #
def drawdowns(rows):
    """For each equity index, % below its 52-week high + a depth label.

    Returns a list of {name, ticker, price, high, pct_below, label, tone}.
    """
    out = []
    for r in _equities(rows):
        price = r.get("price")
        high = r.get("52w_high") or r.get("high_52w")
        if price is None or not high:
            continue
        pct_below = (price - high) / high * 100.0  # <= 0
        if pct_below >= -2:
            label, tone = "Near High", "pos"
        elif pct_below >= -10:
            label, tone = "Pullback", "caution"
        else:
            label, tone = "Correction", "neg"
        out.append({"name": r["name"], "ticker": r["ticker"], "price": price,
                    "high": high, "pct_below": pct_below,
                    "label": label, "tone": tone})
    return out


# --------------------------------------------------------------------------- #
# Cross-asset correlation note
# --------------------------------------------------------------------------- #
def correlation_note(rows):
    """One plain-English sentence on today's equity / bond / gold relationship."""
    stats = _equity_stats(rows)
    avg_eq = stats["mean"] if stats else None
    yield_chg = _field(rows, "^TNX", "change_pct")
    gold_chg = _field(rows, "GC=F", "change_pct")

    # Dead-banded so a flat tape is not described as a rally or a selloff.
    eq_dir = _dir(avg_eq)
    bond_dir = -_dir(yield_chg, YIELD_FLAT_PCT)  # yields up => bond prices down
    gold_dir = _dir(gold_chg)

    # Breadth has to agree with the average before either direction is claimed.
    # One outlier can carry the mean on its own — a +4.03% Nikkei against four
    # small decliners lifts the average to +0.64% while 4 of 5 indices are
    # lower — and "equities climb ... a classic reflationary, risk-on
    # configuration" then contradicts the four. This is the same guard
    # _equity_phrase applies to the briefing paragraph.
    breadth_dir = _dir(stats["breadth"], 0.0) if stats else 0
    eq_mixed = eq_dir != 0 and breadth_dir != 0 and eq_dir != breadth_dir
    if eq_mixed:
        eq_dir = 0

    eq_val = f"average index move {_pct(avg_eq)}" if avg_eq is not None else "equities n/a"
    y_val = f"10Y yield {_pct(yield_chg)}" if yield_chg is not None else "10Y n/a"

    if eq_mixed and bond_dir > 0 and stats["breadth"] > 0:
        # Most indices rose but one decliner drags the mean negative, and bonds
        # are bid: a rates-led move, not a broad flight to quality. Named
        # explicitly because the interpretation is worth more than "mixed".
        base = (f"Bonds catch a bid while equity breadth stays positive "
                f"({stats['up']} of {stats['n']} indices higher, {eq_val}, "
                f"{y_val}) — a rates-led move rather than a broad flight to quality.")
    elif eq_mixed:
        bond_word = ("bonds catch a bid" if bond_dir > 0
                     else "bonds sell off" if bond_dir < 0
                     else "bonds are little changed")
        base = (f"Equities are mixed ({stats['up']} of {stats['n']} indices "
                f"higher, {eq_val}) while {bond_word} ({y_val}) — dispersion "
                f"inside the equity complex rather than one cross-asset theme.")
    elif eq_dir == 0 and bond_dir == 0:
        base = (f"Equities and bonds are little changed ({eq_val}, {y_val}), "
                f"offering few cross-asset signals today.")
    elif eq_dir > 0 and bond_dir > 0:
        base = (f"Equities and bonds are rallying together as yields ease "
                f"({eq_val}, {y_val}) — a liquidity-friendly backdrop supportive of risk.")
    elif eq_dir > 0 and bond_dir < 0:
        base = (f"Equities climb while bonds sell off ({eq_val}, {y_val}) — a classic "
                f"reflationary, risk-on configuration.")
    elif eq_dir < 0 and bond_dir > 0:
        # Breadth is guaranteed non-positive here: a negative average with
        # positive breadth is eq_mixed and was handled above.
        base = (f"Equities fall as bonds catch a bid ({eq_val}, {y_val}) — textbook "
                f"risk-off, flight-to-quality positioning.")
    elif eq_dir < 0 and bond_dir < 0:
        base = (f"Both equities and bonds are under pressure ({eq_val}, {y_val}) — "
                f"rising yields into a stock selloff point to a rates-driven, "
                f"correlation-up regime.")
    else:
        base = (f"Cross-asset moves are mixed ({eq_val}, {y_val}), with no single "
                f"dominant theme.")

    if gold_dir > 0:
        base += f" Gold is firmer ({_pct(gold_chg)}), consistent with hedging demand."
    elif gold_dir < 0:
        base += f" Gold is softer ({_pct(gold_chg)}), suggesting limited safe-haven demand."
    elif gold_chg is not None:
        base += f" Gold is unchanged ({_pct(gold_chg)}), with no haven signal either way."
    return base


# --------------------------------------------------------------------------- #
# S&P 500 key levels
# --------------------------------------------------------------------------- #
def key_levels(rows):
    """S&P 500 price vs its 52-week high/low, with within-3% flags.

    Returns {price, high, low, pct_from_high, pct_from_low,
    near_high, near_low, message} or None if the S&P is missing.
    """
    r = _get(rows, "^GSPC")
    if not r:
        return None
    price = r.get("price")
    high = r.get("52w_high") or r.get("high_52w")
    low = r.get("52w_low") or r.get("low_52w")
    if price is None or not high or not low:
        return None

    pct_from_high = (price - high) / high * 100.0
    pct_from_low = (price - low) / low * 100.0
    near_high = abs(pct_from_high) <= 3
    near_low = abs(pct_from_low) <= 3

    if near_high:
        message = "Trading within 3% of the 52-week high — watch for a breakout or rejection."
    elif near_low:
        message = "Trading within 3% of the 52-week low — a key support zone under test."
    else:
        message = "Mid-range between its 52-week high and low."

    return {"price": price, "high": high, "low": low,
            "pct_from_high": pct_from_high, "pct_from_low": pct_from_low,
            "near_high": near_high, "near_low": near_low, "message": message}


# --------------------------------------------------------------------------- #
# USD strength helper
# --------------------------------------------------------------------------- #
def _usd_read(rows):
    """Crude USD-direction score and label from the three FX pairs.

    EUR/USD and GBP/USD down => USD up; USD/JPY up => USD up.
    Returns (score, label) where label is 'broadly firmer' / 'broadly softer' / 'mixed'.
    """
    eur = _field(rows, "EURUSD=X", "change_pct")
    gbp = _field(rows, "GBPUSD=X", "change_pct")
    jpy = _field(rows, "JPY=X", "change_pct")
    # Dead-band of 0.05% so FX noise does not count as a directional move.
    score = -_dir(eur, 0.05) - _dir(gbp, 0.05) + _dir(jpy, 0.05)
    if score >= 2:
        return score, "broadly firmer"
    if score <= -2:
        return score, "broadly softer"
    return score, "mixed"


# --------------------------------------------------------------------------- #
# Goldman-style morning briefing (paragraph + bullets)
# --------------------------------------------------------------------------- #
def morning_briefing(rows):
    """Build the analyst-style opening note from the actual data.

    Returns {paragraph, bullets} where bullets is a list of
    {label, text, tone}. Everything is derived from the day's numbers.
    """
    regime = market_regime(rows)
    curve = yield_curve(rows)
    vix = _field(rows, "^VIX", "price")
    stats = _equity_stats(rows)
    avg_eq = stats["mean"] if stats else None
    best = stats["best"] if stats else None
    worst = stats["worst"] if stats else None

    gold_chg = _field(rows, "GC=F", "change_pct")
    oil_chg = _field(rows, "CL=F", "change_pct")
    sp = _field(rows, "^GSPC", "price")
    kl = key_levels(rows)
    usd_score, usd_label = _usd_read(rows)
    score = regime.get("score")

    # --- Opening paragraph (3 sentences) ----------------------------------- #
    # The lead comes from the weighted score (itself derived from the day's
    # numbers), NOT from the regime label — and it carries the magnitude, so a
    # -0.03% tape can never read as "on the defensive".
    if regime["label"] == "Stress":
        lead = "Markets are under visible stress"
    elif score is None:
        lead = "Markets open with an incomplete data picture"
    elif score >= RISK_ON_AT:
        lead = "Risk appetite is firm this morning"
    elif score >= 0.10:
        lead = "Markets open with a mild constructive tilt"
    elif score > -0.10:
        lead = "Markets open in a holding pattern"
    elif score > RISK_OFF_AT:
        lead = "Markets open with a mild defensive tilt"
    else:
        lead = "Markets open on the defensive"

    eq_phrase, eq_shape = _equity_phrase(stats)

    if vix is None:
        vol_phrase = "volatility data is thin"
    else:
        vol_word = ("subdued" if vix < 20 else
                    "above-average" if vix < 30 else "elevated")
        vol_phrase = f"a {vol_word} VIX at {vix:.1f}"
    s1 = f"{lead} as {eq_phrase} and {vol_phrase} frames the tape."

    # Sentence 2 states price and the 52-week high as separate, labelled
    # numbers so the two can never be read as the same level.
    if kl:
        s2 = (f"The S&P 500 trades at {kl['price']:,.0f}, "
              f"{abs(kl['pct_from_high']):.1f}% below its 52-week closing high "
              f"of {kl['high']:,.0f}")
    elif sp is not None:
        s2 = f"The S&P 500 trades at {sp:,.0f}"
    else:
        s2 = "S&P 500 pricing is unavailable"
    if stats and eq_shape == "mixed":
        # Breadth was already quoted in eq_phrase; give the dispersion instead.
        s2 += (f", with index moves spanning {_pct(stats['worst']['change_pct'])} "
               f"({stats['worst']['name']}) to {_pct(stats['best']['change_pct'])} "
               f"({stats['best']['name']}).")
    elif stats:
        s2 += (f", with {stats['up']} of {stats['n']} tracked indices higher "
               f"on the day.")
    else:
        s2 += "."

    curve_phrase = (f"the 2s10s curve reads {curve['label'].lower()} at "
                    f"{curve['spread_bps']:+.0f}bps"
                    if curve["spread_bps"] is not None
                    else "curve data is unavailable")
    if score is None:
        tail = "leaving the cross-asset picture unresolved"
    else:
        picture = ("supportive" if score >= RISK_ON_AT else
                   "risk-negative" if score <= RISK_OFF_AT else "mixed")
        tail = f"leaving the cross-asset picture {picture} (score {score:+.2f})"
    s3 = f"The dollar is {usd_label} and {curve_phrase}, {tail}."
    paragraph = " ".join([s1, s2, s3])

    # --- Bullets ----------------------------------------------------------- #
    bullets = []

    # 1. Sentiment
    bullets.append({"label": "Sentiment",
                    "text": f"{regime['label']} — {regime['detail']}",
                    "tone": regime["tone"]})

    # 2. Equities — breadth stated alongside the average so a single outlier
    #    index is visible rather than silently driving the read.
    if best and worst:
        if best["ticker"] == worst["ticker"]:
            eq_text = f"{best['name']} {_pct(best['change_pct'])} on the day."
        else:
            eq_text = (f"{best['name']} leads ({_pct(best['change_pct'])}); "
                       f"{worst['name']} lags ({_pct(worst['change_pct'])}); "
                       f"average index move {_pct(avg_eq)} with "
                       f"{stats['up']} of {stats['n']} higher.")
        # A tape whose breadth and average disagree is neutral, not directional.
        eq_dir = 0 if eq_shape == "mixed" else _dir(avg_eq)
        eq_tone = "pos" if eq_dir > 0 else "neg" if eq_dir < 0 else "neutral"
    else:
        eq_text, eq_tone = "Equity data unavailable.", "neutral"
    bullets.append({"label": "Equities", "text": eq_text, "tone": eq_tone})

    # 3. VIX regime
    if vix is None:
        vix_text, vix_tone = "VIX data unavailable.", "neutral"
    elif vix >= 30:
        vix_text, vix_tone = (f"VIX {vix:.1f} — elevated volatility; expect outsized swings and hedge demand.", "neg")
    elif vix >= 20:
        vix_text, vix_tone = (f"VIX {vix:.1f} — above-average volatility; markets are nervous but not panicked.", "caution")
    else:
        vix_text, vix_tone = (f"VIX {vix:.1f} — subdued volatility signals a calm, risk-tolerant tape.", "pos")
    bullets.append({"label": "Volatility", "text": vix_text, "tone": vix_tone})

    # 4. FX / USD — use the same label as the opening paragraph for consistency.
    if usd_label == "broadly firmer":
        fx_text, fx_tone = ("Dollar broadly firmer — a mild headwind for commodities and EM risk.", "caution")
    elif usd_label == "broadly softer":
        fx_text, fx_tone = ("Dollar broadly softer — supportive of commodities and risk assets.", "pos")
    else:
        fx_text, fx_tone = ("Dollar mixed against majors — no clear FX-driven theme.", "neutral")
    bullets.append({"label": "FX", "text": fx_text, "tone": fx_tone})

    # 5. Rates / curve — the equity read comes from the 10Y's actual move, with
    #    a dead-band, so "rising yields are a headwind" can only print on a day
    #    the 10Y is genuinely higher. The level and the change are both quoted
    #    so the claim is checkable against the number beside it.
    if curve["spread"] is None:
        rates_text, rates_tone = "Curve data unavailable.", "neutral"
    else:
        yield_chg = _field(rows, "^TNX", "change_pct")
        ten_y = curve["ten_y"]
        level = f" at {ten_y:.2f}%" if ten_y is not None else ""
        if yield_chg is None:
            rate_bias = f"the 10Y{level} has no comparable prior close"
        elif _dir(yield_chg, YIELD_FLAT_PCT) > 0:
            rate_bias = (f"the 10Y is higher{level} ({_pct(yield_chg)}) — rising "
                         f"yields are a headwind for equities")
        elif _dir(yield_chg, YIELD_FLAT_PCT) < 0:
            rate_bias = (f"the 10Y is lower{level} ({_pct(yield_chg)}) — easing "
                         f"yields offer relief to equities")
        else:
            rate_bias = (f"the 10Y is little changed{level} ({_pct(yield_chg)}) — "
                         f"no rates impulse for equities either way")
        rates_text = (f"2s10s at {curve['spread_bps']:+.0f}bps ({curve['label']}); "
                      f"{rate_bias}.")
        rates_tone = curve["tone"]
    bullets.append({"label": "Rates", "text": rates_text, "tone": rates_tone})

    # 6. Commodities — the haven clause reads off gold's own sign, so it can
    #    never claim a bid while gold is negative.
    gold_dir = _dir(gold_chg)
    oil_dir = _dir(oil_chg)
    oil_word = "firmer" if oil_dir > 0 else "softer" if oil_dir < 0 else "steady"
    gold_word = "bid" if gold_dir > 0 else "offered" if gold_dir < 0 else "flat"
    haven = ("haven demand evident" if gold_dir > 0
             else "limited haven demand" if gold_dir < 0
             else "no haven signal either way")
    comm_text = (f"Crude {oil_word} ({_pct(oil_chg)}); gold {gold_word} "
                 f"({_pct(gold_chg)}) — {haven}.")
    comm_tone = "caution" if gold_dir > 0 and _dir(avg_eq) < 0 else "neutral"
    bullets.append({"label": "Commodities", "text": comm_text, "tone": comm_tone})

    # 7. Watch today (actionable)
    if curve["spread"] is not None and curve["spread"] < 0:
        watch = "Watch the inverted curve — any further inversion reinforces recession pricing."
        watch_tone = "neg"
    elif kl and kl["near_high"]:
        # Name both levels explicitly — the high is the target, not the price.
        watch = (f"Watch the S&P 500 52-week high at {kl['high']:,.0f}, "
                 f"{abs(kl['pct_from_high']):.1f}% above spot at {kl['price']:,.0f}; "
                 f"a clean break opens upside.")
        watch_tone = "pos"
    elif vix is not None and vix >= 20:
        watch = "Watch volatility — a VIX move above 25 would confirm a risk-off shift."
        watch_tone = "caution"
    else:
        watch = "Watch the dollar and 10Y yield — they remain the key swing factors for risk today."
        watch_tone = "neutral"
    bullets.append({"label": "Watch today", "text": watch, "tone": watch_tone})

    return {"paragraph": paragraph, "bullets": bullets}


# --------------------------------------------------------------------------- #
# GS analyst notebook — short handwritten-style jottings from the day's data
# --------------------------------------------------------------------------- #
def analyst_notebook(rows):
    """Return 5-6 terse, handwritten-style notes derived from the live data.

    Each note is a short string in the voice of an analyst scribbling in a
    notebook before the open. Everything keys off real values that day.
    """
    notes = []
    vix = _field(rows, "^VIX", "price")
    curve = yield_curve(rows)
    kl = key_levels(rows)
    gold_chg = _field(rows, "GC=F", "change_pct")
    avg_eq = _avg([r.get("change_pct") for r in _equities(rows)])
    btc = _get(rows, "BTC-USD")
    dxy = _get(rows, "DX-Y.NYB")

    # 1. S&P key levels — spot and the level are always named separately.
    if kl and kl["near_high"]:
        notes.append(f"S&P {kl['price']:,.0f}, {abs(kl['pct_from_high']):.1f}% under the "
                     f"52W high at {kl['high']:,.0f} — watch for rejection or breakout")
    elif kl and kl["near_low"]:
        notes.append(f"S&P {kl['price']:,.0f}, {kl['pct_from_low']:+.1f}% off the "
                     f"52W low at {kl['low']:,.0f} — support zone, watch for a bounce or breakdown")
    elif kl:
        notes.append(f"S&P mid-range at {kl['price']:,.0f} — no level urgency, let it come to me")

    # 2. VIX read
    if vix is not None:
        if vix < 15:
            notes.append(f"VIX {vix:.0f} = complacency? Check if vol is being suppressed")
        elif vix < 20:
            notes.append(f"VIX {vix:.0f} — calm but not euphoric, hedges still cheap")
        elif vix < 30:
            notes.append(f"VIX {vix:.0f} — nerves building, size down and respect stops")
        else:
            notes.append(f"VIX {vix:.0f} = stress! Don't catch falling knives, wait for capitulation")

    # 3. Curve
    if curve["spread_bps"] is not None:
        if curve["spread"] < 0:
            notes.append(f"2s10s {curve['spread_bps']:+.0f}bps — INVERTED, recession clock ticking")
        elif curve["spread"] < 0.5:
            notes.append(f"2s10s {curve['spread_bps']:+.0f}bps — flat, late-cycle, stay nimble")
        else:
            notes.append(f"2s10s {curve['spread_bps']:+.0f}bps — curve normal, no recession signal")

    # 4. Gold vs equities cross-check (dead-banded on both legs)
    gold_dir, eq_dir = _dir(gold_chg), _dir(avg_eq)
    if gold_dir > 0 and eq_dir > 0:
        notes.append(f"Gold bid ({_pct(gold_chg)}) alongside equities = unusual, "
                     f"monitor for a regime change")
    elif gold_dir > 0 and eq_dir < 0:
        notes.append(f"Gold bid ({_pct(gold_chg)}) as stocks slip ({_pct(avg_eq)}) — "
                     f"classic haven flows, risk-off creeping in")
    elif gold_dir > 0:
        notes.append(f"Gold bid ({_pct(gold_chg)}) into a flat tape — hedges being "
                     f"added quietly, worth watching")
    elif gold_dir < 0:
        notes.append(f"Gold offered ({_pct(gold_chg)}) — haven demand light, "
                     f"risk appetite intact for now")

    # 5. Stretched equity (best YTD performer)
    rated_ytd = [e for e in _equities(rows) if e.get("ytd_change_pct") is not None]
    if rated_ytd:
        top = max(rated_ytd, key=lambda e: e["ytd_change_pct"])
        if top["ytd_change_pct"] >= 15:
            notes.append(f"{top['name']} {top['ytd_change_pct']:+.0f}% YTD — stretched, mean reversion risk")
        elif top["ytd_change_pct"] <= -10:
            notes.append(f"{top['name']} {top['ytd_change_pct']:+.0f}% YTD — beaten up, watch for value bid")

    # 6. Bitcoin / Dollar macro colour (rotates in if room)
    if btc and btc.get("price") is not None:
        chg = btc.get("change_pct")
        if chg is not None and abs(chg) >= 3:
            direction = "ripping" if chg > 0 else "dumping"
            notes.append(f"BTC {direction} ({chg:+.1f}%) — risk-sentiment tell, desks are watching")
        else:
            notes.append(f"BTC {btc['price']:,.0f} — quiet, no crypto-led risk impulse today")
    if dxy and dxy.get("change_pct") is not None and abs(dxy["change_pct"]) >= 0.4:
        direction = "firmer" if dxy["change_pct"] > 0 else "softer"
        notes.append(f"DXY {direction} ({dxy['change_pct']:+.2f}%) — macro crosswind for commodities & EM")

    # Keep it scannable: 5-6 notes max.
    return notes[:6]


# --------------------------------------------------------------------------- #
# Analyst decision framework — how each signal drives a real decision
# --------------------------------------------------------------------------- #
def decision_framework(rows):
    """Return 6-7 {icon, title, text} dicts explaining how today's signals
    would shape an analyst's actual positioning decisions."""
    points = []
    vix = _field(rows, "^VIX", "price")
    curve = yield_curve(rows)
    kl = key_levels(rows)
    gold_chg = _field(rows, "GC=F", "change_pct")
    avg_eq = _avg([r.get("change_pct") for r in _equities(rows)])
    regime = market_regime(rows)
    usd_score, usd_label = _usd_read(rows)
    btc = _get(rows, "BTC-USD")

    # 1. Volatility -> options vs stock
    if vix is not None:
        if vix < 18:
            points.append({"icon": "📉", "title": "Cheap optionality",
                           "text": f"VIX at {vix:.0f} tells me options are cheap — if I had conviction on a "
                                   f"trade I'd buy calls rather than stock for better risk/reward."})
        elif vix < 30:
            points.append({"icon": "📈", "title": "Vol picking up",
                           "text": f"VIX at {vix:.0f} means premium is no longer a giveaway — I lean toward "
                                   f"spreads over outright options and tighten position sizing."})
        else:
            points.append({"icon": "⚠️", "title": "Stressed vol",
                           "text": f"VIX at {vix:.0f} is expensive and jumpy — I sell premium only in defined-risk "
                                   f"structures and hold extra cash for dislocation."})

    # 2. Curve -> equity stance
    if curve["spread_bps"] is not None:
        if curve["spread"] < 0:
            points.append({"icon": "🪤", "title": "Inverted curve",
                           "text": f"Curve inverted at {curve['spread_bps']:+.0f}bps — recession risk is live, so "
                                   f"I trim cyclicals and add duration and quality defensives."})
        elif curve["spread"] < 0.5:
            points.append({"icon": "🧭", "title": "Flat curve",
                           "text": f"Curve flat at {curve['spread_bps']:+.0f}bps — late-cycle, so I stay invested "
                                   f"but rotate toward quality and keep some dry powder."})
        else:
            points.append({"icon": "🟢", "title": "Curve normal",
                           "text": f"Curve normal at {curve['spread_bps']:+.0f}bps means no imminent recession "
                                   f"priced in — I stay long equities with no urgency to reduce risk."})

    # 3. Gold / equities -> stop management
    gold_dir, eq_dir = _dir(gold_chg), _dir(avg_eq)
    if gold_dir > 0 and eq_dir >= 0:
        points.append({"icon": "🥇", "title": "Gold + stocks both up",
                       "text": f"Gold rising ({_pct(gold_chg)}) with equities ({_pct(avg_eq)}) is a mixed "
                               f"signal — I trim nothing but I raise my stop-losses slightly as a precaution."})
    elif gold_dir > 0:
        points.append({"icon": "🛡️", "title": "Haven bid",
                       "text": f"Gold bid ({_pct(gold_chg)}) while equities slip ({_pct(avg_eq)}) is textbook "
                               f"risk-off — I let winners run into the haven but add a small "
                               f"gold/duration hedge to the book."})
    elif gold_dir < 0:
        points.append({"icon": "🥇", "title": "Gold offered",
                       "text": f"Gold soft ({_pct(gold_chg)}) tells me haven demand is light — I'm comfortable "
                               f"carrying full equity risk and don't need a defensive overlay today."})
    else:
        points.append({"icon": "🥇", "title": "Gold flat",
                       "text": "Gold is unchanged, so there is no haven signal to trade off — I leave the "
                               "defensive overlay exactly where it was."})

    # 4. Regime -> overall posture
    posture = {
        "Risk-On": "I run net-long and let beta work, focusing energy on the best ideas rather than hedges.",
        "Risk-Off": "I cut gross exposure, hedge the index, and only keep my highest-conviction longs.",
        "Stress": "I move to capital preservation — raise cash, hedge aggressively, and wait for the dust to settle.",
        "Transitional": "Signals are mixed, so I keep balanced exposure and avoid making big directional bets.",
    }.get(regime["label"], "I keep balanced exposure until the cross-asset picture clarifies.")
    score = regime.get("score")
    regime_title = (f"Regime: {regime['label']} ({score:+.2f})" if score is not None
                    else f"Regime: {regime['label']}")
    points.append({"icon": "🎯", "title": regime_title,
                   "text": f"{posture} {regime['detail']}"})

    # 5. S&P key levels -> entries
    if kl and kl["near_high"]:
        points.append({"icon": "🚀", "title": "Pressing the highs",
                       "text": f"S&P at {kl['price']:,.0f} is within 3% of its {kl['high']:,.0f} 52-week high "
                               f"({kl['pct_from_high']:+.1f}%) — I wait for a clean break to add, rather than "
                               f"chase into resistance and risk a rejection."})
    elif kl and kl["near_low"]:
        points.append({"icon": "🩹", "title": "Testing the lows",
                       "text": f"S&P near its {kl['low']:,.0f} low — I scale into longs in tranches so I'm not "
                               f"fully committed if support gives way."})
    elif kl:
        points.append({"icon": "🎚️", "title": "Mid-range",
                       "text": "S&P is mid-range with no level urgency — I let the market come to defined "
                               "support or resistance before committing fresh risk."})

    # 6. Dollar -> FX / commodity tilt
    if usd_label == "broadly firmer":
        points.append({"icon": "💵", "title": "Dollar firmer",
                       "text": "A firmer dollar is a headwind for commodities and EM — I trim those exposures "
                               "and favour domestic, dollar-earning names."})
    elif usd_label == "broadly softer":
        points.append({"icon": "💵", "title": "Dollar softer",
                       "text": "A softer dollar is a tailwind for commodities and EM — I tilt toward materials, "
                               "energy and emerging-market risk."})
    else:
        points.append({"icon": "💵", "title": "Dollar mixed",
                       "text": "No clear dollar trend, so FX isn't dictating the book today — I keep commodity "
                               "and EM exposure at neutral weight."})

    # 7. Crypto sentiment (optional colour)
    if btc and btc.get("change_pct") is not None:
        chg = btc["change_pct"]
        if abs(chg) >= 3:
            tilt = ("a risk-on tell that supports staying long the risk complex" if chg > 0
                    else "a warning that risk sentiment is fragile, so I keep hedges on")
            points.append({"icon": "₿", "title": "Crypto signal",
                           "text": f"Bitcoin {chg:+.1f}% is {tilt} — I treat it as a sentiment gauge, not a "
                                   f"position, but it informs how aggressive I am elsewhere."})

    return points[:7]


# --------------------------------------------------------------------------- #
# Narrative / data consistency check
# --------------------------------------------------------------------------- #
# Guards against the class of bug where prose is selected by a label rather
# than by the number it describes — e.g. "defensive bid in gold" printed on a
# day gold closed -1.46%. Every directional phrase the briefing can emit is
# paired here with the metric that must agree with it.

def _check_metrics(rows):
    """The raw values every narrative claim is validated against."""
    stats = _equity_stats(rows)
    curve = yield_curve(rows)
    usd_raw, _ = _usd_read(rows)
    return {
        "gold":      {"value": _field(rows, "GC=F", "change_pct"), "unit": "pct",
                      "name": "gold"},
        "crude":     {"value": _field(rows, "CL=F", "change_pct"), "unit": "pct",
                      "name": "crude"},
        "equities":  {"value": stats["mean"] if stats else None, "unit": "pct",
                      "name": "average index move"},
        "yield_10y": {"value": _field(rows, "^TNX", "change_pct"), "unit": "pct",
                      "name": "10Y yield change"},
        "vix":       {"value": _field(rows, "^VIX", "price"), "unit": "level",
                      "name": "VIX"},
        "curve_bps": {"value": curve["spread_bps"], "unit": "bps",
                      "name": "2s10s spread"},
        "usd":       {"value": float(usd_raw), "unit": "score",
                      "name": "USD direction score"},
        "breadth":   {"value": stats["breadth"] if stats else None,
                      "unit": "ratio", "name": "equity breadth"},
        # 1.0 when breadth and the average point the same way (or either is
        # flat), 0.0 when one index is dragging the mean against the breadth.
        "eq_agreement": {
            "value": (None if stats is None else
                      float(_dir(stats["mean"]) == 0
                            or _dir(stats["breadth"], 0.0) == 0
                            or _dir(stats["mean"]) == _dir(stats["breadth"], 0.0))),
            "unit": "flag", "name": "breadth/average agreement"},
    }


# (regex, metric key, predicate the metric must satisfy, plain-English rule)
_NARRATIVE_RULES = [
    (r"haven demand evident|defensive bid|gold (?:is )?bid|haven bid"
     r"|gold (?:is )?firmer|gold rising|hedging demand",
     "gold", lambda v: v > 0, "gold must be positive to claim a haven/hedging bid"),
    (r"limited haven demand|limited safe-haven demand|gold (?:is )?offered"
     r"|gold (?:is )?soft|haven demand is light",
     "gold", lambda v: v < 0, "gold must be negative to call haven demand light"),
    (r"no haven signal|gold is unchanged|gold flat",
     "gold", lambda v: abs(v) <= FLAT_PCT, "gold must be inside the flat band"),

    (r"crude firmer", "crude", lambda v: v > FLAT_PCT, "crude must be up"),
    (r"crude softer", "crude", lambda v: v < -FLAT_PCT, "crude must be down"),

    (r"equities extend gains|equities climb|equities (?:and bonds )?are rallying"
     r"|breadth .{0,20}constructive",
     "equities", lambda v: v > FLAT_PCT,
     "the average index move must be meaningfully positive"),
    (r"equities trade lower|equities soft|equities slip|equities fall"
     r"|equities are under pressure|stocks slip",
     "equities", lambda v: v < -FLAT_PCT,
     "the average index move must be meaningfully negative"),
    # Scoped to the equity subject: "little changed" on its own also describes
    # the 10Y, which has its own rule below and its own dead-band.
    (r"equities trade broadly flat|equities and bonds are little changed"
     r"|flat tape",
     "equities", lambda v: abs(v) <= FLAT_PCT,
     "the average index move must be inside the flat band"),

    # A directional equity claim also needs breadth to agree with the average,
    # otherwise one outlier index is speaking for the whole complex.
    (r"equities extend gains|equities trade lower|equities climb|equities fall"
     r"|equities (?:and bonds )?are rallying",
     "eq_agreement", lambda v: v == 1.0,
     "breadth and the average must agree before calling a direction"),
    (r"equities are mixed", "eq_agreement", lambda v: v == 0.0,
     "breadth and the average must actually disagree to call the tape mixed"),
    (r"textbook risk-off|flight-to-quality|flight to quality",
     "breadth", lambda v: v <= 0,
     "equity breadth must not be positive to call it a broad flight to quality"),

    # Yield direction. Dead-banded on both sides so a fractional-basis-point
    # drift cannot licence "rising yields are a headwind for equities" — the
    # templated phrase that used to print off a bare sign test.
    (r"easing yields|yields ease|yields are easing|falling yields"
     r"|the 10Y is lower|10Y lower|bonds catch a bid",
     "yield_10y", lambda v: v < -YIELD_FLAT_PCT,
     "the 10Y yield must be meaningfully lower"),
    (r"rising yields|yields higher|yields are rising"
     r"|the 10Y is higher|10Y higher|bonds sell off",
     "yield_10y", lambda v: v > YIELD_FLAT_PCT,
     "the 10Y yield must be meaningfully higher"),
    (r"the 10Y is little changed|yields little changed|yields are flat"
     r"|no rates impulse|bonds are little changed",
     "yield_10y", lambda v: abs(v) <= YIELD_FLAT_PCT,
     "the 10Y yield must be inside the flat band"),

    (r"subdued volatility|calm, risk-tolerant|hedges still cheap|options are cheap",
     "vix", lambda v: v < 20, "VIX must be below 20"),
    (r"above-average volatility|nerves building|markets are nervous",
     "vix", lambda v: 20 <= v < 30, "VIX must be between 20 and 30"),
    (r"elevated volatility|acute market stress|stress!",
     "vix", lambda v: v >= 30, "VIX must be at or above 30"),

    (r"inverted|inversion", "curve_bps", lambda v: v < 0,
     "the 2s10s spread must be negative"),
    (r"curve normal|curve reads normal|no recession signal|no imminent recession",
     "curve_bps", lambda v: v >= 50, "the 2s10s spread must be 50bps or steeper"),
    (r"curve flat|curve is flat|reads flattening", "curve_bps",
     lambda v: 0 <= v < 50, "the 2s10s spread must be between 0 and 50bps"),

    (r"dollar (?:is )?(?:broadly )?firmer|firmer dollar",
     "usd", lambda v: v >= 2, "the USD score must show broad dollar strength"),
    (r"dollar (?:is )?(?:broadly )?softer|softer dollar",
     "usd", lambda v: v <= -2, "the USD score must show broad dollar weakness"),
]


# Prose legitimately negates a phrase ("rather than a broad flight to quality",
# "no haven demand"). A negated mention makes no directional claim, so the rule
# must not fire on it.
# Word-boundary matched so "cannot" / "another" do not read as negations.
_NEGATION_RE = re.compile(
    r"\b(?:rather than|instead of|not|no|never|without|isn't|aren't|"
    r"far from|nothing)\b", re.IGNORECASE)

# How far back to look for a negation governing the match.
_NEGATION_WINDOW = 30


def _is_negated(text, start):
    """True if a negation governs the phrase matched at *start*.

    Only the clause containing the match is considered — a negation on the far
    side of a comma, dash or semicolon belongs to a different claim, so
    "Gold is offered; haven demand evident" is still correctly flagged.
    """
    window = text[max(0, start - _NEGATION_WINDOW):start]
    for boundary in (";", "—", " - ", ","):
        if boundary in window:
            window = window.rsplit(boundary, 1)[1]
    return bool(_NEGATION_RE.search(window))


def _format_metric(metric):
    value, unit = metric["value"], metric["unit"]
    if value is None:
        return "n/a"
    if unit == "pct":
        return f"{value:+.2f}%"
    if unit == "bps":
        return f"{value:+.0f}bps"
    if unit == "level":
        return f"{value:.2f}"
    if unit == "ratio":
        return f"{value:+.2f}"
    if unit == "flag":
        return "agree" if value == 1.0 else "disagree"
    return f"{value:+.0f}"


# --------------------------------------------------------------------------- #
# Structural check: regime-component lean groups
# --------------------------------------------------------------------------- #
# The regex rules above pair a *phrase* with a *metric*. Lean groups need a
# different shape of check: the claim is "this named component leans this way",
# and the number it must agree with is that component's own regime score. So we
# find every "... lean(s) risk-on / risk-off" and "... read(s) neutral" clause,
# see which component readings are named inside it, and hold each one to
# COMPONENT_LEAN_AT.
_LEAN_CLAUSE_RE = re.compile(
    r"\blean(?:s)?\s+risk-(on|off)\b|\bread(?:s)?\s+neutral\b", re.IGNORECASE)

# Clause separators. A component named on the far side of one belongs to a
# different lean group, so each clause is examined on its own. A full stop only
# ends a clause when whitespace or the end of the string follows it — component
# readings are full of decimal points ("VIX 19.5", "gold +2.51%") and splitting
# on those would hide the very claims this check exists to catch.
_CLAUSE_SPLIT_RE = re.compile(r";|\.(?=\s|$)")


def _lean_claim_issues(components, sections):
    """Flag lean-group memberships that the component's own score contradicts.

    Two failure modes, both of which shipped in the briefing:

        * a component inside the neutral dead-band listed as leaning risk-on or
          risk-off (VIX 19.5, score -0.13, printed as a risk-off driver), and
        * a component listed on the wrong side of its own sign.

    The mirror case is checked too: anything described as reading neutral must
    actually be inside the band.

    Returns (issues, claims_checked).
    """
    if not components:
        return [], 0

    issues, claims = [], 0
    for source, text in sections:
        for clause in _CLAUSE_SPLIT_RE.split(text):
            match = _LEAN_CLAUSE_RE.search(clause)
            if not match:
                continue
            side = (match.group(1) or "neutral").lower()
            # Only the text *before* the verb names the group's members.
            listed = clause[:match.start()]
            for component in components:
                if component["text"] not in listed:
                    continue
                claims += 1
                score = component["score"]
                leaning = abs(score) >= COMPONENT_LEAN_AT
                if side == "neutral":
                    ok, rule = (not leaning,
                                f"a component may only read neutral while its "
                                f"score is inside +/-{COMPONENT_LEAN_AT:.2f}")
                elif not leaning:
                    ok, rule = (False,
                                f"a component inside the neutral dead-band "
                                f"(+/-{COMPONENT_LEAN_AT:.2f}) must not be listed "
                                f"as leaning risk-{side}")
                else:
                    ok, rule = ((score > 0) == (side == "on"),
                                f"the component score must be "
                                f"{'positive' if side == 'on' else 'negative'} "
                                f"to lean risk-{side}")
                if ok:
                    continue
                issues.append({
                    "source": source,
                    "phrase": f"{component['text']} {match.group(0)}",
                    "metric": f"{component['label']} component score",
                    "value": f"{score:+.2f}",
                    "rule": rule,
                    "text": text,
                })
    return issues, claims


def narrative_sections(rows):
    """Every generated sentence in the briefing, tagged with its source."""
    brief = morning_briefing(rows)
    sections = [("briefing paragraph", brief["paragraph"])]
    for bullet in brief["bullets"]:
        sections.append((f"bullet · {bullet['label']}", bullet["text"]))
    sections.append(("correlation note", correlation_note(rows)))
    for i, note in enumerate(analyst_notebook(rows), 1):
        sections.append((f"notebook {i}", note))
    for point in decision_framework(rows):
        sections.append((f"framework · {point['title']}", point["text"]))
    return sections


def consistency_check(rows, sections=None):
    """Cross-check every directional claim in the narrative against its metric.

    Two passes run over every sentence:

        1. the phrase/metric rules in ``_NARRATIVE_RULES``, and
        2. :func:`_lean_claim_issues`, which holds each regime component named
           in a lean group to its own score.

    Returns {issues, claims_checked, sections_checked}. Each issue is
    {source, phrase, metric, value, rule, text} — the sentence that made the
    claim, and the number that contradicts it. An empty ``issues`` list means
    no sentence disagrees with the data behind it.
    """
    metrics = _check_metrics(rows)
    if sections is None:
        sections = narrative_sections(rows)

    issues, claims = [], 0
    for source, text in sections:
        for pattern, key, predicate, rule in _NARRATIVE_RULES:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                metric = metrics[key]
                if metric["value"] is None:
                    continue  # nothing to contradict
                if _is_negated(text, match.start()):
                    continue  # a denied claim is not a claim
                claims += 1
                if predicate(metric["value"]):
                    continue
                issues.append({
                    "source": source,
                    "phrase": match.group(0),
                    "metric": metric["name"],
                    "value": _format_metric(metric),
                    "rule": rule,
                    "text": text,
                })

    lean_issues, lean_claims = _lean_claim_issues(
        market_regime(rows)["components"], sections)
    issues.extend(lean_issues)
    claims += lean_claims

    return {"issues": issues, "claims_checked": claims,
            "sections_checked": len(sections)}


def print_consistency_check(rows, indent="      "):
    """Run the check and report it on the console. Returns the result dict."""
    result = consistency_check(rows)
    issues = result["issues"]
    if not issues:
        print(f"{indent}Narrative consistency: OK — {result['claims_checked']} "
              f"directional claim(s) across {result['sections_checked']} "
              f"sentence(s) agree with the data.")
        return result

    print(f"{indent}! Narrative consistency: {len(issues)} contradiction(s) in "
          f"{result['claims_checked']} checked claim(s):")
    for issue in issues:
        print(f"{indent}  ! [{issue['source']}] says \"{issue['phrase']}\" but "
              f"{issue['metric']} = {issue['value']} — {issue['rule']}.")
        print(f"{indent}    > {issue['text']}")
    return result
