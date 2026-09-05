from __future__ import annotations

import pandas as pd

_EMPTY = {"", "<NA>", "NA", "nan", "NaN", "None", "none"}
_TRUE = {"true", "1", "yes", "t", "y"}
_FALSE = {"false", "0", "no", "f", "n"}
_VALID_SEVERITY = {"Sev-1", "Sev-2", "Sev-3"}


def normalize_id(series: pd.Series) -> pd.Series:
    """Strip commas/whitespace and keep identifiers as canonical strings."""
    s = series.astype("string").str.replace(",", "", regex=False).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True)
    return s.mask(s.isna() | s.isin(_EMPTY), pd.NA)


def clean_numeric(series: pd.Series) -> pd.Series:
    """Strip commas and coerce to numeric; invalid values become NA."""
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    s = series.astype("string").str.replace(",", "", regex=False).str.strip()
    s = s.mask(s.isin(_EMPTY), pd.NA)
    return pd.to_numeric(s, errors="coerce")


def clean_non_negative_numeric(series: pd.Series) -> pd.Series:
    """Numeric cleaner that nulls physically invalid negative distances."""
    num = clean_numeric(series)
    return num.mask(num < 0)


def invalid_negative_mask(series: pd.Series) -> pd.Series:
    num = clean_numeric(series)
    return num.notna() & (num < 0)


def epoch_to_timestamp(series: pd.Series) -> pd.Series:
    seconds = clean_numeric(series)
    ts = pd.to_datetime(seconds, unit="s", errors="coerce", utc=True)
    return ts.dt.tz_convert(None)


def parse_datetime(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, errors="coerce", utc=True, format="mixed")
    return ts.dt.tz_convert(None)


def clean_boolean(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype("boolean")
    s = series.astype("string").str.strip().str.lower()
    out = pd.Series(pd.NA, index=series.index, dtype="boolean")
    return out.mask(s.isin(_TRUE), True).mask(s.isin(_FALSE), False)


def normalize_alert_severity(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    return s.where(s.isin(_VALID_SEVERITY), pd.NA)


def normalize_category(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    return s.mask(s.isna() | s.isin(_EMPTY), pd.NA)
