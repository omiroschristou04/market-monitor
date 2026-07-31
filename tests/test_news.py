"""Tests for headline ordering and 'time ago' labelling.

Two bugs shipped in the live report, both invisible until you read the
timestamps next to each other:

* Headlines were sorted *inside* each feed and then concatenated feed by feed,
  so every Reuters item preceded every BBC item regardless of publication time
  — a BBC story at 13:27 UTC sat below a Reuters one at 12:57.
* The "X ago" labels were stamped once, when the feed was fetched, and the page
  printed its own generation time as naive local wall time with no timezone. On
  a UTC+3 machine that put "Generated 17:30" next to "14:04 UTC · 25m ago".

Run with:  python tests/test_news.py
(No pytest dependency — the project ships without a test runner.)
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import news  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not condition else ""))


# The render clock from the live report: 2026-07-31 14:30 UTC (17:30 local on
# a UTC+3 machine, which is what the footer printed).
NOW = datetime(2026, 7, 31, 14, 30, tzinfo=timezone.utc)


def at(hour, minute, source="Reuters", title=None):
    published = datetime(2026, 7, 31, hour, minute, tzinfo=timezone.utc)
    return {"title": title or f"{source} story {hour:02d}:{minute:02d}",
            "source": source, "url": "https://example.com/x",
            "published": published,
            "published_str": published.strftime("%H:%M UTC"),
            "relative": news._relative_time(published, NOW)}


# Exactly the ten items the live report published, in the order it published
# them: five Reuters, then five BBC. Two BBC items are newer than three of the
# Reuters items above them.
LIVE_ORDER = [
    at(14, 4), at(13, 42), at(13, 41), at(12, 57), at(12, 51),
    at(13, 27, "BBC Business"), at(13, 16, "BBC Business"),
    at(12, 42, "BBC Business"), at(12, 19, "BBC Business"),
    at(11, 19, "BBC Business"),
]


# --------------------------------------------------------------------------- #
# 1. Strict newest-first across sources
# --------------------------------------------------------------------------- #
def test_sort_across_sources():
    print("\n1. Headlines sort strictly by time, across every source")

    times = [h["published_str"] for h in LIVE_ORDER]
    print(f"      as published: {', '.join(times)}")
    check("the shipped order was not sorted",
          times != sorted(times, reverse=True), str(times))

    ordered = news.sort_by_published(LIVE_ORDER)
    stamps = [h["published_str"] for h in ordered]
    print(f"      sorted:       {', '.join(stamps)}")

    check("every item is kept", len(ordered) == len(LIVE_ORDER))
    check("strictly newest-first",
          all(ordered[i]["published"] >= ordered[i + 1]["published"]
              for i in range(len(ordered) - 1)), str(stamps))
    check("the 13:27 BBC item now sits above the 12:57 Reuters one",
          stamps.index("13:27 UTC") < stamps.index("12:57 UTC"))
    check("the 13:16 BBC item now sits above the 12:57 Reuters one",
          stamps.index("13:16 UTC") < stamps.index("12:57 UTC"))
    check("the newest item overall leads", stamps[0] == "14:04 UTC")
    check("the oldest item overall trails", stamps[-1] == "11:19 UTC")

    # Sources must interleave now, not run in blocks.
    sources = [h["source"] for h in ordered]
    blocks = sum(1 for i in range(len(sources) - 1) if sources[i] != sources[i + 1])
    print(f"      source runs:  {' '.join('R' if s == 'Reuters' else 'B' for s in sources)}")
    check("sources interleave rather than grouping", blocks > 1, f"{blocks} switches")

    # Robustness: an item with no parsed time must not crash the sort or
    # displace a dated one.
    mixed = news.sort_by_published(LIVE_ORDER + [{"title": "undated",
                                                  "source": "Reuters",
                                                  "url": "https://example.com/u"}])
    check("an undated item sorts last, and is not dropped",
          len(mixed) == 11 and mixed[-1]["title"] == "undated")
    check("sorting is stable for equal timestamps",
          [h["title"] for h in news.sort_by_published([at(9, 0, title="a"),
                                                       at(9, 0, title="b")])]
          == ["a", "b"])


# --------------------------------------------------------------------------- #
# 2. Relative labels reconcile with the UTC stamp beside them
# --------------------------------------------------------------------------- #
def test_relative_labels():
    print("\n2. 'X ago' agrees with the UTC stamp and the render clock")

    check("25 minutes reads as minutes",
          news._relative_time(NOW - timedelta(minutes=25), NOW) == "25m ago")
    check("under a minute reads as just now",
          news._relative_time(NOW - timedelta(seconds=20), NOW) == "just now")
    check("hours round down",
          news._relative_time(NOW - timedelta(minutes=119), NOW) == "1h ago")
    check("days roll over",
          news._relative_time(NOW - timedelta(hours=26), NOW) == "1d ago")

    # The report renders after the fetch, so labels are recomputed against the
    # render clock. Six minutes later, "25m ago" must have become "31m ago".
    items = [dict(h) for h in LIVE_ORDER]
    later = NOW + timedelta(minutes=6)
    news.refresh_relative_times(items, later)

    for h in items[:3]:
        print(f"      {h['published_str']}  {h['relative']}")

    check("the fetch-time label was 26m ago",
          LIVE_ORDER[0]["relative"] == "26m ago", LIVE_ORDER[0]["relative"])
    check("labels are recomputed against the render clock, not the fetch",
          items[0]["relative"] == "32m ago", items[0]["relative"])

    # Every label must reconcile with its own stamp against the same clock.
    bad = []
    for h in items:
        expected = news._relative_time(h["published"], later)
        if h["relative"] != expected:
            bad.append(f"{h['published_str']}: {h['relative']} != {expected}")
    check("every label reconciles with its UTC stamp", not bad, "; ".join(bad))

    check("stamps stay in UTC and are labelled",
          all(h["published_str"].endswith(" UTC") for h in items))
    check("the stamp is rewritten from the same instant as the label",
          all(h["published_str"] == h["published"].strftime("%H:%M UTC")
              for h in items))

    # A naive datetime must be read as UTC, not as local wall time — that
    # assumption is exactly what made the labels unreconcilable.
    naive = [{"title": "n", "source": "Reuters", "url": "u",
              "published": datetime(2026, 7, 31, 14, 4)}]
    news.refresh_relative_times(naive, later)
    check("a naive publish time is treated as UTC",
          naive[0]["relative"] == "32m ago" and naive[0]["published_str"] == "14:04 UTC",
          f"{naive[0]['relative']} / {naive[0]['published_str']}")

    # Items the parser could not date are left alone rather than mislabelled.
    undated = [{"title": "u", "source": "Reuters", "url": "u",
                "relative": "2h ago", "published_str": "07:14 UTC"}]
    news.refresh_relative_times(undated, later)
    check("an undated item keeps its existing label",
          undated[0]["relative"] == "2h ago")

    # No label may claim a story from the future.
    check("a future timestamp does not produce a negative label",
          news._relative_time(NOW + timedelta(minutes=5), NOW) == "just now")


# --------------------------------------------------------------------------- #
# 3. The per-source cap still keeps each feed's freshest items
# --------------------------------------------------------------------------- #
def test_per_source_cap():
    print("\n3. Per-source capping still takes the newest of each feed")

    feed = [at(9, 0), at(14, 4), at(11, 30), at(13, 42), at(10, 15), at(12, 51)]
    kept = news.sort_by_published(feed)[:news.MAX_PER_SOURCE]
    stamps = [h["published_str"] for h in kept]
    print(f"      kept: {', '.join(stamps)}")
    check(f"keeps {news.MAX_PER_SOURCE} items", len(kept) == news.MAX_PER_SOURCE)
    check("keeps the newest, drops the oldest",
          "14:04 UTC" in stamps and "09:00 UTC" not in stamps, str(stamps))


def main():
    print("=" * 70)
    print(" NEWS TESTS — cross-source ordering + UTC 'time ago' labels")
    print("=" * 70)
    test_sort_across_sources()
    test_relative_labels()
    test_per_source_cap()

    print("\n" + "=" * 70)
    print(f" {len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"   FAILED: {name}")
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
