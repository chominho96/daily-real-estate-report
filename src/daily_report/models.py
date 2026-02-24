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
    provider: str
    max_items: int
    queries: list[str]
    timeout_sec: int
    naver_client_id_env: str
    naver_client_secret_env: str
    naver_sort: str


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
class BuyerProfileConfig:
    monthly_net_income_manwon: float
    monthly_saving_manwon: float
    available_cash_manwon: float
    target_price_min_eok: float
    target_price_max_eok: float
    expected_ltv_pct: float
    acquisition_cost_pct: float
    loan_term_years: int
    base_rate_pct: float
    stress_rate_pct: float
    affordability_threshold_pct: float


@dataclass
class ReportConfig:
    timezone: str
    language: str
    llm: LLMConfig
    news: NewsConfig
    real_estate_api: RealEstateAPIConfig
    buyer_profile: BuyerProfileConfig
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
