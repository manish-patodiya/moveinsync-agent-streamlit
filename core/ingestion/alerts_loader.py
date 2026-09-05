from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.ingestion.common_cleaners import (
    normalize_alert_severity,
    normalize_category,
    normalize_id,
    parse_datetime,
)


def load_safety_alerts(data_dir: Path) -> tuple[pd.DataFrame, int]:
    path = data_dir / "alerts_data.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, low_memory=False)
    raw_severity = df["severity"].astype("string").str.strip()
    invalid_severity_count = int((raw_severity == "False").sum())
    df["trip_id"] = normalize_id(df["trip_id"])
    df["stwid"] = normalize_id(df["stwid"])
    df["business_unit"] = normalize_category(df["business_unit"])
    df["event_id"] = normalize_category(df["event_id"])
    df["event_type"] = normalize_category(df["event_type"])
    df["state_text"] = normalize_category(df["state_text"])
    df["source"] = normalize_category(df["source"])
    df["severity"] = normalize_alert_severity(df["severity"])
    df["start_ts"] = parse_datetime(df["start_time"])
    df["acknowledge_ts"] = parse_datetime(df["acknowledge_time"])
    df["acknowledgement_minutes"] = (
        df["acknowledge_ts"] - df["start_ts"]
    ).dt.total_seconds() / 60.0
    cleaned = df[
        [
            "trip_id",
            "stwid",
            "business_unit",
            "event_id",
            "event_type",
            "state_text",
            "severity",
            "source",
            "start_ts",
            "acknowledge_ts",
            "acknowledgement_minutes",
        ]
    ]
    return cleaned, invalid_severity_count
