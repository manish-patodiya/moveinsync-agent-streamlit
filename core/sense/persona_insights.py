from __future__ import annotations

from typing import Any

import duckdb
from pydantic import BaseModel, Field

from core.models.evidence import Evidence
from core.models.issue import CandidateIssue, IssueType, Severity
from core.models.persona import Persona, PersonaScope
from core.models.workflow_state import AnalysisFilters
from core.privacy.masking import mask_rider
from core.sense.anomaly_detector import detect_anomalies
from core.sense.benchmarks import period_kpis, prior_period_dates, prior_period_filters
from core.sense.kpis import RECORDED_DELAY_REASON, calculate_kpis, filter_sql


class PulseCard(BaseModel):
    title: str
    what_happened: str
    compared_with: str
    why_it_matters: str
    what_to_do: str
    severity: str = "MEDIUM"
    issue_id: str | None = None
    caveats: list[str] = Field(default_factory=list)


class PersonaPulse(BaseModel):
    persona: Persona
    kpi: dict[str, Any]
    cards: list[PulseCard]
    issues: list[CandidateIssue]
    roster: list[dict[str, Any]] = Field(default_factory=list)
    vendor_scorecard: list[dict[str, Any]] = Field(default_factory=list)
    vendor_hotspots: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def scope_to_filters(scope: PersonaScope) -> AnalysisFilters:
    return AnalysisFilters(
        business_unit=scope.business_unit,
        office=scope.office,
        shift=scope.shift,
        start_date=scope.start_date,
        end_date=scope.end_date,
    )


def _filters_for(scope: PersonaScope) -> AnalysisFilters:
    filters = scope_to_filters(scope)
    if scope.persona != Persona.LINE_MANAGER:
        filters.shift = None
    return filters


