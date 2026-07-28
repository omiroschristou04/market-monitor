"""Financial news headlines via RSS.

Pulls recent business / markets headlines from two working sources using
feedparser, keeps the top N per source, and filters to items published in
the last 24 hours. Each returned headline is a plain dict the report layer
can render directly.

Sources
-------
* BBC Business — official RSS feed (works directly).
* Reuters      — Reuters discontinued its own public RSS feeds (the old
                 feeds.reuters.com no longer resolves), so we surface genuine
                 Reuters articles via a Google News RSS query scoped to
                 site:reuters.com. Links open the article on reuters.com.
"""

import calendar
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import feedparser

try:
    import requests
except ImportError:  # requests is optional — without it we just skip thumbnails
    requests = None

# RSS feeds to pull from. Order here is the order shown in the report.
FEEDS = [
    {
        "source": "Reuters",
        # Google News RSS scoped to Reuters business/markets, last 24h.
        "url": ("https://news.google.com/rss/search?"
                "q=when:24h+site:reuters.com+(business+OR+markets+OR+economy)"
                "&hl=en-US&gl=US&ceid=US:en"),
    },
    {
        "source": "BBC Business",
        "url": "http://feeds.bbci.co.uk/news/business/rss.xml",
    },
]

MAX_PER_SOURCE = 5
MAX_AGE_HOURS = 24

# A realistic desktop-browser user agent — some feeds and article pages reject
# the default feedparser/requests agent.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Full browser-like header set used when scraping article pages for thumbnails.
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

# Cookie that skips Google's EU consent interstitial (so redirects resolve).
_CONSENT_COOKIES = {"CONSENT": "YES+", "SOCS": "CAI"}


def _entry_time(entry):
    """Return an entry's publish time as a UTC datetime, or None.

    feedparser exposes parsed times as time.struct_time in UTC, so we use
    calendar.timegm (which treats the struct as UTC) rather than time.mktime
    (which would assume local time).
    """
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


def _clean_title(title, source):
    """Strip the trailing ' - Source' that Google News appends to titles."""
    title = title.strip()
    # Remove a trailing " - <something>" (e.g. " - Reuters").
    title = re.sub(r"\s+-\s+[^-]+$", "", title) if " - " in title else title
    return title.strip()


