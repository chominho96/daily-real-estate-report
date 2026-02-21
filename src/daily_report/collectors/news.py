from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from daily_report.models import NewsItem

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    from dateutil import parser as dateparser
except ImportError:
    dateparser = None


def _parse_published_at(raw_value: str) -> datetime | None:
    text = str(raw_value).strip()
    if not text:
        return None

    if dateparser is not None:
        try:
            return dateparser.parse(text)
        except (ValueError, TypeError):
            return None

    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None


def _google_news_rss_url(query: str) -> str:
    # Keep time window coarse in feed query, then filter precisely by datetime window.
    encoded = quote_plus(f"{query} when:2d")
    return f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"


def fetch_policy_news(start: datetime, end: datetime, queries: list[str], max_items: int) -> list[NewsItem]:
    if feedparser is None:
        return []

    unique: dict[str, NewsItem] = {}

    for query in queries:
        feed = feedparser.parse(_google_news_rss_url(query))
        for entry in feed.entries:
            link = str(entry.get("link", "")).strip()
            title = str(entry.get("title", "")).strip()
            if not link or not title:
                continue

            published_raw = entry.get("published") or entry.get("updated")
            if published_raw:
                published_at = _parse_published_at(str(published_raw))
                if published_at is None:
                    continue
            else:
                continue

            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=start.tzinfo)

            if not (start <= published_at <= end):
                continue

            source = "Google News"
            if link not in unique:
                unique[link] = NewsItem(
                    title=title,
                    link=link,
                    source=source,
                    published_at=published_at,
                )

    items = sorted(unique.values(), key=lambda x: x.published_at, reverse=True)
    return items[:max_items]
