import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import streamlit as st

from app import require_service
from app.ui import ISSUE_ICON, render_agent_timeline, render_issue_card, render_llm_banner
from core.models.issue import Severity
from core.models.workflow_state import AnalysisFilters

st.set_page_config(page_title="Command Centre · Mobility Pulse", page_icon="🚌", layout="wide")
st.title("Command Centre")
service = require_service()
options = service.filter_options()
defaults = service.default_filters()
render_llm_banner(service.llm_status())

with st.sidebar:
    st.header("Analysis filters")
    bu_options = ["All", *options["business_units"]]
    bu_index = bu_options.index(defaults.business_unit) if defaults.business_unit in bu_options else 0
    business_unit = st.selectbox("Business unit", bu_options, index=bu_index)
    office = st.selectbox("Office", ["All", *options["offices"]])
    date_range = st.date_input(
        "Date range",
        value=(defaults.start_date, defaults.end_date),
        min_value=options["min_date"],
        max_value=options["max_date"],
    )
    run_clicked = st.button("▶ Run Mobility Pulse", type="primary", width="stretch")
    st.caption("Sense runs on DuckDB; Reason only sees aggregated evidence.")

if run_clicked:
    if len(date_range) != 2:
        st.error("Select both a start and an end date.")
    else:
        filters = AnalysisFilters(
            business_unit=None if business_unit == "All" else business_unit,
            office=None if office == "All" else office,
            start_date=date_range[0],
            end_date=date_range[1],
        )
        with st.status("Running the agent…", expanded=True) as status:
            result = service.run(filters, progress=st.write)
            status.update(label="Agent run complete", state="complete", expanded=False)
        st.session_state["pulse_result"] = result
        service.save_actions(result["actions"])
        st.session_state["actions"] = service.saved_actions()
        st.session_state["audit_trail"] = service.action_audit()

result = st.session_state.get("pulse_result")
if not result:
    st.info("Set your filters in the sidebar, then run Mobility Pulse.")
    st.stop()

if result["errors"]:
    st.error(" · ".join(result["errors"]))

render_agent_timeline(result["workflow_trace"])
st.divider()

kpi = result["kpi_summary"]
issues = result["candidate_issues"]
critical = sum(issue.severity == Severity.CRITICAL for issue in issues)
high = sum(issue.severity == Severity.HIGH for issue in issues)
ota = kpi.get("ota_pct")
target = kpi.get("sla_target_pct")

st.markdown("#### Operating picture")
row = st.columns(4)
row[0].metric(
    "Trip-end OTA",
    f"{ota}%" if ota is not None else "N/A",
    delta=(
        f"{kpi['ota_delta_pp']} pp vs {kpi['prior_period_start']}–{kpi['prior_period_end']}"
        if kpi.get("ota_delta_pp") is not None
        else None if ota is None or target is None else f"{round(ota - target, 2)} pp vs SLA"
    ),
    delta_color="normal",
)
row[1].metric("Trips analysed", f"{kpi.get('trip_count', 0):,}")
row[2].metric("Late trips", f"{kpi.get('late_trips', 0):,}")
row[3].metric("Employees affected", f"{kpi.get('employees_affected', 0):,}")
row = st.columns(4)
row[0].metric("Critical issues", critical)
row[1].metric("High issues", high)
row[2].metric("Safety alerts", f"{kpi.get('safety_alerts', 0):,}")
row[3].metric("Billed cost", f"{kpi.get('total_billed_cost', 0):,.0f}")

st.info(f"**Daily brief** — {result['daily_brief']}")

if issues:
    st.markdown("#### Where the problems are")
    breakdown = (
        pd.DataFrame(
            [
                {"Issue type": str(issue.issue_type), "Severity": str(issue.severity)}
                for issue in issues
            ]
        )
        .value_counts()
        .reset_index(name="Issues")
    )
    chart, table = st.columns([2, 1])
    chart.bar_chart(
        breakdown.pivot(index="Issue type", columns="Severity", values="Issues").fillna(0),
        height=260,
    )
    table.dataframe(breakdown, hide_index=True, width="stretch", height=260)

st.divider()
st.markdown("#### Issues")
reasoned_ids = set(result["reasoning_outputs"])
tab_focus, tab_all = st.tabs(
    [f"Agent focus ({len(result['prioritized_issues'])})", f"All detected ({len(issues)})"]
)

with tab_focus:
    st.caption(
        "These passed the severity gate, so Reason explained them and Act proposed responses."
    )
    if not result["prioritized_issues"]:
        st.success("Nothing reached the high or critical threshold in this period.")
    for issue in result["prioritized_issues"]:
        render_issue_card(issue, result["reasoning_outputs"].get(issue.issue_id))

with tab_all:
    if not issues:
        st.success("No operational anomalies met the configured thresholds.")
        st.stop()
    filter_row = st.columns(2)
    chosen_severity = filter_row[0].multiselect(
        "Severity",
        ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        default=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
    )
    chosen_types = filter_row[1].multiselect(
        "Issue type",
        sorted({str(issue.issue_type) for issue in issues}),
        default=sorted({str(issue.issue_type) for issue in issues}),
    )
    visible = [
        issue
        for issue in issues
        if str(issue.severity) in chosen_severity and str(issue.issue_type) in chosen_types
    ]
    st.caption(f"Showing {len(visible)} of {len(issues)} detected issues.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "": ISSUE_ICON.get(str(issue.issue_type), "•"),
                    "Severity": str(issue.severity),
                    "Title": issue.title,
                    "Metric": issue.current_metric,
                    "Value": issue.current_value,
                    "Trips": issue.affected_trip_count,
                    "Reasoned": "Yes" if issue.issue_id in reasoned_ids else "No",
                }
                for issue in visible
            ]
        ),
        hide_index=True,
        width="stretch",
        height=420,
    )
