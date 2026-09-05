from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pandas as pd

from core.bootstrap.app_context import AppContext
from core.models.action import Action, ActionType, ManagerRole
from core.models.chat import ChatRoute, ChatTool, ChatToolResult
from core.sense.kpis import RECORDED_DELAY_REASON

GROUP_COLUMNS = {
    "vendor": "vendor_id",
    "office": "office",
    "shift": "shift_type",
    "business_unit": "business_unit",
}


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    clean = df.astype(object).where(pd.notna(df), None)
    for column in clean.columns:
        if pd.api.types.is_datetime64_any_dtype(df[column]):
            clean[column] = clean[column].map(lambda value: None if value is None else str(value))
    return clean.to_dict(orient="records")


def _period_where(context: AppContext, route: ChatRoute, alias: str = "m") -> tuple[str, list]:
    latest: date = context.conn.execute("SELECT MAX(trip_date) FROM mobility_trip_360").fetchone()[0]
    prefix = f"{alias}." if alias else ""
    clauses = [f"{prefix}trip_date BETWEEN ? AND ?"]
    params: list = [latest - pd.Timedelta(days=route.period_days - 1), latest]
    if route.business_unit:
        clauses.append(f"{prefix}business_unit = ?")
        params.append(route.business_unit)
    if route.office:
        clauses.append(f"{prefix}office = ?")
        params.append(route.office)
    if route.vendor:
        clauses.append(f"{prefix}vendor_id = ?")
        params.append(route.vendor)
    if route.shift:
        clauses.append(f"{prefix}shift_type = ?")
        params.append(route.shift)
    return " AND ".join(clauses), params


def _proposed_action(
    route: ChatRoute,
    action_type: ActionType,
    title: str,
    rationale: str,
    priority: str = "HIGH",
) -> Action:
    key = f"{route.model_dump_json()}|{action_type}"
    return Action(
        action_id=f"chat-{hashlib.sha1(key.encode()).hexdigest()[:10]}",
        issue_id=f"chat-{route.tool.lower()}",
        action_type=action_type,
        priority=priority,
        title=title,
        rationale=rationale,
        owner_role=ManagerRole(route.manager_role),
        requested_by=ManagerRole(route.manager_role),
        requires_human_approval=True,
        source="COPILOT",
        supporting_evidence=[rationale],
        expected_outcome="The reported operational exception has a named owner and response.",
        monitoring_condition="Review status at the next operating checkpoint.",
    )


def requested_action(route: ChatRoute, evidence_summary: str) -> Action | None:
    """Translate a user verb into a typed, approval-gated simulated action."""
    if not route.action_intent:
        return None
    action_type = {
        "draft": ActionType.DRAFT_VENDOR_ESCALATION_EMAIL,
        "email": ActionType.DRAFT_VENDOR_ESCALATION_EMAIL,
        "call": ActionType.REQUEST_VENDOR_CALL,
        "assign": (
            ActionType.ASSIGN_SHIFT_FOLLOW_UP
            if route.tool == ChatTool.SHIFT_READINESS_REPORT or route.persona == "LINE_MANAGER"
            else ActionType.ASSIGN_OWNER
        ),
        "investigate": (
            ActionType.INVESTIGATE_SAFETY
            if route.tool in {ChatTool.TRIP_SAFETY, ChatTool.ALERTS_REPORT}
            else ActionType.INVESTIGATE_BILLING
            if route.tool == ChatTool.BILLING_REPORT
            else ActionType.INVESTIGATE_DELAY
        ),
        "monitor": ActionType.MONITOR_KPI,
        "acknowledge": ActionType.ACKNOWLEDGE_ALERT,
        "escalate": ActionType.CREATE_SAFETY_ESCALATION,
        "corrective_plan": ActionType.REQUEST_CORRECTIVE_PLAN,
    }.get(route.action_intent)
    if action_type is None:
        return None
    key = f"{route.model_dump_json()}|requested|{action_type}"
    role = ManagerRole(route.manager_role)
    email = action_type == ActionType.DRAFT_VENDOR_ESCALATION_EMAIL
    call = action_type in {
        ActionType.REQUEST_VENDOR_CALL,
        ActionType.REQUEST_DRIVER_CALL,
        ActionType.REQUEST_EMPLOYEE_CALL,
    }
    target = route.vendor or route.trip_id or route.office or route.business_unit
    return Action(
        action_id=f"chat-{hashlib.sha1(key.encode()).hexdigest()[:10]}",
        issue_id=f"chat-{route.tool.lower()}",
        action_type=action_type,
        priority="HIGH",
        title=f"{action_type.value.replace('_', ' ').title()}: {target or 'selected scope'}",
        rationale=evidence_summary,
        owner_role=role,
        requested_by=role,
        requires_human_approval=True,
        email_subject=(
            f"Action required: {route.tool.value.replace('_', ' ').title()}"
            if email
            else None
        ),
        email_body=(
            "Hello,\n\nPlease review the verified operational evidence below and confirm "
            f"the owner, corrective steps and closure date.\n\n{evidence_summary}\n\n"
            "Regards,\nTransport Operations"
            if email
            else None
        ),
        call_script=(
            f"Confirm the current status, owner and next action. Verified context: {evidence_summary}"
            if call
            else None
        ),
        target_type="VENDOR" if route.vendor or email or call else "OPERATIONS_SCOPE",
        target_name=target,
        source="COPILOT",
        supporting_evidence=[evidence_summary],
        expected_outcome="The requested follow-up has a named owner and recorded response.",
        monitoring_condition="Review status in the next operating checkpoint.",
    )