def vendor_scorecard(
    con: duckdb.DuckDBPyConnection,
    filters: AnalysisFilters,
    sla_target_pct: float = 90.0,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    where, params = filter_sql(filters)
    prior = prior_period_filters(filters)
    prior_where, prior_params = filter_sql(prior)
    df = con.execute(
        f"""
        WITH current_v AS (
            SELECT vendor_id vendor,
                   COUNT(*) FILTER (WHERE is_ota_eligible) eligible,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                     / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0), 2) ota_pct,
                   ROUND(SUM(billed_cost), 2) billed_cost,
                   ROUND(SUM(billed_cost) / NULLIF(SUM(billed_km), 0), 2) cost_per_km,
                   COALESCE(SUM(alert_count), 0) alerts,
                   ROUND(AVG(avg_safety_rating), 2) safety_rating
            FROM mobility_trip_360
            WHERE {where} AND vendor_id IS NOT NULL
            GROUP BY 1
            HAVING COUNT(*) FILTER (WHERE is_ota_eligible) > 0
        ),
        prior_v AS (
            SELECT vendor_id vendor,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                     / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0), 2) prior_ota_pct
            FROM mobility_trip_360
            WHERE {prior_where} AND vendor_id IS NOT NULL
            GROUP BY 1
            HAVING COUNT(*) FILTER (WHERE is_ota_eligible) >= 5
        )
        SELECT c.vendor, c.eligible, c.ota_pct,
               ROUND(c.ota_pct - ?, 2) sla_gap_pp,
               p.prior_ota_pct,
               ROUND((SELECT MEDIAN(peer.ota_pct) FROM current_v peer WHERE peer.vendor <> c.vendor), 2) peer_median_ota,
               c.billed_cost, c.cost_per_km, c.alerts, c.safety_rating
        FROM current_v c
        LEFT JOIN prior_v p ON p.vendor = c.vendor
        ORDER BY c.ota_pct
        LIMIT ?
        """,
        [*params, *prior_params, sla_target_pct, limit],
    ).fetchdf()
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


def vendor_drill(
    con: duckdb.DuckDBPyConnection,
    filters: AnalysisFilters,
    vendor: str,
) -> dict[str, Any]:
    where, params = filter_sql(filters)
    vendor_where = f"{where} AND vendor_id = ?"
    vendor_params = [*params, vendor]
    offices = con.execute(
        f"""
        SELECT office, shift_type AS shift,
               COUNT(*) FILTER (WHERE is_ota_eligible) eligible,
               ROUND(100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                 / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0), 2) ota_pct,
               COUNT(*) FILTER (WHERE is_ota_eligible AND NOT is_on_time) late_trips
        FROM mobility_trip_360
        WHERE {vendor_where} AND office IS NOT NULL
        GROUP BY 1, 2
        HAVING COUNT(*) FILTER (WHERE is_ota_eligible) > 0
        ORDER BY ota_pct
        LIMIT 12
        """,
        vendor_params,
    ).fetchdf()
    delays = con.execute(
        f"""
        SELECT * FROM (
          SELECT CASE WHEN {RECORDED_DELAY_REASON} THEN delay_reason ELSE 'NOT RECORDED' END delay_reason,
                 COUNT(*) late_trips
          FROM mobility_trip_360
          WHERE {vendor_where} AND is_ota_eligible AND NOT is_on_time
          GROUP BY 1
        )
        ORDER BY (delay_reason = 'NOT RECORDED'), late_trips DESC
        LIMIT 8
        """,
        vendor_params,
    ).fetchdf()
    safety = con.execute(
        f"""
        SELECT COALESCE(SUM(alert_count), 0) alerts,
               COALESCE(SUM(open_alert_count), 0) open_alerts,
               COALESCE(SUM(sev1_alert_count), 0) sev1_alerts
        FROM mobility_trip_360
        WHERE {vendor_where}
        """,
        vendor_params,
    ).fetchone()
    trips = con.execute(
        f"""
        SELECT trip_id, office, shift_type AS shift, delay_reason,
               ROUND(calculated_delay_minutes, 1) delay_minutes, alert_count
        FROM mobility_trip_360
        WHERE {vendor_where} AND (
            (is_ota_eligible AND NOT is_on_time) OR alert_count > 0
        )
        ORDER BY alert_count DESC, calculated_delay_minutes DESC NULLS LAST
        LIMIT 15
        """,
        vendor_params,
    ).fetchdf()
    return {
        "vendor": vendor,
        "offices": offices.astype(object).where(offices.notna(), None).to_dict(orient="records"),
        "delay_reasons": delays.astype(object).where(delays.notna(), None).to_dict(orient="records"),
        "alerts": int(safety[0] or 0),
        "open_alerts": int(safety[1] or 0),
        "sev1_alerts": int(safety[2] or 0),
        "impacted_trips": trips.astype(object).where(trips.notna(), None).to_dict(orient="records"),
    }


def rider_trip_context(con: duckdb.DuckDBPyConnection, trip_id: str) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT trip_id, vendor_id, office, shift_type, delay_reason,
               ROUND(calculated_delay_minutes, 1), COALESCE(alert_count, 0)
        FROM mobility_trip_360
        WHERE trip_id = ?
        """,
        [trip_id],
    ).fetchone()
    if not row:
        return {"trip_id": trip_id}
    return {
        "trip_id": row[0],
        "vendor": row[1],
        "office": row[2],
        "shift": row[3],
        "delay_reason": row[4],
        "delay_minutes": row[5],
        "alerts": int(row[6] or 0),
    }


def shift_vendor_hotspots(con: duckdb.DuckDBPyConnection, scope: PersonaScope) -> list[dict[str, Any]]:
    clauses = ["e.trip_date BETWEEN ? AND ?", "e.pickup_delay_minutes > 10"]
    params: list[Any] = [scope.start_date, scope.end_date]
    if scope.office:
        clauses.append("e.office = ?")
        params.append(scope.office)
    if scope.shift:
        clauses.append("e.shift_type = ?")
        params.append(scope.shift)
    where = " AND ".join(clauses)
    df = con.execute(
        f"""
        SELECT m.vendor_id vendor, COUNT(*) late_pickups
        FROM employee_legs_clean e
        JOIN mobility_trip_360 m ON m.trip_id = e.trip_id
        WHERE {where} AND m.vendor_id IS NOT NULL
        GROUP BY 1
        ORDER BY late_pickups DESC
        LIMIT 5
        """,
        params,
    ).fetchdf()
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


def sustainability_proxy(con: duckdb.DuckDBPyConnection, filters: AnalysisFilters) -> dict[str, Any]:
    where, params = filter_sql(filters)
    row = con.execute(
        f"""
        SELECT COUNT(*) trips,
               COUNT(*) FILTER (WHERE LOWER(COALESCE(actual_cab_fuel_type, '')) LIKE '%electric%') electric_trips,
               ROUND(100.0 * COUNT(*) FILTER (
                   WHERE LOWER(COALESCE(actual_cab_fuel_type, '')) LIKE '%electric%'
               ) / NULLIF(COUNT(*), 0), 2) electric_trip_pct
        FROM mobility_trip_360
        WHERE {where}
        """,
        params,
    ).fetchone()
    return {
        "trips": int(row[0]),
        "electric_trips": int(row[1]),
        "electric_trip_pct": row[2],
        "label": "Share of trips recorded as electric fuel type — a sustainability proxy, not emissions.",
    }


def shift_readiness(
    con: duckdb.DuckDBPyConnection,
    scope: PersonaScope,
    *,
    roster_limit: int = 80,
) -> dict[str, Any]:
    clauses = ["trip_date BETWEEN ? AND ?", "stwid IS NOT NULL", "stwid <> '0'"]
    params: list[Any] = [scope.start_date, scope.end_date]
    if scope.business_unit:
        clauses.append("business_unit = ?")
        params.append(scope.business_unit)
    if scope.office:
        clauses.append("office = ?")
        params.append(scope.office)
    if scope.shift:
        clauses.append("shift_type = ?")
        params.append(scope.shift)
    where = " AND ".join(clauses)
    totals = con.execute(
        f"""
        SELECT COUNT(*) riders,
               COUNT(*) FILTER (WHERE boarding_status = 'Boarded') boarded,
               COUNT(*) FILTER (WHERE is_no_show) no_shows,
               COUNT(*) FILTER (WHERE boarding_status = 'Not Boarded') not_boarded,
               COUNT(*) FILTER (WHERE pickup_delay_minutes > 10) late_pickups
        FROM employee_legs_clean
        WHERE {where}
        """,
        params,
    ).fetchone()
    rows = con.execute(
        f"""
        SELECT stwid, boarding_status, is_no_show, pickup_delay_minutes,
               not_boarding_reason, shift_type, office, trip_id
        FROM employee_legs_clean
        WHERE {where} AND (is_no_show OR pickup_delay_minutes > 10 OR boarding_status = 'Not Boarded')
        ORDER BY is_no_show DESC, pickup_delay_minutes DESC NULLS LAST
        LIMIT ?
        """,
        [*params, roster_limit],
    ).fetchall()
    roster = [
        {
            "rider": mask_rider(stwid),
            "status": "No-show" if no_show else ("Late pickup" if (delay or 0) > 10 else status or "Not boarded"),
            "late_pickup_minutes": None if delay is None else round(float(delay), 1),
            "not_boarding_reason": reason,
            "shift": shift,
            "office": office,
            "trip_id": trip_id,
        }
        for stwid, status, no_show, delay, reason, shift, office, trip_id in rows
    ]
    riders = int(totals[0])
    return {
        "riders": riders,
        "boarded": int(totals[1]),
        "no_shows": int(totals[2]),
        "not_boarded": int(totals[3]),
        "late_pickups": int(totals[4]),
        "boarded_pct": round(100.0 * int(totals[1]) / riders, 1) if riders else None,
        "roster": roster,
    }


def _card_from_issue(issue: CandidateIssue, what_to_do: str) -> PulseCard:
    comparisons = issue.comparisons
    compared = []
    if comparisons.get("sla_target_pct") is not None:
        compared.append(f"SLA {comparisons['sla_target_pct']}%")
    if comparisons.get("prior_period_ota_pct") is not None:
        compared.append(
            f"prior period {comparisons['prior_period_ota_pct']}% "
            f"({comparisons.get('prior_period_start')}–{comparisons.get('prior_period_end')})"
        )
    if comparisons.get("peer_median_ota_pct") is not None:
        compared.append(f"peer median {comparisons['peer_median_ota_pct']}%")
    evidence = "; ".join(f"{item.label}: {item.value}" for item in issue.evidence[:3])
    return PulseCard(
        title=issue.title,
        what_happened=f"{issue.current_metric} is {issue.current_value} across {issue.affected_trip_count} trips.",
        compared_with="; ".join(compared) or "Absolute condition (no peer comparison required).",
        why_it_matters=evidence or issue.title,
        what_to_do=what_to_do,
        severity=str(issue.severity),
        issue_id=issue.issue_id,
    )


def _select_issues(persona: Persona, issues: list[CandidateIssue]) -> list[CandidateIssue]:
    if persona == Persona.FACILITIES_HEAD:
        preferred = {IssueType.VENDOR_OTA_BREACH, IssueType.BILLING_ANOMALY, IssueType.SAFETY_ESCALATION}
        ranked = [issue for issue in issues if issue.issue_type in preferred]
        return ranked[:2]
    if persona == Persona.LINE_MANAGER:
        return [
            issue
            for issue in issues
            if issue.issue_type in {IssueType.SAFETY_ESCALATION, IssueType.VENDOR_OTA_BREACH}
        ][:2]
    return [issue for issue in issues if issue.severity in {Severity.HIGH, Severity.CRITICAL}][:2]


def build_persona_pulse(
    con: duckdb.DuckDBPyConnection,
    scope: PersonaScope,
    sla: dict,
    thresholds: dict,
) -> PersonaPulse:
    filters = _filters_for(scope)
    target = float(sla["service_levels"]["trip_end_ota"]["target_pct"])
    kpi = calculate_kpis(con, filters, target)
    issues = detect_anomalies(con, filters, sla, thresholds)
    selected = _select_issues(scope.persona, issues)
    caveats = []
    if kpi.get("prior_ota_pct") is None:
        caveats.append("Matching prior-period OTA was unavailable (sample too small or no overlap).")
    cards: list[PulseCard] = []
    actions = {
        Persona.TRANSPORT_MANAGER: "Escalate the vendor and investigate the recorded delay reasons.",
        Persona.FACILITIES_HEAD: "Record an SLA recovery decision and review billed cost concentration.",
        Persona.LINE_MANAGER: "Follow up the unready riders on this office and shift.",
    }
    for issue in selected:
        cards.append(_card_from_issue(issue, actions[scope.persona]))
    roster: list[dict[str, Any]] = []
    scorecard: list[dict[str, Any]] = []
    hotspots: list[dict[str, Any]] = []
    if scope.persona in {Persona.TRANSPORT_MANAGER, Persona.FACILITIES_HEAD}:
        scorecard = vendor_scorecard(con, filters, target)
    if scope.persona == Persona.LINE_MANAGER:
        readiness = shift_readiness(con, scope)
        kpi.update({k: v for k, v in readiness.items() if k != "roster"})
        roster = readiness["roster"]
        if not scope.office or not scope.shift:
            caveats.append("Select an office and shift. Line-manager readiness is office+shift scoped.")
        if readiness["no_shows"] or readiness["late_pickups"]:
            issue = CandidateIssue(
                issue_id=f"readiness-{scope.office}-{scope.shift}-{scope.end_date}",
                issue_type=IssueType.SHIFT_READINESS,
                severity=Severity.HIGH if readiness["no_shows"] else Severity.MEDIUM,
                title=f"Shift readiness risk · {scope.office} · {scope.shift}",
                business_scope={"office": scope.office, "shift": scope.shift},
                current_metric="unready_riders",
                current_value=readiness["no_shows"] + readiness["late_pickups"],
                affected_trip_count=readiness["riders"],
                affected_employee_count=readiness["no_shows"] + readiness["not_boarded"],
                evidence=[
                    Evidence(label="Boarded", value=readiness["boarded"], source="employee_legs_clean"),
                    Evidence(label="No-shows", value=readiness["no_shows"], source="employee_legs_clean"),
                    Evidence(label="Late pickups", value=readiness["late_pickups"], source="employee_legs_clean"),
                    Evidence(label="Masked riders listed", value=len(roster), source="employee_legs_clean"),
                ],
                data_confidence="HIGH",
                allowed_action_types=[
                    "REQUEST_RIDER_FOLLOW_UP",
                    "ASSIGN_SHIFT_FOLLOW_UP",
                    "ACKNOWLEDGE_READINESS_RISK",
                ],
            )
            selected = [issue, *selected][:2]
            cards.insert(
                0,
                PulseCard(
                    title="Shift readiness risk",
                    what_happened=(
                        f"{readiness['no_shows']} no-show(s) and {readiness['late_pickups']} late pickup(s) "
                        f"among {readiness['riders']} riders."
                    ),
                    compared_with="Counts are for the selected office/shift window only.",
                    why_it_matters="Unready riders reduce floor/ops start-of-shift coverage.",
                    what_to_do="Contact or assign follow-up for the masked riders listed below.",
                    severity=str(issue.severity),
                    issue_id=issue.issue_id,
                ),
            )
        hotspots = shift_vendor_hotspots(con, scope)
    if scope.persona == Persona.FACILITIES_HEAD:
        kpi.update(sustainability_proxy(con, filters))
        prior = period_kpis(con, prior_period_filters(filters))
        kpi["prior_billed_cost"] = prior["total_billed_cost"]
        start, end = prior_period_dates(filters)
        kpi["prior_period_start"] = start
        kpi["prior_period_end"] = end
        caveats.append("Billed cost is actual invoice spend, not budget vs actual.")
        caveats.append(kpi["label"])
    return PersonaPulse(
        persona=scope.persona,
        kpi=kpi,
        cards=cards[:2],
        issues=selected[:2],
        roster=roster,
        vendor_scorecard=scorecard,
        vendor_hotspots=hotspots,
        caveats=caveats,
    )