# --- Thumbnail meta-tag patterns ------------------------------------------- #
# og:image (and its secure_url variant) in either attribute order.
_OG_PATTERNS = [
    re.compile(
        r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url|:url)?["\'][^>]*'
        r'content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*'
        r'(?:property|name)=["\']og:image(?::secure_url|:url)?["\']',
        re.IGNORECASE,
    ),
]
# twitter:image / twitter:image:src in either attribute order.
_TWITTER_PATTERNS = [
    re.compile(
        r'<meta[^>]+(?:name|property)=["\']twitter:image(?::src)?["\'][^>]*'
        r'content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*'
        r'(?:name|property)=["\']twitter:image(?::src)?["\']',
        re.IGNORECASE,
    ),
]
# Any <img ...> tag (we then inspect its attributes for a usable large image).
_IMG_TAG = re.compile(r'<img\b[^>]*>', re.IGNORECASE)
_ATTR = lambda name: re.compile(  # noqa: E731 - tiny attribute extractor
    name + r'\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_SRC_ATTR = _ATTR("(?:data-src|src)")
_WIDTH_ATTR = _ATTR("width")
_HEIGHT_ATTR = _ATTR("height")

# Junk we never want as a thumbnail (icons, sprites, tracking pixels, logos).
_IMG_BLOCKLIST = re.compile(
    r"(sprite|logo|icon|favicon|avatar|placeholder|blank|pixel|tracking|"
    r"1x1|spacer|\.svg)", re.IGNORECASE)


def _normalise_image(image):
    """Clean a scraped image URL; return an absolute http(s) URL or None."""
    if not image:
        return None
    image = image.strip().replace("&amp;", "&")
    if image.startswith("//"):
        image = "https:" + image
    return image if image.startswith("http") else None


def _first_large_img(text):
    """Return the first <img> whose width/height attribute exceeds 200px.

    Falls back to the first reasonable-looking content image when no explicit
    dimensions are present (skipping obvious icons, logos and tracking pixels).
    """
    fallback = None
    for tag in _IMG_TAG.finditer(text):
        raw = tag.group(0)
        src_match = _SRC_ATTR.search(raw)
        if not src_match:
            continue
        src = _normalise_image(src_match.group(1))
        if not src or _IMG_BLOCKLIST.search(src):
            continue

        def _dim(attr):
            m = attr.search(raw)
            try:
                return int(re.sub(r"[^\d].*$", "", m.group(1))) if m else 0
            except (TypeError, ValueError):
                return 0

        width, height = _dim(_WIDTH_ATTR), _dim(_HEIGHT_ATTR)
        if width > 200 or height > 200:
            return src                       # explicitly large -> take it
        if width == 0 and height == 0 and fallback is None:
            fallback = src                   # unknown size -> remember as backup
    return fallback


def fetch_og_image(url, timeout=8):
    """Best-effort article thumbnail URL, or None.

    Tries, in order:
        1. Open Graph image  (og:image / og:image:secure_url)
        2. Twitter card image (twitter:image / twitter:image:src)
        3. The first in-article <img> larger than 200px
    Uses a realistic browser header set and an 8s timeout, and transparently
    clears Google's consent interstitial so redirected links resolve. Any
    failure returns None so the report falls back to a coloured placeholder.
    """
    if requests is None or not url:
        return None
    try:
        resp = requests.get(
            url, timeout=timeout, headers=BROWSER_HEADERS,
            cookies=_CONSENT_COOKIES, stream=True, allow_redirects=True,
        )
        resp.raise_for_status()
        # Read up to ~500 KB so the <img> fallback can see body content too.
        chunk = b""
        for piece in resp.iter_content(chunk_size=32768):
            chunk += piece
            if len(chunk) > 500_000:
                break
        resp.close()
        text = chunk.decode("utf-8", errors="ignore")

        # 1. Open Graph, then 2. Twitter card.
        for pattern in _OG_PATTERNS + _TWITTER_PATTERNS:
            match = pattern.search(text)
            if match:
                image = _normalise_image(match.group(1))
                if image:
                    return image

        # 3. First sizeable in-article image.
        return _first_large_img(text)
    except Exception:  # network / decode / parse errors are non-fatal
        return None


def attach_images(headlines, max_workers=8):
    """Populate an ``image`` field on each headline dict (in place).

    Thumbnails are fetched concurrently with a small thread pool so the extra
    network round-trips do not noticeably slow the run. Returns *headlines*.
    """
    if not headlines or requests is None:
        for h in headlines:
            h.setdefault("image", None)
        return headlines

    def _grab(h):
        h["image"] = fetch_og_image(h["url"])
        return h

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(_grab, headlines))
    return headlines


def _relative_time(dt, now):
    """Human-friendly 'time ago' string, e.g. '3h ago' / '12m ago'."""
    delta = now - dt
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def fetch_feed(feed, now=None):
    """Fetch and normalise a single feed. Returns a list of headline dicts."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)

    parsed = feedparser.parse(feed["url"], agent=USER_AGENT)

    headlines = []
    for entry in parsed.entries:
        published = _entry_time(entry)
        # Keep only items we can date and that fall within the time window.
        if published is None or published < cutoff:
            continue
        title = _clean_title(entry.get("title") or "", feed["source"])
        url = (entry.get("link") or "").strip()
        if not title or not url:
            continue
        headlines.append({
            "title": title,
            "source": feed["source"],
            "url": url,
            "published": published,
            "published_str": published.strftime("%H:%M UTC"),
            "relative": _relative_time(published, now),
        })

    # Newest first, then cap per source.
    headlines.sort(key=lambda h: h["published"], reverse=True)
    return headlines[:MAX_PER_SOURCE]


def fetch_headlines(now=None):
    """Fetch headlines from every configured feed.

    Returns a flat list of headline dicts (top N per source, last 24h).
    Individual feed failures are caught and reported, not fatal.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    all_headlines = []
    for feed in FEEDS:
        try:
            items = fetch_feed(feed, now=now)
            all_headlines.extend(items)
            print(f"  - {feed['source']:<14} {len(items)} headline(s)")
        except Exception as exc:  # network / parse errors per feed
            print(f"  ! Failed to fetch {feed['source']} news: {exc}")

    # Best-effort Open Graph thumbnails for each headline (placeholder on miss).
    attach_images(all_headlines)
    with_images = sum(1 for h in all_headlines if h.get("image"))
    print(f"  - Thumbnails    {with_images}/{len(all_headlines)} image(s) found")
    return all_headlines
