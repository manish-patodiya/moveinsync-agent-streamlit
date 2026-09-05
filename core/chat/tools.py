from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pandas as pd

from core.bootstrap.app_context import AppContext
from core.models.action import Action, ActionType
from core.models.chat import ChatRoute, ChatTool, ChatToolResult

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
        requires_human_approval=True,
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


def run_curated_tool(context: AppContext, route: ChatRoute) -> ChatToolResult:
    handlers = {
        ChatTool.TRIP_LOOKUP: trip_lookup,
        ChatTool.TRIP_SAFETY: trip_safety,
        ChatTool.ALERTS_REPORT: alerts_report,
        ChatTool.OTA_REPORT: ota_report,
        ChatTool.SLA_BREACH_REPORT: sla_breach_report,
    }
    if route.tool == ChatTool.HELP:
        return ChatToolResult(
            tool=route.tool,
            answer=(
                "I can answer four kinds of question from the May–July 2026 data: trip "
                "details for a trip ID, the safety alerts on a specific trip, alert "
                "breakdowns over a period, on-time arrival by vendor/office/shift/business "
                "unit, and which vendor-office pairs breach the OTA SLA. Try “what safety "
                "alerts are on trip 4927479?” or “OTA by vendor for the last 14 days”."
            ),
        )
    return handlers[route.tool](context, route)
