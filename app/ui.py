"""Presentation helpers shared by the pages. No SQL and no LLM construction here."""

from __future__ import annotations

import streamlit as st

SEVERITY_COLOR = {
    "CRITICAL": "#b3261e",
    "HIGH": "#d76d00",
    "MEDIUM": "#8a7300",
    "LOW": "#4a6572",
}

ISSUE_ICON = {
    "VENDOR_OTA_BREACH": "⏱️",
    "SAFETY_ESCALATION": "🚨",
    "BILLING_ANOMALY": "💰",
    "DATA_QUALITY": "🧪",
}

STATUS_COLOR = {
    "PROPOSED": "#4a6572",
    "APPROVED": "#1a7f37",
    "REJECTED": "#b3261e",
    "REVIEWED": "#5b3fa8",
    "SIMULATED_SENT": "#0b6bcb",
}

STAGE_LABEL = {
    "bootstrap": ("Data", "CSV → DuckDB, grain invariant checked"),
    "sense_node": ("Sense", "Deterministic DuckDB SQL detects anomalies"),
    "reason_node": ("Reason", "Evidence explained in manager language"),
    "act_node": ("Act", "Fixed policy creates simulated actions"),
    "daily_brief_node": ("Brief", "Manager summary assembled"),
}


def chip(text: str, color: str) -> str:
    return (
        f"<span style='background:{color};color:#fff;padding:2px 9px;"
        f"border-radius:11px;font-size:0.74rem;font-weight:600;"
        f"letter-spacing:0.02em;white-space:nowrap'>{text}</span>"
    )


def severity_chip(severity: str) -> str:
    return chip(severity, SEVERITY_COLOR.get(severity, "#4a6572"))


def status_chip(status: str) -> str:
    return chip(status.replace("_", " "), STATUS_COLOR.get(status, "#4a6572"))


def source_chip(result) -> str:
    if result is None:
        return chip("NOT REASONED", "#4a6572")
    if result.source == "llm":
        latency = f" · {result.latency_seconds}s" if result.latency_seconds else ""
        return chip(f"LLM · {result.source_detail}{latency}", "#0b6bcb")
    return chip("DETERMINISTIC TEMPLATE", "#6b5b95")


def render_agent_timeline(trace: list) -> None:
    """Show what each LangGraph node actually did during the run."""
    st.markdown("#### Agent run")
    executed = {event.node for event in trace}
    columns = st.columns(len(STAGE_LABEL))
    for column, (node, (label, purpose)) in zip(columns, STAGE_LABEL.items()):
        event = next((e for e in trace if e.node == node), None)
        with column:
            if event is None:
                st.markdown(chip("SKIPPED", "#9aa0a6"), unsafe_allow_html=True)
                st.markdown(f"**{label}**")
                st.caption(
                    "Skipped by conditional routing."
                    if executed
                    else "Not reached in this run."
                )
                continue
            color = "#1a7f37" if event.status == "SUCCESS" else "#b3261e"
            timing = f" · {event.duration_seconds}s" if event.duration_seconds else ""
            st.markdown(chip(f"{event.status}{timing}", color), unsafe_allow_html=True)
            st.markdown(f"**{label}**")
            st.caption(event.description)
        column.caption(f":grey[{purpose}]")


def render_issue_card(issue, result, *, key_prefix: str = "") -> None:
    icon = ISSUE_ICON.get(str(issue.issue_type), "•")
    with st.container(border=True):
        header, metric = st.columns([4, 1])
        with header:
            st.markdown(
                f"{severity_chip(str(issue.severity))} &nbsp; {source_chip(result)}",
                unsafe_allow_html=True,
            )
            st.markdown(f"### {icon} {issue.title}")
        metric.metric(
            issue.current_metric.replace("_", " ").title(),
            issue.current_value,
        )
        facts = st.columns(3)
        facts[0].markdown(f"**Affected trips**  \n{issue.affected_trip_count:,}")
        facts[1].markdown(
            f"**Employees affected**  \n"
            f"{'—' if issue.affected_employee_count is None else f'{issue.affected_employee_count:,}'}"
        )
        facts[2].markdown(f"**Data confidence**  \n{issue.data_confidence}")

        if result is not None:
            st.markdown(f"**Why it matters:** {result.output.manager_summary}")
            st.markdown(f"**Recommended:** {'; '.join(result.output.recommended_actions)}")
        with st.expander("Evidence used by Sense"):
            for item in issue.evidence:
                suffix = f" — {item.comparison}" if item.comparison else ""
                st.markdown(f"- **{item.label}:** {item.value}{suffix}")
            if issue.comparisons:
                st.markdown("**Comparisons**")
                for label, value in issue.comparisons.items():
                    shown = "Unavailable" if value is None else value
                    st.markdown(f"- {label.replace('_', ' ').title()}: {shown}")


def render_llm_banner(status: dict) -> None:
    if status["active"]:
        st.caption(
            f"Reason is configured for **{status['provider']} · {status['model']}**. "
            "Each issue below shows whether the model or the template answered. "
            "Sense and Act stay deterministic."
        )
    else:
        st.caption(
            "Reason will use **deterministic templates** "
            f"(provider `{status['provider']}` is not configured). Sense and Act are unaffected."
        )
