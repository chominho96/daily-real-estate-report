from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class RegionConfig:
    name: str
    code: str
    enabled: bool
    assets: list[str]


@dataclass
class SectionConfig:
    id: str
    title: str
    instruction: str


@dataclass
class LLMConfig:
    provider: str
    model: str
    temperature: float


@dataclass
class NewsConfig:
    max_items: int
    queries: list[str]


@dataclass
class RealEstateAPIConfig:
    enabled: bool
    base_url: str
    service_key_env: str
    timeout_sec: int
    num_rows: int
    lawd_code_digits: int
    endpoint_by_asset: dict[str, str]


@dataclass
class ReportConfig:
    timezone: str
    language: str
    llm: LLMConfig
    news: NewsConfig
    real_estate_api: RealEstateAPIConfig
    sections: list[SectionConfig]


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published_at: datetime


@dataclass
class MarketPoint:
    region_name: str
    region_code: str
    asset: str
    observed_at: datetime
    avg_price: float
    txn_count: int


@dataclass
class MarketMetric:
    region_name: str
    region_code: str
    asset: str
    current_avg_price: float
    daily_change_pct: float
    weekly_change_pct: float
    current_txn_count: int
    txn_daily_change_pct: float


@dataclass
class ReportWindow:
    start: datetime
    end: datetime
