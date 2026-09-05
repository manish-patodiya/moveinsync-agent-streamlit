from __future__ import annotations

import hashlib
from typing import Any

import duckdb

from core.models.evidence import Evidence
from core.models.issue import CandidateIssue, IssueType, Severity
from core.models.workflow_state import AnalysisFilters
from core.sense.benchmarks import (
    ota_scope_benchmark,
    period_kpis,
    prior_period_dates,
    prior_period_filters,
)
from core.sense.kpis import RECORDED_DELAY_REASON, filter_sql


def _delay_reason_evidence(rows: list[tuple[Any, int]]) -> Evidence:
    return Evidence(
        label="Top recorded delay reasons",
        value=", ".join(f"{reason} ({n})" for reason, n in rows) or "None recorded on late trips",
    )


def _id(kind: str, scope: str, filters: AnalysisFilters) -> str:
    value = f"{kind}|{scope}|{filters.start_date}|{filters.end_date}"
    return f"{kind.lower()}-{hashlib.sha1(value.encode()).hexdigest()[:10]}"


def _scope(filters: AnalysisFilters, **extra: str | None) -> dict[str, str | None]:
    return {
        "business_unit": filters.business_unit,
        "office": filters.office,
        **extra,
    }


def detect_vendor_ota_breaches(
    con: duckdb.DuckDBPyConnection,
    filters: AnalysisFilters,
    sla: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[CandidateIssue]:
    cfg = thresholds["punctuality"]
    target = float(sla["service_levels"]["trip_end_ota"]["target_pct"])
    where, params = filter_sql(filters)
    rows = con.execute(
        f"""
        WITH vendor AS (
            SELECT vendor_id, business_unit, office,
                   COUNT(*) FILTER (WHERE is_ota_eligible) eligible,
                   COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time) on_time,
                   COUNT(*) FILTER (WHERE is_ota_eligible AND NOT is_on_time) late,
                   COALESCE(SUM(valid_employee_count) FILTER (
                       WHERE is_ota_eligible AND NOT is_on_time
                   ), 0) affected,
                   100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                       / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0) ota
            FROM mobility_trip_360
            WHERE {where}
            GROUP BY vendor_id, business_unit, office
        )
        SELECT v.*,
               (SELECT MEDIAN(p.ota) FROM vendor p
                WHERE p.business_unit = v.business_unit
                  AND p.office = v.office
                  AND p.vendor_id <> v.vendor_id
                  AND p.eligible >= ?) peer_ota
        FROM vendor v
        WHERE eligible >= ? AND vendor_id IS NOT NULL
        ORDER BY ota
        """,
        [
            *params,
            int(cfg["min_eligible_trips"]),
            int(cfg["min_eligible_trips"]),
        ],
    ).fetchall()
    issues: list[CandidateIssue] = []
    for vendor, bu, office, eligible, _on_time, late, affected, ota, peer in rows:
        sla_gap = target - float(ota)
        peer_gap = float(peer) - float(ota) if peer is not None else 0
        if sla_gap < float(cfg["ota_sla_gap_pct_points"]) and peer_gap < float(
            cfg["vendor_peer_gap_pct_points"]
        ):
            continue
        benchmark = ota_scope_benchmark(
            con,
            filters,
            column="vendor_id",
            value=str(vendor),
            min_trips=int(cfg["prior_period_min_trips"]),
            min_peers=int(cfg["peer_min_groups"]),
        )
        baseline = benchmark["prior_period_ota_pct"]
        reasons = con.execute(
            f"""
            SELECT delay_reason, COUNT(*) n FROM mobility_trip_360
            WHERE {where} AND vendor_id = ? AND is_ota_eligible AND NOT is_on_time
              AND {RECORDED_DELAY_REASON}
            GROUP BY delay_reason ORDER BY n DESC LIMIT 3
            """,
            [*params, vendor],
        ).fetchall()
        severity = Severity.HIGH if sla_gap >= 10 or peer_gap >= 10 else Severity.MEDIUM
        if (
            baseline is not None
            and float(baseline) - float(ota)
            >= float(cfg["vendor_baseline_gap_pct_points"])
        ):
            severity = Severity.HIGH
        issues.append(
            CandidateIssue(
                issue_id=_id("OTA", str(vendor), filters),
                issue_type=IssueType.VENDOR_OTA_BREACH,
                severity=severity,
                title=f"Vendor OTA breach: {vendor}",
                business_scope=_scope(filters, business_unit=bu, office=office, vendor=vendor),
                current_metric="trip_end_ota_pct",
                current_value=round(float(ota), 2),
                comparisons={
                    "sla_target_pct": target,
                    **benchmark,
                },
                affected_trip_count=int(late),
                affected_employee_count=int(affected),
                evidence=[
                    Evidence(label="Eligible trips", value=int(eligible)),
                    Evidence(label="Late trips", value=int(late)),
                    Evidence(label="OTA", value=round(float(ota), 2), comparison=f"SLA {target}%"),
                    Evidence(
                        label="Prior-period OTA",
                        value=baseline if baseline is not None else "Unavailable",
                        comparison=(
                            f"{benchmark['prior_period_start']} to "
                            f"{benchmark['prior_period_end']}; "
                            f"n={benchmark['prior_period_eligible_trips']}"
                        ),
                    ),
                    Evidence(
                        label="Peer median OTA",
                        value=(
                            benchmark["peer_median_ota_pct"]
                            if benchmark["peer_median_ota_pct"] is not None
                            else "Unavailable"
                        ),
                        comparison=(
                            f"rank {benchmark['peer_rank']} of {benchmark['peer_count']}"
                        ),
                    ),
                    _delay_reason_evidence(reasons),
                ],
                data_confidence="HIGH" if eligible >= 2 * int(cfg["min_eligible_trips"]) else "MEDIUM",
                allowed_action_types=["CREATE_MANAGER_ALERT", "DRAFT_VENDOR_ESCALATION_EMAIL"],
            )
        )
    dimensions = [("overall", None)]
    if not filters.office:
        dimensions.append(("office", "office"))
    dimensions.append(("shift", "shift_type"))
    for label, column in dimensions:
        group_select = f"{column} AS scope_value," if column else "'All selected trips' AS scope_value,"
        group_by = f"GROUP BY {column}" if column else ""
        grouped = con.execute(
            f"""
            SELECT {group_select}
                   COUNT(*) FILTER (WHERE is_ota_eligible) eligible,
                   100.0 * COUNT(*) FILTER (WHERE is_ota_eligible AND is_on_time)
                     / NULLIF(COUNT(*) FILTER (WHERE is_ota_eligible), 0) ota,
                   COUNT(*) FILTER (WHERE is_ota_eligible AND NOT is_on_time) late,
                   COALESCE(SUM(valid_employee_count) FILTER (
                     WHERE is_ota_eligible AND NOT is_on_time
                   ), 0) affected
            FROM mobility_trip_360 WHERE {where}
            {group_by}
            HAVING COUNT(*) FILTER (WHERE is_ota_eligible) >= ?
               AND ota <= ?
            ORDER BY ota LIMIT 5
            """,
            [*params, int(cfg["min_eligible_trips"]), target - float(cfg["ota_sla_gap_pct_points"])],
        ).fetchall()
        for scope_value, eligible, ota, late, affected in grouped:
            gap = target - float(ota)
            if column:
                benchmark = ota_scope_benchmark(
                    con,
                    filters,
                    column=column,
                    value=str(scope_value),
                    min_trips=int(cfg["prior_period_min_trips"]),
                    min_peers=int(cfg["peer_min_groups"]),
                )
            else:
                prior = period_kpis(con, prior_period_filters(filters))
                prior_start, prior_end = prior_period_dates(filters)
                prior_ota = prior["ota_pct"]
                benchmark = {
                    "current_eligible_trips": int(eligible),
                    "prior_period_start": str(prior_start),
                    "prior_period_end": str(prior_end),
                    "prior_period_eligible_trips": prior["eligible_trips"],
                    "prior_period_ota_pct": prior_ota,
                    "prior_period_delta_pp": (
                        None if prior_ota is None else round(float(ota) - float(prior_ota), 2)
                    ),
                    "peer_median_ota_pct": None,
                    "peer_delta_pp": None,
                    "peer_rank": None,
                    "peer_count": 0,
                    "peer_group": "No peer group for overall scope",
                }
            scope_clause = f"AND {column} = ?" if column else ""
            reason_params = [*params, *([scope_value] if column else [])]
            reasons = con.execute(
                f"""
                SELECT delay_reason, COUNT(*) n FROM mobility_trip_360
                WHERE {where} {scope_clause} AND is_ota_eligible AND NOT is_on_time
                  AND {RECORDED_DELAY_REASON}
                GROUP BY delay_reason ORDER BY n DESC LIMIT 3
                """,
                reason_params,
            ).fetchall()
            issues.append(
                CandidateIssue(
                    issue_id=_id(f"OTA-{label}", str(scope_value), filters),
                    issue_type=IssueType.VENDOR_OTA_BREACH,
                    severity=Severity.HIGH if gap >= 10 else Severity.MEDIUM,
                    title=f"{label.title()} punctuality breach: {scope_value}",
                    business_scope=_scope(filters, **{label: str(scope_value)}),
                    current_metric="trip_end_ota_pct",
                    current_value=round(float(ota), 2),
                    comparisons={"sla_target_pct": target, **benchmark},
                    affected_trip_count=int(late),
                    affected_employee_count=int(affected),
                    evidence=[
                        Evidence(label="Eligible trips", value=int(eligible)),
                        Evidence(label="Late trips", value=int(late)),
                        Evidence(label="OTA", value=round(float(ota), 2), comparison=f"SLA {target}%"),
                        Evidence(
                            label="Prior-period OTA",
                            value=(
                                benchmark["prior_period_ota_pct"]
                                if benchmark["prior_period_ota_pct"] is not None
                                else "Unavailable"
                            ),
                            comparison=(
                                f"{benchmark['prior_period_start']} to "
                                f"{benchmark['prior_period_end']}; "
                                f"n={benchmark['prior_period_eligible_trips']}"
                            ),
                        ),
                        Evidence(
                            label="Peer median OTA",
                            value=(
                                benchmark["peer_median_ota_pct"]
                                if benchmark["peer_median_ota_pct"] is not None
                                else "Unavailable"
                            ),
                            comparison=f"rank {benchmark['peer_rank']} of {benchmark['peer_count']}",
                        ),
                        _delay_reason_evidence(reasons),
                    ],
                    data_confidence="HIGH" if eligible >= 2 * int(cfg["min_eligible_trips"]) else "MEDIUM",
                    allowed_action_types=["CREATE_MANAGER_ALERT"],
                )
            )
    return issues


def detect_safety_escalations(
    con: duckdb.DuckDBPyConnection,
    filters: AnalysisFilters,
    sla: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[CandidateIssue]:
    where, params = filter_sql(filters)
    sev1_target = float(sla["service_levels"]["safety_acknowledgement"]["sev1_target_minutes"])
    sev2_target = float(sla["service_levels"]["safety_acknowledgement"]["sev2_target_minutes"])
    max_open = float(thresholds["safety"]["max_open_alert_minutes"])
    row = con.execute(
        f"""
        SELECT COALESCE(SUM(sev1_alert_count), 0), COALESCE(SUM(alert_count), 0),
               COUNT(*) FILTER (WHERE has_panic_alert), COALESCE(SUM(open_alert_count), 0),
               COUNT(*) FILTER (WHERE max_sev1_acknowledgement_minutes > ?),
               COUNT(*) FILTER (WHERE max_sev2_acknowledgement_minutes > ?),
               COUNT(*) FILTER (
                   WHERE oldest_open_alert_ts IS NOT NULL
                     AND DATE_DIFF(
                         'minute',
                         oldest_open_alert_ts,
                         CAST(? AS DATE) + INTERVAL 1 DAY
                     ) > ?
               ),
               COUNT(*) FILTER (WHERE (sev1_alert_count > 0 OR has_panic_alert)
                                     AND (is_driver_nc OR is_cab_nc)),
               COALESCE(SUM(valid_employee_count) FILTER (
                   WHERE sev1_alert_count > 0 OR has_panic_alert
               ), 0),
               COUNT(*) FILTER (WHERE sev1_alert_count > 0 OR has_panic_alert),
               COUNT(*)
        FROM mobility_trip_360 WHERE {where}
        """,
        [sev1_target, sev2_target, filters.end_date, max_open, *params],
    ).fetchone()
    (
        sev1, alerts, panic_trips, open_alerts, sev1_late_ack, sev2_late_ack,
        stale_open, nc_trips,
        employees, critical_trips, trip_count,
    ) = map(int, row)
    prior_start, prior_end = prior_period_dates(filters)
    baseline_clauses = ["trip_date BETWEEN ? AND ?"]
    baseline_params: list[Any] = [prior_start, prior_end]
    if filters.business_unit:
        baseline_clauses.append("business_unit = ?")
        baseline_params.append(filters.business_unit)
    if filters.office:
        baseline_clauses.append("office = ?")
        baseline_params.append(filters.office)
    baseline_row = con.execute(
        f"""
        SELECT COALESCE(SUM(alert_count), 0), COUNT(*)
        FROM mobility_trip_360 WHERE {' AND '.join(baseline_clauses)}
        """,
        baseline_params,
    ).fetchone()
    current_rate = alerts / trip_count if trip_count else 0
    baseline_rate = int(baseline_row[0]) / int(baseline_row[1]) if baseline_row[1] else None
    multiplier = (
        current_rate / baseline_rate if baseline_rate and baseline_rate > 0 else None
    )
    alert_spike = multiplier is not None and multiplier >= float(
        thresholds["safety"]["alert_spike_multiplier"]
    )
    if not (sev1 or panic_trips or alert_spike):
        return []
    severity = Severity.CRITICAL if (
        (sev1 and thresholds["safety"]["sev1_is_critical"])
        or (panic_trips and thresholds["safety"]["panic_is_critical"])
    ) else Severity.HIGH
    return [
        CandidateIssue(
            issue_id=_id("SAFETY", filters.office or filters.business_unit or "all", filters),
            issue_type=IssueType.SAFETY_ESCALATION,
            severity=severity,
            title=(
                "Critical safety alerts require escalation"
                if sev1 or panic_trips
                else "Safety alert-rate spike requires review"
            ),
            business_scope=_scope(filters),
            current_metric=(
                "critical_safety_events" if sev1 or panic_trips else "alerts_per_trip"
            ),
            current_value=sev1 + panic_trips if sev1 or panic_trips else round(current_rate, 4),
            comparisons={
                "sev1_ack_target_minutes": sev1_target,
                "sev2_ack_target_minutes": sev2_target,
                "max_open_alert_minutes": max_open,
                "historical_alert_rate": None if baseline_rate is None else round(baseline_rate, 4),
                "alert_rate_multiplier": None if multiplier is None else round(multiplier, 2),
                "prior_period_start": str(prior_start),
                "prior_period_end": str(prior_end),
                "prior_period_trip_count": int(baseline_row[1]),
            },
            affected_trip_count=critical_trips if critical_trips else trip_count,
            affected_employee_count=employees,
            evidence=[
                Evidence(label="Sev-1 alerts", value=sev1),
                Evidence(label="Trips with panic alerts", value=panic_trips),
                Evidence(label="Open/new alerts", value=open_alerts),
                Evidence(label="Sev-1 trips beyond acknowledgement SLA", value=sev1_late_ack),
                Evidence(label="Sev-2 trips beyond acknowledgement SLA", value=sev2_late_ack),
                Evidence(label="Open/new trips beyond age threshold", value=stale_open),
                Evidence(label="Critical safety plus vehicle/driver non-compliance", value=nc_trips),
                Evidence(label="All safety alerts in period", value=alerts),
                Evidence(
                    label="Alert-rate versus baseline",
                    value="Unavailable" if multiplier is None else round(multiplier, 2),
                    comparison="multiplier",
                ),
            ],
            data_confidence="HIGH",
            allowed_action_types=[
                "CREATE_MANAGER_ALERT",
                "CREATE_SAFETY_ESCALATION",
                "DRAFT_VENDOR_ESCALATION_EMAIL",
            ],
        )
    ]


def detect_billing_anomalies(
    con: duckdb.DuckDBPyConnection,
    filters: AnalysisFilters,
    thresholds: dict[str, Any],
) -> list[CandidateIssue]:
    where, params = filter_sql(filters)
    cfg = thresholds["cost"]
    issues: list[CandidateIssue] = []
    zero = con.execute(
        f"""
        SELECT COUNT(*), COALESCE(SUM(billed_cost), 0)
        FROM mobility_trip_360
        WHERE {where} AND has_zero_km_billing_exception
        """,
        params,
    ).fetchone()
    if zero[0]:
        severity = Severity.HIGH if cfg["zero_km_positive_cost_is_high"] else Severity.MEDIUM
        issues.append(
            CandidateIssue(
                issue_id=_id("BILL-ZERO", filters.office or "all", filters),
                issue_type=IssueType.BILLING_ANOMALY,
                severity=severity,
                title="Positive billing on zero-kilometre lines",
                business_scope=_scope(filters),
                current_metric="zero_km_positive_cost_trips",
                current_value=int(zero[0]),
                comparisons={},
                affected_trip_count=int(zero[0]),
                evidence=[
                    Evidence(label="Affected trips", value=int(zero[0])),
                    Evidence(label="Billed cost on affected trips", value=round(float(zero[1]), 2)),
                ],
                data_confidence="HIGH",
                allowed_action_types=["CREATE_MANAGER_ALERT", "CREATE_BILLING_REVIEW_ALERT"],
            )
        )
    rows = con.execute(
        f"""
        WITH v AS (
          SELECT COALESCE(billing_vendor, vendor_id) AS vendor,
                 COUNT(*) FILTER (WHERE billed_km > 0) n,
                 SUM(billed_cost) / NULLIF(SUM(billed_km), 0) cpk
          FROM mobility_trip_360 WHERE {where}
          GROUP BY COALESCE(billing_vendor, vendor_id)
        )
        SELECT vendor, n, cpk, MEDIAN(cpk) OVER () peer_median
        FROM v WHERE n >= ? AND cpk IS NOT NULL AND vendor IS NOT NULL
        """,
        [*params, int(cfg["min_billed_trips"])],
    ).fetchall()
    for vendor, n, cpk, median in rows:
        gap = 100.0 * (float(cpk) - float(median)) / float(median) if median else 0
        prior_start, prior_end = prior_period_dates(filters)
        baseline_clauses = [
            "trip_date BETWEEN ? AND ?",
            "COALESCE(billing_vendor, vendor_id) = ?",
            "billed_km > 0",
        ]
        baseline_params: list[Any] = [prior_start, prior_end, vendor]
        if filters.business_unit:
            baseline_clauses.append("business_unit = ?")
            baseline_params.append(filters.business_unit)
        if filters.office:
            baseline_clauses.append("office = ?")
            baseline_params.append(filters.office)
        baseline = con.execute(
            f"""
            SELECT SUM(billed_cost) / NULLIF(SUM(billed_km), 0), COUNT(*)
            FROM mobility_trip_360 WHERE {' AND '.join(baseline_clauses)}
            """,
            baseline_params,
        ).fetchone()
        baseline_gap = (
            100.0 * (float(cpk) - float(baseline[0])) / float(baseline[0])
            if baseline[0] and int(baseline[1]) >= int(cfg["min_billed_trips"])
            else None
        )
        threshold = float(cfg["cost_per_km_peer_gap_pct"])
        if gap <= threshold and (baseline_gap is None or baseline_gap <= threshold):
            continue
        issues.append(
            CandidateIssue(
                issue_id=_id("BILL-CPK", str(vendor), filters),
                issue_type=IssueType.BILLING_ANOMALY,
                severity=Severity.HIGH if max(gap, baseline_gap or 0) >= 50 else Severity.MEDIUM,
                title=f"Billing cost/km anomaly: {vendor}",
                business_scope=_scope(filters, vendor=vendor),
                current_metric="cost_per_billed_km",
                current_value=round(float(cpk), 2),
                comparisons={
                    "peer_median_cost_per_km": round(float(median), 2),
                    "peer_gap_pct": round(gap, 2),
                    "historical_cost_per_km": None if baseline[0] is None else round(float(baseline[0]), 2),
                    "historical_gap_pct": None if baseline_gap is None else round(baseline_gap, 2),
                    "prior_period_start": str(prior_start),
                    "prior_period_end": str(prior_end),
                    "prior_period_billed_trips": int(baseline[1]),
                },
                affected_trip_count=int(n),
                evidence=[
                    Evidence(label="Billed trips", value=int(n)),
                    Evidence(label="Cost per billed km", value=round(float(cpk), 2)),
                    Evidence(label="Peer median gap", value=round(gap, 2), comparison="percent above median"),
                    Evidence(
                        label="Historical cost/km gap",
                        value="Unavailable" if baseline_gap is None else round(baseline_gap, 2),
                        comparison="percent",
                    ),
                ],
                data_confidence="MEDIUM",
                allowed_action_types=["CREATE_MANAGER_ALERT", "CREATE_BILLING_REVIEW_ALERT"],
            )
        )
    return issues


def detect_anomalies(
    con: duckdb.DuckDBPyConnection,
    filters: AnalysisFilters,
    sla: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[CandidateIssue]:
    issues = [
        *detect_vendor_ota_breaches(con, filters, sla, thresholds),
        *detect_safety_escalations(con, filters, sla, thresholds),
        *detect_billing_anomalies(con, filters, thresholds),
    ]
    rank = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
    return sorted(issues, key=lambda issue: (rank[issue.severity], -issue.affected_trip_count))
