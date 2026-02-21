from __future__ import annotations

import hashlib
import random
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from daily_report.models import MarketPoint, RealEstateAPIConfig, RegionConfig


def fetch_market_points(
    regions: list[RegionConfig],
    start: datetime,
    end: datetime,
    api_config: RealEstateAPIConfig,
) -> tuple[list[MarketPoint], str]:
    if api_config.enabled:
        points = _fetch_market_points_via_public_api(
            regions=regions,
            start=start,
            end=end,
            api_config=api_config,
        )
        if points:
            return points, "public_api"

    return _build_synthetic_market_points(regions=regions, start=start, end=end), "synthetic"


def _fetch_market_points_via_public_api(
    regions: list[RegionConfig],
    start: datetime,
    end: datetime,
    api_config: RealEstateAPIConfig,
) -> list[MarketPoint]:
    import os

    service_key = os.getenv(api_config.service_key_env, "").strip()
    if not service_key:
        return []

    monthly_buckets: dict[tuple[str, str, str, date], list[float]] = defaultdict(list)

    for region in regions:
        lawd_code = region.code[: max(1, api_config.lawd_code_digits)]

        for asset in region.assets:
            endpoint = api_config.endpoint_by_asset.get(asset, "").strip()
            if not endpoint:
                continue

            for yyyymm in _iter_yyyymm(start.date(), end.date()):
                params = {
                    "serviceKey": service_key,
                    "LAWD_CD": lawd_code,
                    "DEAL_YMD": yyyymm,
                    "numOfRows": str(api_config.num_rows),
                    "pageNo": "1",
                }

                items = _fetch_xml_items(endpoint=endpoint, params=params, api_config=api_config)
                for item in items:
                    parsed = _parse_trade_item(item=item, tzinfo=start.tzinfo)
                    if parsed is None:
                        continue

                    observed_at, deal_price = parsed
                    if not (start <= observed_at <= end):
                        continue

                    key = (region.name, region.code, asset, observed_at.date())
                    monthly_buckets[key].append(deal_price)

    points: list[MarketPoint] = []
    for (region_name, region_code, asset, observed_date), prices in monthly_buckets.items():
        if not prices:
            continue

        observed_at = datetime.combine(observed_date, datetime.min.time(), tzinfo=start.tzinfo)
        points.append(
            MarketPoint(
                region_name=region_name,
                region_code=region_code,
                asset=asset,
                observed_at=observed_at,
                avg_price=round(sum(prices) / len(prices), 2),
                txn_count=len(prices),
            )
        )

    points.sort(key=lambda x: (x.observed_at, x.region_code, x.asset))
    return points


def _iter_yyyymm(start_date: date, end_date: date) -> Iterable[str]:
    cursor = date(start_date.year, start_date.month, 1)
    limit = date(end_date.year, end_date.month, 1)

    while cursor <= limit:
        yield f"{cursor.year:04d}{cursor.month:02d}"
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def _fetch_xml_items(endpoint: str, params: dict[str, str], api_config: RealEstateAPIConfig) -> list[ElementTree.Element]:
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        base = endpoint
    else:
        base = f"{api_config.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    url = f"{base}?{urlencode(params)}"
    request = Request(url=url, headers={"User-Agent": "daily-real-estate-report/0.1"})

    try:
        with urlopen(request, timeout=api_config.timeout_sec) as resp:
            payload = resp.read()
    except (HTTPError, URLError, TimeoutError, ValueError):
        return []

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []

    return list(root.findall(".//item"))


def _parse_trade_item(item: ElementTree.Element, tzinfo) -> tuple[datetime, float] | None:
    year_text = _find_text(item, ["년", "dealYear", "deal_year"])
    month_text = _find_text(item, ["월", "dealMonth", "deal_month"])
    day_text = _find_text(item, ["일", "dealDay", "deal_day"])

    if not (year_text and month_text and day_text):
        ymd_text = _find_text(item, ["dealYmd", "deal_ymd", "계약년월"])
        if ymd_text:
            digits = _digits_only(ymd_text)
            if len(digits) >= 8:
                year_text, month_text, day_text = digits[:4], digits[4:6], digits[6:8]

    if not (year_text and month_text and day_text):
        return None

    amount_text = _find_text(
        item,
        [
            "거래금액",
            "dealAmount",
            "deal_amount",
            "price",
            "거래금액(만원)",
        ],
    )
    if not amount_text:
        return None

    amount = _parse_number(amount_text)
    if amount is None:
        return None

    try:
        year = int(_digits_only(year_text))
        month = int(_digits_only(month_text))
        day = int(_digits_only(day_text))
    except ValueError:
        return None

    try:
        observed_date = date(year, month, day)
    except ValueError:
        return None

    observed_at = datetime.combine(observed_date, datetime.min.time(), tzinfo=tzinfo)
    return observed_at, amount


def _find_text(item: ElementTree.Element, candidates: list[str]) -> str:
    candidate_set = set(candidates)

    for child in item:
        tag = child.tag.split("}")[-1]
        if tag in candidate_set and child.text:
            text = child.text.strip()
            if text:
                return text

    return ""


def _digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _parse_number(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def _build_synthetic_market_points(regions: list[RegionConfig], start: datetime, end: datetime) -> list[MarketPoint]:
    points: list[MarketPoint] = []
    total_days = max((end.date() - start.date()).days + 1, 8)

    for region in regions:
        for asset in region.assets:
            seed = int(hashlib.sha256(f"{region.code}:{asset}".encode("utf-8")).hexdigest()[:8], 16)
            rng = random.Random(seed + int(end.timestamp() // 86400))
            base_price = 60000 + (seed % 70000)
            trend = rng.uniform(-0.0035, 0.0045)

            for offset in range(total_days):
                observed_at = datetime.combine(start.date() + timedelta(days=offset), datetime.min.time(), tzinfo=start.tzinfo)
                drift = 1 + trend * offset
                noise = rng.uniform(-0.01, 0.01)
                avg_price = base_price * drift * (1 + noise)
                txn_count = int(max(3, 45 + rng.randint(-15, 20) - (offset % 5)))

                points.append(
                    MarketPoint(
                        region_name=region.name,
                        region_code=region.code,
                        asset=asset,
                        observed_at=observed_at,
                        avg_price=round(avg_price, 2),
                        txn_count=txn_count,
                    )
                )

    return points
