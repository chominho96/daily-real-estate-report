from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, time, timedelta
import math
from pathlib import Path
import statistics
from typing import Any
from zoneinfo import ZoneInfo

from daily_report.analysis.metrics import build_market_metrics, extract_top_movers
from daily_report.collectors.news import fetch_policy_news
from daily_report.collectors.real_estate import fetch_market_points
from daily_report.llm.section_writer import SectionWriter
from daily_report.models import BuyerProfileConfig, MarketMetric, MarketPoint, NewsItem, ReportWindow, SectionConfig
from daily_report.render.markdown import render_docs_index, render_fixed_report
from daily_report.settings import load_regions, load_report_config

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
MIN_MARKET_HISTORY_DAYS = 30


def _fmt_dt(value: datetime) -> str:
    return value.strftime(DATETIME_FMT)


class DailyReportPipeline:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config_dir = root / "config"
        self.docs_dir = root / "docs"
        self.reports_dir = self.docs_dir / "reports"
        self.data_processed_dir = root / "data" / "processed"
        self.state_file = root / "state" / "last_run.json"

    def run(self) -> Path:
        regions = load_regions(self.config_dir / "regions.yaml")
        report_cfg = load_report_config(self.config_dir / "report.yaml")
        tz = ZoneInfo(report_cfg.timezone)

        now = datetime.now(tz)
        last_run = self._read_last_run(tz)
        if last_run is None:
            previous_day = now.date() - timedelta(days=1)
            last_run = datetime.combine(previous_day, time.min, tzinfo=tz)

        report_window = ReportWindow(start=last_run, end=now)
        # Ensure enough history for stable day/week comparisons even when runs are frequent.
        market_start = min(
            report_window.start - timedelta(days=8),
            report_window.end - timedelta(days=MIN_MARKET_HISTORY_DAYS),
        )

        enabled_regions = [region for region in regions if region.enabled]

        news_items = fetch_policy_news(
            start=report_window.start,
            end=report_window.end,
            news_cfg=report_cfg.news,
        )

        market_points, market_data_mode = fetch_market_points(
            regions=enabled_regions,
            start=market_start,
            end=report_window.end,
            api_config=report_cfg.real_estate_api,
        )
        market_metrics = build_market_metrics(market_points)
        top_movers = extract_top_movers(market_metrics)
        health_issues: list[dict[str, str]] = []
        health_issues.extend(self._market_data_health_issues(market_data_mode=market_data_mode, market_metrics=market_metrics))

        writer = SectionWriter(report_cfg.llm, report_cfg.language)
        section_bodies: dict[str, str] = {}

        for section in report_cfg.sections:
            section_context = self._build_section_context(
                section=section,
                report_window=report_window,
                news_items=news_items,
                market_metrics=market_metrics,
                top_movers=top_movers,
                market_data_mode=market_data_mode,
                buyer_profile=report_cfg.buyer_profile,
            )
            generated_body = writer.write(section, section_context)
            section_bodies[section.id] = self._compose_section_body(
                section=section,
                generated_body=generated_body,
                news_items=news_items,
                market_metrics=market_metrics,
            )

        llm_health = writer.health_snapshot()
        health_issues.extend(llm_health.get("issues", []))
        health_status = self._health_status(health_issues)
        health_payload = {
            "status": health_status,
            "issues": health_issues,
            "market_data_mode": market_data_mode,
            "llm": llm_health,
        }

        report_text = render_fixed_report(
            generated_at=now,
            window=report_window,
            sections=report_cfg.sections,
            section_bodies=section_bodies,
            regions=regions,
            data_mode=market_data_mode,
        )

        report_path = self._write_report(now, report_text)
        self._write_index()
        self._write_run_artifact(
            now=now,
            window=report_window,
            news_items=news_items,
            market_points=market_points,
            market_metrics=market_metrics,
            section_bodies=section_bodies,
            report_path=report_path,
            market_data_mode=market_data_mode,
            health=health_payload,
        )
        self._write_last_run(now)

        return report_path

    def _market_data_health_issues(self, market_data_mode: str, market_metrics: list[MarketMetric]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        if market_data_mode == "public_api_error":
            issues.append(
                {
                    "source": "api",
                    "section_id": "price_trend",
                    "severity": "critical",
                    "code": "public_api_error",
                    "message": "실거래 공공 API 호출 오류로 시장 지표 신뢰도가 크게 낮습니다.",
                    "possible_cause": "일시적 네트워크 장애, API 키 이슈, 또는 공공 API 응답 오류일 수 있습니다.",
                }
            )
        elif market_data_mode == "synthetic":
            issues.append(
                {
                    "source": "api",
                    "section_id": "price_trend",
                    "severity": "critical",
                    "code": "synthetic_data_used",
                    "message": "실거래 데이터 대신 샘플 데이터가 사용되었습니다.",
                    "possible_cause": "실거래 API 설정 비활성화 또는 키 누락 가능성이 있습니다.",
                }
            )
        elif market_data_mode == "public_api_empty":
            issues.append(
                {
                    "source": "api",
                    "section_id": "price_trend",
                    "severity": "warning",
                    "code": "public_api_empty",
                    "message": "실거래 API 응답은 있었지만 집계 가능한 데이터가 없습니다.",
                    "possible_cause": "최근 신고 지연/표본 부족 구간일 수 있으며 다음 실행에서 복구될 수 있습니다.",
                }
            )
        elif market_data_mode == "public_api_extended":
            issues.append(
                {
                    "source": "api",
                    "section_id": "price_trend",
                    "severity": "warning",
                    "code": "public_api_extended_window",
                    "message": "기본 기간 데이터 부족으로 90일 확장 조회 결과를 사용했습니다.",
                    "possible_cause": "최근 데이터 신고 지연 또는 API 반영 지연일 수 있습니다.",
                }
            )

        if not market_metrics:
            issues.append(
                {
                    "source": "api",
                    "section_id": "price_trend",
                    "severity": "warning",
                    "code": "empty_market_metrics",
                    "message": "가격/거래량 지표가 비어 있어 해석 섹션 신뢰도가 낮습니다.",
                    "possible_cause": "관심 지역 표본 부족 또는 API 집계 실패 가능성이 있습니다.",
                }
            )
        return issues

    def _health_status(self, issues: list[dict[str, str]]) -> str:
        if any(issue.get("severity") == "critical" for issue in issues):
            return "critical"
        if any(issue.get("severity") == "warning" for issue in issues):
            return "warning"
        return "ok"

    def _build_section_context(
        self,
        section: SectionConfig,
        report_window: ReportWindow,
        news_items: list[NewsItem],
        market_metrics: list[MarketMetric],
        top_movers: list[MarketMetric],
        market_data_mode: str,
        buyer_profile: BuyerProfileConfig,
    ) -> dict[str, Any]:
        base = {
            "window_start": _fmt_dt(report_window.start),
            "window_end": _fmt_dt(report_window.end),
            "market_data_mode": market_data_mode,
        }

        if section.id == "policy_news":
            base["news_items"] = [
                {
                    "title": item.title,
                    "link": item.link,
                    "source": item.source,
                    "published_at": _fmt_dt(item.published_at),
                }
                for item in news_items
            ]
            return base

        if section.id == "price_trend":
            base["metrics"] = [
                {
                    **asdict(metric),
                    "current_median_price_eok": round(metric.current_avg_price / 10000.0, 4),
                    # Keep legacy key for compatibility with existing prompts/fallback paths.
                    "current_avg_price_eok": round(metric.current_avg_price / 10000.0, 4),
                }
                for metric in market_metrics
            ]
            return base

        if section.id == "insights":
            base["top_movers"] = [asdict(metric) for metric in top_movers]
            base["metric_count"] = len(market_metrics)
            return base

        if section.id == "buy_readiness":
            target_min_eok = float(buyer_profile.target_price_min_eok)
            target_max_eok = float(buyer_profile.target_price_max_eok)
            if target_max_eok < target_min_eok:
                target_min_eok, target_max_eok = target_max_eok, target_min_eok

            target_mid_eok = round((target_min_eok + target_max_eok) / 2.0, 2)
            market_prices_eok = [metric.current_avg_price / 10000.0 for metric in market_metrics]
            market_ref_eok = round(
                statistics.median(market_prices_eok) if market_prices_eok else target_mid_eok,
                2,
            )

            expected_ltv_pct = max(0.0, min(100.0, float(buyer_profile.expected_ltv_pct)))
            acquisition_cost_pct = max(0.0, float(buyer_profile.acquisition_cost_pct))
            loan_term_years = max(1, int(buyer_profile.loan_term_years))
            base_rate_pct = max(0.0, float(buyer_profile.base_rate_pct))
            stress_rate_pct = max(0.0, float(buyer_profile.stress_rate_pct))
            monthly_income_manwon = max(0.0, float(buyer_profile.monthly_net_income_manwon))
            monthly_saving_manwon = max(0.0, float(buyer_profile.monthly_saving_manwon))
            available_cash_manwon = max(0.0, float(buyer_profile.available_cash_manwon))
            threshold_pct = max(10.0, min(80.0, float(buyer_profile.affordability_threshold_pct)))

            target_mid_manwon = target_mid_eok * 10000.0
            max_loan_manwon = round(target_mid_manwon * (expected_ltv_pct / 100.0), 1)
            acquisition_cost_manwon = round(target_mid_manwon * (acquisition_cost_pct / 100.0), 1)
            required_cash_manwon = round(max(0.0, target_mid_manwon - max_loan_manwon) + acquisition_cost_manwon, 1)
            cash_gap_manwon = round(required_cash_manwon - available_cash_manwon, 1)

            months_to_goal: int | None
            if cash_gap_manwon <= 0:
                months_to_goal = 0
            elif monthly_saving_manwon > 0:
                months_to_goal = int(math.ceil(cash_gap_manwon / monthly_saving_manwon))
            else:
                months_to_goal = None

            base_payment_manwon = round(
                self._monthly_payment_manwon(max_loan_manwon, base_rate_pct, loan_term_years),
                1,
            )
            stress_payment_manwon = round(
                self._monthly_payment_manwon(max_loan_manwon, stress_rate_pct, loan_term_years),
                1,
            )

            base_burden_pct = round((base_payment_manwon / monthly_income_manwon) * 100.0, 1) if monthly_income_manwon else 0.0
            stress_burden_pct = (
                round((stress_payment_manwon / monthly_income_manwon) * 100.0, 1) if monthly_income_manwon else 0.0
            )

            readiness_status = "준비 필요"
            if monthly_income_manwon > 0:
                if cash_gap_manwon <= 0 and stress_burden_pct <= threshold_pct:
                    readiness_status = "준비 완료"
                elif (
                    (cash_gap_manwon <= 0 and stress_burden_pct <= threshold_pct + 8.0)
                    or (
                        cash_gap_manwon > 0
                        and months_to_goal is not None
                        and months_to_goal <= 18
                        and stress_burden_pct <= threshold_pct + 10.0
                    )
                ):
                    readiness_status = "준비 진행"

            target_vs_market_pct = (
                round(((target_mid_eok - market_ref_eok) / market_ref_eok) * 100.0, 1) if market_ref_eok > 0 else 0.0
            )

            base.update(
                {
                    "readiness_status": readiness_status,
                    "target_price_min_eok": round(target_min_eok, 2),
                    "target_price_max_eok": round(target_max_eok, 2),
                    "target_price_mid_eok": target_mid_eok,
                    "market_reference_price_eok": market_ref_eok,
                    "target_vs_market_pct": target_vs_market_pct,
                    "expected_ltv_pct": round(expected_ltv_pct, 1),
                    "acquisition_cost_pct": round(acquisition_cost_pct, 1),
                    "loan_term_years": loan_term_years,
                    "base_rate_pct": round(base_rate_pct, 2),
                    "stress_rate_pct": round(stress_rate_pct, 2),
                    "monthly_net_income_manwon": round(monthly_income_manwon, 1),
                    "monthly_saving_manwon": round(monthly_saving_manwon, 1),
                    "available_cash_manwon": round(available_cash_manwon, 1),
                    "required_cash_manwon": required_cash_manwon,
                    "cash_gap_manwon": cash_gap_manwon,
                    "months_to_goal": months_to_goal,
                    "estimated_loan_manwon": max_loan_manwon,
                    "monthly_payment_base_manwon": base_payment_manwon,
                    "monthly_payment_stress_manwon": stress_payment_manwon,
                    "monthly_burden_base_pct": base_burden_pct,
                    "monthly_burden_stress_pct": stress_burden_pct,
                    "affordability_threshold_pct": round(threshold_pct, 1),
                }
            )
            return base

        if section.id == "today_signal":
            metric_count = len(market_metrics)
            daily_changes = [float(metric.daily_change_pct) for metric in market_metrics]
            weekly_changes = [float(metric.weekly_change_pct) for metric in market_metrics]
            txn_changes = [float(metric.txn_daily_change_pct) for metric in market_metrics]

            def _avg(values: list[float]) -> float:
                if not values:
                    return 0.0
                return round(sum(values) / len(values), 4)

            base.update(
                {
                    "news_count": len(news_items),
                    "metric_count": metric_count,
                    "avg_daily_change_pct": _avg(daily_changes),
                    "avg_weekly_change_pct": _avg(weekly_changes),
                    "avg_txn_daily_change_pct": _avg(txn_changes),
                    "positive_daily_count": sum(1 for value in daily_changes if value > 0),
                    "negative_daily_count": sum(1 for value in daily_changes if value < 0),
                    "top_movers": [asdict(metric) for metric in top_movers[:5]],
                    "metrics": [asdict(metric) for metric in market_metrics],
                }
            )
            return base

        base["metrics"] = [asdict(metric) for metric in market_metrics]
        base["news_items"] = [
            {
                "title": item.title,
                "link": item.link,
                "source": item.source,
                "published_at": _fmt_dt(item.published_at),
            }
            for item in news_items
        ]
        return base

    def _monthly_payment_manwon(self, principal_manwon: float, annual_rate_pct: float, term_years: int) -> float:
        principal = max(0.0, float(principal_manwon))
        months = max(1, int(term_years) * 12)
        monthly_rate = max(0.0, float(annual_rate_pct)) / 100.0 / 12.0
        if monthly_rate == 0.0:
            return principal / months

        growth = (1.0 + monthly_rate) ** months
        denominator = growth - 1.0
        if denominator == 0.0:
            return principal / months
        return principal * monthly_rate * growth / denominator

    def _compose_section_body(
        self,
        section: SectionConfig,
        generated_body: str,
        news_items: list[NewsItem],
        market_metrics: list[MarketMetric],
    ) -> str:
        if section.id == "policy_news":
            news_list = self._render_news_list(news_items)
            if generated_body.strip():
                return f"{generated_body.strip()}\n\n### 수집 뉴스 목록\n{news_list}"
            return f"### 수집 뉴스 목록\n{news_list}"

        if section.id == "price_trend":
            table = self._render_price_trend_table(market_metrics)
            if not market_metrics:
                return table
            commentary = generated_body.strip()
            if not commentary:
                return table
            return f"{table}\n\n### 해석\n{commentary}"

        return generated_body

    def _render_news_list(self, news_items: list[NewsItem]) -> str:
        if not news_items:
            return "- 수집된 뉴스가 없습니다."

        lines: list[str] = []
        for item in news_items:
            lines.append(f"- [{item.title}]({item.link}) ({item.source}, {_fmt_dt(item.published_at)})")
        return "\n".join(lines)

    def _render_price_trend_table(self, market_metrics: list[MarketMetric]) -> str:
        if not market_metrics:
            return "- 집계 가능한 시세 지표가 없습니다."

        asset_map = {
            "apartment": "아파트",
            "villa": "빌라",
            "officetel": "오피스텔",
        }
        lines = [
            "| 지역 | 자산군 | 중앙 가격(억 원) | 전일 대비 | 전주 대비 | 거래 건수 | 거래 건수(전일 대비) |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for metric in market_metrics:
            lines.append(
                "| {region} | {asset} | {price:.2f} | {d:+.2f}% | {w:+.2f}% | {txn} | {td:+.2f}% |".format(
                    region=metric.region_name,
                    asset=asset_map.get(metric.asset, metric.asset),
                    price=metric.current_avg_price / 10000.0,
                    d=metric.daily_change_pct,
                    w=metric.weekly_change_pct,
                    txn=metric.current_txn_count,
                    td=metric.txn_daily_change_pct,
                )
            )
        return "\n".join(lines)

    def _write_report(self, now: datetime, report_text: str) -> Path:
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        filename = now.strftime("%Y-%m-%d")
        report_path = self.reports_dir / f"{filename}.md"
        if report_path.exists():
            report_path = self.reports_dir / f"{filename}-{now.strftime('%H-%M')}.md"

        report_path.write_text(report_text, encoding="utf-8")
        return report_path

    def _write_index(self) -> None:
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        reports = sorted(
            self.reports_dir.glob("*.md"),
            key=self._report_sort_key,
            reverse=True,
        )
        relative = [f"reports/{path.name}" for path in reports[:90]]
        index_text = render_docs_index(relative)
        (self.docs_dir / "index.md").write_text(index_text, encoding="utf-8")

    def _report_sort_key(self, path: Path) -> datetime:
        stem = path.stem
        patterns = ["%Y-%m-%d-%H-%M", "%Y-%m-%d-%H%M", "%Y-%m-%d"]
        for pattern in patterns:
            try:
                return datetime.strptime(stem, pattern)
            except ValueError:
                continue
        return datetime.min

    def _write_run_artifact(
        self,
        now: datetime,
        window: ReportWindow,
        news_items: list[NewsItem],
        market_points: list[MarketPoint],
        market_metrics: list[MarketMetric],
        section_bodies: dict[str, str],
        report_path: Path,
        market_data_mode: str,
        health: dict[str, Any],
    ) -> None:
        self.data_processed_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": _fmt_dt(now),
            "window": {
                "start": _fmt_dt(window.start),
                "end": _fmt_dt(window.end),
            },
            "counts": {
                "news_items": len(news_items),
                "market_points": len(market_points),
                "market_metrics": len(market_metrics),
            },
            "market_data_mode": market_data_mode,
            "health": health,
            "sections": section_bodies,
            "report_path": str(report_path),
        }

        output_path = self.data_processed_dir / f"run-{now.strftime('%Y%m%d-%H%M%S')}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_last_run(self, tz: ZoneInfo) -> datetime | None:
        if not self.state_file.exists():
            return None

        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

        raw = data.get("last_run_at")
        if not raw:
            return None

        try:
            parsed = datetime.fromisoformat(str(raw))
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=tz)

        return parsed.astimezone(tz)

    def _write_last_run(self, now: datetime) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_run_at": now.isoformat()}
        self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
