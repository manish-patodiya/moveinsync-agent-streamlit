from __future__ import annotations

from typing import Any

import duckdb

from core.models.workflow_state import AnalysisFilters
from core.sense.benchmarks import period_kpis, prior_period_dates, prior_period_filters

# The source writes NODELAY when no reason was captured, so it is missing data rather than a
# cause. Counting it ranks "we don't know" above every real reason.
NON_DELAY_REASONS = ("NODELAY", "NA", "UNKNOWN")
RECORDED_DELAY_REASON = (
    "delay_reason IS NOT NULL AND delay_reason NOT IN "
    f"({', '.join(repr(reason) for reason in NON_DELAY_REASONS)})"
)


def filter_sql(filters: AnalysisFilters, alias: str = "") -> tuple[str, list[Any]]:
    p = f"{alias}." if alias else ""
    clauses = [f"{p}trip_date BETWEEN ? AND ?"]
    params: list[Any] = [filters.start_date, filters.end_date]
    if filters.business_unit:
        clauses.append(f"{p}business_unit = ?")
        params.append(filters.business_unit)
    if filters.office:
        clauses.append(f"{p}office = ?")
        params.append(filters.office)
    if filters.shift:
        clauses.append(f"{p}shift_type = ?")
        params.append(filters.shift)
    return " AND ".join(clauses), params


def calculate_kpis(
    con: duckdb.DuckDBPyConnection,
    filters: AnalysisFilters,
    target_pct: float,
) -> dict[str, Any]:
    where, params = filter_sql(filters)
    row = con.execute(
        f"""
        SELECT
            COUNT(*) AS trips,
            COUNT(*) FILTER (WHERE is_ota_eligible) AS eligible_trips,
            COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time) AS on_time_trips,
            COUNT(*) FILTER (WHERE is_ota_eligible AND NOT is_on_time) AS late_trips,
            COALESCE(SUM(valid_employee_count) FILTER (
                WHERE is_ota_eligible AND NOT is_on_time
            ), 0) AS employees_affected,
            COALESCE(SUM(alert_count), 0) AS safety_alerts,
            COALESCE(SUM(billed_cost), 0) AS total_billed_cost
        FROM mobility_trip_360
        WHERE {where}
        """,
        params,
    ).fetchone()
    eligible = int(row[1])
    result = {
        "trip_count": int(row[0]),
        "eligible_trips": eligible,
        "ota_pct": round(100.0 * int(row[2]) / eligible, 2) if eligible else None,
        "sla_target_pct": target_pct,
        "late_trips": int(row[3]),
        "employees_affected": int(row[4]),
        "safety_alerts": int(row[5]),
        "total_billed_cost": float(row[6]),
    }
    prior = period_kpis(con, prior_period_filters(filters))
    prior_start, prior_end = prior_period_dates(filters)
    prior_ota = prior["ota_pct"]
    result.update(
        {
            "prior_period_start": prior_start,
            "prior_period_end": prior_end,
            "prior_trip_count": prior["trip_count"],
            "prior_eligible_trips": prior["eligible_trips"],
            "prior_ota_pct": prior_ota,
            "ota_delta_pp": (
                None
                if result["ota_pct"] is None or prior_ota is None
                else round(float(result["ota_pct"]) - float(prior_ota), 2)
            ),
            "prior_late_trips": prior["late_trips"],
            "prior_alert_rate_per_trip": prior["alert_rate_per_trip"],
        }
    )
    return result
