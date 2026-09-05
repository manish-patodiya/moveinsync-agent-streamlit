from __future__ import annotations

from datetime import timedelta
from typing import Any

import duckdb

from core.models.workflow_state import AnalysisFilters


def prior_period_dates(filters: AnalysisFilters) -> tuple:
    """Return the immediately preceding window with the same inclusive length."""
    days = (filters.end_date - filters.start_date).days + 1
    return filters.start_date - timedelta(days=days), filters.start_date - timedelta(days=1)


def prior_period_filters(filters: AnalysisFilters) -> AnalysisFilters:
    start, end = prior_period_dates(filters)
    return filters.model_copy(update={"start_date": start, "end_date": end})


def _scope(
    filters: AnalysisFilters,
    *,
    alias: str = "",
    column: str | None = None,
    value: str | None = None,
) -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    clauses = [f"{prefix}trip_date BETWEEN ? AND ?"]
    params: list[Any] = [filters.start_date, filters.end_date]
    if filters.business_unit:
        clauses.append(f"{prefix}business_unit = ?")
        params.append(filters.business_unit)
    if filters.office:
        clauses.append(f"{prefix}office = ?")
        params.append(filters.office)
    if filters.shift:
        clauses.append(f"{prefix}shift_type = ?")
        params.append(filters.shift)
    if column and value is not None:
        clauses.append(f"{prefix}{column} = ?")
        params.append(value)
    return " AND ".join(clauses), params


def period_kpis(
    con: duckdb.DuckDBPyConnection,
    filters: AnalysisFilters,
) -> dict[str, float | int | None]:
    where, params = _scope(filters)
    row = con.execute(
        f"""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE is_ota_eligible),
               COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time),
               COUNT(*) FILTER (WHERE is_ota_eligible AND NOT is_on_time),
               COALESCE(SUM(alert_count), 0),
               COALESCE(SUM(billed_cost), 0)
        FROM mobility_trip_360 WHERE {where}
        """,
        params,
    ).fetchone()
    eligible = int(row[1])
    return {
        "trip_count": int(row[0]),
        "eligible_trips": eligible,
        "ota_pct": round(100.0 * int(row[2]) / eligible, 2) if eligible else None,
        "late_trips": int(row[3]),
        "alert_rate_per_trip": round(int(row[4]) / int(row[0]), 4) if row[0] else None,
        "total_billed_cost": float(row[5]),
    }


def ota_scope_benchmark(
    con: duckdb.DuckDBPyConnection,
    filters: AnalysisFilters,
    *,
    column: str,
    value: str,
    min_trips: int,
    min_peers: int = 2,
) -> dict[str, float | int | str | None]:
    """Compare one scope to its same-length history and current-period peers."""
    current_where, current_params = _scope(filters)
    prior = prior_period_filters(filters)
    prior_where, prior_params = _scope(prior, column=column, value=value)

    current = con.execute(
        f"""
        WITH grouped AS (
          SELECT {column} scope_value,
                 COUNT(*) FILTER (WHERE is_ota_eligible) eligible,
                 100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                   / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0) ota
          FROM mobility_trip_360
          WHERE {current_where} AND {column} IS NOT NULL
          GROUP BY {column}
          HAVING COUNT(*) FILTER (WHERE is_ota_eligible) >= ?
        ), ranked AS (
          SELECT *,
                 RANK() OVER (ORDER BY ota DESC) performance_rank,
                 COUNT(*) OVER () peer_count
          FROM grouped
        )
        SELECT eligible, ota,
               (SELECT MEDIAN(peer.ota) FROM grouped peer
                WHERE peer.scope_value <> ranked.scope_value) peer_median,
               performance_rank, peer_count
        FROM ranked WHERE scope_value = ?
        """,
        [*current_params, min_trips, value],
    ).fetchone()
    previous = con.execute(
        f"""
        SELECT COUNT(*) FILTER (WHERE is_ota_eligible) eligible,
               100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                 / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0) ota
        FROM mobility_trip_360 WHERE {prior_where}
        """,
        prior_params,
    ).fetchone()

    current_ota = float(current[1]) if current else None
    peer = (
        float(current[2])
        if current and current[2] is not None and int(current[4]) - 1 >= min_peers
        else None
    )
    prior_count = int(previous[0])
    prior_ota = float(previous[1]) if previous[1] is not None and prior_count >= min_trips else None
    prior_start, prior_end = prior_period_dates(filters)
    return {
        "current_eligible_trips": int(current[0]) if current else 0,
        "prior_period_start": str(prior_start),
        "prior_period_end": str(prior_end),
        "prior_period_eligible_trips": prior_count,
        "prior_period_ota_pct": None if prior_ota is None else round(prior_ota, 2),
        "prior_period_delta_pp": (
            None if current_ota is None or prior_ota is None else round(current_ota - prior_ota, 2)
        ),
        "peer_median_ota_pct": None if peer is None else round(peer, 2),
        "peer_delta_pp": (
            None if current_ota is None or peer is None else round(current_ota - peer, 2)
        ),
        "peer_rank": int(current[3]) if current else None,
        "peer_count": int(current[4]) if current else 0,
        "peer_group": f"other {column.replace('_', ' ')} values in the selected scope",
    }
