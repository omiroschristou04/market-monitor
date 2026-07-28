"""SQLite persistence for the market monitor.

Stores one row per ticker per day in a ``daily_prices`` table. A UNIQUE
constraint on (date, ticker) makes inserts idempotent: re-running on the
same day updates the existing row instead of creating duplicates.
"""

import os
import sqlite3

from .config import DB_PATH


def _connect(db_path=DB_PATH):
    """Open a connection, ensuring the parent directory exists."""
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return sqlite3.connect(db_path)


def create_table(db_path=DB_PATH):
    """Create the daily_prices table if it does not already exist."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_prices (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                date             TEXT    NOT NULL,
                ticker           TEXT    NOT NULL,
                name             TEXT,
                asset_class      TEXT,
                price            REAL,
                change_pct       REAL,
                week_change_pct  REAL,
                month_change_pct REAL,
                ytd_change_pct   REAL,
                high_52w         REAL,
                low_52w          REAL,
                UNIQUE(date, ticker)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_daily_data(rows, db_path=DB_PATH):
    """Insert (or replace) a batch of metric dicts into the database.

    *rows* is the list of dicts produced by ``fetch_all_tickers``. The
    ``history`` field is ignored here — only the scalar metrics are stored.
    Returns the number of rows written.
    """
    conn = _connect(db_path)
    try:
        for r in rows:
            conn.execute(
                """
                INSERT INTO daily_prices (
                    date, ticker, name, asset_class, price, change_pct,
                    week_change_pct, month_change_pct, ytd_change_pct,
                    high_52w, low_52w
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, ticker) DO UPDATE SET
                    name             = excluded.name,
                    asset_class      = excluded.asset_class,
                    price            = excluded.price,
                    change_pct       = excluded.change_pct,
                    week_change_pct  = excluded.week_change_pct,
                    month_change_pct = excluded.month_change_pct,
                    ytd_change_pct   = excluded.ytd_change_pct,
                    high_52w         = excluded.high_52w,
                    low_52w          = excluded.low_52w
                """,
                (
                    r["date"], r["ticker"], r["name"], r["asset_class"],
                    r["price"], r["change_pct"], r["week_change_pct"],
                    r["month_change_pct"], r["ytd_change_pct"],
                    r["52w_high"], r["52w_low"],
                ),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_latest_data(db_path=DB_PATH):
    """Return the most recent stored row for each ticker as a list of dicts."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT d.*
            FROM daily_prices d
            JOIN (
                SELECT ticker, MAX(date) AS max_date
                FROM daily_prices
                GROUP BY ticker
            ) latest
              ON d.ticker = latest.ticker
             AND d.date   = latest.max_date
            ORDER BY d.asset_class, d.name
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
