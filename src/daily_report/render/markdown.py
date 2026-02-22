from __future__ import annotations

from datetime import datetime
import re

from daily_report.models import RegionConfig, ReportWindow, SectionConfig


DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def _fmt_dt(value: datetime) -> str:
    return value.strftime(DATETIME_FMT)


def _fmt_data_mode(data_mode: str) -> str:
    mapping = {
        "public_api": "실거래 공공 API",
        "public_api_extended": "실거래 공공 API (최근 90일 확장 조회)",
        "public_api_empty": "실거래 공공 API (데이터 없음)",
        "public_api_error": "실거래 공공 API (호출 오류)",
        "synthetic": "샘플 데이터(폴백)",
    }
    return mapping.get(data_mode, data_mode)


def _fmt_asset(asset: str) -> str:
    return {
        "apartment": "아파트",
        "villa": "빌라",
        "officetel": "오피스텔",
    }.get(asset, asset)


def _format_report_label(report_path: str) -> str:
    filename = report_path.split("/")[-1].replace(".md", "")
    # Supported: YYYY-MM-DD, YYYY-MM-DD-HH-MM, legacy YYYY-MM-DD-HHMM
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:-(\d{2})-(\d{2})|-(\d{4}))?$", filename)
    if not match:
        return filename

    base_date = match.group(1)
    hour = match.group(2)
    minute = match.group(3)
    compact_hhmm = match.group(4)
    if compact_hhmm and len(compact_hhmm) == 4:
        hour, minute = compact_hhmm[:2], compact_hhmm[2:]
    if hour and minute:
        return f"{base_date} {hour}:{minute}"
    return base_date


def render_fixed_report(
    generated_at: datetime,
    window: ReportWindow,
    sections: list[SectionConfig],
    section_bodies: dict[str, str],
    regions: list[RegionConfig],
    data_mode: str,
) -> str:
    generated_at_text = _fmt_dt(generated_at)
    lines: list[str] = [
        f"# 일일 부동산 시황 보고서 ({generated_at_text})",
        "",
        "[홈으로 이동](../index.md)",
        "",
        f"- 생성 시각: {generated_at_text}",
        f"- 집계 구간: {_fmt_dt(window.start)} ~ {_fmt_dt(window.end)}",
        f"- 데이터 소스: {_fmt_data_mode(data_mode)}",
        "",
    ]

    for idx, section in enumerate(sections, start=1):
        lines.append(f"## {idx}. {section.title}")
        lines.append("")
        lines.append(section_bodies.get(section.id, "- 섹션 내용이 없습니다."))
        lines.append("")

    lines.extend(
        [
            "## 관심 지역 설정",
            "",
            "| 지역 | 코드 | 자산군 | 사용 여부 |",
            "|---|---|---|---|",
        ]
    )

    for region in regions:
        assets = ", ".join(_fmt_asset(asset) for asset in region.assets)
        enabled = "활성" if region.enabled else "비활성"
        lines.append(f"| {region.name} | {region.code} | {assets} | {enabled} |")

    lines.append("")
    lines.append("## 참고")
    lines.append("")
    lines.append("- 지역 설정은 `config/regions.yaml`에서 수정할 수 있습니다.")
    lines.append("- 같은 날짜에 여러 번 생성되면 파일명에 시:분이 함께 표시됩니다.")
    lines.append("- [홈으로 이동](../index.md)")

    return "\n".join(lines)


def render_docs_index(latest_reports: list[str]) -> str:
    lines = [
        "# 일일 부동산 시황 보고서",
        "",
        "매일 자동 생성되는 부동산 시황 보고서입니다.",
        "",
        "## 최신 보고서",
        "",
    ]

    if not latest_reports:
        lines.append("- 아직 생성된 보고서가 없습니다.")
    else:
        for report_path in latest_reports:
            lines.append(f"- [{_format_report_label(report_path)}]({report_path})")

    lines.append("")
    lines.append("## 지역 설정 방법")
    lines.append("")
    lines.append("`config/regions.yaml` 수정 후 커밋하면 다음 실행부터 반영됩니다.")
    return "\n".join(lines)
