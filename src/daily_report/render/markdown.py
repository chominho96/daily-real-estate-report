from __future__ import annotations

from datetime import datetime

from daily_report.models import RegionConfig, ReportWindow, SectionConfig


def render_fixed_report(
    generated_at: datetime,
    window: ReportWindow,
    sections: list[SectionConfig],
    section_bodies: dict[str, str],
    regions: list[RegionConfig],
    data_mode: str,
) -> str:
    lines: list[str] = [
        "# Daily Real-Estate Market Report",
        "",
        f"- Generated at: {generated_at.isoformat()}",
        f"- Window: {window.start.isoformat()} ~ {window.end.isoformat()}",
        f"- Data mode: {data_mode}",
        "",
    ]

    for idx, section in enumerate(sections, start=1):
        lines.append(f"## {idx}. {section.title}")
        lines.append("")
        lines.append(section_bodies.get(section.id, "- Empty section."))
        lines.append("")

    lines.extend(
        [
            "## Monitored Regions",
            "",
            "| Region | Code | Assets | Enabled |",
            "|---|---|---|---|",
        ]
    )

    for region in regions:
        assets = ", ".join(region.assets)
        lines.append(f"| {region.name} | {region.code} | {assets} | {region.enabled} |")

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Report structure is fixed; only section bodies are regenerated every run.")
    lines.append("- You can edit tracked regions in `config/regions.yaml`.")

    return "\n".join(lines)


def render_docs_index(latest_reports: list[str]) -> str:
    lines = [
        "# Real-Estate Daily Report",
        "",
        "Mobile-friendly daily market updates published by GitHub Actions.",
        "",
        "## Latest Reports",
        "",
    ]

    if not latest_reports:
        lines.append("- No report yet.")
    else:
        for report_path in latest_reports:
            name = report_path.split("/")[-1].replace(".md", "")
            lines.append(f"- [{name}]({report_path})")

    lines.append("")
    lines.append("## How To Update Regions")
    lines.append("")
    lines.append("Edit `config/regions.yaml` and commit.")
    return "\n".join(lines)
