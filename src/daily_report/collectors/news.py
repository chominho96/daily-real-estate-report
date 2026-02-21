from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode, urlparse
from urllib.request import Request, urlopen

from daily_report.models import NewsConfig, NewsItem

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


def _strip_html_tags(value: str) -> str:
    text = html.unescape(value)
    return re.sub(r"<[^>]+>", "", text).strip()


def _google_news_rss_url(query: str) -> str:
    encoded = quote_plus(f"{query} when:2d")
    return f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"


def fetch_policy_news(start: datetime, end: datetime, news_cfg: NewsConfig) -> list[NewsItem]:
    provider = (news_cfg.provider or "").lower().strip()

    if provider == "naver":
        naver_items = _fetch_policy_news_from_naver(start=start, end=end, news_cfg=news_cfg)
        if naver_items:
            return naver_items
        return _fetch_policy_news_from_google_rss(
            start=start,
            end=end,
            queries=news_cfg.queries,
            max_items=news_cfg.max_items,
        )

    if provider == "google":
        return _fetch_policy_news_from_google_rss(
            start=start,
            end=end,
            queries=news_cfg.queries,
            max_items=news_cfg.max_items,
        )

    naver_items = _fetch_policy_news_from_naver(start=start, end=end, news_cfg=news_cfg)
    if naver_items:
        return naver_items
    return _fetch_policy_news_from_google_rss(start=start, end=end, queries=news_cfg.queries, max_items=news_cfg.max_items)


def _host_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _publisher_label_from_host(host: str) -> str:
    if not host:
        return "언론사 미상"

    known = {
        "yna.co.kr": "연합뉴스",
        "newsis.com": "뉴시스",
        "donga.com": "동아일보",
        "chosun.com": "조선일보",
        "joongang.co.kr": "중앙일보",
        "hani.co.kr": "한겨레",
        "khan.co.kr": "경향신문",
        "mk.co.kr": "매일경제",
        "hankyung.com": "한국경제",
        "sedaily.com": "서울경제",
        "mt.co.kr": "머니투데이",
        "edaily.co.kr": "이데일리",
        "fnnews.com": "파이낸셜뉴스",
        "seoul.co.kr": "서울신문",
        "heraldcorp.com": "헤럴드경제",
        "news1.kr": "뉴스1",
        "nocutnews.co.kr": "노컷뉴스",
        "asiae.co.kr": "아시아경제",
        "biz.chosun.com": "조선비즈",
        "naver.com": "네이버뉴스",
        "n.news.naver.com": "네이버뉴스",
        "news.naver.com": "네이버뉴스",
    }
    if host in known:
        return known[host]

    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _publisher_from_links(link: str, originallink: str) -> str:
    origin_host = _host_from_url(originallink)
    link_host = _host_from_url(link)

    # Prefer original publisher host when Naver API forwards through Naver URLs.
    if origin_host and not origin_host.endswith("naver.com"):
        return _publisher_label_from_host(origin_host)
    if link_host:
        return _publisher_label_from_host(link_host)
    if origin_host:
        return _publisher_label_from_host(origin_host)
    return "언론사 미상"


def _fetch_policy_news_from_naver(start: datetime, end: datetime, news_cfg: NewsConfig) -> list[NewsItem]:
    client_id = os.getenv(news_cfg.naver_client_id_env, "").strip()
    client_secret = os.getenv(news_cfg.naver_client_secret_env, "").strip()
    if not client_id or not client_secret:
        return []

    unique: dict[str, NewsItem] = {}
    display = max(1, min(news_cfg.max_items, 100))
    sort = news_cfg.naver_sort if news_cfg.naver_sort in {"date", "sim"} else "date"

    for query in news_cfg.queries:
        params = {
            "query": query,
            "display": str(display),
            "start": "1",
            "sort": sort,
        }
        url = f"https://openapi.naver.com/v1/search/news.json?{urlencode(params)}"
        request = Request(
            url=url,
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
                "User-Agent": "daily-real-estate-report/0.1",
            },
        )

        try:
            with urlopen(request, timeout=news_cfg.timeout_sec) as resp:
                body = resp.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, ValueError):
            continue

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue

        for item in payload.get("items", []):
            link = str(item.get("link") or item.get("originallink") or "").strip()
            originallink = str(item.get("originallink", "")).strip()
            title = _strip_html_tags(str(item.get("title", "")))
            published_raw = str(item.get("pubDate", "")).strip()

            if not link or not title or not published_raw:
                continue

            published_at = _parse_published_at(published_raw)
            if published_at is None:
                continue

            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=start.tzinfo)

            if not (start <= published_at <= end):
                continue

            if link not in unique:
                unique[link] = NewsItem(
                    title=title,
                    link=link,
                    source=_publisher_from_links(link=link, originallink=originallink),
                    published_at=published_at,
                )

    items = sorted(unique.values(), key=lambda x: x.published_at, reverse=True)
    return items[: news_cfg.max_items]


def _fetch_policy_news_from_google_rss(
    start: datetime,
    end: datetime,
    queries: list[str],
    max_items: int,
) -> list[NewsItem]:
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
            if not published_raw:
                continue

            published_at = _parse_published_at(str(published_raw))
            if published_at is None:
                continue

            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=start.tzinfo)

            if not (start <= published_at <= end):
                continue

            source_title = ""
            source_obj = entry.get("source")
            if source_obj:
                source_title = str(source_obj.get("title", "")).strip()
            if not source_title:
                source_title = _publisher_label_from_host(_host_from_url(link))

            if link not in unique:
                unique[link] = NewsItem(
                    title=title,
                    link=link,
                    source=source_title or "언론사 미상",
                    published_at=published_at,
                )

    items = sorted(unique.values(), key=lambda x: x.published_at, reverse=True)
    return items[:max_items]
