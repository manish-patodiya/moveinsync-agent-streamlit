import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import streamlit as st

from app import require_service

st.set_page_config(page_title="Data Health · Mobility Pulse", page_icon="🧪", layout="wide")
st.title("Data Health")
st.caption("Sense refuses to run unless the trip-grain invariant holds.")
health = require_service().data_health()

if health["grain_invariant_ok"]:
    st.success(
        f"Trip grain valid — {health['grain_360_rows']:,} rows in `mobility_trip_360` match "
        f"{health['grain_distinct_trip_ids']:,} distinct trip IDs."
    )
else:
    st.error("Trip grain invariant failed; analytics are disabled.")

cols = st.columns(4)
cols[0].metric("Timestamp coverage", f"{health['timestamp_coverage_pct']:.1f}%")
cols[1].metric("Negative distances", f"{health['negative_employee_distance_count']:,}")
cols[2].metric("Invalid severities", f"{health['invalid_severity_count']:,}")
cols[3].metric("Duplicate rows collapsed", f"{health['duplicate_ride_trip_rows_collapsed']:,}")

st.markdown("#### Sources and date coverage")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Source": source,
                "Rows": rows,
                "Start": health["date_coverage"].get(source, {}).get("min_date"),
                "End": health["date_coverage"].get(source, {}).get("max_date"),
                "Unmatched trip IDs": health["unmatched_child_trip_ids"].get(source, 0),
            }
            for source, rows in health["source_row_counts"].items()
        ]
    ),
    hide_index=True,
    width="stretch",
)

left, right = st.columns(2)
with left:
    st.markdown("#### Cleaning exceptions")
    st.markdown(
        f"- Zero-km positive-cost billing lines: **{health['zero_km_positive_cost_count']:,}**\n"
        f"- Negative employee distances nulled: **{health['negative_employee_distance_count']:,}**\n"
        f"- Alert severities normalised from `False`: **{health['invalid_severity_count']:,}**\n"
        f"- Repeated trip rows across monthly files: **{health['duplicate_ride_trip_rows_collapsed']:,}**"
    )
    st.caption(
        "Unmatched trip IDs are child rows whose trip is absent from the spine; they are "
        "excluded from per-trip aggregates by design."
    )

with right:
    st.markdown("#### Analysis capabilities")
    st.caption("A capability switches off when its source data cannot support the rule.")
    for capability, enabled in health["analysis_capabilities"].items():
        st.markdown(
            f"{'✅' if enabled else '⛔'} {capability.replace('_', ' ').title()}"
        )

if health["errors"]:
    st.error(" · ".join(health["errors"]))