def trip_lookup(context: AppContext, route: ChatRoute) -> ChatToolResult:
    df = context.conn.execute(
        """
        SELECT trip_id, business_unit, office, trip_date, shift_type, product_type,
               trip_direction, vendor_id, planned_start_ts, actual_start_ts,
               planned_end_ts, actual_end_ts, calculated_delay_minutes,
               is_on_time, planned_trip_km, traveled_trip_km,
               planned_employee_count, actual_employee_count, no_show_count,
               is_driver_nc, is_cab_nc, alert_count, sev1_alert_count,
               open_alert_count, has_panic_alert, has_overspeed_alert,
               billed_cost, billed_km, cost_per_billed_km,
               avg_safety_rating
        FROM mobility_trip_360 WHERE trip_id = ?
        """,
        [route.trip_id],
    ).fetchdf()
    if df.empty:
        return ChatToolResult(
            answer=f"I could not find trip `{route.trip_id}` in the May–July 2026 trip spine.",
            tool=route.tool,
        )
    row = df.iloc[0]
    delay = row["calculated_delay_minutes"]
    answer = (
        f"Trip **{route.trip_id}** was a {row['trip_direction']} trip for "
        f"**{row['business_unit']} · {row['office']}** on {row['trip_date']}. "
        f"It was {'on time' if row['is_on_time'] else 'late'}"
        f"{'' if pd.isna(delay) else f' by {float(delay):.1f} minutes'}, carried "
        f"{int(row['actual_employee_count'])} employee(s), and recorded "
        f"{int(row['alert_count'])} safety alert(s)."
    )
    action = None
    if int(row["sev1_alert_count"]) or bool(row["has_panic_alert"]):
        action = _proposed_action(
            route,
            ActionType.CREATE_SAFETY_ESCALATION,
            f"Review critical safety events on trip {route.trip_id}",
            f"Trip {route.trip_id} has a Sev-1 or panic alert requiring manager review.",
            "CRITICAL",
        )
    return ChatToolResult(
        answer=answer,
        tool=route.tool,
        rows=_records(df),
        report_name=f"trip_{route.trip_id}.csv",
        proposed_action=action,
    )


