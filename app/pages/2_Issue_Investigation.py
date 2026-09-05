import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import streamlit as st

from app import require_service
from app.ui import ISSUE_ICON, severity_chip, source_chip, status_chip

st.set_page_config(page_title="Issue Investigation · Mobility Pulse", page_icon="🔍", layout="wide")
st.title("Issue Investigation")
require_service()
result = st.session_state.get("pulse_result")
if not result or not result["candidate_issues"]:
    st.info("Run Mobility Pulse from the Command Centre first, then return here.")
    st.stop()

labels = {
    f"{ISSUE_ICON.get(str(i.issue_type), '•')} {i.severity} · {i.title}": i
    for i in result["candidate_issues"]
}
issue = labels[st.selectbox("Select an issue", list(labels))]
reasoning = result["reasoning_outputs"].get(issue.issue_id)

st.markdown(
    f"{severity_chip(str(issue.severity))} &nbsp; {source_chip(reasoning)}",
    unsafe_allow_html=True,
)
st.markdown(f"## {issue.title}")

cols = st.columns(4)
cols[0].metric(issue.current_metric.replace("_", " ").title(), issue.current_value)
cols[1].metric("Affected trips", f"{issue.affected_trip_count:,}")
cols[2].metric(
    "Employees affected",
    "—" if issue.affected_employee_count is None else f"{issue.affected_employee_count:,}",
)
cols[3].metric("Data confidence", issue.data_confidence)

scope = {k: v for k, v in issue.business_scope.items() if v}
if scope:
    st.caption(" · ".join(f"**{k.replace('_', ' ').title()}:** {v}" for k, v in scope.items()))

evidence_col, context_col = st.columns(2)
with evidence_col:
    st.markdown("#### Evidence from Sense")
    st.caption("Every row below comes from deterministic DuckDB SQL.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Evidence": item.label,
                    "Value": str(item.value),
                    "Comparison": item.comparison or "—",
                }
                for item in issue.evidence
            ]
        ),
        hide_index=True,
        width="stretch",
    )

with context_col:
    st.markdown("#### SLA, peer and baseline context")
    if issue.comparisons:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Benchmark": label.replace("_", " ").title(),
                        "Value": "Unavailable" if value is None else str(value),
                    }
                    for label, value in issue.comparisons.items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("This rule triggers on an absolute condition, so it needs no benchmark.")
    st.markdown("#### Allowed action types")
    st.caption("Act may only choose from this list, whatever the model suggests.")
    for action_type in issue.allowed_action_types:
        st.markdown(f"- `{action_type}`")

st.divider()
st.markdown("#### Manager explanation from Reason")
if reasoning is None:
    st.info(
        "Reason only runs on high and critical issues, so this one was left to the daily brief."
    )
else:
    output = reasoning.output
    st.markdown(f"**Summary.** {output.manager_summary}")
    st.markdown(f"**Operational read.** {output.operational_interpretation}")
    st.warning(f"**Urgency.** {output.urgency_reason}")
    st.markdown("**Recommended next steps**")
    for step in output.recommended_actions:
        st.markdown(f"- {step}")
    if reasoning.fallback_reason:
        st.caption(f"Template fallback used because: {reasoning.fallback_reason}")
    st.caption(output.caveat or f"Data confidence: {issue.data_confidence}")

st.divider()
st.markdown("#### Actions proposed by Act")
actions = [a for a in st.session_state.get("actions", []) if a.issue_id == issue.issue_id]
if not actions:
    st.caption("No action was generated for this issue under the current automation settings.")
for action in actions:
    with st.container(border=True):
        st.markdown(
            f"{status_chip(str(action.status))} &nbsp; **{action.action_type}**",
            unsafe_allow_html=True,
        )
        st.caption(action.rationale)
        if action.requires_human_approval:
            st.caption("Requires your approval before a simulated send.")
st.page_link("pages/3_Actions_and_Audit.py", label="Go to Actions and Audit →")
