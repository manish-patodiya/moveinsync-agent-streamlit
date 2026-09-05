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
    "SHIFT_READINESS": "🧭",
}

STATUS_COLOR = {
    "PROPOSED": "#4a6572",
    "APPROVED": "#1a7f37",
    "REJECTED": "#b3261e",
    "REVIEWED": "#5b3fa8",
    "SIMULATED_SENT": "#0b6bcb",
    "SIMULATED_COMPLETED": "#0b6bcb",
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


def role_chip(role: str) -> str:
    return chip(role.replace("_", " "), "#5b3fa8" if role == "SHIFT_MANAGER" else "#0b6bcb")


def render_benchmarks(issue) -> None:
    comparisons = issue.comparisons
    current = issue.current_value
    items = [
        (
            "Current",
            current,
            None,
        ),
        (
            "SLA",
            comparisons.get("sla_target_pct"),
            (
                None
                if comparisons.get("sla_target_pct") is None
                else f"{round(float(current) - float(comparisons['sla_target_pct']), 2)} pp"
            ),
        ),
        (
            "Matching prior period",
            comparisons.get("prior_period_ota_pct"),
            (
                None
                if comparisons.get("prior_period_delta_pp") is None
                else f"{comparisons['prior_period_delta_pp']} pp"
            ),
        ),
        (
            "Peer median",
            comparisons.get("peer_median_ota_pct"),
            (
                None
                if comparisons.get("peer_delta_pp") is None
                else f"{comparisons['peer_delta_pp']} pp"
            ),
        ),
    ]
    visible = [(label, value, delta) for label, value, delta in items if value is not None]
    if len(visible) < 2:
        return
    st.caption("Deterministic benchmark context")
    for column, (label, value, delta) in zip(st.columns(len(visible)), visible):
        suffix = "%" if isinstance(value, (int, float)) else ""
        column.metric(label, f"{value}{suffix}", delta=delta)


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
        render_benchmarks(issue)

        if result is not None:
            st.markdown(f"**Why it matters:** {result.output.manager_summary}")
            if result.output.role_recommendations:
                st.markdown("**Who should do what**")
                for recommendation in result.output.role_recommendations:
                    st.markdown(
                        f"{role_chip(recommendation.role)} &nbsp; "
                        f"**{recommendation.action}**  \n"
                        f"{recommendation.rationale}  \n"
                        f":grey[Outcome: {recommendation.expected_outcome} · "
                        f"Monitor: {recommendation.monitoring_condition}]",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(f"**Recommended:** {'; '.join(result.output.recommended_actions)}")
        st.markdown("**Evidence used by Sense**")
        for item in issue.evidence[:3]:
            suffix = f" — {item.comparison}" if item.comparison else ""
            st.markdown(f"- **{item.label}:** {item.value}{suffix}")
        with st.expander("All evidence and comparisons"):
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


def render_pulse_card(card) -> None:
    with st.container(border=True):
        st.markdown(
            f"{severity_chip(card.severity)} &nbsp; **{card.title}**",
            unsafe_allow_html=True,
        )
        st.markdown(f"**What happened.** {card.what_happened}")
        st.markdown(f"**Compared with.** {card.compared_with}")
        st.markdown(f"**Why it matters.** {card.why_it_matters}")
        st.markdown(f"**What to do now.** {card.what_to_do}")
        for caveat in card.caveats:
            st.caption(caveat)


def render_readiness_roster(roster: list[dict]) -> None:
    if not roster:
        st.caption("No unready riders in this office/shift window.")
        return
    st.dataframe(roster, hide_index=True, width="stretch")


def render_vendor_scorecard(rows: list[dict], *, key: str | None = None):
    if not rows:
        st.caption("No vendor scorecard rows for this scope.")
        return
    st.dataframe(rows, hide_index=True, width="stretch", key=key)


def render_vendor_drill(drill: dict) -> None:
    st.markdown(f"**{drill['vendor']}** · delay reasons are recorded signals, not proven causes.")
    facts = st.columns(3)
    facts[0].metric("Alerts", drill.get("alerts"))
    facts[1].metric("Open alerts", drill.get("open_alerts"))
    facts[2].metric("Sev-1 alerts", drill.get("sev1_alerts"))
    if drill.get("delay_reasons"):
        st.caption("Top delay reasons")
        st.dataframe(drill["delay_reasons"], hide_index=True, width="stretch")
    if drill.get("offices"):
        st.caption("Offices and shifts")
        st.dataframe(drill["offices"], hide_index=True, width="stretch")
    if drill.get("impacted_trips"):
        st.caption("Impacted trips (ids only)")
        st.dataframe(drill["impacted_trips"], hide_index=True, width="stretch")


def render_rider_drill(row: dict, trip: dict) -> None:
    st.markdown(f"**{row.get('rider')}** · {row.get('status')}")
    st.caption(
        f"Trip `{trip.get('trip_id') or row.get('trip_id')}` · "
        f"late pickup {row.get('late_pickup_minutes')} min · "
        f"reason {row.get('not_boarding_reason') or '—'} · "
        f"vendor {trip.get('vendor') or '—'}"
    )
    st.caption("Lateness is pickup delay. Rider labels are masked; employee IDs are not shown.")


def render_action_card(action, *, key_prefix: str, on_transition) -> None:
    with st.container(border=True):
        st.markdown(
            f"{status_chip(str(action.status))} {role_chip(str(action.owner_role))} &nbsp; **{action.title}**",
            unsafe_allow_html=True,
        )
        st.caption(action.rationale)
        st.caption(
            f"Outcome: {action.expected_outcome or '—'} · Monitor: {action.monitoring_condition or '—'}"
        )
        buttons = st.columns(3)
        from core.models.action import ActionStatus

        if buttons[0].button(
            "Approve",
            key=f"{key_prefix}-approve-{action.action_id}",
            disabled=action.status not in {ActionStatus.PROPOSED, ActionStatus.REVIEWED},
            width="stretch",
        ):
            on_transition(action, ActionStatus.APPROVED)
        if buttons[1].button(
            "Reject",
            key=f"{key_prefix}-reject-{action.action_id}",
            disabled=action.status in {ActionStatus.REJECTED, ActionStatus.SIMULATED_SENT},
            width="stretch",
        ):
            on_transition(action, ActionStatus.REJECTED)
        completion = (
            ActionStatus.SIMULATED_SENT
            if action.email_subject is not None
            else ActionStatus.SIMULATED_COMPLETED
        )
        if buttons[2].button(
            "Simulate",
            key=f"{key_prefix}-sim-{action.action_id}",
            disabled=action.status != ActionStatus.APPROVED,
            width="stretch",
        ):
            on_transition(action, completion)