def trip_safety(context: AppContext, route: ChatRoute) -> ChatToolResult:
    trip = context.conn.execute(
        """
        SELECT business_unit, office, vendor_id, trip_date, trip_direction,
               shift_type, is_on_time, calculated_delay_minutes
        FROM mobility_trip_360 WHERE trip_id = ?
        """,
        [route.trip_id],
    ).fetchdf()
    if trip.empty:
        return ChatToolResult(
            answer=f"I could not find trip `{route.trip_id}` in the May–July 2026 trip spine.",
            tool=route.tool,
        )
    df = context.conn.execute(
        """
        SELECT a.event_type,
               COALESCE(a.severity, 'UNSPECIFIED') severity,
               a.state_text state,
               a.source,
               a.start_ts alert_time,
               a.acknowledge_ts,
               a.acknowledgement_minutes,
               CASE WHEN a.acknowledge_ts IS NULL THEN 'OPEN' ELSE 'ACKNOWLEDGED' END ack_status
        FROM safety_alerts_clean a
        WHERE a.trip_id = ?
        ORDER BY a.start_ts
        """,
        [route.trip_id],
    ).fetchdf()
    row = trip.iloc[0]
    scope = (
        f"**{row['business_unit']} · {row['office']}** on "
        f"{pd.Timestamp(row['trip_date']).date()} ({row['vendor_id']})"
    )
    if df.empty:
        return ChatToolResult(
            answer=f"Trip **{route.trip_id}** ({scope}) recorded no safety alerts.",
            tool=route.tool,
        )
    open_alerts = int((df["ack_status"] == "OPEN").sum())
    panic = int(df["event_type"].str.contains("PANIC", case=False, na=False).sum())
    minutes = df["acknowledgement_minutes"].dropna()
    breakdown = ", ".join(
        f"{count}× {event}" for event, count in df["event_type"].value_counts().items()
    )
    # Supplying the real average stops the writer inventing one from the raw rows.
    timing = (
        ""
        if minutes.empty
        else (
            f" Acknowledgement took between **{minutes.min():.0f}** and "
            f"**{minutes.max():.0f}** minutes, averaging **{minutes.mean():.0f}** minutes."
        )
    )
    answer = (
        f"Trip **{route.trip_id}** ({scope}) recorded **{len(df)} safety alert(s)**: {breakdown}. "
        f"**{open_alerts}** remain unacknowledged.{timing}"
    )
    action = None
    if panic or open_alerts:
        action = _proposed_action(
            route,
            ActionType.CREATE_SAFETY_ESCALATION,
            f"Escalate safety events on trip {route.trip_id}",
            f"Trip {route.trip_id} has {panic} panic alert(s) and {open_alerts} unacknowledged alert(s).",
            "CRITICAL" if panic else "HIGH",
        )
    return ChatToolResult(
        answer=answer,
        tool=route.tool,
        rows=_records(df),
        report_name=f"trip_{route.trip_id}_safety.csv",
        proposed_action=action,
        row_order="one row per alert, oldest alert first",
    )


