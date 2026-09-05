from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml

from core.database.duckdb_connection import create_memory_connection, load_dataframe
from core.database.mobility_360 import DataModelError, build_trip_aggregates_and_360
from core.ingestion.alerts_loader import load_safety_alerts
from core.ingestion.billing_loader import load_billing_lines
from core.ingestion.data_health import DataHealthReport, build_data_health
from core.ingestion.employee_loader import load_employee_legs
from core.ingestion.feedback_loader import load_feedback
from core.ingestion.ride_trip_loader import discover_ride_files, load_ride_trips

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_DATA_DIR = REPO_ROOT / "data"


class BootstrapError(Exception):
    pass


@dataclass
class AppContext:
    conn: duckdb.DuckDBPyConnection
    settings: dict[str, Any]
    sla: dict[str, Any]
    thresholds: dict[str, Any]
    quality_rules: dict[str, Any]
    impact_scoring: dict[str, Any]
    data_health: DataHealthReport
    data_dir: Path


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return yaml.safe_load(fh)


def load_configs(config_dir: Path = CONFIG_DIR) -> tuple[dict, dict, dict, dict]:
    return (
        load_yaml(config_dir / "settings.yaml"),
        load_yaml(config_dir / "sla_config.yaml"),
        load_yaml(config_dir / "anomaly_thresholds.yaml"),
        load_yaml(config_dir / "quality_rules.yaml"),
    )


def _missing_sources(data_dir: Path) -> list[str]:
    missing: list[str] = []
    if not discover_ride_files(data_dir):
        missing.append("Ride_data _trip-*.csv")
    for name in ("emp_data.csv", "emp_Data.csv"):
        if (data_dir / name).exists():
            break
    else:
        missing.append("emp_data.csv")
    for name in ("bill_data.csv", "alerts_data.csv", "trip_feedback.csv"):
        if not (data_dir / name).exists():
            missing.append(name)
    return missing


def bootstrap(
    data_dir: Path | None = None,
    config_dir: Path = CONFIG_DIR,
) -> AppContext:
    data_dir = data_dir or DEFAULT_DATA_DIR
    settings, sla, thresholds, quality_rules = load_configs(config_dir)
    impact_scoring = load_yaml(config_dir / "impact_scoring.yaml")
    missing = _missing_sources(data_dir)
    if missing:
        raise BootstrapError(
            f"Required CSV files missing under {data_dir}: {', '.join(missing)}"
        )

    allowed_delay = float(sla["service_levels"]["trip_end_ota"]["allowed_delay_minutes"])
    rides, duplicate_ride_trip_rows_collapsed = load_ride_trips(data_dir, allowed_delay)
    employees, negative_distance_count = load_employee_legs(data_dir)
    billing, zero_km_positive_cost_count = load_billing_lines(data_dir)
    alerts, invalid_severity_count = load_safety_alerts(data_dir)
    feedback = load_feedback(data_dir)

    conn = create_memory_connection()
    load_dataframe(conn, "ride_trips_clean", rides)
    load_dataframe(conn, "employee_legs_clean", employees)
    load_dataframe(conn, "billing_lines_clean", billing)
    load_dataframe(conn, "safety_alerts_clean", alerts)
    load_dataframe(conn, "feedback_clean", feedback)

    n_360, n_trips = build_trip_aggregates_and_360(
        conn,
        quality_rules["feedback"]["zero_rating_policy"],
    )
    health = build_data_health(
        conn,
        source_row_counts={
            "ride_trips_clean": len(rides),
            "employee_legs_clean": len(employees),
            "billing_lines_clean": len(billing),
            "safety_alerts_clean": len(alerts),
            "feedback_clean": len(feedback),
        },
        duplicate_ride_trip_rows_collapsed=duplicate_ride_trip_rows_collapsed,
        negative_employee_distance_count=negative_distance_count,
        invalid_severity_count=invalid_severity_count,
        zero_km_positive_cost_count=zero_km_positive_cost_count,
        grain_360_rows=n_360,
        grain_distinct_trip_ids=n_trips,
        min_timestamp_coverage_pct=float(thresholds["quality"]["min_timestamp_coverage_pct"]),
    )
    if not health.grain_invariant_ok:
        raise DataModelError("mobility_trip_360 grain invariant failed")
    return AppContext(
        conn=conn,
        settings=settings,
        sla=sla,
        thresholds=thresholds,
        quality_rules=quality_rules,
        impact_scoring=impact_scoring,
        data_health=health,
        data_dir=data_dir,
    )


if __name__ == "__main__":
    ctx = bootstrap()
    h = ctx.data_health
    print("source_row_counts", h.source_row_counts)
    print("mobility_trip_360", h.grain_360_rows)
    print("distinct_trip_ids", h.grain_distinct_trip_ids)
    print("grain_invariant_ok", h.grain_invariant_ok)
    print("duplicate_ride_trip_rows_collapsed", h.duplicate_ride_trip_rows_collapsed)
    print("timestamp_coverage_pct", h.timestamp_coverage_pct)
    print("negative_employee_distance_count", h.negative_employee_distance_count)
    print("invalid_severity_count", h.invalid_severity_count)
    print("zero_km_positive_cost_count", h.zero_km_positive_cost_count)
    print("unmatched_child_trip_ids", h.unmatched_child_trip_ids)
    print("date_coverage", {k: v.model_dump() for k, v in h.date_coverage.items()})
    print("analysis_capabilities", h.analysis_capabilities)
