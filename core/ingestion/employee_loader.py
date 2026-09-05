from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.ingestion.common_cleaners import (
    clean_boolean,
    clean_non_negative_numeric,
    epoch_to_timestamp,
    invalid_negative_mask,
    normalize_category,
    normalize_id,
    parse_datetime,
)

EMP_FILENAMES = ("emp_data.csv", "emp_Data.csv")


def discover_emp_file(data_dir: Path) -> Path:
    for name in EMP_FILENAMES:
        path = data_dir / name
        if path.exists():
            return path
    raise FileNotFoundError(f"No employee file {EMP_FILENAMES} in {data_dir}")


def load_employee_legs(data_dir: Path) -> tuple[pd.DataFrame, int]:
    df = pd.read_csv(discover_emp_file(data_dir), low_memory=False)
    negative_distance_count = int(
        (invalid_negative_mask(df["planned_km"]) | invalid_negative_mask(df["traveled_km"])).sum()
    )
    df["trip_id"] = normalize_id(df["trip_id"])
    df["stwid"] = normalize_id(df["stwid"])
    df["business_unit"] = normalize_category(df["business_unit"])
    df["office"] = normalize_category(df["office"])
    df["product_type"] = normalize_category(df["product_type"])
    df["trip_date"] = parse_datetime(df["trip_date"]).dt.normalize()
    df["shift_type"] = normalize_category(df["shift_type"])
    df["boarding_status"] = normalize_category(df["boarding_status"])
    df["planned_km"] = clean_non_negative_numeric(df["planned_km"])
    df["traveled_km"] = clean_non_negative_numeric(df["traveled_km"])
    df["planned_pickup_ts"] = epoch_to_timestamp(df["planned_pickup_epoch"])
    df["planned_drop_ts"] = epoch_to_timestamp(df["planned_drop_epoch"])
    df["actual_pickup_ts"] = epoch_to_timestamp(df["actual_pickup_epoch"])
    df["actual_drop_ts"] = epoch_to_timestamp(df["actual_drop_epoch"])
    df["is_no_show"] = clean_boolean(df["is_no_show"])
    df["pickup_delay_minutes"] = (
        df["actual_pickup_ts"] - df["planned_pickup_ts"]
    ).dt.total_seconds() / 60.0
    df["drop_delay_minutes"] = (
        df["actual_drop_ts"] - df["planned_drop_ts"]
    ).dt.total_seconds() / 60.0
    cleaned = df[
        [
            "trip_id",
            "stwid",
            "business_unit",
            "office",
            "product_type",
            "trip_date",
            "shift_type",
            "boarding_status",
            "is_no_show",
            "planned_km",
            "traveled_km",
            "planned_pickup_ts",
            "planned_drop_ts",
            "actual_pickup_ts",
            "actual_drop_ts",
            "pickup_delay_minutes",
            "drop_delay_minutes",
        ]
    ]
    return cleaned, negative_distance_count
