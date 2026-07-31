"""Tests for the regime classifier and the narrative consistency guard.

Run with:  python tests/test_analysis.py
(No pytest dependency — the project ships without a test runner.)
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import analysis  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #
def _row(ticker, name, asset_class, price=None, change=None,
         high=None, low=None, ytd=None):
    return {"ticker": ticker, "name": name, "asset_class": asset_class,
            "price": price, "change_pct": change, "52w_high": high,
            "52w_low": low, "ytd_change_pct": ytd}


def make_rows(spx=0.28, stoxx=0.45, nikkei=-2.25, ftse=0.33, dax=1.04,
              vix=18.21, tnx_chg=-0.80, tnx=4.604, two_y=4.064,
              eur=-0.06, gbp=-0.50, jpy=0.15, gold=-1.46, crude=0.24,
              btc=0.20, spx_price=7428.78, spx_high=7609.78, spx_low=6238.01,
              vix_chg=-2.46):
    """Full universe with sane defaults = the day the bug was reported."""
    return [
        _row("^GSPC", "S&P 500", "Equity", spx_price, spx, spx_high, spx_low, 8.3),
        _row("^STOXX50E", "Euro Stoxx 50", "Equity", 6282.21, stoxx, 6412.68, 5242.32, 6.0),
        _row("^N225", "Nikkei 225", "Equity", 64931.19, nikkei, 72366.34, 40290.70, 25.3),
        _row("^FTSE", "FTSE 100", "Equity", 10871.02, ftse, 10910.60, 9068.60, 9.2),
        _row("^GDAXI", "DAX", "Equity", 25361.03, dax, 25817.89, 22300.75, 3.3),
        _row("^VIX", "VIX", "Volatility", vix, vix_chg),
        _row("^TNX", "10Y US Treasury", "Rate", tnx, tnx_chg),
        _row("2YY=F", "2Y US Treasury", "Rate", two_y, 0.10),
        _row("EURUSD=X", "EUR/USD", "FX", 1.1388, eur),
        _row("GBPUSD=X", "GBP/USD", "FX", 1.3285, gbp),
        _row("JPY=X", "USD/JPY", "FX", 163.85, jpy),
        _row("DX-Y.NYB", "US Dollar Index", "Macro", 101.39, 0.21),
        _row("CL=F", "WTI Crude Oil", "Commodity", 81.26, crude),
        _row("GC=F", "Gold", "Commodity", 4023.60, gold),
        _row("BTC-USD", "Bitcoin", "Crypto", 63850.0, btc),
    ]


PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not condition else ""))


# --------------------------------------------------------------------------- #
# 1. The reported day must no longer classify as Risk-Off
# --------------------------------------------------------------------------- #
def test_reported_day():
    print("\n1. The reported day (VIX 18.21, 2s10s +54bps, gold -1.46%, avg -0.03%)")
    rows = make_rows()
    regime = analysis.market_regime(rows)
    stats = analysis._equity_stats(rows)

    print(f"      avg index move {stats['mean']:+.2f}%  "
          f"({stats['up']} of {stats['n']} higher)")
    print(f"      score {regime['score']:+.3f} -> {regime['label']}")
    for c in regime["components"]:
        print(f"        {c['label']:<9} {c['text']:<38} "
              f"score {c['score']:+.2f} x w {c['weight']:.2f} "
              f"= {c['contribution']:+.3f}")

    check("not classified Risk-Off", regime["label"] != "Risk-Off", regime["label"])
    check("score is positive (neutral-to-risk-on)", regime["score"] > 0,
          f"{regime['score']:+.3f}")
    check("detail mentions no gold bid",
          "defensive bid" not in regime["detail"].lower())
    check("gold component is risk-on (gold down)",
          next(c for c in regime["components"] if c["key"] == "gold")["score"] > 0)
    check("curve component is risk-on (+54bps normal)",
          next(c for c in regime["components"] if c["key"] == "curve")["score"] > 0)
    check("no single component exceeds 30% of total weight",
          max(c["weight"] for c in regime["components"]) <= 0.30 + 1e-9)


# --------------------------------------------------------------------------- #
# 2. A single outlier index must not flip the regime
# --------------------------------------------------------------------------- #
def test_outlier_index():
    print("\n2. One outlier index cannot dominate")
    # Four indices up ~+0.5%, Nikkei crashes -8%. Plain mean is deeply negative.
    rows = make_rows(spx=0.5, stoxx=0.5, ftse=0.5, dax=0.5, nikkei=-8.0)
    stats = analysis._equity_stats(rows)
    regime = analysis.market_regime(rows)
    print(f"      plain mean {stats['mean']:+.2f}%, winsorised "
          f"{stats['winsor_mean']:+.2f}%, breadth {stats['breadth']:+.2f}")
    print(f"      score {regime['score']:+.3f} -> {regime['label']}")
    check("winsorised mean is less negative than the plain mean",
          stats["winsor_mean"] > stats["mean"])
    eq = next(c for c in regime["components"] if c["key"] == "equities")
    check("equity component is not driven fully negative by one index",
          eq["score"] > -0.5, f"{eq['score']:+.2f}")

    # The real 2026-07-28 snapshot: 4 of 5 indices higher, but Nikkei -3.95%
    # drags the average to -0.46%. Neither "gains" nor "lower" is honest.
    print("      -- breadth vs average disagreement --")
    rows = make_rows(spx=0.21, stoxx=0.23, nikkei=-3.95, ftse=0.83, dax=0.37)
    stats = analysis._equity_stats(rows)
    phrase, shape = analysis._equity_phrase(stats)
    print(f"      avg {stats['mean']:+.2f}%, {stats['up']} of {stats['n']} higher")
    print(f"      -> {phrase}")
    check("disagreement is described as mixed", shape == "mixed", shape)
    check("mixed phrase names the outlier index", "Nikkei 225" in phrase, phrase)

    brief = analysis.morning_briefing(rows)
    print(f"      -> {brief['paragraph']}")
    check("paragraph does not say 'equities trade lower' when most rose",
          "equities trade lower" not in brief["paragraph"], brief["paragraph"])
    eq_bullet = next(b for b in brief["bullets"] if b["label"] == "Equities")
    check("equities bullet tone is neutral on a mixed tape",
          eq_bullet["tone"] == "neutral", eq_bullet["tone"])
    corr = analysis.correlation_note(rows)
    check("correlation note does not assert flight-to-quality on positive breadth",
          "textbook risk-off" not in corr
          and "rather than a broad flight to quality" in corr, corr)

    # The mirror case, caught on live 2026-07-31 data: Nikkei +4.03% against
    # four small decliners lifts the average to +0.64% while 4 of 5 indices are
    # LOWER. The note claimed "Equities climb ... a classic reflationary,
    # risk-on configuration" — contradicting the four.
    # NB: bound to its own names — `rows`/`stats`/`corr` are reused by the
    # assertions further down this function.
    print("      -- outlier carries the mean the other way --")
    up_rows = make_rows(spx=-0.21, stoxx=-0.08, nikkei=4.03, ftse=-0.42,
                        dax=-0.13, tnx_chg=1.50)
    up_stats = analysis._equity_stats(up_rows)
    up_corr = analysis.correlation_note(up_rows)
    print(f"      avg {up_stats['mean']:+.2f}%, {up_stats['up']} of "
          f"{up_stats['n']} higher, breadth {up_stats['breadth']:+.2f}")
    print(f"      -> {up_corr}")
    check("positive average with negative breadth is not called a climb",
          "Equities climb" not in up_corr, up_corr)
    check("nor a reflationary risk-on configuration",
          "reflationary" not in up_corr, up_corr)
    check("it is described as mixed", "Equities are mixed" in up_corr, up_corr)
    check("breadth is quoted alongside the average",
          "1 of 5 indices higher" in up_corr, up_corr)
    up_res = analysis.consistency_check(up_rows)
    for issue in up_res["issues"]:
        print(f"        ! {issue['source']}: \"{issue['phrase']}\" vs "
              f"{issue['metric']} = {issue['value']}")
    check("outlier-carried-mean narrative is self-consistent", not up_res["issues"])
    check("checker rejects the old wording on this data",
          "breadth/average agreement" in
          {i["metric"] for i in analysis.consistency_check(
              up_rows, sections=[("planted",
                                  "Equities climb while bonds sell off — a classic "
                                  "reflationary, risk-on configuration.")])["issues"]})
    res = analysis.consistency_check(rows)
    for issue in res["issues"]:
        print(f"        ! {issue['source']}: \"{issue['phrase']}\" vs "
              f"{issue['metric']} = {issue['value']}")
    check("mixed-tape narrative is self-consistent", not res["issues"])

    # And the checker must reject the dishonest phrasings on this same data.
    for text, metric in [
        ("Equities trade lower across the board.", "breadth/average agreement"),
        ("Equities fall as bonds catch a bid — textbook risk-off.", "equity breadth"),
    ]:
        flagged = {i["metric"] for i in
                   analysis.consistency_check(rows, sections=[("planted", text)])["issues"]}
        check(f"checker rejects: {text[:40]}", metric in flagged, str(flagged))


# --------------------------------------------------------------------------- #
# 3. Genuine regimes still classify correctly (no over-correction)
# --------------------------------------------------------------------------- #
def test_genuine_regimes():
    print("\n3. Genuine regimes still classify correctly")

    risk_off = make_rows(spx=-1.8, stoxx=-2.1, nikkei=-2.4, ftse=-1.5, dax=-2.0,
                         vix=24.0, tnx_chg=-2.5, two_y=4.70, tnx=4.60,
                         gold=1.9, eur=-0.4, gbp=-0.5, jpy=0.4)
    r = analysis.market_regime(risk_off)
    print(f"      broad selloff, VIX 24, gold +1.9%, curve inverted "
          f"-> {r['label']} ({r['score']:+.3f})")
    check("broad selloff classifies Risk-Off", r["label"] == "Risk-Off", r["label"])

    risk_on = make_rows(spx=1.1, stoxx=1.3, nikkei=0.9, ftse=0.8, dax=1.4,
                        vix=13.5, tnx_chg=0.3, gold=-0.9,
                        eur=0.3, gbp=0.4, jpy=-0.3)
    r = analysis.market_regime(risk_on)
    print(f"      broad rally, VIX 13.5, dollar softer -> {r['label']} ({r['score']:+.3f})")
    check("broad rally classifies Risk-On", r["label"] == "Risk-On", r["label"])

    stress = make_rows(spx=-3.5, stoxx=-4.0, nikkei=-5.0, ftse=-3.0, dax=-4.2,
                       vix=34.0, gold=2.5, two_y=4.9, tnx=4.4)
    r = analysis.market_regime(stress)
    print(f"      VIX 34 crash -> {r['label']} ({r['score']:+.3f})")
    check("VIX 34 classifies Stress", r["label"] == "Stress", r["label"])

    # The 18-20 VIX band used to be structurally incapable of Risk-On.
    band = make_rows(spx=1.2, stoxx=1.4, nikkei=1.1, ftse=1.0, dax=1.5,
                     vix=19.0, gold=-1.0, eur=0.3, gbp=0.3, jpy=-0.3)
    r = analysis.market_regime(band)
    print(f"      strong rally with VIX 19.0 -> {r['label']} ({r['score']:+.3f})")
    check("VIX 18-20 band can now reach Risk-On", r["label"] == "Risk-On", r["label"])


# --------------------------------------------------------------------------- #
# 4. Symmetry: mirrored inputs must give mirrored scores
# --------------------------------------------------------------------------- #
def test_symmetry():
    """The bug was asymmetric *thresholds* (Risk-On needed VIX < 18 while
    Risk-Off needed only VIX >= 20, so the 18-20 band could never be Risk-On).

    The linearly-mirrorable components must now mirror exactly. VIX and the
    curve are deliberately NOT mirror-symmetric — VIX is bounded below with a
    fat right tail, and an inverted curve carries more signal than an equally
    steep one — so those two are asserted to lean the risk-aware way instead.
    """
    print("\n4. Classifier symmetry")

    # Neutral VIX (18) and a flat curve (0bps) zero out the two intentionally
    # asymmetric components, leaving equities / gold / dollar to mirror.
    up = make_rows(spx=1.0, stoxx=1.0, nikkei=1.0, ftse=1.0, dax=1.0,
                   vix=18.0, two_y=4.604, tnx=4.604, gold=-1.0,
                   eur=0.3, gbp=0.3, jpy=-0.3)
    down = make_rows(spx=-1.0, stoxx=-1.0, nikkei=-1.0, ftse=-1.0, dax=-1.0,
                     vix=18.0, two_y=4.604, tnx=4.604, gold=1.0,
                     eur=-0.3, gbp=-0.3, jpy=0.3)
    a, b = analysis.market_regime(up), analysis.market_regime(down)
    print(f"      mirrored inputs -> {a['score']:+.3f} / {b['score']:+.3f} "
          f"({a['label']} / {b['label']})")
    check("mirrored inputs produce mirrored scores",
          abs(a["score"] + b["score"]) < 1e-9,
          f"{a['score']:+.3f} vs {b['score']:+.3f}")
    check("mirrored inputs produce opposite labels",
          {a["label"], b["label"]} == {"Risk-On", "Risk-Off"},
          f"{a['label']} / {b['label']}")
    check("thresholds are symmetric",
          abs(analysis.RISK_ON_AT + analysis.RISK_OFF_AT) < 1e-9)

    # The 18-20 VIX band must be reachable from both directions — this is the
    # asymmetry that produced the original misclassification.
    for vix in (18.5, 19.0, 19.5):
        hot = analysis.market_regime(
            make_rows(spx=1.3, stoxx=1.4, nikkei=1.2, ftse=1.1, dax=1.5,
                      vix=vix, gold=-1.0, eur=0.3, gbp=0.3, jpy=-0.3))
        cold = analysis.market_regime(
            make_rows(spx=-1.3, stoxx=-1.4, nikkei=-1.2, ftse=-1.1, dax=-1.5,
                      vix=vix, gold=1.0, eur=-0.3, gbp=-0.3, jpy=0.3,
                      two_y=4.70, tnx=4.60))
        check(f"VIX {vix} reaches both Risk-On and Risk-Off",
              hot["label"] == "Risk-On" and cold["label"] == "Risk-Off",
              f"{hot['label']} / {cold['label']}")

    # Documented, intentional asymmetries — assert they lean risk-aware.
    check("VIX component: 6pts below 18 is worth as much as 12pts above",
          abs(analysis._vix_component(12.0)) == abs(analysis._vix_component(30.0))
          and analysis._vix_component(24.0) > -1.0,
          f"{analysis._vix_component(24.0):+.2f}")
    check("VIX component is neutral at 18",
          abs(analysis._vix_component(18.0)) < 1e-9)
    check("curve: -50bps inverted scores as hard as +100bps steep",
          analysis._curve_component(-50.0) == -1.0
          and analysis._curve_component(100.0) == 1.0)
    check("curve component is neutral at 0bps",
          abs(analysis._curve_component(0.0)) < 1e-9)


# --------------------------------------------------------------------------- #
# 5. Narrative must never contradict the data
# --------------------------------------------------------------------------- #
def test_narrative():
    print("\n5. Narrative agrees with the data")
    rows = make_rows()
    result = analysis.consistency_check(rows)
    print(f"      {result['claims_checked']} claim(s) across "
          f"{result['sections_checked']} sentence(s), "
          f"{len(result['issues'])} issue(s)")
    for issue in result["issues"]:
        print(f"        ! {issue['source']}: \"{issue['phrase']}\" vs "
              f"{issue['metric']} = {issue['value']}")
    check("reported day produces no contradictions", not result["issues"])
    check("something was actually checked", result["claims_checked"] > 0)

    brief = analysis.morning_briefing(rows)
    blob = brief["paragraph"] + " " + " ".join(b["text"] for b in brief["bullets"])
    check("no 'defensive bid' claim while gold is negative",
          "defensive bid" not in blob.lower())
    check("flat tape is not described as trading lower",
          "equities trade lower" not in blob.lower(), blob)
    check("commodities line says limited haven demand",
          "limited haven demand" in blob)

    # Same check across several randomised-ish regimes.
    scenarios = {
        "broad selloff": make_rows(spx=-1.8, stoxx=-2.1, nikkei=-2.4, ftse=-1.5,
                                   dax=-2.0, vix=24.0, gold=1.9, tnx_chg=-2.5,
                                   two_y=4.70),
        "broad rally": make_rows(spx=1.1, stoxx=1.3, nikkei=0.9, ftse=0.8,
                                 dax=1.4, vix=13.5, gold=-0.9, tnx_chg=0.3,
                                 eur=0.3, gbp=0.4, jpy=-0.3),
        "stress": make_rows(spx=-3.5, stoxx=-4.0, nikkei=-5.0, ftse=-3.0,
                            dax=-4.2, vix=34.0, gold=2.5, two_y=4.9, tnx=4.4),
        "dead flat": make_rows(spx=0.02, stoxx=-0.01, nikkei=0.03, ftse=0.0,
                               dax=-0.02, vix=16.0, gold=0.01, crude=0.0,
                               tnx_chg=0.0, eur=0.0, gbp=0.0, jpy=0.0),
        "missing gold/vix": [r for r in make_rows()
                             if r["ticker"] not in ("GC=F", "^VIX")],
    }
    for name, scenario in scenarios.items():
        res = analysis.consistency_check(scenario)
        for issue in res["issues"]:
            print(f"        ! [{name}] {issue['source']}: \"{issue['phrase']}\" "
                  f"vs {issue['metric']} = {issue['value']}")
        check(f"'{name}' narrative is self-consistent", not res["issues"])


# --------------------------------------------------------------------------- #
# 6. The checker itself must catch a real contradiction
# --------------------------------------------------------------------------- #
def test_checker_catches_the_original_bug():
    print("\n6. The checker catches the original bug")
    rows = rows_flat = make_rows()  # gold -1.46%, avg -0.03%
    # The exact string the old classifier emitted on this data.
    planted = [("planted", "Equities soft with defensive bid in bonds/gold.")]
    result = analysis.consistency_check(rows, sections=planted)
    check("'defensive bid' is flagged while gold is negative",
          any(i["metric"] == "gold" for i in result["issues"]))
    check("'equities soft' is flagged on a flat tape",
          any(i["metric"] == "average index move" for i in result["issues"]))
    for issue in result["issues"]:
        print(f"        caught: \"{issue['phrase']}\" -- {issue['metric']} = "
              f"{issue['value']} ({issue['rule']})")

    # A few more deliberate contradictions.
    for text, expect in [
        ("Gold is firmer, consistent with hedging demand.", "gold"),
        ("VIX shows elevated volatility today.", "VIX"),
        ("The curve is inverted.", "2s10s spread"),
        ("Dollar broadly softer supports commodities.", "USD direction score"),
        ("Equities extend gains across the board.", "average index move"),
        ("Easing yields offer relief.", None),   # 10Y is -0.80%: this is TRUE
    ]:
        res = analysis.consistency_check(rows, sections=[("planted", text)])
        found = {i["metric"] for i in res["issues"]}
        if expect is None:
            check(f"true statement not flagged: {text[:38]}", not res["issues"], str(found))
        else:
            check(f"contradiction flagged: {text[:38]}", expect in found, str(found))

    # Negation handling must suppress denied claims WITHOUT creating a blind
    # spot for asserted ones. gold is -1.46% on this fixture.
    print("      -- negation handling --")
    for text, should_flag in [
        ("A rates-led move rather than a flight to quality.", False),
        ("There is no haven bid in gold today.", False),
        ("Gold is bid and haven demand evident.", True),
        ("Gold bid is the story of the session.", True),
        ("Despite the selloff, gold is firmer.", True),
        # A negation on the far side of a clause boundary governs a different
        # claim and must NOT suppress the rule.
        ("There is no equity bid; haven demand evident in gold.", True),
        ("Not a rates story — gold is firmer on the day.", True),
        ("Nothing unusual in vol, gold bid regardless.", True),
    ]:
        res = analysis.consistency_check(rows_flat, sections=[("planted", text)])
        flagged = bool(res["issues"])
        check(f"negation: {'flags' if should_flag else 'ignores'} "
              f"\"{text[:44]}\"", flagged == should_flag,
              f"flagged={flagged}")


# --------------------------------------------------------------------------- #
# 7. S&P price and 52-week high must stay distinct
# --------------------------------------------------------------------------- #
def test_sp_levels():
    print("\n7. S&P price vs 52-week high are never conflated")
    rows = make_rows()
    kl = analysis.key_levels(rows)
    print(f"      price {kl['price']:,.2f} | 52w high {kl['high']:,.2f} | "
          f"52w low {kl['low']:,.2f} | from high {kl['pct_from_high']:+.2f}%")
    check("price is the last price, not the high", kl["price"] == 7428.78)
    check("high is the 52-week high, not the price", kl["high"] == 7609.78)
    check("price and high are distinct", kl["price"] != kl["high"])
    check("pct_from_high computed off both", abs(kl["pct_from_high"] + 2.3785) < 0.001,
          f"{kl['pct_from_high']:+.4f}")
    check("price sits below its own 52-week high", kl["price"] < kl["high"])
    check("pct_from_high is never positive", kl["pct_from_high"] <= 0)

    brief = analysis.morning_briefing(rows)
    watch = next(b["text"] for b in brief["bullets"] if b["label"] == "Watch today")
    print(f"      watch: {watch}")
    check("watch line names both spot and the high",
          "7,610" in watch and "7,429" in watch, watch)
    check("paragraph names both spot and the high",
          "7,429" in brief["paragraph"] and "7,610" in brief["paragraph"],
          brief["paragraph"])

    # A synthetic index sitting exactly at its high must read 0.00%, not error.
    at_high = make_rows(spx_price=7609.78, spx_high=7609.78)
    kl2 = analysis.key_levels(at_high)
    check("index at its high reads 0.00% from high", abs(kl2["pct_from_high"]) < 1e-9)


# --------------------------------------------------------------------------- #
# 8. Missing data must not crash anything
# --------------------------------------------------------------------------- #
def test_missing_data():
    print("\n8. Missing data degrades gracefully")
    full = make_rows()
    for drop in ("^VIX", "GC=F", "^TNX", "2YY=F", "DX-Y.NYB", "^GSPC"):
        rows = [r for r in full if r["ticker"] != drop]
        try:
            regime = analysis.market_regime(rows)
            analysis.morning_briefing(rows)
            analysis.analyst_notebook(rows)
            analysis.decision_framework(rows)
            analysis.correlation_note(rows)
            analysis.consistency_check(rows)
            ok, detail = True, f"{regime['label']}"
        except Exception as exc:                     # noqa: BLE001 - test guard
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        check(f"survives missing {drop}", ok, detail)

    # Weights must renormalise to 1.0 over whatever is present.
    no_gold = [r for r in full if r["ticker"] != "GC=F"]
    regime = analysis.market_regime(no_gold)
    total = sum(c["weight"] for c in regime["components"])
    check("weights renormalise to 1.0 when a component is missing",
          abs(total - 1.0) < 1e-9, f"{total:.4f}")
    check("gold is absent from the components",
          not any(c["key"] == "gold" for c in regime["components"]))

    # Nothing at all.
    empty = analysis.market_regime([])
    check("empty input returns Transitional", empty["label"] == "Transitional")
    check("empty input returns no score", empty["score"] is None)


# --------------------------------------------------------------------------- #
# 9. A neutral component is never listed as a lean
# --------------------------------------------------------------------------- #
def test_neutral_components_never_lean():
    """The reported bug: 'gold +2.51% and vix 19.5 lean risk-off' printed on a
    page whose Volatility line called the same VIX 'a calm, risk-tolerant tape'.
    VIX 19.5 scores -0.13 — inside the dead-band — so it is neutral, and a
    neutral component may not appear in either lean group."""
    print("\n9. Neutral components are described as neutral, never as a lean")
    rows = make_rows(vix=19.5, gold=2.51)
    regime = analysis.market_regime(rows)
    detail = regime["detail"]
    vix_c = next(c for c in regime["components"] if c["key"] == "vix")
    print(f"      VIX component score {vix_c['score']:+.3f} "
          f"(band +/-{analysis.COMPONENT_LEAN_AT:.2f})")
    print(f"      -> {detail}")

    check("VIX 19.5 scores inside the neutral dead-band",
          abs(vix_c["score"]) < analysis.COMPONENT_LEAN_AT, f"{vix_c['score']:+.3f}")
    risk_off_clause = next((c for c in detail.split(";") if "risk-off" in c), "")
    risk_on_clause = next((c for c in detail.split(";") if "risk-on" in c), "")
    check("VIX is absent from the risk-off lean group",
          "VIX" not in risk_off_clause, risk_off_clause)
    check("VIX is absent from the risk-on lean group",
          "VIX" not in risk_on_clause, risk_on_clause)
    check("VIX is described as reading neutral",
          "VIX 19.5 reads neutral" in detail, detail)
    check("case is preserved (capitalize() no longer flattens 'VIX')",
          "vix 19.5" not in detail, detail)
    check("the genuine drags are still named",
          "gold +2.51%" in risk_off_clause, risk_off_clause)

    # The Sentiment line and the Volatility line must agree about the same VIX.
    brief = analysis.morning_briefing(rows)
    vol = next(b["text"] for b in brief["bullets"] if b["label"] == "Volatility")
    sent = next(b["text"] for b in brief["bullets"] if b["label"] == "Sentiment")
    check("Volatility line calls VIX 19.5 a calm tape", "calm" in vol, vol)
    check("Sentiment line does not contradict it",
          "VIX 19.5 reads neutral" in sent, sent)
    check("whole briefing is self-consistent",
          not analysis.consistency_check(rows)["issues"])

    # The checker must reject every dishonest lean-group phrasing on this data.
    for text, expect in [
        ("gold +2.51% and VIX 19.5 lean risk-off.", "VIX component score"),
        ("VIX 19.5 leans risk-on.", "VIX component score"),
        ("gold +2.51% leans risk-on.", "Gold component score"),
        ("gold +2.51% reads neutral.", "Gold component score"),
        ("gold +2.51% and dollar broadly firmer lean risk-off.", None),
        ("VIX 19.5 reads neutral.", None),
    ]:
        res = analysis.consistency_check(rows, sections=[("planted", text)])
        found = {i["metric"] for i in res["issues"]}
        if expect is None:
            check(f"lean check allows: {text[:44]}", not res["issues"], str(found))
        else:
            check(f"lean check rejects: {text[:44]}", expect in found, str(found))

    # A component sitting just outside the band must still be reported as a
    # lean — the dead-band must not silence real signals.
    strong = analysis.market_regime(make_rows(vix=24.0))
    vix_c = next(c for c in strong["components"] if c["key"] == "vix")
    check("VIX 24 (score -0.50) is still named as a risk-off drag",
          abs(vix_c["score"]) >= analysis.COMPONENT_LEAN_AT
          and "VIX 24.0" in strong["detail"].split(";")[-1] + strong["detail"],
          strong["detail"])


# --------------------------------------------------------------------------- #
# 10. The Rates line reads the actual 10Y move
# --------------------------------------------------------------------------- #
def test_rates_line():
    """'rising yields are a headwind for equities' used to be selected off a
    bare sign test, so it printed on any positive change — including a
    fractional-basis-point drift — and on days the 10Y was flat."""
    print("\n10. Rates commentary follows the actual 10Y change")

    def rates_of(rows):
        return next(b["text"] for b in analysis.morning_briefing(rows)["bullets"]
                    if b["label"] == "Rates")

    falling = rates_of(make_rows(tnx_chg=-0.80))
    rising = rates_of(make_rows(tnx_chg=0.90))
    drift = rates_of(make_rows(tnx_chg=0.02))
    flat = rates_of(make_rows(tnx_chg=0.0))
    for text in (falling, rising, drift, flat):
        print(f"      {text}")

    check("falling 10Y does not claim rising yields",
          "rising yields" not in falling, falling)
    check("falling 10Y says yields are easing",
          "easing yields" in falling and "the 10Y is lower" in falling, falling)
    check("rising 10Y says rising yields", "rising yields" in rising, rising)
    check("a +0.02% drift is not called rising",
          "rising yields" not in drift and "little changed" in drift, drift)
    check("an unchanged 10Y is not called rising or easing",
          "rising yields" not in flat and "easing yields" not in flat, flat)
    check("the Rates line quotes the 10Y level and its change",
          "4.60%" in falling and "-0.80%" in falling, falling)
    check("the curve reading is still carried",
          all("2s10s at +54bps (Normal)" in t for t in (falling, rising, drift)))

    # Every one of those days must survive the checker.
    for chg in (-1.50, -0.80, -0.06, -0.04, 0.0, 0.03, 0.20, 1.10):
        rows = make_rows(tnx_chg=chg)
        res = analysis.consistency_check(rows)
        for issue in res["issues"]:
            print(f"        ! 10Y {chg:+.2f}%: \"{issue['phrase']}\" vs "
                  f"{issue['metric']} = {issue['value']}")
        check(f"10Y {chg:+.2f}% narrative is self-consistent", not res["issues"])

    # And the checker must catch a yield claim that disagrees with the number.
    rows = make_rows(tnx_chg=-0.80)
    for text, expect in [
        ("Rising yields are a headwind for equities.", "10Y yield change"),
        ("The 10Y is little changed at 4.60%.", "10Y yield change"),
        ("Bonds sell off as yields push higher.", "10Y yield change"),
        ("The 10Y is lower at 4.60%.", None),
    ]:
        res = analysis.consistency_check(rows, sections=[("planted", text)])
        found = {i["metric"] for i in res["issues"]}
        if expect is None:
            check(f"yield check allows: {text[:44]}", not res["issues"], str(found))
        else:
            check(f"yield check rejects: {text[:44]}", expect in found, str(found))


# --------------------------------------------------------------------------- #
# 11. One VIX rounding, everywhere
# --------------------------------------------------------------------------- #
def test_vix_rounding():
    """The decision framework and the notebook rounded VIX to whole points
    while every other section printed one decimal, so a single tape read
    "VIX 18.5" in the masthead and "VIX at 19" three sections further down."""
    print("\n11. VIX is quoted the same way in every section")

    vix = 18.52
    rows = make_rows(vix=vix)
    expected = f"{vix:.1f}"          # 18.5
    rounded = f"{vix:.0f}"           # 19

    sections = analysis.narrative_sections(rows)
    quoting = [(src, text) for src, text in sections if "VIX" in text]
    check("several sections quote the VIX", len(quoting) >= 3, str(len(quoting)))

    # Every VIX level anywhere in the narrative must carry one decimal place.
    bad = []
    for src, text in sections:
        for match in re.finditer(r"VIX\s*(?:at\s*)?(\d+(?:\.\d+)?)", text):
            number = match.group(1)
            if number != expected:
                bad.append(f"{src}: {match.group(0)}")
    for src, text in quoting:
        print(f"      {src}: {text[:96]}")
    check(f"every section quotes VIX as {expected}", not bad, "; ".join(bad))
    check(f"no section rounds VIX to '{rounded}'",
          not any(re.search(rf"VIX\s*(?:at\s*)?{rounded}\b", t) for _, t in sections))

    # The framework and the notebook are the two that used to disagree.
    framework = " ".join(p["text"] for p in analysis.decision_framework(rows))
    notebook = " ".join(analysis.analyst_notebook(rows))
    check("decision framework quotes one decimal",
          expected in framework and f"VIX at {rounded}" not in framework, framework[:120])
    check("analyst notebook quotes one decimal",
          expected in notebook and f"VIX {rounded} " not in notebook, notebook[:120])

    # The shared helper is the single source of the format.
    check("a single helper renders every VIX level",
          analysis._vix_str(18.52) == "18.5" and analysis.VIX_DP == 1,
          analysis._vix_str(18.52))

    # Levels that round differently at 0 and 1 dp must still agree everywhere.
    for level in (12.44, 18.52, 19.49, 24.95, 31.06):
        texts = [t for _, t in analysis.narrative_sections(make_rows(vix=level))]
        want = f"{level:.1f}"
        found = {m for t in texts for m in re.findall(r"VIX\s*(?:at\s*)?(\d+\.?\d*)", t)}
        check(f"VIX {level} is quoted only as {want}", found == {want}, str(found))


# --------------------------------------------------------------------------- #
# 12. A sharp one-day VIX move is not described as calm
# --------------------------------------------------------------------------- #
def test_vix_daily_move():
    """The volatility copy was selected purely off the level, so a session on
    which the VIX rose +8.37% into 18.5 still printed "subdued volatility
    signals a calm, risk-tolerant tape". The level was honest; "calm" was not."""
    print("\n12. Volatility prose reads the 1-day move, not just the level")

    def vol_of(rows):
        return next(b["text"] for b in analysis.morning_briefing(rows)["bullets"]
                    if b["label"] == "Volatility")

    spike = make_rows(vix=18.52, vix_chg=8.37)     # the live report's day
    calm = make_rows(vix=18.52, vix_chg=-2.46)
    drift = make_rows(vix=18.52, vix_chg=1.20)
    collapse = make_rows(vix=18.52, vix_chg=-9.10)

    for label, rows in (("spike +8.37%", spike), ("calm -2.46%", calm),
                        ("drift +1.20%", drift), ("collapse -9.10%", collapse)):
        print(f"      {label}: {vol_of(rows)}")

    spike_text = vol_of(spike)
    check("a +8.37% VIX day is not called calm",
          "calm" not in spike_text.lower(), spike_text)
    check("a +8.37% VIX day is not called subdued",
          "subdued" not in spike_text.lower(), spike_text)
    check("the spike day says volatility is ticking up from a low base",
          "ticking up from a low base" in spike_text, spike_text)
    check("the spike day still acknowledges the low absolute level",
          "still low" in spike_text, spike_text)
    check("the spike day quotes the actual 1-day change",
          "+8.37%" in spike_text, spike_text)
    check("the spike day is toned caution, not positive",
          next(b["tone"] for b in analysis.morning_briefing(spike)["bullets"]
               if b["label"] == "Volatility") == "caution")

    # The regime label reads off the level and must be unaffected.
    check("VIX regime scoring still reads the level, not the move",
          analysis._vix_component(18.52) == analysis._vix_component(18.52))
    check("a quiet day is still called calm", "calm" in vol_of(calm), vol_of(calm))
    check("a small +1.20% drift is not a spike", "calm" in vol_of(drift), vol_of(drift))
    check("a sharp fall is not called a spike",
          "ticking up" not in vol_of(collapse), vol_of(collapse))

    # The threshold behaves at its own boundary.
    check("just under the threshold reads calm",
          "calm" in vol_of(make_rows(vix=18.52, vix_chg=4.99)))
    check("at the threshold reads as ticking up",
          "ticking up" in vol_of(make_rows(vix=18.52, vix_chg=5.0)))

    # The whole narrative, not just the bullet, must stop saying "calm".
    brief = analysis.morning_briefing(spike)
    notebook = " ".join(analysis.analyst_notebook(spike))
    framework = " ".join(p["text"] for p in analysis.decision_framework(spike))
    print(f"      paragraph: {brief['paragraph'][:130]}")
    print(f"      notebook:  {notebook[:130]}")
    check("the opening paragraph acknowledges the move",
          "ticking up from a low base" in brief["paragraph"], brief["paragraph"])
    check("the notebook does not call the spike day calm",
          "calm but not euphoric" not in notebook, notebook)
    check("the notebook does not say hedges are still cheap on a spike",
          "hedges still cheap" not in notebook, notebook)
    check("the framework does not flatly call options cheap on a spike",
          "options are cheap" not in framework, framework[:200])

    # Every one of those days must survive the checker.
    for chg in (-9.10, -5.0, -2.46, 0.0, 1.20, 4.99, 5.0, 8.37, 22.0):
        rows = make_rows(vix=18.52, vix_chg=chg)
        res = analysis.consistency_check(rows)
        for issue in res["issues"]:
            print(f"        ! VIX {chg:+.2f}%: \"{issue['phrase']}\" vs "
                  f"{issue['metric']} = {issue['value']}")
        check(f"VIX {chg:+.2f}% narrative is self-consistent", not res["issues"])

    # And the checker must catch volatility copy that disagrees with the move.
    spiking = make_rows(vix=18.52, vix_chg=8.37)
    quiet = make_rows(vix=18.52, vix_chg=-2.46)
    for rows, text, expect in [
        (spiking, "Subdued volatility signals a calm, risk-tolerant tape.",
         "VIX 1-day change"),
        (spiking, "VIX 18.5 — calm but not euphoric, hedges still cheap",
         "VIX 1-day change"),
        (quiet, "Volatility is ticking up from a low base.", "VIX 1-day change"),
        (spiking, "Volatility is ticking up from a low base.", None),
        (quiet, "Subdued volatility signals a calm, risk-tolerant tape.", None),
    ]:
        res = analysis.consistency_check(rows, sections=[("planted", text)])
        found = {i["metric"] for i in res["issues"]}
        if expect is None:
            check(f"vol check allows: {text[:46]}", not res["issues"], str(found))
        else:
            check(f"vol check rejects: {text[:46]}", expect in found, str(found))


