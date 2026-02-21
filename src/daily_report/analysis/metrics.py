from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from daily_report.models import MarketMetric, MarketPoint


def _pct_change(current: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0
    return ((current - baseline) / baseline) * 100


def build_market_metrics(points: list[MarketPoint]) -> list[MarketMetric]:
    grouped: dict[tuple[str, str], list[MarketPoint]] = defaultdict(list)
    for point in points:
        grouped[(point.region_code, point.asset)].append(point)

    metrics: list[MarketMetric] = []

    for (_, _), series in grouped.items():
        series.sort(key=lambda x: x.observed_at)
        latest = series[-1]
        prev = series[-2] if len(series) > 1 else latest

        week_cutoff = latest.observed_at - timedelta(days=7)
        week_ref = next((item for item in reversed(series) if item.observed_at <= week_cutoff), series[0])

        metric = MarketMetric(
            region_name=latest.region_name,
            region_code=latest.region_code,
            asset=latest.asset,
            current_avg_price=latest.avg_price,
            daily_change_pct=round(_pct_change(latest.avg_price, prev.avg_price), 2),
            weekly_change_pct=round(_pct_change(latest.avg_price, week_ref.avg_price), 2),
            current_txn_count=latest.txn_count,
            txn_daily_change_pct=round(_pct_change(float(latest.txn_count), float(prev.txn_count)), 2),
        )
        metrics.append(metric)

    metrics.sort(key=lambda x: abs(x.daily_change_pct), reverse=True)
    return metrics


def extract_top_movers(metrics: list[MarketMetric], limit: int = 5) -> list[MarketMetric]:
    return metrics[:limit]
