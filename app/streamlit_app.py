import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st

from app import require_service
from app.ui import render_llm_banner

st.set_page_config(page_title="Mobility Pulse", page_icon="🚌", layout="wide")

service = require_service()
health = service.data_health()
llm = service.llm_status()

st.title("🚌 Mobility Pulse")
st.markdown("##### Transport Operations Agent — Sense → Reason → Act")
render_llm_banner(llm)

cols = st.columns(4)
cols[0].metric("Trips loaded", f"{health['grain_360_rows']:,}")
cols[1].metric("Sources", len(health["source_row_counts"]))
cols[2].metric(
    "Coverage",
    f"{health['date_coverage']['ride_trips_clean']['min_date']} → "
    f"{health['date_coverage']['ride_trips_clean']['max_date']}",
)
cols[3].metric("Trip grain", "Valid" if health["grain_invariant_ok"] else "Broken")

st.divider()
st.markdown("#### How the agent works")
stages = st.columns(3)
stages[0].info(
    "**1 · Sense**\n\nDuckDB SQL over `mobility_trip_360` finds punctuality, safety "
    "and billing anomalies. No LLM is involved, so every number is reproducible."
)
stages[1].info(
    "**2 · Reason**\n\nOnly aggregated evidence for high/critical issues is sent to the "
    "model, which explains impact and urgency. It never sees raw rows or employee IDs."
)
stages[2].info(
    "**3 · Act**\n\nA fixed policy turns issues into manager alerts and email drafts. "
    "Emails always wait for your approval before a simulated send."
)

st.divider()
left, right = st.columns([2, 1])
left.markdown(
    "**Start in Command Centre** (sidebar) to pick a business unit, office and date "
    "range, then run the agent. Issue Investigation shows the evidence behind any "
    "finding, and Actions and Audit is where you approve what the agent proposes."
)
right.warning("Email and alert actions are simulated. No real message is ever sent.")
