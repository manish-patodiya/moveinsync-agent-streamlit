from __future__ import annotations

from typing import Any

import duckdb

from core.models.workflow_state import AnalysisFilters


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
    return {
        "trip_count": int(row[0]),
        "eligible_trips": eligible,
        "ota_pct": round(100.0 * int(row[2]) / eligible, 2) if eligible else None,
        "sla_target_pct": target_pct,
        "late_trips": int(row[3]),
        "employees_affected": int(row[4]),
        "safety_alerts": int(row[5]),
        "total_billed_cost": float(row[6]),
    }
