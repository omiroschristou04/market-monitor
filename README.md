# Market Monitor

A lightweight cross-asset market monitor. It fetches daily price data for a
curated universe of equities, rates, FX, commodities and volatility from
Yahoo Finance, stores each snapshot in SQLite, and generates a styled,
self-contained HTML report with colour-coded performance and 30-day trend
charts.

## Tracked instruments

| Asset class | Instruments |
|-------------|-------------|
| Equity      | S&P 500 (`^GSPC`), Euro Stoxx 50 (`^STOXX50E`), Nikkei 225 (`^N225`) |
| Volatility  | VIX (`^VIX`) |
| Rate        | 10Y US Treasury (`^TNX`), 2Y US Treasury (`2YY=F`) |
| FX          | EUR/USD (`EURUSD=X`), GBP/USD (`GBPUSD=X`), USD/JPY (`JPY=X`) |
| Commodity   | WTI Crude Oil (`CL=F`), Gold (`GC=F`) |

## Project layout

```
market-monitor/
├── data/                 # SQLite database (created at runtime)
├── logs/                 # reserved for log output
├── reports/              # generated HTML reports
├── src/
│   ├── config.py         # ticker universe & paths
│   ├── fetch.py          # yfinance download + metric calculation
│   ├── store.py          # SQLite persistence
│   └── report.py         # HTML + matplotlib report generation
├── run.py                # master pipeline
├── requirements.txt
└── README.md
```

## Setup

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Run the full pipeline:

```bash
python run.py
```

This will:

1. Create the SQLite table (`data/market_data.db`) if needed.
2. Fetch the latest data for every ticker.
3. Store today's snapshot (idempotent — re-running updates the same day).
4. Write a report to `reports/market_report_YYYYMMDD.html`.

Open the generated HTML file in any browser.

## Report contents

- **VIX regime banner** — LOW VOL / NORMAL / ELEVATED / STRESS.
- **Snapshot table** grouped by asset class, with 1D / 1W / 1M / YTD changes
  colour-coded green (up) or red (down), plus the 52-week range.
- **30-day trend charts** for Equity, FX and Commodity, normalised to
  percentage change and embedded inline as base64 PNGs.

## Notes

Data is provided by Yahoo Finance via the `yfinance` package and is intended
for informational purposes only — not investment advice.
