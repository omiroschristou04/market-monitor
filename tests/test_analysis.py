"""Tests for the regime classifier and the narrative consistency guard.

Run with:  python tests/test_analysis.py
(No pytest dependency — the project ships without a test runner.)
"""

import os
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
              btc=0.20, spx_price=7428.78, spx_high=7609.78, spx_low=6238.01):
    """Full universe with sane defaults = the day the bug was reported."""
    return [
        _row("^GSPC", "S&P 500", "Equity", spx_price, spx, spx_high, spx_low, 8.3),
        _row("^STOXX50E", "Euro Stoxx 50", "Equity", 6282.21, stoxx, 6412.68, 5242.32, 6.0),
        _row("^N225", "Nikkei 225", "Equity", 64931.19, nikkei, 72366.34, 40290.70, 25.3),
        _row("^FTSE", "FTSE 100", "Equity", 10871.02, ftse, 10910.60, 9068.60, 9.2),
        _row("^GDAXI", "DAX", "Equity", 25361.03, dax, 25817.89, 22300.75, 3.3),
        _row("^VIX", "VIX", "Volatility", vix, -2.46),
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

    print("\n" + "=" * 70)
    print(f" {len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"   FAILED: {name}")
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
