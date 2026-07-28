"""Derived market analytics for the morning briefing.

Turns the raw metric dicts from fetch into the higher-level, plain-English
signals shown in the report: the market-regime label, the Goldman-style
opening note and bullets, the yield-curve read, equity drawdowns, the
cross-asset correlation note, and S&P key levels.

Every function is defensive about missing values (None) so a single failed
ticker never breaks the briefing. Functions return semantic "tone" strings
("pos" / "neg" / "caution" / "neutral") rather than colours, so the report
layer keeps full control of the visual language.
"""

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


# --------------------------------------------------------------------------- #
# Market regime
# --------------------------------------------------------------------------- #
def market_regime(rows):
    """Classify the environment: Risk-On / Risk-Off / Transitional / Stress.

    Uses VIX level, average equity direction, and the gold/bond reaction.
    Returns {label, tone, detail}.
    """
    vix = _field(rows, "^VIX", "price")
    avg_eq = _avg([r.get("change_pct") for r in _equities(rows)])
    gold_chg = _field(rows, "GC=F", "change_pct")
    yield_chg = _field(rows, "^TNX", "change_pct")  # 10Y yield % change

    # Defaults if we somehow have no data.
    if avg_eq is None and vix is None:
        return {"label": "Transitional", "tone": "caution",
                "detail": "Insufficient data to classify the regime."}

    # Stress dominates everything else.
    if vix is not None and vix >= 30:
        return {"label": "Stress", "tone": "neg",
                "detail": "VIX above 30 signals acute market stress."}

    eq = avg_eq if avg_eq is not None else 0.0
    gold_up = _sign(gold_chg) > 0
    yields_down = _sign(yield_chg) < 0  # bond prices up

    if eq < 0 and ((vix is not None and vix >= 20) or gold_up or yields_down):
        return {"label": "Risk-Off", "tone": "neg",
                "detail": "Equities soft with defensive bid in bonds/gold."}
    if eq > 0 and (vix is None or vix < 18):
        return {"label": "Risk-On", "tone": "pos",
                "detail": "Equities firm and volatility contained."}
    return {"label": "Transitional", "tone": "caution",
            "detail": "Mixed cross-asset signals; no clear directional bias."}


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
    avg_eq = _avg([r.get("change_pct") for r in _equities(rows)])
    yield_chg = _field(rows, "^TNX", "change_pct")
    gold_chg = _field(rows, "GC=F", "change_pct")

    eq_dir = _sign(avg_eq)
    bond_dir = -_sign(yield_chg)  # yields up => bond prices down
    gold_dir = _sign(gold_chg)

    if eq_dir == 0 and bond_dir == 0:
        base = "Equities and bonds are little changed, offering few cross-asset signals today."
    elif eq_dir > 0 and bond_dir > 0:
        base = ("Equities and bonds are rallying together as yields ease — a "
                "liquidity-friendly backdrop supportive of risk.")
    elif eq_dir > 0 and bond_dir < 0:
        base = ("Equities climb while bonds sell off (yields higher) — a classic "
                "reflationary, risk-on configuration.")
    elif eq_dir < 0 and bond_dir > 0:
        base = ("Equities fall as bonds catch a bid — textbook risk-off, "
                "flight-to-quality positioning.")
    elif eq_dir < 0 and bond_dir < 0:
        base = ("Both equities and bonds are under pressure — rising yields into a "
                "stock selloff point to a rates-driven, correlation-up regime.")
    else:
        base = "Cross-asset moves are mixed, with no single dominant theme."

    if gold_dir > 0:
        base += " Gold is firmer, consistent with hedging demand."
    elif gold_dir < 0:
        base += " Gold is softer, suggesting limited safe-haven demand."
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
    score = -_sign(eur) - _sign(gbp) + _sign(jpy)
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
    avg_eq = _avg([r.get("change_pct") for r in _equities(rows)])
    eqs = _equities(rows)

    # Best / worst equity index on the day.
    rated = [e for e in eqs if e.get("change_pct") is not None]
    best = max(rated, key=lambda e: e["change_pct"]) if rated else None
    worst = min(rated, key=lambda e: e["change_pct"]) if rated else None

    gold_chg = _field(rows, "GC=F", "change_pct")
    oil_chg = _field(rows, "CL=F", "change_pct")
    sp = _field(rows, "^GSPC", "price")
    usd_score, usd_label = _usd_read(rows)

    # --- Opening paragraph (2-3 sentences) --------------------------------- #
    if regime["label"] == "Risk-On":
        lead = "Risk appetite is firm this morning"
    elif regime["label"] == "Risk-Off":
        lead = "Markets open on the defensive"
    elif regime["label"] == "Stress":
        lead = "Markets are under visible stress"
    else:
        lead = "Markets open in a holding pattern"

    eq_phrase = ("equities extend gains" if (avg_eq or 0) > 0
                 else "equities trade lower" if (avg_eq or 0) < 0
                 else "equities trade broadly flat")
    vol_phrase = (f"the VIX at {vix:.1f}" if vix is not None else "volatility data thin")

    s1 = f"{lead} as {eq_phrase} and {vol_phrase} frames the tape."
    if sp is not None:
        s2 = (f"The S&P 500 sits at {sp:,.0f}, with breadth across global indices "
              f"{'constructive' if (avg_eq or 0) >= 0 else 'soft'}.")
    else:
        s2 = "Global index breadth is the key tell into the session."
    s3 = (f"The dollar is {usd_label} and the curve reads "
          f"{curve['label'].lower()}, leaving cross-asset signals "
          f"{'supportive' if regime['tone'] == 'pos' else 'cautious' if regime['tone'] == 'caution' else 'risk-negative'}.")
    paragraph = " ".join([s1, s2, s3])

    # --- Bullets ----------------------------------------------------------- #
    bullets = []

    # 1. Sentiment
    bullets.append({"label": "Sentiment",
                    "text": f"{regime['label']} — {regime['detail']}",
                    "tone": regime["tone"]})

    # 2. Equities
    if best and worst:
        if best["ticker"] == worst["ticker"]:
            eq_text = f"{best['name']} {_pct(best['change_pct'])} on the day."
        else:
            eq_text = (f"{best['name']} leads ({_pct(best['change_pct'])}); "
                       f"{worst['name']} lags ({_pct(worst['change_pct'])}); "
                       f"average index move {_pct(avg_eq)}.")
        eq_tone = "pos" if (avg_eq or 0) > 0 else "neg" if (avg_eq or 0) < 0 else "neutral"
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

    # 5. Rates / curve
    if curve["spread"] is None:
        rates_text, rates_tone = "Curve data unavailable.", "neutral"
    else:
        yields_dir = _sign(_field(rows, "^TNX", "change_pct"))
        rate_bias = ("rising yields are a headwind for equities" if yields_dir > 0
                     else "easing yields offer relief to equities" if yields_dir < 0
                     else "yields little changed")
        rates_text = (f"2s10s at {curve['spread_bps']:+.0f}bps ({curve['label']}); "
                      f"{rate_bias}.")
        rates_tone = curve["tone"]
    bullets.append({"label": "Rates", "text": rates_text, "tone": rates_tone})

    # 6. Commodities
    oil_word = ("firmer" if _sign(oil_chg) > 0 else "softer" if _sign(oil_chg) < 0 else "steady")
    gold_word = ("bid" if _sign(gold_chg) > 0 else "offered" if _sign(gold_chg) < 0 else "flat")
    comm_text = (f"Crude {oil_word} ({_pct(oil_chg)}); gold {gold_word} ({_pct(gold_chg)}) — "
                 f"{'haven demand evident' if _sign(gold_chg) > 0 else 'limited haven demand'}.")
    comm_tone = "caution" if _sign(gold_chg) > 0 and (avg_eq or 0) < 0 else "neutral"
    bullets.append({"label": "Commodities", "text": comm_text, "tone": comm_tone})

    # 7. Watch today (actionable)
    kl = key_levels(rows)
    if curve["spread"] is not None and curve["spread"] < 0:
        watch = "Watch the inverted curve — any further inversion reinforces recession pricing."
        watch_tone = "neg"
    elif kl and kl["near_high"]:
        watch = f"Watch S&P 500 {kl['high']:,.0f} — a test of the 52-week high; a clean break opens upside."
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

    # 1. S&P key levels
    if kl and kl["near_high"]:
        notes.append(f"S&P near 52W high ({kl['high']:,.0f}) — watch for rejection or breakout")
    elif kl and kl["near_low"]:
        notes.append(f"S&P near 52W low ({kl['low']:,.0f}) — support zone, watch for a bounce or breakdown")
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

    # 4. Gold vs equities cross-check
    if _sign(gold_chg) > 0 and (avg_eq or 0) > 0:
        notes.append("Gold bid alongside equities = unusual, monitor for a regime change")
    elif _sign(gold_chg) > 0 and (avg_eq or 0) < 0:
        notes.append("Gold bid as stocks slip — classic haven flows, risk-off creeping in")
    elif _sign(gold_chg) < 0:
        notes.append("Gold offered — haven demand light, risk appetite intact for now")

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
    if _sign(gold_chg) > 0 and (avg_eq or 0) >= 0:
        points.append({"icon": "🥇", "title": "Gold + stocks both up",
                       "text": "Gold rising with equities is a mixed signal — I trim nothing but I raise my "
                               "stop-losses slightly as a precaution."})
    elif _sign(gold_chg) > 0 and (avg_eq or 0) < 0:
        points.append({"icon": "🛡️", "title": "Haven bid",
                       "text": "Gold bid while equities slip is textbook risk-off — I let winners run into the "
                               "haven but add a small gold/duration hedge to the book."})
    else:
        points.append({"icon": "🥇", "title": "Gold offered",
                       "text": "Gold soft tells me haven demand is light — I'm comfortable carrying full equity "
                               "risk and don't need a defensive overlay today."})

    # 4. Regime -> overall posture
    posture = {
        "Risk-On": "I run net-long and let beta work, focusing energy on the best ideas rather than hedges.",
        "Risk-Off": "I cut gross exposure, hedge the index, and only keep my highest-conviction longs.",
        "Stress": "I move to capital preservation — raise cash, hedge aggressively, and wait for the dust to settle.",
        "Transitional": "Signals are mixed, so I keep balanced exposure and avoid making big directional bets.",
    }.get(regime["label"], "I keep balanced exposure until the cross-asset picture clarifies.")
    points.append({"icon": "🎯", "title": f"Regime: {regime['label']}", "text": posture})

    # 5. S&P key levels -> entries
    if kl and kl["near_high"]:
        points.append({"icon": "🚀", "title": "Pressing the highs",
                       "text": f"S&P within 3% of its {kl['high']:,.0f} high — I wait for a clean break to add, "
                               f"rather than chase into resistance and risk a rejection."})
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
