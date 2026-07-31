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

Google News redirects
---------------------
Google News hands out opaque ``news.google.com/rss/articles/CBMi...`` links
instead of the publisher's own URL, which left every Reuters item pointing at
a redirect and showing a blank thumbnail. :func:`resolve_google_news` undoes
that, so Reuters links open on reuters.com and a real article image reaches
the report. See its docstring for why the image needs three tiers.
"""

import calendar
import html
import json
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


def _og_image_in(text):
    """First og:image / twitter:image URL in an already-fetched HTML string."""
    for pattern in _OG_PATTERNS + _TWITTER_PATTERNS:
        match = pattern.search(text)
        if match:
            image = _normalise_image(match.group(1))
            if image:
                return image
    return None


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

        # 1. Open Graph, then 2. Twitter card, then 3. first sizeable image.
        return _og_image_in(text) or _first_large_img(text)
    except Exception:  # network / decode / parse errors are non-fatal
        return None


# --- Google News redirect resolution --------------------------------------- #
# Only /rss/articles/ links are opaque redirects; anything else is already the
# publisher's own URL and is left alone.
_GNEWS_ARTICLE_RE = re.compile(
    r"^https?://news\.google\.[a-z.]+/rss/articles/", re.IGNORECASE)

_GNEWS_BATCH_URL = ("https://news.google.com/_/DotsSplashUi/data/batchexecute")

# The splash page carries the three values its own JavaScript posts back to
# Google: the article blob, a timestamp and a request signature. They sit
# together at the tail of a c-wiz element's (HTML-escaped) data-p attribute.
_DATA_P_RE = re.compile(r'data-p="([^"]*)"', re.IGNORECASE)
_GNEWS_SIG_RE = re.compile(
    r'"([A-Za-z0-9_\-]{20,})"\s*,\s*\d+\s*,\s*\d+\s*,\s*null\s*,\s*false\s*,'
    r'\s*(\d{10,})\s*,\s*"([^"]+)"')

# Last-resort scrape of the batchexecute response if its JSON envelope changes.
_GARTURL_RE = re.compile(r'garturlres\\?"\s*,\s*\\?"(https?://[^\\"]+)')


def _google_news_params(page_html):
    """Return (blob, timestamp, signature) from a Google News splash page."""
    for raw in _DATA_P_RE.findall(page_html):
        match = _GNEWS_SIG_RE.search(html.unescape(raw))
        if match:
            return match.group(1), int(match.group(2)), match.group(3)
    return None


def _parse_garturl(body):
    """Pull the resolved article URL out of a batchexecute response body."""
    # Responses are anti-JSON-hijacking prefixed with ")]}'".
    payload = body[body.index("["):] if "[" in body else body
    try:
        for entry in json.loads(payload):
            if len(entry) > 2 and entry[0] == "wrb.fr" and entry[2]:
                inner = json.loads(entry[2])
                if len(inner) > 1 and str(inner[1]).startswith("http"):
                    return inner[1]
    except (ValueError, TypeError, IndexError):
        pass
    match = _GARTURL_RE.search(body)
    return match.group(1) if match else None


def resolve_google_news(url, timeout=10):
    """Resolve a Google News redirect to the publisher's own article URL.

    Google no longer embeds the destination in the link: the newer
    ``/rss/articles/CBMi...`` blobs are opaque and the splash page resolves
    them in JavaScript, so merely following redirects lands back on
    news.google.com. We do what that page does — read the blob, timestamp and
    signature out of its ``c-wiz`` element and post them to the same
    ``batchexecute`` endpoint, which answers with the real URL.

    Returns the publisher's URL, or the input unchanged on any failure, so a
    resolution miss only costs us the improvement.

    Note on thumbnails: the splash page is *not* a usable image source. Its
    only images are the publisher's favicon (16-48px) and a static Google News
    app logo served as the page's own og:image — identical for every article,
    so using it would stamp the same picture on every Reuters item. The image
    chain therefore ends at the publisher, and Reuters (which answers 401 to
    any scraper) falls through to the report's placeholder tile.
    """
    if requests is None or not url or not _GNEWS_ARTICLE_RE.match(url):
        return url
    try:
        session = requests.Session()
        page = session.get(url, timeout=timeout, headers=BROWSER_HEADERS,
                           cookies=_CONSENT_COOKIES, allow_redirects=True)
        page.raise_for_status()

        params = _google_news_params(page.text)
        if not params:
            return url
        blob, timestamp, signature = params

        request = ["garturlreq",
                   [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
                     None, None, None, None, None, 0, 1],
                    "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                   blob, timestamp, signature]
        body = {"f.req": json.dumps([[["Fbv4je", json.dumps(request)]]])}
        headers = dict(BROWSER_HEADERS)
        headers["Content-Type"] = ("application/x-www-form-urlencoded;"
                                   "charset=UTF-8")
        resp = session.post(_GNEWS_BATCH_URL, data=body, headers=headers,
                            cookies=_CONSENT_COOKIES, timeout=timeout)
        resp.raise_for_status()

        return _parse_garturl(resp.text) or url
    except Exception:  # network / parse errors leave the redirect in place
        return url


def resolve_links(headlines, max_workers=8):
    """Point every Google News redirect in *headlines* at its publisher.

    Rewrites ``url`` in place so links open on reuters.com rather than
    news.google.com, and so :func:`attach_images` scrapes the real article.
    Runs concurrently — each item costs two round-trips. Returns *headlines*.
    """
    targets = [h for h in headlines if _GNEWS_ARTICLE_RE.match(h.get("url", ""))]
    if not targets or requests is None:
        return headlines

    def _resolve(h):
        h["url"] = resolve_google_news(h["url"])

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(_resolve, targets))

    resolved = sum(1 for h in targets
                   if not _GNEWS_ARTICLE_RE.match(h["url"]))
    print(f"  - Redirects     {resolved}/{len(targets)} resolved to publisher URLs")
    return headlines


def attach_images(headlines, max_workers=8):
    """Populate an ``image`` field on each headline dict (in place).

    The image is scraped from ``url``, which :func:`resolve_links` has already
    pointed at the publisher rather than a Google News redirect. Publishers
    that refuse scrapers (Reuters answers 401) leave ``image`` as None and the
    report renders its placeholder tile instead.

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

    # Newest first, then cap per source. Redirects are resolved *after* the
    # cap so we only pay the round-trips for items that reach the report.
    headlines.sort(key=lambda h: h["published"], reverse=True)
    return resolve_links(headlines[:MAX_PER_SOURCE])


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
