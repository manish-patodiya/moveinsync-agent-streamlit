from core.ingestion.common_cleaners import (
    clean_non_negative_numeric,
    clean_numeric,
    normalize_alert_severity,
    normalize_id,
)
import pandas as pd


def test_comma_formatted_ids_normalize():
    s = pd.Series(["1,097,662", " 1123974 ", 1530200, "149,530"])
    assert normalize_id(s).tolist() == ["1097662", "1123974", "1530200", "149530"]


def test_comma_formatted_numerics_parse():
    s = pd.Series(["1,200", "10,644", "0", "1,777,595,400"])
    parsed = clean_numeric(s)
    assert parsed.tolist() == [1200.0, 10644.0, 0.0, 1777595400.0]


def test_negative_distances_become_null():
    s = pd.Series([-2.0, 9.9, -6.63, 0.0])
    cleaned = clean_non_negative_numeric(s)
    assert pd.isna(cleaned.iloc[0])
    assert cleaned.iloc[1] == 9.9
    assert pd.isna(cleaned.iloc[2])
    assert cleaned.iloc[3] == 0.0


def test_invalid_severity_false_becomes_null():
    s = pd.Series(["Sev-1", "False", None, "Sev-2", "nope"])
    out = normalize_alert_severity(s)
    assert out.iloc[0] == "Sev-1"
    assert pd.isna(out.iloc[1])
    assert pd.isna(out.iloc[2])
    assert out.iloc[3] == "Sev-2"
    assert pd.isna(out.iloc[4])