# --------------------------------------------------------------------------- #
# 13. The 52-week-high sentence says which level sits above which
# --------------------------------------------------------------------------- #
def test_high_vs_spot_wording():
    """"2.4% above spot at 7,429" let the percentage attach to either number,
    so the sentence could be read as the high being 2.4% above 7,429 or as
    7,429 itself being 2.4% above something."""
    print("\n13. 52-week-high wording is unambiguous")

    rows = make_rows(spx_price=7428.78, spx_high=7609.78)
    watch = next(b["text"] for b in analysis.morning_briefing(rows)["bullets"]
                 if b["label"] == "Watch today")
    print(f"      {watch}")

    check("the ambiguous 'above spot' phrasing is gone",
          "above spot" not in watch, watch)
    check("the high is named as the high",
          "52-week high of 7,610" in watch, watch)
    check("the price is named as the current price",
          "current price of 7,429" in watch, watch)
    check("the sentence states the relationship explicitly",
          "sits 2.4% above the current price" in watch, watch)
    check("both numbers still appear",
          "7,610" in watch and "7,429" in watch, watch)

    # The other places that quote both levels were already unambiguous and
    # must stay that way.
    notebook = " ".join(analysis.analyst_notebook(rows))
    framework = " ".join(p["text"] for p in analysis.decision_framework(rows))
    paragraph = analysis.morning_briefing(rows)["paragraph"]
    for name, text in (("notebook", notebook), ("framework", framework),
                       ("paragraph", paragraph)):
        check(f"{name} never says 'above spot'", "above spot" not in text, text[:140])
    check("the paragraph still labels both numbers",
          "trades at 7,429" in paragraph and "52-week closing high of 7,610" in paragraph,
          paragraph[:160])

    # The whole day still passes the checker with the new wording.
    res = analysis.consistency_check(rows)
    check("rewording did not break narrative consistency", not res["issues"],
          str([i["phrase"] for i in res["issues"]]))


def main():
    print("=" * 70)
    print(" ANALYSIS TESTS — regime classifier + narrative consistency")
    print("=" * 70)
    test_reported_day()
    test_outlier_index()
    test_genuine_regimes()
    test_symmetry()
    test_narrative()
    test_checker_catches_the_original_bug()
    test_sp_levels()
    test_missing_data()
    test_neutral_components_never_lean()
    test_rates_line()
    test_vix_rounding()
    test_vix_daily_move()
    test_high_vs_spot_wording()

    print("\n" + "=" * 70)
    print(f" {len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"   FAILED: {name}")
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
