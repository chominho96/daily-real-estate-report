from __future__ import annotations

import argparse
from pathlib import Path

from daily_report.pipeline import DailyReportPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate daily real-estate report")
    parser.add_argument(
        "--root",
        default=".",
        help="Project root path (default: current working directory)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    pipeline = DailyReportPipeline(root=root)
    report_path = pipeline.run()
    print(report_path)


if __name__ == "__main__":
    main()