def alerts_report(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    df = context.conn.execute(
        f"""
        WITH grouped AS (
          SELECT COALESCE(a.severity, 'UNKNOWN') severity, a.event_type,
                 a.state_text state, COUNT(*) alerts,
                 COUNT(*) FILTER (WHERE a.acknowledge_ts IS NULL) unacknowledged,
                 ROUND(AVG(a.acknowledgement_minutes), 1) avg_ack_minutes
          FROM safety_alerts_clean a
          JOIN mobility_trip_360 m USING (trip_id)
          WHERE {where}
          GROUP BY 1, 2, 3
        )
        SELECT * FROM grouped ORDER BY
          CASE severity WHEN 'Sev-1' THEN 1 WHEN 'Sev-2' THEN 2
                        WHEN 'Sev-3' THEN 3 ELSE 4 END,
          alerts DESC
        """,
        params,
    ).fetchdf()
    total = int(df["alerts"].sum()) if not df.empty else 0
    unack = int(df["unacknowledged"].sum()) if not df.empty else 0
    sev1 = int(df.loc[df["severity"] == "Sev-1", "alerts"].sum()) if not df.empty else 0
    answer = (
        f"Over the latest **{route.period_days} day(s)** there were **{total:,} alerts**, "
        f"including **{sev1:,} Sev-1** and **{unack:,} unacknowledged**. "
        "The downloadable breakdown is grouped by severity, event type and state."
    )
    action = None
    if sev1 or unack:
        action = _proposed_action(
            route,
            ActionType.CREATE_SAFETY_ESCALATION,
            "Review critical or unacknowledged safety alerts",
            f"The report found {sev1} Sev-1 and {unack} unacknowledged alert(s).",
            "CRITICAL" if sev1 else "HIGH",
        )
    return ChatToolResult(
        answer=answer,
        tool=route.tool,
        rows=_records(df),
        report_name=f"alerts_last_{route.period_days}_days.csv",
        proposed_action=action,
        row_order="most severe first (Sev-1 before Sev-2), then most frequent first",
    )


def ota_report(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    column = GROUP_COLUMNS.get(route.group_by)
    dimension = f"m.{column}" if column else "'Overall'"
    group = f"GROUP BY m.{column}" if column else ""
    df = context.conn.execute(
        f"""
        SELECT {dimension} scope_name,
               COUNT(*) FILTER (WHERE is_ota_eligible) eligible_trips,
               COUNT(*) FILTER (WHERE is_ota_eligible AND NOT is_on_time) late_trips,
               ROUND(100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                 / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0), 2) ota_pct
        FROM mobility_trip_360 m WHERE {where}
        {group}
        HAVING COUNT(*) FILTER (WHERE is_ota_eligible) > 0
        ORDER BY ota_pct, eligible_trips DESC
        """,
        params,
    ).fetchdf()
    overall = context.conn.execute(
        f"""
        SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
          / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0), 2)
        FROM mobility_trip_360 m WHERE {where}
        """,
        params,
    ).fetchone()[0]
    target = float(context.sla["service_levels"]["trip_end_ota"]["target_pct"])
    extremes = ""
    if not df.empty:
        worst, best = df.iloc[0], df.iloc[-1]
        extremes = (
            f" The weakest {route.group_by} is **{worst['scope_name']}** at "
            f"**{worst['ota_pct']}%** and the strongest is **{best['scope_name']}** at "
            f"**{best['ota_pct']}%**."
        )
    answer = (
        f"Trip-end OTA for the latest **{route.period_days} day(s)** was **{overall}%** "
        f"against the **{target}% SLA**. The report is grouped by **{route.group_by}**."
        + extremes
    )
    return ChatToolResult(
        answer=answer,
        tool=route.tool,
        rows=_records(df),
        report_name=f"ota_by_{route.group_by}_{route.period_days}_days.csv",
        row_order="lowest OTA first, so the first row is the worst performer",
    )


def sla_breach_report(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    target = float(context.sla["service_levels"]["trip_end_ota"]["target_pct"])
    minimum = int(context.thresholds["punctuality"]["min_eligible_trips"])
    df = context.conn.execute(
        f"""
        SELECT m.business_unit, m.office, m.vendor_id vendor,
               COUNT(*) FILTER (WHERE is_ota_eligible) eligible_trips,
               COUNT(*) FILTER (WHERE is_ota_eligible AND NOT is_on_time) late_trips,
               ROUND(100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                 / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0), 2) ota_pct,
               ROUND(? - ota_pct, 2) sla_gap_points
        FROM mobility_trip_360 m WHERE {where}
        GROUP BY 1, 2, 3
        HAVING eligible_trips >= ? AND ota_pct < ?
        ORDER BY sla_gap_points DESC
        """,
        [target, *params, minimum, target],
    ).fetchdf()
    answer = (
        f"I found **{len(df):,} vendor-office combinations** below the **{target}% OTA SLA** "
        f"over the latest {route.period_days} day(s). "
        + (
            f"The largest gap is **{df.iloc[0]['sla_gap_points']} points** for "
            f"**{df.iloc[0]['vendor']}** at **{df.iloc[0]['office']}**."
            if not df.empty
            else "No combination with enough eligible trips breached the target."
        )
    )
    action = None
    if not df.empty:
        action = _proposed_action(
            route,
            ActionType.CREATE_MANAGER_ALERT,
            "Review OTA SLA breaches",
            f"{len(df)} vendor-office combination(s) are below the {target}% SLA.",
        )
    return ChatToolResult(
        answer=answer,
        tool=route.tool,
        rows=_records(df),
        report_name=f"ota_sla_breaches_{route.period_days}_days.csv",
        proposed_action=action,
        row_order="largest SLA gap first, so the first row is the worst performer",
    )


def operational_overview(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    row = context.conn.execute(
        f"""
        SELECT COUNT(*) trips,
               COUNT(*) FILTER (WHERE is_ota_eligible) eligible,
               ROUND(100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                 / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0), 2) ota_pct,
               COUNT(*) FILTER (WHERE is_ota_eligible AND NOT is_on_time) late_trips,
               COALESCE(SUM(alert_count), 0) alerts,
               COALESCE(SUM(open_alert_count), 0) open_alerts,
               COALESCE(SUM(employee_no_show_count), 0) no_shows,
               ROUND(AVG(avg_safety_rating), 2) safety_rating,
               ROUND(SUM(billed_cost), 2) billed_cost
        FROM mobility_trip_360 m WHERE {where}
        """,
        params,
    ).fetchdf()
    item = row.iloc[0]
    answer = (
        f"Across **{int(item['trips']):,} trips** in the latest **{route.period_days} days**, "
        f"OTA was **{item['ota_pct']}%**, with **{int(item['late_trips']):,} late trips**. "
        f"There were **{int(item['alerts']):,} safety alerts** "
        f"({int(item['open_alerts']):,} open), **{int(item['no_shows']):,} no-shows**, "
        f"and billed cost of **{float(item['billed_cost'] or 0):,.2f}**."
    )
    return ChatToolResult(
        answer=answer,
        tool=route.tool,
        rows=_records(row),
        report_name=f"operations_overview_{route.period_days}_days.csv",
        row_order="one consolidated row",
    )


def entity_comparison(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    column = GROUP_COLUMNS.get(route.group_by, "vendor_id")
    df = context.conn.execute(
        f"""
        SELECT m.{column} scope_name,
               COUNT(*) trips,
               COUNT(*) FILTER (WHERE is_ota_eligible) eligible_trips,
               ROUND(100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                 / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0), 2) ota_pct,
               COALESCE(SUM(alert_count), 0) alerts,
               COALESCE(SUM(employee_no_show_count), 0) no_shows,
               ROUND(SUM(billed_cost) / NULLIF(SUM(billed_km), 0), 2) cost_per_km,
               ROUND(AVG(avg_safety_rating), 2) safety_rating
        FROM mobility_trip_360 m WHERE {where} AND m.{column} IS NOT NULL
        GROUP BY 1 ORDER BY ota_pct, trips DESC
        """,
        params,
    ).fetchdf()
    if df.empty:
        answer = "No comparable entities matched those filters."
    else:
        worst, best = df.iloc[0], df.iloc[-1]
        answer = (
            f"Compared **{len(df)} {route.group_by} values** over {route.period_days} days. "
            f"OTA ranges from **{worst['ota_pct']}%** for **{worst['scope_name']}** to "
            f"**{best['ota_pct']}%** for **{best['scope_name']}**; the report also compares "
            "alerts, no-shows, cost/km and safety rating."
        )
    return ChatToolResult(
        answer=answer,
        tool=route.tool,
        rows=_records(df),
        report_name=f"{route.group_by}_comparison_{route.period_days}_days.csv",
        row_order="lowest OTA first",
    )


def delay_report(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    df = context.conn.execute(
        f"""
        SELECT * FROM (
          SELECT CASE WHEN {RECORDED_DELAY_REASON} THEN delay_reason ELSE 'NOT RECORDED' END delay_reason,
                 COUNT(*) late_trips,
                 ROUND(AVG(calculated_delay_minutes), 1) avg_delay_minutes,
                 ROUND(MAX(calculated_delay_minutes), 1) max_delay_minutes,
                 COALESCE(SUM(valid_employee_count), 0) employees_affected
          FROM mobility_trip_360 m
          WHERE {where} AND is_ota_eligible AND NOT is_on_time
          GROUP BY 1
        )
        ORDER BY (delay_reason = 'NOT RECORDED'), late_trips DESC
        """,
        params,
    ).fetchdf()
    total = int(df["late_trips"].sum()) if not df.empty else 0
    recorded = df[df["delay_reason"] != "NOT RECORDED"]
    unrecorded = total - int(recorded["late_trips"].sum() if not recorded.empty else 0)
    top = (
        "no reason recorded on any late trip"
        if recorded.empty
        else (
            f"**{recorded.iloc[0]['delay_reason']}** on **{int(recorded.iloc[0]['late_trips']):,}** "
            "of them"
        )
    )
    return ChatToolResult(
        answer=(
            f"Found **{total:,} late trips**. The most common recorded delay reason is {top}; "
            f"**{unrecorded:,}** late trips carry no recorded reason, so the cause of those is "
            "not in the data."
        ),
        tool=route.tool,
        rows=_records(df),
        report_name=f"delay_reasons_{route.period_days}_days.csv",
        row_order="recorded reasons first, most late trips first; 'NOT RECORDED' is missing data",
    )


def no_show_report(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    group = GROUP_COLUMNS.get(route.group_by, "shift_type")
    df = context.conn.execute(
        f"""
        SELECT m.{group} scope_name, COUNT(*) trips,
               COALESCE(SUM(employee_no_show_count), 0) no_shows,
               ROUND(100.0 * SUM(employee_no_show_count)
                 / NULLIF(SUM(valid_employee_count), 0), 2) no_show_pct
        FROM mobility_trip_360 m WHERE {where} AND m.{group} IS NOT NULL
        GROUP BY 1 HAVING SUM(employee_no_show_count) > 0
        ORDER BY no_shows DESC
        """,
        params,
    ).fetchdf()
    total = int(df["no_shows"].sum()) if not df.empty else 0
    return ChatToolResult(
        answer=f"Recorded **{total:,} no-shows** over the latest {route.period_days} days.",
        tool=route.tool,
        rows=_records(df),
        report_name=f"no_shows_by_{route.group_by}_{route.period_days}_days.csv",
        row_order="most no-shows first",
    )


def feedback_report(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    group = GROUP_COLUMNS.get(route.group_by, "vendor_id")
    df = context.conn.execute(
        f"""
        SELECT m.{group} scope_name, SUM(feedback_count) responses,
               ROUND(AVG(avg_route_rating), 2) route_rating,
               ROUND(AVG(avg_driver_rating), 2) driver_rating,
               ROUND(AVG(avg_cab_rating), 2) cab_rating,
               ROUND(AVG(avg_safety_rating), 2) safety_rating,
               SUM(low_rating_count) low_ratings
        FROM mobility_trip_360 m WHERE {where} AND m.{group} IS NOT NULL
        GROUP BY 1 HAVING SUM(feedback_count) > 0
        ORDER BY safety_rating, responses DESC
        """,
        params,
    ).fetchdf()
    return ChatToolResult(
        answer=(
            f"Found feedback for **{len(df)} {route.group_by} values**. "
            + (
                f"The lowest safety rating shown is **{df.iloc[0]['safety_rating']}** for "
                f"**{df.iloc[0]['scope_name']}**."
                if not df.empty
                else "No feedback matched those filters."
            )
        ),
        tool=route.tool,
        rows=_records(df),
        report_name=f"feedback_by_{route.group_by}_{route.period_days}_days.csv",
        row_order="lowest safety rating first",
    )


def utilization_report(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    group = GROUP_COLUMNS.get(route.group_by, "vendor_id")
    df = context.conn.execute(
        f"""
        SELECT m.{group} scope_name, COUNT(*) trips,
               SUM(actual_employee_count) actual_riders,
               SUM(planned_employee_count) planned_riders,
               ROUND(100.0 * SUM(actual_employee_count)
                 / NULLIF(SUM(planned_employee_count), 0), 2) rider_plan_pct,
               ROUND(AVG(actual_employee_count / NULLIF(actual_cab_capacity, 0)) * 100, 2)
                 avg_capacity_utilization_pct
        FROM mobility_trip_360 m WHERE {where} AND m.{group} IS NOT NULL
        GROUP BY 1 ORDER BY avg_capacity_utilization_pct
        """,
        params,
    ).fetchdf()
    return ChatToolResult(
        answer=f"Compared capacity utilization across **{len(df)} {route.group_by} values**.",
        tool=route.tool,
        rows=_records(df),
        report_name=f"utilization_by_{route.group_by}_{route.period_days}_days.csv",
        row_order="lowest average capacity utilization first",
    )


def billing_report(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    df = context.conn.execute(
        f"""
        SELECT COALESCE(billing_vendor, vendor_id) vendor,
               COUNT(*) FILTER (WHERE billing_line_count > 0) billed_trips,
               ROUND(SUM(billed_cost), 2) billed_cost,
               ROUND(SUM(billed_cost) / NULLIF(SUM(billed_km), 0), 2) cost_per_km,
               COUNT(*) FILTER (WHERE has_zero_km_billing_exception) zero_km_exceptions
        FROM mobility_trip_360 m WHERE {where}
        GROUP BY 1 HAVING COUNT(*) FILTER (WHERE billing_line_count > 0) > 0
        ORDER BY cost_per_km DESC
        """,
        params,
    ).fetchdf()
    total = float(df["billed_cost"].sum()) if not df.empty else 0
    return ChatToolResult(
        answer=f"Total billed cost is **{total:,.2f}** across **{len(df)} vendors**.",
        tool=route.tool,
        rows=_records(df),
        report_name=f"billing_by_vendor_{route.period_days}_days.csv",
        row_order="highest cost/km first",
    )


def impacted_trips(context: AppContext, route: ChatRoute) -> ChatToolResult:
    where, params = _period_where(context, route)
    df = context.conn.execute(
        f"""
        SELECT trip_id, trip_date, business_unit, office, shift_type, vendor_id,
               calculated_delay_minutes, delay_reason, alert_count, open_alert_count,
               employee_no_show_count, is_driver_nc, is_cab_nc
        FROM mobility_trip_360 m
        WHERE {where} AND (
          (is_ota_eligible AND NOT is_on_time) OR alert_count > 0 OR
          employee_no_show_count > 0 OR is_driver_nc OR is_cab_nc
        )
        ORDER BY open_alert_count DESC, alert_count DESC,
                 calculated_delay_minutes DESC NULLS LAST
        LIMIT 250
        """,
        params,
    ).fetchdf()
    return ChatToolResult(
        answer=f"Found **{len(df):,} impacted trips** (showing up to 250) for investigation.",
        tool=route.tool,
        rows=_records(df),
        report_name=f"impacted_trips_{route.period_days}_days.csv",
        row_order="open alerts, alert volume and delay descending",
    )


def shift_readiness_report(context: AppContext, route: ChatRoute) -> ChatToolResult:
    from datetime import timedelta

    from core.models.persona import Persona, PersonaScope
    from core.privacy.masking import assert_no_raw_stwid
    from core.sense.persona_insights import shift_readiness

    latest = context.conn.execute("SELECT MAX(trip_date) FROM mobility_trip_360").fetchone()[0]
    start = latest - timedelta(days=route.period_days - 1)
    scope = PersonaScope(
        persona=Persona.LINE_MANAGER,
        business_unit=route.business_unit or None,
        office=route.office or None,
        shift=route.shift or None,
        start_date=start,
        end_date=latest,
    )
    data = shift_readiness(context.conn, scope)
    roster = data["roster"]
    assert_no_raw_stwid(roster)
    answer = (
        f"Shift readiness: **{data['boarded']} boarded**, **{data['no_shows']} no-show(s)**, "
        f"**{data['late_pickups']} late pickup(s)** among **{data['riders']} riders**. "
        "Riders are masked labels, not employee IDs. Lateness is pickup delay, not office arrival."
    )
    action = None
    if data["no_shows"] or data["late_pickups"]:
        action = _proposed_action(
            route,
            ActionType.ASSIGN_SHIFT_FOLLOW_UP,
            "Assign shift follow-up for unready riders",
            answer,
        )
    return ChatToolResult(
        answer=answer,
        tool=route.tool,
        rows=roster,
        report_name="shift_readiness_masked.csv",
        proposed_action=action,
        row_order="no-shows and late pickups first; rider labels are masked",
    )


def run_curated_tool(context: AppContext, route: ChatRoute) -> ChatToolResult:
    handlers = {
        ChatTool.OPERATIONAL_OVERVIEW: operational_overview,
        ChatTool.TRIP_LOOKUP: trip_lookup,
        ChatTool.TRIP_SAFETY: trip_safety,
        ChatTool.ALERTS_REPORT: alerts_report,
        ChatTool.OTA_REPORT: ota_report,
        ChatTool.SLA_BREACH_REPORT: sla_breach_report,
        ChatTool.ENTITY_COMPARISON: entity_comparison,
        ChatTool.DELAY_REPORT: delay_report,
        ChatTool.NO_SHOW_REPORT: no_show_report,
        ChatTool.FEEDBACK_REPORT: feedback_report,
        ChatTool.UTILIZATION_REPORT: utilization_report,
        ChatTool.BILLING_REPORT: billing_report,
        ChatTool.IMPACTED_TRIPS: impacted_trips,
        ChatTool.SHIFT_READINESS_REPORT: shift_readiness_report,
    }
    if route.tool == ChatTool.HELP:
        return ChatToolResult(
            tool=route.tool,
            answer=(
                "I can analyse trips, safety, OTA/SLA, delays, no-shows, feedback, capacity "
                "utilization and billing; compare vendors, offices or shifts; find impacted "
                "trips; and prepare approval-gated manager actions. Try “compare vendors for "
                "the last 14 days” or “give me an operational overview and proposed actions”."
            ),
        )
    return handlers[route.tool](context, route)
