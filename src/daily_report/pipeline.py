from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from daily_report.analysis.metrics import build_market_metrics, extract_top_movers
from daily_report.collectors.news import fetch_policy_news
from daily_report.collectors.real_estate import fetch_market_points
from daily_report.llm.section_writer import SectionWriter
from daily_report.models import MarketMetric, MarketPoint, NewsItem, ReportWindow, SectionConfig
from daily_report.render.markdown import render_docs_index, render_fixed_report
from daily_report.settings import load_regions, load_report_config

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


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
        market_start = report_window.start - timedelta(days=8)

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
            )
            section_bodies[section.id] = writer.write(section, section_context)

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
        )
        self._write_last_run(now)

        return report_path

    def _build_section_context(
        self,
        section: SectionConfig,
        report_window: ReportWindow,
        news_items: list[NewsItem],
        market_metrics: list[MarketMetric],
        top_movers: list[MarketMetric],
        market_data_mode: str,
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
            base["metrics"] = [asdict(metric) for metric in market_metrics]
            return base

        if section.id == "insights":
            base["top_movers"] = [asdict(metric) for metric in top_movers]
            base["metric_count"] = len(market_metrics)
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
