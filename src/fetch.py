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


def _value_n_sessions_ago(closes, n):
    """Close price n trading sessions before the last available one."""
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

    return {
        "ticker": symbol,
        "name": meta["name"],
        "asset_class": meta["asset_class"],
        "date": closes.index[-1].strftime("%Y-%m-%d"),
        "price": last,
        "change_pct": _pct_change(last, prev),
        "week_change_pct": _pct_change(last, week_ago),
        "month_change_pct": _pct_change(last, month_ago),
        "ytd_change_pct": _pct_change(last, ytd_ref),
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
