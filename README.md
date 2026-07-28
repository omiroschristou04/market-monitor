<div align="center">

# 📈 Market Monitor

**An automated cross-asset market intelligence platform that fetches, stores, analyses and publishes a daily institutional-style morning briefing — with zero manual input.**

[![Live Report](https://img.shields.io/badge/📊_Live_Report-View_Today's_Briefing-0b2545?style=for-the-badge&labelColor=1e3a5f)](https://omiroschristou04.github.io/market-monitor/)

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/SQLite-Time_Series_Store-003B57?style=flat-square&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Pandas](https://img.shields.io/badge/Pandas-Data_Analysis-150458?style=flat-square&logo=pandas&logoColor=white)](https://pandas.pydata.org/)
[![Matplotlib](https://img.shields.io/badge/Matplotlib-Charting-11557C?style=flat-square&logo=python&logoColor=white)](https://matplotlib.org/)
[![yfinance](https://img.shields.io/badge/yfinance-Market_Data-6001D2?style=flat-square&logo=yahoo&logoColor=white)](https://pypi.org/project/yfinance/)
[![GitHub Pages](https://img.shields.io/badge/GitHub_Pages-Auto_Published-222222?style=flat-square&logo=githubpages&logoColor=white)](https://omiroschristou04.github.io/market-monitor/)

*15 instruments · 7 asset classes · 10 report sections · delivered every weekday at 06:30 London time*

</div>

---

## What it does

- **Tracks 15 instruments across 7 asset classes** — global equity indices, volatility, US rates, G10 FX, the dollar index, commodities and crypto — in a single consolidated view.
- **Computes point-in-time performance metrics** for every instrument: 1-day, 1-week, 1-month and year-to-date change, plus the 52-week trading range.
- **Builds a time-series history** in SQLite, with a `UNIQUE(date, ticker)` constraint making every run idempotent — re-running the same day updates rather than duplicates.
- **Derives analyst-grade signals, not just numbers** — market regime classification, 2s10s curve read, drawdown-from-high tracking, S&P key levels and a cross-asset correlation note.
- **Writes a plain-English morning briefing** in the voice of a sell-side analyst, translating the day's price action into what actually matters.
- **Pulls live financial headlines** from Reuters and BBC Business via RSS, filtered to the last 24 hours and rendered with Open Graph thumbnails.
- **Publishes automatically to GitHub Pages** and emails a mobile-friendly summary over Gmail SMTP — the full pipeline runs unattended via Windows Task Scheduler.
- **Fails soft, never hard** — a dead ticker, missing RSS feed, absent git remote or unconfigured mailbox degrades that one step and lets the briefing ship anyway.

---

## Tech Stack

| Layer | Technology | Role |
|---|---|---|
| **Language** | Python 3.10+ | Pipeline orchestration and analytics |
| **Market data** | `yfinance` | 1-year daily OHLC history per instrument |
| **Analysis** | `pandas` | Return windows, YTD baselines, 52-week ranges |
| **Persistence** | `SQLite` | Local time-series store, idempotent daily snapshots |
| **Charting** | `matplotlib` | Normalised 30-day trend charts, embedded as base64 PNGs |
| **News** | `feedparser` + `requests` | RSS ingestion and Open Graph thumbnail scraping |
| **Delivery** | `smtplib` (Gmail STARTTLS) + GitHub Pages | Email briefing and public live report |
| **Config** | `python-dotenv` | Secrets kept in a local `.env`, never committed |
| **Automation** | Windows Task Scheduler (PowerShell) | Weekday 06:30 UK-time trigger, DST-aware |

The report is a **single self-contained HTML file** — every chart and style is inlined, so it opens anywhere with no server, no build step and no external assets.

---

## Features

The generated briefing is composed of ten sections, in reading order:

| # | Section | What it shows |
|---|---|---|
| 1 | **Morning Briefing** | Market-regime badge (Risk-On / Risk-Off / Transitional / Stress) with a Goldman-style opening note and seven analyst bullets |
| 2 | **Markets Snapshot** | All 15 instruments grouped by asset class — price, 1D / 1W / 1M / YTD change colour-coded green/red, plus the 52-week range |
| 3 | **Yield Curve · 2s10s** | The 2s10s spread, its direction, and whether the curve is steepening, flattening or inverted |
| 4 | **Drawdown Tracker** | Every equity index measured against its own 52-week high, ranked by distance from peak |
| 5 | **Key Levels to Watch** | S&P 500 support and resistance levels derived from the trailing range |
| 6 | **Cross-Asset Correlation Note** | Whether equities, rates, gold and the dollar are moving together or diverging — and what that implies |
| 7 | **Analyst Notebook** | "What a GS analyst writes in their notebook this morning" — the day distilled into trader shorthand |
| 8 | **Analyst Decision Framework** | The explicit if-this-then-that logic linking today's signals to positioning bias |
| 9 | **30-Day Trends** | Normalised percentage-change charts for the equity, FX and commodity complexes |
| 10 | **Headlines** | Top Reuters and BBC Business stories from the last 24 hours with thumbnails and source links |

Presentation details: dark-navy institutional styling, scroll-triggered reveal animations, count-up numerics, and a fully responsive layout that reads cleanly on a phone.

---

## How it works

```
                          ┌───────────────────────┐
                          │     Yahoo Finance     │
                          │  15 tickers · 1y OHLC │
                          └───────────┬───────────┘
                                      │  yfinance
                                      ▼
                        ┌─────────────────────────┐
                        │        fetch.py         │
                        │  1D / 1W / 1M / YTD %   │
                        │  52-week high & low     │
                        └───────────┬─────────────┘
                                    │  metric dicts
                     ┌──────────────┴───────────────┐
                     ▼                              ▼
        ┌─────────────────────────┐    ┌─────────────────────────┐
        │        store.py         │    │       analysis.py       │
        │  SQLite: daily_prices   │    │  regime · 2s10s · draw- │
        │  UNIQUE(date, ticker)   │    │  downs · levels · corr  │
        │   → idempotent writes   │    └───────────┬─────────────┘
        └─────────────────────────┘                │
                     ▲                             │  signals
                     │  history                    ▼
                     │                 ┌─────────────────────────┐
        ┌─────────────────────────┐    │        report.py        │
        │        news.py          │───▶│  10 sections · inline   │
        │  Reuters + BBC RSS 24h  │    │  matplotlib base64 PNGs │
        └─────────────────────────┘    │  → self-contained HTML  │
                                       └───────────┬─────────────┘
                                                   │
                        ┌──────────────────────────┼──────────────────────────┐
                        ▼                          ▼                          ▼
             ┌────────────────────┐     ┌────────────────────┐     ┌────────────────────┐
             │     publish.py     │     │  email_report.py   │     │    Desktop copy    │
             │  docs/index.html   │     │  Gmail SMTP · TLS  │     │  latest only, old  │
             │  git commit + push │     │  summary + CTA     │     │  copies cleared    │
             └─────────┬──────────┘     └─────────┬──────────┘     └────────────────────┘
                       ▼                          ▼
              ┌────────────────┐          ┌────────────────┐
              │  GitHub Pages  │          │      Inbox     │
              │   LIVE SITE    │          │   06:30 (UK)   │
              └────────────────┘          └────────────────┘

  Orchestrated end to end by run.py — 8 stages, every external step soft-skips on failure.
  Triggered weekday mornings by schedule_task.ps1 (Windows Task Scheduler, DST-aware).
```

---

## Setup

**Requirements:** Python 3.10 or newer.

```bash
# 1. Clone
git clone https://github.com/omiroschristou04/market-monitor.git
cd market-monitor

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the pipeline
python run.py
```

That's it — the report lands in `reports/market_report_YYYYMMDD.html` and opens in any browser. Database creation, data fetching and chart generation all happen on the first run.

### Optional — email and auto-publish

Copy `.env.example` to `.env` and fill in your values:

```ini
GMAIL_ADDRESS=youraddress@gmail.com
GMAIL_APP_PASSWORD=your16charapppassword   # Gmail App Password, spaces removed
EMAIL_RECIPIENT=recipient@example.com      # optional, defaults to sender
PAGES_URL=https://yourusername.github.io/market-monitor/
```

Gmail requires an [App Password](https://myaccount.google.com/apppasswords) with 2-Step Verification enabled — a normal password will not authenticate. Both email and GitHub Pages publishing are optional: leave `.env` unconfigured and the pipeline simply skips those stages.

### Optional — schedule it

```powershell
# Run PowerShell as Administrator
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\schedule_task.ps1
```

Registers a Windows Scheduled Task firing every weekday at **06:30 Europe/London**, converted to local machine time automatically.

### Project layout

```
market-monitor/
├── data/                    # SQLite database (created at runtime)
├── docs/                    # GitHub Pages root — index.html served live
├── reports/                 # Archive of generated daily reports
├── src/
│   ├── config.py            # Ticker universe & asset-class ordering
│   ├── fetch.py             # yfinance download + metric calculation
│   ├── store.py             # SQLite persistence (idempotent)
│   ├── analysis.py          # Regime, curve, drawdowns, levels, correlation
│   ├── news.py              # RSS ingestion + Open Graph thumbnails
│   ├── report.py            # HTML + matplotlib report generation
│   ├── publish.py           # Copy to docs/ + git commit & push
│   └── email_report.py      # Gmail SMTP summary briefing
├── run.py                   # Master 8-stage pipeline
├── schedule_task.ps1        # Windows Task Scheduler registration
└── requirements.txt
```

---

## Sample Output

The report is regenerated and published every weekday morning — this link always shows the most recent briefing:

### 👉 **[View the live report](https://omiroschristou04.github.io/market-monitor/)**

Historical briefings are archived in [`reports/`](reports/), each a standalone HTML file that renders offline with no dependencies. A companion summary email — regime badge, VIX level, the seven analyst bullets and a link through to the full report — lands in the inbox at the same time.

---

## Instrument universe

| Asset class | Instruments |
|---|---|
| **Equity** | S&P 500 `^GSPC` · Euro Stoxx 50 `^STOXX50E` · Nikkei 225 `^N225` · FTSE 100 `^FTSE` · DAX `^GDAXI` |
| **Volatility** | VIX `^VIX` |
| **Rates** | 10Y US Treasury `^TNX` · 2Y US Treasury `2YY=F` |
| **FX** | EUR/USD `EURUSD=X` · GBP/USD `GBPUSD=X` · USD/JPY `JPY=X` |
| **Macro** | US Dollar Index `DX-Y.NYB` |
| **Commodities** | WTI Crude `CL=F` · Gold `GC=F` |
| **Crypto** | Bitcoin `BTC-USD` |

---

<div align="center">

*Market data sourced from Yahoo Finance via `yfinance`. For informational and educational purposes only — not investment advice.*

---

**Built by Omiros Christou** · *Finance & Actuarial Science Student*

</div>
