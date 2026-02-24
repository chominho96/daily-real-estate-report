from __future__ import annotations

import json
from pathlib import Path

from daily_report.models import (
    BuyerProfileConfig,
    LLMConfig,
    NewsConfig,
    RealEstateAPIConfig,
    RegionConfig,
    ReportConfig,
    SectionConfig,
)

try:
    import yaml
except ImportError:
    yaml = None


def _load_structured_file(path: Path) -> dict:
    if yaml is not None and path.exists():
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    json_path = path.with_suffix(".json")
    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))

    raise RuntimeError(
        f"Could not load config from {path} (or {json_path}). "
        "Install PyYAML or provide JSON fallback files."
    )


def load_regions(path: Path) -> list[RegionConfig]:
    data = _load_structured_file(path)

    regions: list[RegionConfig] = []
    for item in data.get("regions", []):
        regions.append(
            RegionConfig(
                name=item["name"],
                code=str(item["code"]),
                enabled=bool(item.get("enabled", True)),
                assets=[str(asset) for asset in item.get("assets", [])],
            )
        )
    return regions


def load_report_config(path: Path) -> ReportConfig:
    data = _load_structured_file(path)

    llm_cfg = data.get("llm", {})
    news_cfg = data.get("news", {})
    real_estate_api_cfg = data.get("real_estate_api", {})
    buyer_profile_cfg = data.get("buyer_profile", {})

    sections: list[SectionConfig] = []
    for section in data.get("sections", []):
        sections.append(
            SectionConfig(
                id=str(section["id"]),
                title=str(section["title"]),
                instruction=str(section["instruction"]),
            )
        )

    return ReportConfig(
        timezone=str(data.get("timezone", "Asia/Seoul")),
        language=str(data.get("language", "ko")),
        llm=LLMConfig(
            provider=str(llm_cfg.get("provider", "codex")),
            model=str(llm_cfg.get("model", "gpt-5-codex-mini")),
            temperature=float(llm_cfg.get("temperature", 0.2)),
        ),
        news=NewsConfig(
            provider=str(news_cfg.get("provider", "naver")).strip().lower(),
            max_items=int(news_cfg.get("max_items", 12)),
            queries=[str(query) for query in news_cfg.get("queries", [])],
            timeout_sec=int(news_cfg.get("timeout_sec", 10)),
            naver_client_id_env=str(news_cfg.get("naver_client_id_env", "NAVER_NEWS_CLIENT_ID")).strip(),
            naver_client_secret_env=str(news_cfg.get("naver_client_secret_env", "NAVER_NEWS_CLIENT_SECRET")).strip(),
            naver_sort=str(news_cfg.get("naver_sort", "date")).strip(),
        ),
        real_estate_api=RealEstateAPIConfig(
            enabled=bool(real_estate_api_cfg.get("enabled", False)),
            base_url=str(real_estate_api_cfg.get("base_url", "https://apis.data.go.kr")).strip(),
            service_key_env=str(real_estate_api_cfg.get("service_key_env", "MOLIT_API_SERVICE_KEY")).strip(),
            timeout_sec=int(real_estate_api_cfg.get("timeout_sec", 20)),
            num_rows=int(real_estate_api_cfg.get("num_rows", 1000)),
            lawd_code_digits=int(real_estate_api_cfg.get("lawd_code_digits", 5)),
            endpoint_by_asset={
                str(asset): str(url).strip()
                for asset, url in (real_estate_api_cfg.get("endpoint_by_asset", {}) or {}).items()
            },
        ),
        buyer_profile=BuyerProfileConfig(
            monthly_net_income_manwon=float(buyer_profile_cfg.get("monthly_net_income_manwon", 420.0)),
            monthly_saving_manwon=float(buyer_profile_cfg.get("monthly_saving_manwon", 180.0)),
            available_cash_manwon=float(buyer_profile_cfg.get("available_cash_manwon", 8000.0)),
            target_price_min_eok=float(buyer_profile_cfg.get("target_price_min_eok", 9.0)),
            target_price_max_eok=float(buyer_profile_cfg.get("target_price_max_eok", 12.0)),
            expected_ltv_pct=float(buyer_profile_cfg.get("expected_ltv_pct", 70.0)),
            acquisition_cost_pct=float(buyer_profile_cfg.get("acquisition_cost_pct", 4.6)),
            loan_term_years=int(buyer_profile_cfg.get("loan_term_years", 30)),
            base_rate_pct=float(buyer_profile_cfg.get("base_rate_pct", 4.3)),
            stress_rate_pct=float(buyer_profile_cfg.get("stress_rate_pct", 6.0)),
            affordability_threshold_pct=float(buyer_profile_cfg.get("affordability_threshold_pct", 35.0)),
        ),
        sections=sections,
    )
