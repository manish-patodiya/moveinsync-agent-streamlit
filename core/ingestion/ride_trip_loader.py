from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.ingestion.common_cleaners import (
    clean_boolean,
    clean_non_negative_numeric,
    clean_numeric,
    epoch_to_timestamp,
    normalize_category,
    normalize_id,
    parse_datetime,
)

RIDE_GLOB = "Ride_data _trip-*.csv"


def discover_ride_files(data_dir: Path) -> list[Path]:
    return sorted(data_dir.glob(RIDE_GLOB))


def _clean_one(path: Path, allowed_delay_minutes: float) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    df["source_file"] = path.name
    df["trip_id"] = normalize_id(df["trip_id"])
    df["business_unit"] = normalize_category(df["business_unit"])
    df["office"] = normalize_category(df["office"])
    df["product_type"] = normalize_category(df["product_type"])
    df["trip_date"] = parse_datetime(df["trip_date"]).dt.normalize()
    df["shift_type"] = normalize_category(df["shift_type"])
    df["trip_direction"] = normalize_category(df["trip_direction"])
    df["vendor_id"] = normalize_category(df["vendor_id"])
    df["route_source"] = normalize_category(df["route_source"])
    df["trip_nodal"] = normalize_category(df["trip_nodal"])
    df["actual_cab_fuel_type"] = normalize_category(df["actual_cab_fuel_type"])
    df["delay_reason"] = normalize_category(df["delay_reason"])
    df["actual_cab_capacity"] = clean_numeric(df["actual_cab_capacity"])
    df["planned_trip_km"] = clean_non_negative_numeric(df["planned_km"])
    df["traveled_trip_km"] = clean_non_negative_numeric(df["traveled_km"])
    df["planned_start_ts"] = epoch_to_timestamp(df["planned_start_epoch"])
    df["planned_end_ts"] = epoch_to_timestamp(df["planned_end_epoch"])
    df["actual_start_ts"] = epoch_to_timestamp(df["actual_start_epoch"])
    df["actual_end_ts"] = epoch_to_timestamp(df["actual_end_epoch"])
    df["recorded_delay_minutes"] = clean_numeric(df["delay_minutes"])
    df["is_driver_nc"] = clean_boolean(df["is_driver_nc"])
    df["is_cab_nc"] = clean_boolean(df["is_cab_nc"])
    df["planned_employee_count"] = clean_numeric(df["plannedemployee_cnt"])
    df["actual_employee_count"] = clean_numeric(df["actualemployee_cnt"])
    df["no_show_count"] = clean_numeric(df["noshow_cnt"])
    delay = (df["actual_end_ts"] - df["planned_end_ts"]).dt.total_seconds() / 60.0
    df["calculated_delay_minutes"] = delay
    df["is_ota_eligible"] = df["planned_end_ts"].notna() & df["actual_end_ts"].notna()
    df["is_on_time"] = df["is_ota_eligible"] & (delay <= allowed_delay_minutes)
    cap = df["actual_cab_capacity"]
    df["utilization_pct"] = (df["actual_employee_count"] / cap * 100.0).where(cap > 0)
    return df[
        [
            "trip_id",
            "business_unit",
            "office",
            "product_type",
            "trip_date",
            "shift_type",
            "trip_direction",
            "vendor_id",
            "route_source",
            "trip_nodal",
            "actual_cab_capacity",
            "actual_cab_fuel_type",
            "planned_start_ts",
            "planned_end_ts",
            "actual_start_ts",
            "actual_end_ts",
            "calculated_delay_minutes",
            "recorded_delay_minutes",
            "delay_reason",
            "planned_trip_km",
            "traveled_trip_km",
            "is_driver_nc",
            "is_cab_nc",
            "planned_employee_count",
            "actual_employee_count",
            "no_show_count",
            "utilization_pct",
            "is_ota_eligible",
            "is_on_time",
            "source_file",
        ]
    ]


def dedupe_trip_spine(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Monthly files can repeat trip_id; keep one row per trip (latest trip_date)."""
    if df.empty:
        return df, 0
    before = len(df)
    ordered = df.sort_values(["trip_id", "trip_date", "source_file"], kind="mergesort")
    unique = ordered.drop_duplicates("trip_id", keep="last").reset_index(drop=True)
    return unique, before - len(unique)


def load_ride_trips(data_dir: Path, allowed_delay_minutes: float) -> tuple[pd.DataFrame, int]:
    files = discover_ride_files(data_dir)
    if not files:
        raise FileNotFoundError(f"No ride trip files matching {RIDE_GLOB} in {data_dir}")
    frames = [_clean_one(path, allowed_delay_minutes) for path in files]
    return dedupe_trip_spine(pd.concat(frames, ignore_index=True))
