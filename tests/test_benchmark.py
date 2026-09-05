from datetime import date, timedelta

import pandas as pd

from core.benchmark.benchmark_agent import benchmark_issue
from core.benchmark.tools import (
    classify_trend,
    get_peer_value,
    get_trend_series,
    list_candidate_vendors,
)
from core.database.duckdb_connection import create_memory_connection, load_dataframe
from core.models.evidence import Evidence
from core.models.issue import CandidateIssue, IssueType, Severity
from core.models.workflow_state import AnalysisFilters

WEEKS = [date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 15), date(2026, 6, 22)]
BENCHMARK_CONFIG = {
    "benchmark": {
        "max_vendors_per_issue": 5,
        "min_trips_per_vendor": 5,
        "trend_periods": 4,
        "trend_period_days": 7,
        "trend_slope_flat_threshold": 0.5,
    }
}


def _rows_for_week(vendor: str, week_start: date, ota_pct_target: float, n: int = 20) -> list[dict]:
    on_time_count = round(n * ota_pct_target / 100)
    return [
        {
            "trip_id": f"{vendor}-{week_start}-{i}",
            "trip_date": pd.Timestamp(week_start) + pd.Timedelta(days=i % 7),
            "business_unit": "BU",
            "office": "HQ",
            "vendor_id": vendor,
            "is_ota_eligible": True,
            "is_on_time": i < on_time_count,
            "valid_employee_count": 2,
            "billed_cost": 100.0,
            "billed_km": 10.0,
            "billing_vendor": vendor,
        }
        for i in range(n)
    ]


def _setup_trend():
    # Vendor A worsens week over week; Vendor B stays steady -- lets us assert both directions.
    ota_a = [95, 85, 75, 60]
    ota_b = [90, 90, 90, 90]
    rows = []
    for week, a, b in zip(WEEKS, ota_a, ota_b):
        rows += _rows_for_week("Vendor A", week, a)
        rows += _rows_for_week("Vendor B", week, b)
    con = create_memory_connection()
    load_dataframe(con, "mobility_trip_360", pd.DataFrame(rows))
    filters = AnalysisFilters(
        business_unit="BU", office="HQ", start_date=WEEKS[-1], end_date=WEEKS[-1] + timedelta(days=6)
    )
    return con, filters


def test_list_candidate_vendors_returns_every_vendor_meeting_min_trips():
    con, filters = _setup_trend()
    vendors = list_candidate_vendors(con, filters, min_trips=5)
    assert set(vendors) == {"Vendor A", "Vendor B"}


def test_get_peer_value_excludes_the_named_vendor():
    con, filters = _setup_trend()
    peer = get_peer_value(con, filters, "ota_pct", exclude_vendor="Vendor A")
    assert peer == 90.0


def test_trend_series_is_oldest_to_newest():
    con, filters = _setup_trend()
    trend = get_trend_series(con, "Vendor A", filters, "ota_pct", num_periods=4, period_days=7, sla_target=90.0)
    assert [p.value for p in trend] == [95.0, 85.0, 75.0, 60.0]
    assert trend[0].period_start < trend[-1].period_start


def test_classify_trend_detects_worsening_and_flat():
    con, filters = _setup_trend()
    worsening_trend = get_trend_series(con, "Vendor A", filters, "ota_pct", 4, 7, 90.0)
    flat_trend = get_trend_series(con, "Vendor B", filters, "ota_pct", 4, 7, 90.0)
    direction, slope = classify_trend(worsening_trend, worse_is_lower=True)
    assert direction == "WORSENING"
    assert slope < 0
    direction, _ = classify_trend(flat_trend, worse_is_lower=True)
    assert direction == "FLAT"


def test_benchmark_issue_benchmarks_every_candidate_vendor_not_just_the_named_one():
    con, filters = _setup_trend()
    issue = CandidateIssue(
        issue_id="issue-1",
        issue_type=IssueType.VENDOR_OTA_BREACH,
        severity=Severity.HIGH,
        title="Vendor OTA breach: Vendor A",
        business_scope={"business_unit": "BU", "office": "HQ", "vendor": "Vendor A"},
        current_metric="trip_end_ota_pct",
        current_value=60.0,
        affected_trip_count=8,
        evidence=[Evidence(label="OTA", value=60.0)],
        data_confidence="HIGH",
        allowed_action_types=["CREATE_MANAGER_ALERT", "DRAFT_VENDOR_ESCALATION_EMAIL"],
    )
    sla = {"service_levels": {"trip_end_ota": {"target_pct": 90.0}}}
    benchmarks = benchmark_issue(con, issue, filters, sla, BENCHMARK_CONFIG)
    by_vendor = {b.vendor_id: b for b in benchmarks}
    assert set(by_vendor) == {"Vendor A", "Vendor B"}
    assert by_vendor["Vendor A"].trend_direction == "WORSENING"
    assert by_vendor["Vendor B"].trend_direction == "FLAT"
    assert by_vendor["Vendor A"].gap == 30.0


def test_benchmark_issue_returns_empty_for_issue_type_without_a_vendor_metric():
    con, filters = _setup_trend()
    issue = CandidateIssue(
        issue_id="issue-2",
        issue_type=IssueType.DATA_QUALITY,
        severity=Severity.HIGH,
        title="n/a",
        business_scope={"business_unit": "BU", "office": "HQ"},
        current_metric="n/a",
        current_value=0,
        affected_trip_count=0,
        evidence=[],
        data_confidence="HIGH",
        allowed_action_types=["FLAG_DATA_QUALITY_ISSUE"],
    )
    sla = {"service_levels": {"trip_end_ota": {"target_pct": 90.0}}}
    assert benchmark_issue(con, issue, filters, sla, BENCHMARK_CONFIG) == []
