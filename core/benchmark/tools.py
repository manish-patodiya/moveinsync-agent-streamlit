from __future__ import annotations

from datetime import timedelta
from typing import Any

import duckdb

from core.models.benchmark import TrendPoint
from core.models.issue import CandidateIssue, IssueType
from core.models.workflow_state import AnalysisFilters
from core.sense.kpis import filter_sql

# Which metric benchmark_node compares vendors on, per issue type. Positive `gap` always means
# "worse than the benchmark" regardless of the metric's natural direction (see benchmark_agent).
METRIC_BY_ISSUE_TYPE: dict[IssueType, str] = {
    IssueType.VENDOR_OTA_BREACH: "ota_pct",
    IssueType.BILLING_ANOMALY: "cost_per_km",
    IssueType.SAFETY_ESCALATION: "alerts_per_trip",
}
WORSE_IS_LOWER = {"ota_pct"}


def scope_for_issue(issue: CandidateIssue, filters: AnalysisFilters) -> AnalysisFilters:
    """The business_unit/office cohort to enumerate peer vendors within. Deliberately drops
    the issue's own vendor/shift dimension -- benchmark_node's job is to compare *every*
    vendor in that cohort, not just the one the issue happened to name."""
    return AnalysisFilters(
        business_unit=issue.business_scope.get("business_unit") or filters.business_unit,
        office=issue.business_scope.get("office") or filters.office,
        start_date=filters.start_date,
        end_date=filters.end_date,
    )


def list_candidate_vendors(
    con: duckdb.DuckDBPyConnection, scope: AnalysisFilters, min_trips: int
) -> list[str]:
    """Tool: every vendor with enough trips in scope to be worth benchmarking, busiest first."""
    where, params = filter_sql(scope)
    rows = con.execute(
        f"""
        SELECT vendor_id, COUNT(*) n
        FROM mobility_trip_360
        WHERE {where} AND vendor_id IS NOT NULL
        GROUP BY vendor_id
        HAVING COUNT(*) >= ?
        ORDER BY n DESC
        """,
        [*params, min_trips],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _metric_sql(metric: str) -> tuple[str, str]:
    """Return (vendor grouping column, SELECT list) that computes one metric per vendor."""
    if metric == "ota_pct":
        return (
            "vendor_id",
            """
            100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0) AS value,
            COUNT(*) FILTER (WHERE is_ota_eligible AND NOT is_on_time) AS affected_trips,
            COALESCE(SUM(valid_employee_count) FILTER (
                WHERE is_ota_eligible AND NOT is_on_time
            ), 0) AS affected_employees
            """,
        )
    if metric == "cost_per_km":
        return (
            "COALESCE(billing_vendor, vendor_id)",
            """
            SUM(billed_cost) FILTER (WHERE billed_km > 0)
                / NULLIF(SUM(billed_km) FILTER (WHERE billed_km > 0), 0) AS value,
            COUNT(*) FILTER (WHERE billed_km > 0) AS affected_trips,
            NULL AS affected_employees
            """,
        )
    if metric == "alerts_per_trip":
        return (
            "vendor_id",
            """
            CAST(SUM(alert_count) AS DOUBLE) / NULLIF(COUNT(*), 0) AS value,
            SUM(alert_count) AS affected_trips,
            COALESCE(SUM(valid_employee_count) FILTER (
                WHERE sev1_alert_count > 0 OR has_panic_alert
            ), 0) AS affected_employees
            """,
        )
    raise ValueError(f"Unknown benchmark metric: {metric}")


def get_vendor_metric(
    con: duckdb.DuckDBPyConnection, vendor_id: str, scope: AnalysisFilters, metric: str
) -> dict[str, Any]:
    """Tool: one vendor's current standing on `metric` within `scope`."""
    vendor_col, select = _metric_sql(metric)
    where, params = filter_sql(scope)
    row = con.execute(
        f"SELECT {select} FROM mobility_trip_360 WHERE {where} AND {vendor_col} = ?",
        [*params, vendor_id],
    ).fetchone()
    value, affected_trips, affected_employees = row
    return {
        "value": None if value is None else float(value),
        "affected_trips": int(affected_trips or 0),
        "affected_employees": None if affected_employees is None else int(affected_employees),
    }


def get_peer_value(
    con: duckdb.DuckDBPyConnection,
    scope: AnalysisFilters,
    metric: str,
    exclude_vendor: str | None = None,
) -> float | None:
    """Tool: the peer benchmark for `metric` -- median for cost/km (skew-resistant), average
    otherwise -- computed across other vendors' own per-vendor values in scope."""
    vendor_col, select = _metric_sql(metric)
    where, params = filter_sql(scope)
    aggregate = "MEDIAN" if metric == "cost_per_km" else "AVG"
    exclude_clause = f"AND {vendor_col} != ?" if exclude_vendor else ""
    exclude_params = [exclude_vendor] if exclude_vendor else []
    row = con.execute(
        f"""
        WITH v AS (
            SELECT {vendor_col} AS vendor, {select}
            FROM mobility_trip_360
            WHERE {where} AND {vendor_col} IS NOT NULL {exclude_clause}
            GROUP BY {vendor_col}
        )
        SELECT {aggregate}(value) FROM v WHERE value IS NOT NULL
        """,
        [*params, *exclude_params],
    ).fetchone()
    return None if row is None or row[0] is None else float(row[0])


def get_trend_series(
    con: duckdb.DuckDBPyConnection,
    vendor_id: str,
    scope: AnalysisFilters,
    metric: str,
    num_periods: int,
    period_days: int,
    sla_target: float | None = None,
) -> list[TrendPoint]:
    """Tool: `num_periods` consecutive rolling windows of `period_days`, ending at scope's
    end_date, oldest first -- this is what lets Root Cause and Escalation Advisor talk about a
    trend instead of a single before/after delta."""
    points: list[TrendPoint] = []
    for i in range(num_periods):
        window_end = scope.end_date - timedelta(days=period_days * i)
        window_start = window_end - timedelta(days=period_days - 1)
        window_scope = AnalysisFilters(
            business_unit=scope.business_unit,
            office=scope.office,
            start_date=window_start,
            end_date=window_end,
        )
        result = get_vendor_metric(con, vendor_id, window_scope, metric)
        value = result["value"]
        gap = None if value is None or sla_target is None else round(sla_target - value, 2)
        points.append(
            TrendPoint(
                period_start=window_start,
                period_end=window_end,
                value=None if value is None else round(value, 2),
                sla_target=sla_target,
                gap=gap,
            )
        )
    points.reverse()
    return points


def classify_trend(
    points: list[TrendPoint], worse_is_lower: bool, flat_threshold: float = 0.5
) -> tuple[str, float]:
    """Tool: a least-squares slope over the trend series. The sign of the slope plus
    `worse_is_lower` (does a lower metric value mean worse, e.g. OTA%) decide the label."""
    values = [p.value for p in points if p.value is not None]
    if len(values) < 2:
        return "FLAT", 0.0
    n = len(values)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return "FLAT", 0.0
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / denominator
    if abs(slope) < flat_threshold:
        return "FLAT", slope
    worsening = slope < 0 if worse_is_lower else slope > 0
    return ("WORSENING" if worsening else "IMPROVING"), slope
