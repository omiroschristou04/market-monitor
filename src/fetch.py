"""Data fetching for the market monitor.

Uses yfinance to download recent price history for every ticker in the
configured universe, then derives a set of point-in-time performance
metrics (daily / weekly / monthly / year-to-date change, 52-week range).

The raw close-price history is kept on each result dict so the report
layer can draw trend charts without re-downloading anything.
"""

from datetime import datetime

import pandas as pd
import yfinance as yf

from .config import TICKERS

# Share of sessions repeating the previous close above which a series is
# treated as a stale, forward-filled feed.
#
# Yahoo forward-fills thinly-traded contracts. Measured across this universe,
# the share of sessions whose close merely repeats the previous one is 0-2.4%
# for fourteen of the fifteen instruments and 70.4% for 2YY=F, the 2Y yield
# future — which also carries occasional bad ticks (2026-06-30 printed 4.130
# between neighbours of 3.856 and 3.896). When the reference bar for a horizon
# lands on one of those repeats or ticks and happens to equal the latest close,
# the horizon computes to *exactly* +0.00% and the report presents a feed
# artefact as a confident reading. On a feed this stale that exact zero is not
# an observation, so the horizon is reported as unavailable instead.
STALE_SERIES_AT = 0.50


def _pct_change(current, previous):
    """Percentage change from *previous* to *current*, or None if undefined."""
    if previous is None or current is None:
        return None
    try:
        if previous == 0 or pd.isna(previous) or pd.isna(current):
            return None
        return (current - previous) / previous * 100.0
    except (TypeError, ZeroDivisionError):
        return None


def _stale_share(closes):
    """Share of sessions whose close merely repeats the previous session's.

    A proxy for how much of a series is genuine observation and how much is
    forward fill. See :data:`STALE_SERIES_AT`.
    """
    values = [float(v) for v in closes]
    if len(values) < 2:
        return 0.0
    repeats = sum(1 for i in range(1, len(values)) if values[i] == values[i - 1])
    return repeats / (len(values) - 1)


def _horizon_change(current, previous, stale_share):
    """Multi-session percentage change, or None when it cannot be trusted.

    Same arithmetic as :func:`_pct_change` with one extra guard: on a stale
    feed an *exactly* zero change over several sessions means the reference bar
    is a repeated or glitched print rather than a distinct observation, so
    there is no meaningful answer and the caller renders "n/a".

    The 1-day change deliberately does not use this — "unchanged on the day"
    is a real and expected reading, including on a thin contract.
    """
    change = _pct_change(current, previous)
    if change == 0.0 and stale_share >= STALE_SERIES_AT:
        return None
    return change


def _value_n_sessions_ago(closes, n):
    """Close price n trading sessions before the last available one.

    None when the series is too short to reach back that far, which the report
    renders as "n/a" rather than inventing a number.
    """
    if len(closes) > n:
        return float(closes.iloc[-(n + 1)])
    return None


def _ytd_reference(closes):
    """First close of the current calendar year, used as the YTD baseline."""
    current_year = closes.index[-1].year
    this_year = closes[closes.index.year == current_year]
    if not this_year.empty:
        return float(this_year.iloc[0])
    return None


def fetch_ticker(symbol, meta):
    """Download history for a single symbol and compute its metrics.

    Returns a dict of metrics plus the raw close history, or None if no
    usable data came back.
    """
    ticker = yf.Ticker(symbol)
    # One year of daily data covers the 52-week range and every change window.
    history = ticker.history(period="1y", interval="1d", auto_adjust=False)

    if history is None or history.empty or "Close" not in history.columns:
        return None

    closes = history["Close"].dropna()
    if closes.empty:
        return None

    last = float(closes.iloc[-1])
    prev = _value_n_sessions_ago(closes, 1)
    week_ago = _value_n_sessions_ago(closes, 5)      # ~1 trading week
    month_ago = _value_n_sessions_ago(closes, 21)    # ~1 trading month
    ytd_ref = _ytd_reference(closes)
    stale_share = _stale_share(closes)

    return {
        "ticker": symbol,
        "name": meta["name"],
        "asset_class": meta["asset_class"],
        "date": closes.index[-1].strftime("%Y-%m-%d"),
        "price": last,
        "change_pct": _pct_change(last, prev),
        "week_change_pct": _horizon_change(last, week_ago, stale_share),
        "month_change_pct": _horizon_change(last, month_ago, stale_share),
        "ytd_change_pct": _horizon_change(last, ytd_ref, stale_share),
        "stale_share": stale_share,
        "52w_high": float(closes.max()),
        "52w_low": float(closes.min()),
        # Raw history retained for charting (list of (date_str, close) tuples).
        "history": [(idx.strftime("%Y-%m-%d"), float(val))
                    for idx, val in closes.items()],
    }


def fetch_all_tickers():
    """Fetch and compute metrics for the entire configured universe.

    Returns a list of result dicts. Tickers that fail to return data are
    skipped with a warning rather than aborting the whole run.
    """
    results = []
    for symbol, meta in TICKERS.items():
        try:
            data = fetch_ticker(symbol, meta)
            if data is None:
                print(f"  ! No data returned for {symbol} ({meta['name']})")
                continue
            results.append(data)
            print(f"  - {meta['name']:<16} {data['price']:>12,.2f}  "
                  f"({data['change_pct']:+.2f}%)" if data['change_pct'] is not None
                  else f"  - {meta['name']:<16} {data['price']:>12,.2f}")
        except Exception as exc:  # network / parsing issues per ticker
            print(f"  ! Failed to fetch {symbol} ({meta['name']}): {exc}")
    return results
