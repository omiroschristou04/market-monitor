"""Market Monitor — master run script.

Runs the full pipeline end to end:
    1. ensure the database table exists
    2. fetch the latest data for every configured ticker
    3. persist the snapshot to SQLite
    4. fetch the latest financial news headlines
    5. generate the styled HTML report
    6. copy today's report to the Desktop (clearing older copies)
    7. publish the report to GitHub Pages (copy to docs/ + git push)
    8. email a clean summary briefing (with a link to the full report) via Gmail SMTP

Usage:
    python run.py
"""

import glob
import os
import shutil
import sys
import time

from src.email_report import send_email
from src.fetch import fetch_all_tickers
from src.news import fetch_headlines
from src.publish import publish_to_github
from src.report import generate_report
from src.store import create_table, insert_daily_data

# Where the finished report should land each morning.
DESKTOP_DIR = r"C:\Users\ALEX\Desktop"


def copy_to_desktop(report_path, desktop_dir=DESKTOP_DIR):
    """Copy today's report to the Desktop, removing any prior day's copy.

    Only one ``market_report_*.html`` is left on the Desktop at a time, so
    each morning the latest briefing is the only one waiting there.
    Returns the destination path, or None if the copy could not be made.
    """
    if not os.path.isdir(desktop_dir):
        print(f"      ! Desktop not found at {desktop_dir}; skipping copy.")
        return None

    filename = os.path.basename(report_path)
    destination = os.path.join(desktop_dir, filename)

    # Remove previous reports on the Desktop (anything but today's file).
    removed = 0
    for old in glob.glob(os.path.join(desktop_dir, "market_report_*.html")):
        if os.path.abspath(old) != os.path.abspath(destination):
            try:
                os.remove(old)
                removed += 1
            except OSError as exc:
                print(f"      ! Could not remove old report {old}: {exc}")
    if removed:
        print(f"      Removed {removed} previous report(s) from the Desktop.")

    shutil.copy2(report_path, destination)
    return destination


def main():
    start = time.time()
    print("=" * 60)
    print(" MARKET MONITOR")
    print("=" * 60)

    # 1. Database
    print("\n[1/8] Preparing database...")
    create_table()
    print("      Database ready.")

    # 2. Fetch market data
    print("\n[2/8] Fetching market data from Yahoo Finance...")
    rows = fetch_all_tickers()
    if not rows:
        print("\n  ! No data was fetched. Aborting.")
        return 1
    print(f"      Fetched {len(rows)} instrument(s).")

    # 3. Store
    print("\n[3/8] Storing snapshot...")
    written = insert_daily_data(rows)
    print(f"      Wrote {written} row(s) to the database.")

    # 4. Fetch news
    print("\n[4/8] Fetching financial news headlines...")
    headlines = fetch_headlines()
    print(f"      Collected {len(headlines)} headline(s).")

    # 5. Report
    print("\n[5/8] Generating HTML report...")
    report_path = generate_report(rows, headlines=headlines)
    print(f"      Report written to: {report_path}")

    # 6. Copy to Desktop
    print("\n[6/8] Copying report to Desktop...")
    desktop_path = copy_to_desktop(report_path)
    if desktop_path:
        print(f"      Report copied to: {desktop_path}")

    # 7. Publish to GitHub Pages — soft-skips if git/remote is not set up.
    print("\n[7/8] Publishing report to GitHub Pages...")
    publish_to_github(report_path)

    # 8. Email a clean summary — soft-skips if .env is not configured.
    print("\n[8/8] Emailing report via Gmail SMTP...")
    send_email(report_path, rows=rows)

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f" Done in {elapsed:.1f}s")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
