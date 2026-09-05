from __future__ import annotations

from typing import Any

import duckdb

from core.benchmark.tools import (
    METRIC_BY_ISSUE_TYPE,
    WORSE_IS_LOWER,
    classify_trend,
    get_peer_value,
    get_trend_series,
    get_vendor_metric,
    list_candidate_vendors,
    scope_for_issue,
)
from core.models.benchmark import VendorBenchmark
from core.models.issue import CandidateIssue
from core.models.workflow_state import AnalysisFilters


def benchmark_issue(
    con: duckdb.DuckDBPyConnection,
    issue: CandidateIssue,
    filters: AnalysisFilters,
    sla: dict[str, Any],
    config: dict[str, Any],
) -> list[VendorBenchmark]:
    """benchmark_node's per-issue orchestration.

    Enumerates every vendor operating in the issue's business_unit/office cohort (not just
    the single vendor the issue happened to name) and benchmarks each one: current standing,
    peer comparison, and a multi-period trend. Every number comes from a tool call in
    `core.benchmark.tools` -- this function never computes evidence itself, so a future LLM
    layer added here could narrate the result but could never alter it.
    """
    metric = METRIC_BY_ISSUE_TYPE.get(issue.issue_type)
    if metric is None:
        return []
    cfg = config["benchmark"]
    scope = scope_for_issue(issue, filters)
    vendors = list_candidate_vendors(con, scope, int(cfg["min_trips_per_vendor"]))
    vendors = vendors[: int(cfg["max_vendors_per_issue"])]
    if not vendors:
        return []
    sla_target = (
        float(sla["service_levels"]["trip_end_ota"]["target_pct"]) if metric == "ota_pct" else None
    )
    worse_is_lower = metric in WORSE_IS_LOWER
    flat_threshold = float(cfg["trend_slope_flat_threshold"])
    benchmarks: list[VendorBenchmark] = []
    for vendor_id in vendors:
        current = get_vendor_metric(con, vendor_id, scope, metric)
        peer_value = get_peer_value(con, scope, metric, exclude_vendor=vendor_id)
        trend = get_trend_series(
            con,
            vendor_id,
            scope,
            metric,
            int(cfg["trend_periods"]),
            int(cfg["trend_period_days"]),
            sla_target,
        )
        direction, slope = classify_trend(trend, worse_is_lower, flat_threshold)
        value = current["value"]
        # Positive gap always means "worse than the benchmark," regardless of the metric's
        # natural direction, so downstream impact scoring can treat every metric uniformly.
        if value is None:
            gap = None
        elif sla_target is not None:
            gap = round(sla_target - value, 2)
        elif peer_value is not None:
            gap = round(value - peer_value, 2)
        else:
            gap = None
        benchmarks.append(
            VendorBenchmark(
                vendor_id=vendor_id,
                metric=metric,
                current_value=None if value is None else round(value, 2),
                peer_value=None if peer_value is None else round(peer_value, 2),
                sla_target=sla_target,
                gap=gap,
                affected_trips=current["affected_trips"],
                affected_employees=current["affected_employees"],
                trend=trend,
                trend_direction=direction,
                trend_slope=round(slope, 4),
            )
        )
    return benchmarks
