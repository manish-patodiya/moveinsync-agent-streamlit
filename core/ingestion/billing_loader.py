from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.ingestion.common_cleaners import clean_numeric, normalize_category, normalize_id, parse_datetime


def load_billing_lines(data_dir: Path) -> tuple[pd.DataFrame, int]:
    path = data_dir / "bill_data.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, low_memory=False)
    df["trip_id"] = normalize_id(df["trip_id"])
    df["business_unit"] = normalize_category(df["business_unit"])
    df["office"] = normalize_category(df["office"])
    df["vendor"] = normalize_category(df["vendor"])
    df["contract"] = normalize_category(df["contract"])
    df["slab_name"] = normalize_category(df["slab_name"])
    df["cycle_start"] = parse_datetime(df["cycle_start"])
    df["cycle_end"] = parse_datetime(df["cycle_end"])
    df["total_trip_km"] = clean_numeric(df["total_trip_km"])
    df["trip_cost"] = clean_numeric(df["trip_cost"])
    zero_km_positive_cost_count = int(
        ((df["trip_cost"] > 0) & (df["total_trip_km"].fillna(0) == 0)).sum()
    )
    cleaned = df[
        [
            "trip_id",
            "business_unit",
            "office",
            "vendor",
            "contract",
            "slab_name",
            "cycle_start",
            "cycle_end",
            "total_trip_km",
            "trip_cost",
        ]
    ]
    return cleaned, zero_km_positive_cost_count
