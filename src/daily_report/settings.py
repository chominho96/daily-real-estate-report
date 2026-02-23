from __future__ import annotations

import json
from pathlib import Path

from daily_report.models import LLMConfig, NewsConfig, RealEstateAPIConfig, RegionConfig, ReportConfig, SectionConfig

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
            model=str(llm_cfg.get("model", "gpt-5-mini")),
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
        sections=sections,
    )
