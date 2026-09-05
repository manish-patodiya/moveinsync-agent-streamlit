from datetime import date

import pandas as pd

from core.bootstrap.app_context import load_configs
from core.database.duckdb_connection import create_memory_connection, load_dataframe
from core.models.issue import IssueType
from core.models.workflow_state import AnalysisFilters
from core.sense.anomaly_detector import (
    detect_anomalies,
    detect_billing_anomalies,
    detect_safety_escalations,
    detect_vendor_ota_breaches,
)
from core.sense.kpis import calculate_kpis


def _setup():
    rows = []
    for vendor in ("Vendor A", "Vendor B"):
        for i in range(20):
            on_time = vendor == "Vendor B" or i < 10
            rows.append(
                {
                    "trip_id": f"{vendor}-{i}",
                    "trip_date": pd.Timestamp("2026-07-01"),
                    "business_unit": "BU",
                    "office": "HQ",
                    "shift_type": "09:00",
                    "vendor_id": vendor,
                    "is_ota_eligible": True,
                    "is_on_time": on_time,
                    "valid_employee_count": 2,
                    "delay_reason": "TRAFFIC" if not on_time else "NODELAY",
                    "alert_count": 1 if vendor == "Vendor A" and i == 0 else 0,
                    "sev1_alert_count": 1 if vendor == "Vendor A" and i == 0 else 0,
                    "open_alert_count": 1 if vendor == "Vendor A" and i == 0 else 0,
                    "has_panic_alert": vendor == "Vendor A" and i == 0,
                    "avg_acknowledgement_minutes": 8.0 if i == 0 else None,
                    "max_sev1_acknowledgement_minutes": 8.0 if i == 0 else None,
                    "max_sev2_acknowledgement_minutes": None,
                    "oldest_open_alert_ts": (
                        pd.Timestamp("2026-07-01") if vendor == "Vendor A" and i == 0 else None
                    ),
                    "is_driver_nc": vendor == "Vendor A" and i == 0,
                    "is_cab_nc": False,
                    "billed_cost": 100.0,
                    "billed_km": 0.0 if vendor == "Vendor A" and i == 1 else 10.0,
                    "billing_vendor": vendor,
                    "has_zero_km_billing_exception": vendor == "Vendor A" and i == 1,
                }
            )
    con = create_memory_connection()
    load_dataframe(con, "mobility_trip_360", pd.DataFrame(rows))
    settings, sla, thresholds, _quality = load_configs()
    filters = AnalysisFilters(
        business_unit="BU",
        office="HQ",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 7),
    )
    return con, filters, sla, thresholds


def test_ota_calculation():
    con, filters, sla, _thresholds = _setup()
    kpis = calculate_kpis(con, filters, 90.0)
    assert kpis["ota_pct"] == 75.0
    assert kpis["late_trips"] == 10


def test_vendor_ota_breach_rule():
    con, filters, sla, thresholds = _setup()
    issues = detect_vendor_ota_breaches(con, filters, sla, thresholds)
    assert any(
        issue.issue_type == IssueType.VENDOR_OTA_BREACH
        and issue.business_scope["vendor"] == "Vendor A"
        for issue in issues
    )


def test_safety_escalation_rule():
    con, filters, sla, thresholds = _setup()
    issues = detect_safety_escalations(con, filters, sla, thresholds)
    assert len(issues) == 1
    assert issues[0].issue_type == IssueType.SAFETY_ESCALATION
    assert issues[0].severity == "CRITICAL"


def test_zero_km_positive_cost_billing_rule():
    con, filters, _sla, thresholds = _setup()
    issues = detect_billing_anomalies(con, filters, thresholds)
    assert any(issue.current_metric == "zero_km_positive_cost_trips" for issue in issues)


