from datetime import date

import pandas as pd

from core.bootstrap.app_context import load_configs
from core.database.duckdb_connection import create_memory_connection, load_dataframe
from core.models.issue import IssueType
from core.models.workflow_state import AnalysisFilters
from core.sense.anomaly_detector import (
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
