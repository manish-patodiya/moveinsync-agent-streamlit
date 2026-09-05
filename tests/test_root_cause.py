from datetime import date

import pandas as pd

from core.database.duckdb_connection import create_memory_connection, load_dataframe
from core.models.benchmark import VendorBenchmark
from core.models.evidence import Evidence
from core.models.issue import CandidateIssue, IssueType, Severity
from core.models.workflow_state import AnalysisFilters
from core.root_cause.root_cause_agent import rank_vendors
from core.root_cause.tools import compute_impact_score, get_compliance_flags, get_delay_reason_breakdown

WEIGHTS = {"affected_trips": 1.0, "affected_employees": 0.5, "gap_severity": 2.0, "trend_slope": 1.0}


def _benchmark(**overrides) -> VendorBenchmark:
    defaults = dict(
        vendor_id="Vendor A",
        metric="ota_pct",
        current_value=80.0,
        peer_value=90.0,
        sla_target=90.0,
        gap=10.0,
        affected_trips=5,
        affected_employees=10,
        trend=[],
        trend_direction="FLAT",
        trend_slope=0.0,
    )
    defaults.update(overrides)
    return VendorBenchmark(**defaults)


def test_compute_impact_score_ranks_bigger_damage_higher():
    small = _benchmark(gap=10.0, affected_trips=5, affected_employees=10)
    large = _benchmark(
        vendor_id="Vendor B", gap=40.0, affected_trips=50, affected_employees=100,
        trend_direction="WORSENING", trend_slope=-3.0,
    )
    assert compute_impact_score(large, WEIGHTS) > compute_impact_score(small, WEIGHTS)


def test_compute_impact_score_ignores_slope_unless_trend_is_worsening():
    improving = _benchmark(trend_direction="IMPROVING", trend_slope=5.0)
    flat = _benchmark(trend_direction="FLAT", trend_slope=0.0)
    assert compute_impact_score(improving, WEIGHTS) == compute_impact_score(flat, WEIGHTS)


def _setup_rank():
    rows = [
        {
            "trip_id": f"V-{i}",
            "trip_date": pd.Timestamp("2026-07-01"),
            "business_unit": "BU",
            "office": "HQ",
            "vendor_id": "Vendor A",
            "is_ota_eligible": True,
            "is_on_time": i >= 12,
            "delay_reason": "TRAFFIC" if i < 12 else "NODELAY",
            "shift_type": "MORNING" if i < 10 else "EVENING",
            "is_driver_nc": i < 3,
            "is_cab_nc": False,
        }
        for i in range(20)
    ]
    con = create_memory_connection()
    load_dataframe(con, "mobility_trip_360", pd.DataFrame(rows))
    scope = AnalysisFilters(business_unit="BU", office="HQ", start_date=date(2026, 7, 1), end_date=date(2026, 7, 1))
    return con, scope


def test_get_delay_reason_breakdown_orders_by_count():
    con, scope = _setup_rank()
    reasons = get_delay_reason_breakdown(con, "Vendor A", scope)
    assert reasons[0] == {"reason": "TRAFFIC", "count": 12, "pct": 100.0}


def test_get_compliance_flags_reports_driver_non_compliance_rate():
    con, scope = _setup_rank()
    flags = get_compliance_flags(con, "Vendor A", scope)
    assert flags["driver_nc_pct"] == 15.0
    assert flags["cab_nc_pct"] == 0.0


def test_rank_vendors_orders_by_impact_and_attaches_tool_sourced_factors():
    con, scope = _setup_rank()
    issue = CandidateIssue(
        issue_id="issue-1",
        issue_type=IssueType.VENDOR_OTA_BREACH,
        severity=Severity.HIGH,
        title="Vendor OTA breach: Vendor A",
        business_scope={"business_unit": "BU", "office": "HQ"},
        current_metric="trip_end_ota_pct",
        current_value=40.0,
        affected_trip_count=12,
        evidence=[Evidence(label="OTA", value=40.0)],
        data_confidence="HIGH",
        allowed_action_types=["CREATE_MANAGER_ALERT"],
    )
    benchmarks = [
        _benchmark(current_value=40.0, gap=50.0, affected_trips=12, affected_employees=24, trend_direction="WORSENING", trend_slope=-2.0)
    ]
    attributions = rank_vendors(con, issue, benchmarks, scope, WEIGHTS)
    assert len(attributions) == 1
    top = attributions[0]
    assert top.vendor_id == "Vendor A"
    assert top.impact_rank == 1
    joined = " ".join(top.primary_factors)
    assert "TRAFFIC" in joined
    assert "shift" in joined.lower()
    assert "non-compliance" in joined
    assert "worsening" in joined.lower()


def test_rank_vendors_orders_multiple_vendors_by_score_descending():
    con, scope = _setup_rank()
    issue = CandidateIssue(
        issue_id="issue-1",
        issue_type=IssueType.BILLING_ANOMALY,
        severity=Severity.HIGH,
        title="t",
        business_scope={"business_unit": "BU", "office": "HQ"},
        current_metric="cost_per_billed_km",
        current_value=1.0,
        affected_trip_count=1,
        evidence=[],
        data_confidence="MEDIUM",
        allowed_action_types=["CREATE_MANAGER_ALERT"],
    )
    low_impact = _benchmark(vendor_id="Vendor B", gap=5.0, affected_trips=1, affected_employees=1)
    high_impact = _benchmark(vendor_id="Vendor A", gap=40.0, affected_trips=50, affected_employees=100)
    attributions = rank_vendors(con, issue, [low_impact, high_impact], scope, WEIGHTS)
    assert [a.vendor_id for a in attributions] == ["Vendor A", "Vendor B"]
    assert attributions[0].impact_rank == 1
    assert attributions[1].impact_rank == 2
