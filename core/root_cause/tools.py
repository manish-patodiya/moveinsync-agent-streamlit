from __future__ import annotations

import duckdb

from core.models.benchmark import VendorBenchmark
from core.models.workflow_state import AnalysisFilters
from core.sense.kpis import filter_sql


def compute_impact_score(benchmark: VendorBenchmark, weights: dict[str, float]) -> float:
    """Tool: deterministic negative-impact score for one vendor.

    Kept out of the LLM's hands on purpose -- "who is the culprit" must be reproducible and
    auditable, not a judgment call the model could vary between runs.
    """
    gap_magnitude = abs(benchmark.gap) if benchmark.gap is not None else 0.0
    trend_component = abs(benchmark.trend_slope) if benchmark.trend_direction == "WORSENING" else 0.0
    score = (
        weights["affected_trips"] * benchmark.affected_trips
        + weights["affected_employees"] * (benchmark.affected_employees or 0)
        + weights["gap_severity"] * gap_magnitude
        + weights["trend_slope"] * trend_component
    )
    return round(score, 2)


def get_delay_reason_breakdown(
    con: duckdb.DuckDBPyConnection, vendor_id: str, scope: AnalysisFilters, limit: int = 3
) -> list[dict]:
    """Tool: this vendor's top late-trip reasons in scope (OTA breaches only)."""
    where, params = filter_sql(scope)
    rows = con.execute(
        f"""
        SELECT delay_reason, COUNT(*) n
        FROM mobility_trip_360
        WHERE {where} AND vendor_id = ? AND is_ota_eligible AND NOT is_on_time
        GROUP BY delay_reason ORDER BY n DESC LIMIT ?
        """,
        [*params, vendor_id, limit],
    ).fetchall()
    total = sum(n for _, n in rows) or 1
    return [
        {"reason": reason or "UNKNOWN", "count": int(n), "pct": round(100.0 * n / total, 1)}
        for reason, n in rows
    ]


def get_shift_breakdown(
    con: duckdb.DuckDBPyConnection, vendor_id: str, scope: AnalysisFilters
) -> list[dict]:
    """Tool: this vendor's OTA% by shift in scope, so a breach can be localized to one shift
    rather than assumed to be vendor-wide. Route-level detail isn't available in the current
    schema (no per-trip route identifier), so shift is the finest cut we can offer."""
    where, params = filter_sql(scope)
    rows = con.execute(
        f"""
        SELECT shift_type,
               COUNT(*) FILTER (WHERE is_ota_eligible) eligible,
               100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                   / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0) ota
        FROM mobility_trip_360
        WHERE {where} AND vendor_id = ?
        GROUP BY shift_type
        HAVING COUNT(*) FILTER (WHERE is_ota_eligible) > 0
        ORDER BY ota ASC
        """,
        [*params, vendor_id],
    ).fetchall()
    return [
        {"shift": shift or "UNKNOWN", "eligible": int(eligible), "ota_pct": round(float(ota), 2)}
        for shift, eligible, ota in rows
    ]


def get_compliance_flags(
    con: duckdb.DuckDBPyConnection, vendor_id: str, scope: AnalysisFilters
) -> dict[str, float | None]:
    """Tool: this vendor's driver/cab non-compliance rate in scope."""
    where, params = filter_sql(scope)
    row = con.execute(
        f"""
        SELECT 100.0 * COUNT(*) FILTER (WHERE is_driver_nc) / NULLIF(COUNT(*), 0),
               100.0 * COUNT(*) FILTER (WHERE is_cab_nc) / NULLIF(COUNT(*), 0)
        FROM mobility_trip_360 WHERE {where} AND vendor_id = ?
        """,
        [*params, vendor_id],
    ).fetchone()
    driver_nc, cab_nc = row
    return {
        "driver_nc_pct": None if driver_nc is None else round(float(driver_nc), 1),
        "cab_nc_pct": None if cab_nc is None else round(float(cab_nc), 1),
    }
