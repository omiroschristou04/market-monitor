"""Tests for the metric arithmetic in fetch, and the stale-feed guard.

Guards the 2Y Treasury bug: the snapshot showed "1M +0.00%" for 2YY=F while
every other horizon on that row was positive, which read as a placeholder. The
series was not short — it returned a full 251 sessions — but Yahoo forward-fills
that thinly-traded contract, so 70% of its closes merely repeat the previous
one and it carries occasional bad ticks. The 1-month reference bar landed on
one of those ticks (4.130 sitting between neighbours of 3.856 and 3.896), which
happened to equal the latest close, so the horizon computed to exactly +0.00%.

Run with:  python tests/test_fetch.py
(No pytest dependency — the project ships without a test runner.)
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import fetch  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not condition else ""))


def series(values):
    """A close series indexed by consecutive business days."""
    idx = pd.date_range("2025-08-01", periods=len(values), freq="B")
    return pd.Series([float(v) for v in values], index=idx)


# The real 2YY=F tail as returned by yfinance on 2026-07-30, from the session
# 22 back through the last: the 4.130 print on 2026-06-30 sits between 3.856
# and 3.896, and equals the final close.
TWO_Y_TAIL = [
    3.851, 3.851, 3.856, 3.856, 4.130, 3.896, 3.896, 3.896, 3.910, 3.921,
    3.939, 3.958, 3.965, 3.966, 3.961, 3.987, 3.997, 4.014, 4.027, 4.033,
    4.035, 4.051, 4.062, 4.086, 4.086, 4.130,
]

# Staleness is measured over the whole year the fetcher downloads, not the last
# month, so the fixture has to carry the earlier sessions too — each close held
# for several days, which is what forward filling looks like. Combined with the
# tail this lands at ~70% repeated closes, matching the live feed.
TWO_Y_PREFIX = [round(3.376 + i * 0.002, 3) for i in range(56) for _ in range(4)]
TWO_Y_YEAR = TWO_Y_PREFIX + TWO_Y_TAIL


# --------------------------------------------------------------------------- #
# 1. Staleness measurement
# --------------------------------------------------------------------------- #
def test_stale_share():
    print("\n1. Stale-feed detection")

    check("a clean series is not stale",
          fetch._stale_share(series([1, 2, 3, 4, 5])) == 0.0)
    check("a fully repeated series is completely stale",
          fetch._stale_share(series([4.0] * 10)) == 1.0)
    check("half-repeated reads 0.5",
          abs(fetch._stale_share(series([1, 1, 2, 2, 3])) - 0.5) < 1e-9,
          str(fetch._stale_share(series([1, 1, 2, 2, 3]))))
    check("a one-point series is not stale",
          fetch._stale_share(series([1.0])) == 0.0)
    check("an empty series is not stale", fetch._stale_share(series([])) == 0.0)

    # A live-quoted index never trips the threshold; the 2Y future does. The
    # measured shares were 0-2.4% across the universe against 70.4% for 2YY=F.
    clean = series([100 + i * 0.3 for i in range(60)])
    thin = series([3.85 + i * 0.002 for i in range(15) for _ in range(4)])
    print(f"      clean feed {fetch._stale_share(clean):.1%}, "
          f"thin feed {fetch._stale_share(thin):.1%}, "
          f"threshold {fetch.STALE_SERIES_AT:.0%}")
    check("a live-quoted series stays under the threshold",
          fetch._stale_share(clean) < fetch.STALE_SERIES_AT)
    check("a forward-filled series clears the threshold",
          fetch._stale_share(thin) >= fetch.STALE_SERIES_AT)


# --------------------------------------------------------------------------- #
# 2. The horizon guard: n/a, never a misleading +0.00%
# --------------------------------------------------------------------------- #
def test_horizon_guard():
    print("\n2. An exact zero on a stale feed becomes n/a")

    stale, live = 0.70, 0.0

    check("exactly flat on a stale feed is unavailable",
          fetch._horizon_change(4.130, 4.130, stale) is None)
    check("exactly flat on a live feed is still reported as 0.00%",
          fetch._horizon_change(100.0, 100.0, live) == 0.0)
    check("a real move on a stale feed is still reported",
          abs(fetch._horizon_change(4.130, 4.035, stale) - 2.3544) < 1e-3,
          str(fetch._horizon_change(4.130, 4.035, stale)))
    check("a tiny non-zero move on a stale feed survives",
          fetch._horizon_change(4.130, 4.129, stale) is not None)
    check("a missing reference is unavailable either way",
          fetch._horizon_change(4.130, None, stale) is None
          and fetch._horizon_change(4.130, None, live) is None)
    check("a zero reference is unavailable",
          fetch._horizon_change(4.130, 0.0, stale) is None)
    check("the guard sits exactly at the threshold",
          fetch._horizon_change(1.0, 1.0, fetch.STALE_SERIES_AT) is None
          and fetch._horizon_change(1.0, 1.0, fetch.STALE_SERIES_AT - 1e-9) == 0.0)


# --------------------------------------------------------------------------- #
# 3. Short history still yields n/a rather than an invented number
# --------------------------------------------------------------------------- #
def test_short_history():
    print("\n3. Too little history reports n/a")

    short = series([1.0, 1.1, 1.2])          # 3 sessions
    check("1 session back is reachable", fetch._value_n_sessions_ago(short, 1) == 1.1)
    check("5 sessions back is not", fetch._value_n_sessions_ago(short, 5) is None)
    check("21 sessions back is not", fetch._value_n_sessions_ago(short, 21) is None)
    check("an unreachable reference gives no percentage",
          fetch._pct_change(1.2, fetch._value_n_sessions_ago(short, 21)) is None)
    check("the boundary is exclusive",
          fetch._value_n_sessions_ago(series([1.0, 2.0]), 1) == 1.0
          and fetch._value_n_sessions_ago(series([1.0, 2.0]), 2) is None)


# --------------------------------------------------------------------------- #
# 4. The reported 2Y row, end to end
# --------------------------------------------------------------------------- #
def test_two_year_row():
    print("\n4. The 2Y row that shipped the bug")

    closes = series(TWO_Y_YEAR)
    stale_share = fetch._stale_share(closes)
    last = float(closes.iloc[-1])
    month_ago = fetch._value_n_sessions_ago(closes, 21)
    week_ago = fetch._value_n_sessions_ago(closes, 5)
    prev = fetch._value_n_sessions_ago(closes, 1)

    print(f"      last {last}, 1M reference {month_ago}, "
          f"stale share {stale_share:.0%}")

    check("the 1-month reference is the 4.130 bad tick", month_ago == 4.130)
    check("it equals the latest close exactly", month_ago == last)
    check("the raw arithmetic really is +0.00%",
          fetch._pct_change(last, month_ago) == 0.0)

    # The measured share on the live feed was 70.4%; this fixture reproduces it.
    check("the year of data reads as a stale feed",
          stale_share >= fetch.STALE_SERIES_AT, f"{stale_share:.1%}")
    check("the fixture matches the live feed's staleness",
          abs(stale_share - 0.704) < 0.05, f"{stale_share:.1%} vs 70.4%")

    month = fetch._horizon_change(last, month_ago, stale_share)
    check("the 1-month horizon is reported as unavailable", month is None, str(month))

    # The horizons that are genuinely measurable must survive untouched.
    week = fetch._horizon_change(last, week_ago, stale_share)
    day = fetch._pct_change(last, prev)
    print(f"      1D {day:+.2f}%, 1W {week:+.2f}%, 1M {month}")
    check("the 1-week horizon is unaffected", week is not None and week > 0, str(week))
    check("the 1-day change is unaffected", day is not None and day > 0, str(day))
    check("the guard does not blank the whole row",
          sum(v is None for v in (day, week, month)) == 1)

    # "Last equals the 52-week high" is a true statement about this data, not a
    # placeholder: today's close is the highest close in the series.
    check("last really is the highest close in the series",
          max(TWO_Y_YEAR) == last)


# --------------------------------------------------------------------------- #
# 5. A normal instrument is untouched by any of this
# --------------------------------------------------------------------------- #
def test_live_instrument_unaffected():
    print("\n5. Live-quoted instruments are unaffected")

    closes = series([7000 + i * 12.5 for i in range(40)])
    stale_share = fetch._stale_share(closes)
    last = float(closes.iloc[-1])
    for n, label in ((1, "1D"), (5, "1W"), (21, "1M")):
        ref = fetch._value_n_sessions_ago(closes, n)
        value = fetch._horizon_change(last, ref, stale_share)
        check(f"{label} is computed normally", value is not None and value > 0,
              str(value))
    check("no staleness detected", stale_share == 0.0)


def main():
    print("=" * 70)
    print(" FETCH TESTS — horizon metrics + stale-feed guard")
    print("=" * 70)
    test_stale_share()
    test_horizon_guard()
    test_short_history()
    test_two_year_row()
    test_live_instrument_unaffected()

    print("\n" + "=" * 70)
    print(f" {len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"   FAILED: {name}")
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
