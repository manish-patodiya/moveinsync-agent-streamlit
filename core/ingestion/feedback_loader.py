from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.ingestion.common_cleaners import clean_numeric, normalize_category, normalize_id, parse_datetime


def load_feedback(data_dir: Path) -> pd.DataFrame:
    path = data_dir / "trip_feedback.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, low_memory=False)
    df["trip_id"] = normalize_id(df["trip_id"])
    df["stwid"] = normalize_id(df["stwid"])
    df["business_unit"] = normalize_category(df["business_unit"])
    df["trip_type"] = normalize_category(df["trip_type"])
    df["trip_date"] = parse_datetime(df["trip_date"])
    df["creation_time"] = parse_datetime(df["creation_time"])
    for col in ("route_rating", "driver_rating", "cab_rating", "safety_rating", "marshal_rating"):
        df[col] = clean_numeric(df[col])
    return df[
        [
            "trip_id",
            "stwid",
            "business_unit",
            "trip_type",
            "trip_date",
            "creation_time",
            "route_rating",
            "driver_rating",
            "cab_rating",
            "safety_rating",
            "marshal_rating",
        ]
    ]
