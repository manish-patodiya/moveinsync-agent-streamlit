from __future__ import annotations

from typing import Any

import duckdb
from pydantic import BaseModel, Field


class DateCoverage(BaseModel):
    min_date: str | None = None
    max_date: str | None = None


class DataHealthReport(BaseModel):
    source_row_counts: dict[str, int] = Field(default_factory=dict)
    date_coverage: dict[str, DateCoverage] = Field(default_factory=dict)
    duplicate_ride_trip_rows_collapsed: int = 0
    negative_employee_distance_count: int = 0
    invalid_severity_count: int = 0
    timestamp_coverage_pct: float | None = None
    zero_km_positive_cost_count: int = 0
    unmatched_child_trip_ids: dict[str, int] = Field(default_factory=dict)
    grain_invariant_ok: bool = False
    grain_360_rows: int | None = None
    grain_distinct_trip_ids: int | None = None
    analysis_capabilities: dict[str, bool] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return int(con.execute(sql).fetchone()[0])


def _date_span(con: duckdb.DuckDBPyConnection, sql: str) -> DateCoverage:
    row = con.execute(sql).fetchone()
    lo, hi = row[0], row[1]
    return DateCoverage(
        min_date=None if lo is None else str(lo)[:10],
        max_date=None if hi is None else str(hi)[:10],
    )


def build_data_health(
    con: duckdb.DuckDBPyConnection,
    *,
    source_row_counts: dict[str, int],
    duplicate_ride_trip_rows_collapsed: int,
    negative_employee_distance_count: int,
    invalid_severity_count: int,
    zero_km_positive_cost_count: int,
    grain_360_rows: int,
    grain_distinct_trip_ids: int,
    min_timestamp_coverage_pct: float,
) -> DataHealthReport:
    timestamp_coverage_pct = con.execute(
        """
        SELECT 100.0 * COUNT(*) FILTER (
            WHERE planned_end_ts IS NOT NULL AND actual_end_ts IS NOT NULL
        ) / NULLIF(COUNT(*), 0)
        FROM ride_trips_clean
        """
    ).fetchone()[0]
    unmatched = {
        "employee_legs_clean": _scalar(
            con,
            """
            SELECT COUNT(DISTINCT e.trip_id)
            FROM employee_legs_clean e
            WHERE e.trip_id NOT IN (SELECT trip_id FROM ride_trips_clean)
            """,
        ),
        "billing_lines_clean": _scalar(
            con,
            """
            SELECT COUNT(DISTINCT b.trip_id)
            FROM billing_lines_clean b
            WHERE b.trip_id NOT IN (SELECT trip_id FROM ride_trips_clean)
            """,
        ),
        "safety_alerts_clean": _scalar(
            con,
            """
            SELECT COUNT(DISTINCT a.trip_id)
            FROM safety_alerts_clean a
            WHERE a.trip_id NOT IN (SELECT trip_id FROM ride_trips_clean)
            """,
        ),
        "feedback_clean": _scalar(
            con,
            """
            SELECT COUNT(DISTINCT f.trip_id)
            FROM feedback_clean f
            WHERE f.trip_id NOT IN (SELECT trip_id FROM ride_trips_clean)
            """,
        ),
    }
    ts_ok = timestamp_coverage_pct is not None and float(timestamp_coverage_pct) >= min_timestamp_coverage_pct
    capabilities = {
        "punctuality": source_row_counts.get("ride_trips_clean", 0) > 0 and ts_ok,
        "safety": source_row_counts.get("safety_alerts_clean", 0) > 0,
        "billing": source_row_counts.get("billing_lines_clean", 0) > 0,
        "feedback": source_row_counts.get("feedback_clean", 0) > 0,
        "employee": source_row_counts.get("employee_legs_clean", 0) > 0,
        "mobility_360": grain_360_rows == grain_distinct_trip_ids,
    }
    return DataHealthReport(
        source_row_counts=source_row_counts,
        date_coverage={
            "ride_trips_clean": _date_span(
                con, "SELECT MIN(trip_date), MAX(trip_date) FROM ride_trips_clean"
            ),
            "employee_legs_clean": _date_span(
                con, "SELECT MIN(trip_date), MAX(trip_date) FROM employee_legs_clean"
            ),
            "billing_lines_clean": _date_span(
                con, "SELECT MIN(cycle_start), MAX(cycle_end) FROM billing_lines_clean"
            ),
            "safety_alerts_clean": _date_span(
                con, "SELECT MIN(start_ts), MAX(start_ts) FROM safety_alerts_clean"
            ),
            "feedback_clean": _date_span(
                con, "SELECT MIN(trip_date), MAX(trip_date) FROM feedback_clean"
            ),
        },
        duplicate_ride_trip_rows_collapsed=duplicate_ride_trip_rows_collapsed,
        negative_employee_distance_count=negative_employee_distance_count,
        invalid_severity_count=invalid_severity_count,
        timestamp_coverage_pct=None if timestamp_coverage_pct is None else float(timestamp_coverage_pct),
        zero_km_positive_cost_count=zero_km_positive_cost_count,
        unmatched_child_trip_ids=unmatched,
        grain_invariant_ok=grain_360_rows == grain_distinct_trip_ids,
        grain_360_rows=grain_360_rows,
        grain_distinct_trip_ids=grain_distinct_trip_ids,
        analysis_capabilities=capabilities,
    )