def test_sense_evidence_summary_is_attached_and_traceable_to_the_issue():
    con, filters, sla, thresholds = _setup()
    issues = detect_anomalies(con, filters, sla, thresholds)
    assert issues, "expected at least one flagged signal from the fixture data"
    for issue in issues:
        assert issue.sense_evidence_summary
        assert issue.title in issue.sense_evidence_summary


def _setup_dedupe():
    """A vendor whose own breach explains the entire 'overall' dimension rollup."""
    rows = []
    for i in range(20):
        rows.append(
            {
                "trip_id": f"A-{i}",
                "trip_date": pd.Timestamp("2026-07-01"),
                "business_unit": "BU",
                "office": "HQ",
                "shift_type": "09:00",
                "vendor_id": "Vendor A",
                "is_ota_eligible": True,
                "is_on_time": i < 5,
                "valid_employee_count": 2,
                "delay_reason": "TRAFFIC" if i >= 5 else "NODELAY",
            }
        )
    for i in range(20):
        rows.append(
            {
                "trip_id": f"B-{i}",
                "trip_date": pd.Timestamp("2026-07-01"),
                "business_unit": "BU",
                "office": "HQ",
                "shift_type": "09:00",
                "vendor_id": "Vendor B",
                "is_ota_eligible": True,
                "is_on_time": True,
                "valid_employee_count": 2,
                "delay_reason": "NODELAY",
            }
        )
    con = create_memory_connection()
    load_dataframe(con, "mobility_trip_360", pd.DataFrame(rows))
    settings, sla, thresholds, _quality = load_configs()
    filters = AnalysisFilters(
        business_unit="BU", office="HQ", start_date=date(2026, 7, 1), end_date=date(2026, 7, 7)
    )
    return con, filters, sla, thresholds


def test_dimension_rollup_suppressed_when_a_flagged_vendor_already_explains_it():
    con, filters, sla, thresholds = _setup_dedupe()
    issues = detect_vendor_ota_breaches(con, filters, sla, thresholds)
    titles = [issue.title for issue in issues]
    assert "Vendor OTA breach: Vendor A" in titles
    assert not any(title.startswith("Overall punctuality breach") for title in titles)


def _setup_marginal():
    """Both vendors clear the SLA/peer gap threshold only marginally, at MEDIUM confidence."""
    rows = []
    for i in range(20):
        rows.append(
            {
                "trip_id": f"A-{i}",
                "trip_date": pd.Timestamp("2026-07-01"),
                "business_unit": "BU",
                "office": "HQ",
                "shift_type": "09:00",
                "vendor_id": "Vendor A",
                "is_ota_eligible": True,
                "is_on_time": i < 17,  # 85% OTA: sla_gap == 5, right at the threshold
                "valid_employee_count": 2,
                "delay_reason": "TRAFFIC" if i >= 17 else "NODELAY",
            }
        )
    for i in range(20):
        rows.append(
            {
                "trip_id": f"B-{i}",
                "trip_date": pd.Timestamp("2026-07-01"),
                "business_unit": "BU",
                "office": "HQ",
                "shift_type": "09:00",
                "vendor_id": "Vendor B",
                "is_ota_eligible": True,
                "is_on_time": i < 18,  # 90% OTA: keeps the peer average close, so peer_gap stays small
                "valid_employee_count": 2,
                "delay_reason": "TRAFFIC" if i >= 18 else "NODELAY",
            }
        )
    con = create_memory_connection()
    load_dataframe(con, "mobility_trip_360", pd.DataFrame(rows))
    settings, sla, thresholds, _quality = load_configs()
    filters = AnalysisFilters(
        business_unit="BU", office="HQ", start_date=date(2026, 7, 1), end_date=date(2026, 7, 7)
    )
    return con, filters, sla, thresholds


def test_marginal_medium_confidence_gap_is_not_flagged_as_a_suspicious_signal():
    con, filters, sla, thresholds = _setup_marginal()
    issues = detect_vendor_ota_breaches(con, filters, sla, thresholds)
    assert issues == []
