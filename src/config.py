"""Configuration for the market monitor.

Defines the universe of instruments to track, their display metadata,
and the file-system locations used for storage and reporting.
"""

# Yahoo Finance symbol -> display metadata.
# Each entry maps a ticker to a human-readable name and an asset class.
TICKERS = {
    "^GSPC":     {"name": "S&P 500",        "asset_class": "Equity"},
    "^STOXX50E": {"name": "Euro Stoxx 50",  "asset_class": "Equity"},
    "^N225":     {"name": "Nikkei 225",     "asset_class": "Equity"},
    "^FTSE":     {"name": "FTSE 100",       "asset_class": "Equity"},
    "^GDAXI":    {"name": "DAX",            "asset_class": "Equity"},
    "^VIX":      {"name": "VIX",            "asset_class": "Volatility"},
    "^TNX":      {"name": "10Y US Treasury", "asset_class": "Rate"},
    "2YY=F":     {"name": "2Y US Treasury", "asset_class": "Rate"},
    "EURUSD=X":  {"name": "EUR/USD",        "asset_class": "FX"},
    "GBPUSD=X":  {"name": "GBP/USD",        "asset_class": "FX"},
    "JPY=X":     {"name": "USD/JPY",        "asset_class": "FX"},
    "DX-Y.NYB":  {"name": "US Dollar Index", "asset_class": "Macro"},
    "CL=F":      {"name": "WTI Crude Oil",  "asset_class": "Commodity"},
    "GC=F":      {"name": "Gold",           "asset_class": "Commodity"},
    "BTC-USD":   {"name": "Bitcoin",        "asset_class": "Crypto"},
}

# Order in which asset classes are presented in the report.
ASSET_CLASS_ORDER = [
    "Equity", "Volatility", "Rate", "FX", "Macro", "Commodity", "Crypto",
]

# Storage locations (relative to the project root).
DB_PATH = "data/market_data.db"
REPORTS_DIR = "reports"
