from datetime import date

import pandas as pd

from core.act.action_policy import create_persona_actions
from core.chat.agent import build_plan
from core.database.duckdb_connection import create_memory_connection, load_dataframe
from core.models.chat import ChatRoute, ChatTool
from core.models.evidence import Evidence
from core.models.issue import CandidateIssue, IssueType, Severity
from core.models.persona import PERSONA_PERIOD_DAYS, Persona, PersonaScope, allowed_tools
from core.privacy.masking import assert_no_raw_stwid, mask_rider
from core.reason.reasoning_service import route_chat_question, template_reasoning, to_persona_decision
from core.sense.leadership_brief import leadership_brief_markdown
from core.sense.persona_insights import build_persona_pulse, shift_readiness, vendor_scorecard
from core.models.workflow_state import AnalysisFilters


def test_mask_is_stable_and_hides_stwid():
    assert mask_rider("12345").startswith("Rider-")
    assert mask_rider("12345") == mask_rider("12345")
    assert "12345" not in mask_rider("12345")
    assert_no_raw_stwid([{"rider": mask_rider("99"), "status": "No-show"}])


def test_assert_no_raw_stwid_rejects_key():
    try:
        assert_no_raw_stwid([{"stwid": "1"}])
    except ValueError:
        return
    raise AssertionError("raw stwid must fail")


def test_shift_readiness_masks_roster():
    con = create_memory_connection()
    load_dataframe(
        con,
        "employee_legs_clean",
        pd.DataFrame(
            {
                "trip_date": [date(2026, 7, 1)] * 3,
                "stwid": ["111", "222", "333"],
                "boarding_status": ["Boarded", "Not Boarded", "Boarded"],
                "is_no_show": [False, True, False],
                "pickup_delay_minutes": [0, 0, 25],
                "not_boarding_reason": [None, "NO_SHOW", None],
                "shift_type": ["09:00"] * 3,
                "office": ["HQ"] * 3,
                "business_unit": ["BU"] * 3,
                "trip_id": ["T1", "T2", "T3"],
            }
        ),
    )
    scope = PersonaScope(
        persona=Persona.LINE_MANAGER,
        office="HQ",
        shift="09:00",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
    )
    data = shift_readiness(con, scope)
    assert data["no_shows"] == 1
    assert data["late_pickups"] == 1
    assert_no_raw_stwid(data["roster"])
    assert all(row["rider"].startswith("Rider-") for row in data["roster"])
    assert all("stwid" not in row for row in data["roster"])


def test_facilities_scorecard_and_brief_caveats():
    con = create_memory_connection()
    load_dataframe(
        con,
        "mobility_trip_360",
        pd.DataFrame(
            {
                "trip_date": [date(2026, 7, 1)] * 4,
                "vendor_id": ["A", "A", "B", "B"],
                "is_ota_eligible": [True] * 4,
                "is_on_time": [False, True, True, True],
                "billed_cost": [100.0, 100.0, 50.0, 50.0],
                "billed_km": [10.0, 10.0, 10.0, 10.0],
                "alert_count": [1, 0, 0, 0],
                "avg_safety_rating": [3.0, 4.0, 5.0, 5.0],
                "business_unit": ["BU"] * 4,
                "office": ["HQ"] * 4,
                "shift_type": ["09:00"] * 4,
            }
        ),
    )
    filters = AnalysisFilters(start_date=date(2026, 7, 1), end_date=date(2026, 7, 1))
    rows = vendor_scorecard(con, filters)
    assert rows
    assert "ota_pct" in rows[0] and "cost_per_km" in rows[0]
    assert "sla_gap_pp" in rows[0]
    worst = min(rows, key=lambda row: row["ota_pct"] or 100)
    assert worst["vendor"] == "A"
    scope = PersonaScope(
        persona=Persona.FACILITIES_HEAD,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
    )
    pulse = type("P", (), {"kpi": {"ota_pct": 75, "sla_target_pct": 90, "total_billed_cost": 300, "electric_trip_pct": 2, "safety_alerts": 1, "label": "proxy"}, "vendor_scorecard": rows, "caveats": ["Billed cost is actual invoice spend, not budget vs actual."]})()
    md = leadership_brief_markdown(scope, pulse, [])
    assert "not budget vs actual" in md
    assert "not an emissions figure" in md.lower() or "not emissions" in md.lower()


def test_vendor_drill_has_offices_and_delay_reasons():
    from core.sense.persona_insights import vendor_drill

    con = create_memory_connection()
    load_dataframe(
        con,
        "mobility_trip_360",
        pd.DataFrame(
            {
                "trip_id": ["t1", "t2", "t3"],
                "trip_date": [date(2026, 7, 1)] * 3,
                "vendor_id": ["A", "A", "B"],
                "office": ["HQ", "Park", "HQ"],
                "shift_type": ["09:00", "18:00", "09:00"],
                "is_ota_eligible": [True] * 3,
                "is_on_time": [False, False, True],
                "delay_reason": ["TRAFFIC", "TRAFFIC", "NODELAY"],
                "calculated_delay_minutes": [20.0, 15.0, 0.0],
                "alert_count": [2, 0, 0],
                "open_alert_count": [1, 0, 0],
                "sev1_alert_count": [1, 0, 0],
                "billed_cost": [10.0] * 3,
                "billed_km": [1.0] * 3,
            }
        ),
    )
    drill = vendor_drill(
        con,
        AnalysisFilters(start_date=date(2026, 7, 1), end_date=date(2026, 7, 1)),
        "A",
    )
    assert drill["vendor"] == "A"
    assert drill["delay_reasons"][0]["delay_reason"] == "TRAFFIC"
    assert drill["impacted_trips"]
    assert all("stwid" not in row for row in drill["impacted_trips"])


def test_rider_trip_context_and_shift_hotspots_hide_stwid():
    from core.sense.persona_insights import rider_trip_context, shift_vendor_hotspots

    con = create_memory_connection()
    load_dataframe(
        con,
        "mobility_trip_360",
        pd.DataFrame(
            {
                "trip_id": ["T3"],
                "vendor_id": ["CabCo"],
                "office": ["HQ"],
                "shift_type": ["09:00"],
                "delay_reason": ["TRAFFIC"],
                "calculated_delay_minutes": [12.0],
                "alert_count": [0],
            }
        ),
    )
    load_dataframe(
        con,
        "employee_legs_clean",
        pd.DataFrame(
            {
                "trip_id": ["T3"],
                "trip_date": [date(2026, 7, 1)],
                "office": ["HQ"],
                "shift_type": ["09:00"],
                "pickup_delay_minutes": [25],
                "stwid": ["999"],
            }
        ),
    )
    ctx = rider_trip_context(con, "T3")
    assert ctx["vendor"] == "CabCo"
    assert "stwid" not in ctx
    hot = shift_vendor_hotspots(
        con,
        PersonaScope(
            persona=Persona.LINE_MANAGER,
            office="HQ",
            shift="09:00",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
        ),
    )
    assert hot[0]["vendor"] == "CabCo"
    assert_no_raw_stwid(hot)


def test_inherit_vendor_into_follow_up_route():
    from core.chat.agent import _inherit_context

    route = ChatRoute(tool=ChatTool.DELAY_REPORT)
    route = _inherit_context(route, {"vendor": "Mikhailov Travel", "office": "Cedar Ridge"}, "why late")
    assert route.vendor == "Mikhailov Travel"
    assert route.office == "Cedar Ridge"


def test_line_manager_actions_and_persona_owner():
    issue = CandidateIssue(
        issue_id="r1",
        issue_type=IssueType.SHIFT_READINESS,
        severity=Severity.HIGH,
        title="Readiness",
        business_scope={"office": "HQ", "shift": "09:00"},
        current_metric="unready_riders",
        current_value=2,
        affected_trip_count=10,
        evidence=[Evidence(label="No-shows", value=1)],
        data_confidence="HIGH",
        allowed_action_types=["REQUEST_RIDER_FOLLOW_UP", "ASSIGN_SHIFT_FOLLOW_UP", "ACKNOWLEDGE_READINESS_RISK"],
    )
    actions = create_persona_actions(
        Persona.LINE_MANAGER,
        [issue],
        {issue.issue_id: template_reasoning(issue)},
        {"auto_create_manager_alerts": False, "auto_create_email_drafts": False},
    )
    types = {action.action_type.value for action in actions}
    assert "REQUEST_RIDER_FOLLOW_UP" in types
    assert "ASSIGN_SHIFT_FOLLOW_UP" in types
    assert all(action.owner_role.value == "LINE_MANAGER" for action in actions)
    assert all(action.requires_human_approval for action in actions)


def test_facilities_persona_adds_leadership_actions():
    issue = CandidateIssue(
        issue_id="v1",
        issue_type=IssueType.VENDOR_OTA_BREACH,
        severity=Severity.HIGH,
        title="Vendor OTA",
        business_scope={"vendor": "A"},
        current_metric="ota",
        current_value=70,
        affected_trip_count=20,
        evidence=[Evidence(label="OTA", value=70)],
        data_confidence="HIGH",
        allowed_action_types=["REQUEST_CORRECTIVE_PLAN"],
    )
    types = {
        action.action_type.value
        for action in create_persona_actions(
            Persona.FACILITIES_HEAD,
            [issue],
            {issue.issue_id: template_reasoning(issue)},
            {"auto_create_manager_alerts": False, "auto_create_email_drafts": False},
        )
    }
    assert "REVIEW_VENDOR_PERFORMANCE" in types
    assert "RECORD_SLA_RECOVERY" in types
    assert "SHARE_MOBILITY_BRIEF" in types


def test_persona_decision_schema_from_template():
    issue = CandidateIssue(
        issue_id="i1",
        issue_type=IssueType.VENDOR_OTA_BREACH,
        severity=Severity.HIGH,
        title="Vendor OTA",
        business_scope={"vendor": "A"},
        current_metric="ota",
        current_value=70,
        affected_trip_count=5,
        evidence=[Evidence(label="OTA", value=70)],
        data_confidence="HIGH",
        allowed_action_types=["REQUEST_CORRECTIVE_PLAN"],
    )
    from core.reason.reasoning_service import reason_about_issue, to_persona_decision

    result = reason_about_issue(issue, [], llm_enabled=False)
    decision = to_persona_decision(issue, result, "TRANSPORT_MANAGER")
    assert decision.headline
    assert decision.cited_benchmark_facts
    assert decision.owner
    assert decision.due_or_monitor


def test_line_manager_cannot_open_trip_lookup(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")
    route, *_ = route_chat_question("Give details for trip 1516906", [], None, "LINE_MANAGER")
    assert route.tool in allowed_tools(Persona.LINE_MANAGER)
    assert route.tool != ChatTool.TRIP_LOOKUP


def test_line_manager_plan_stays_on_readiness():
    route = ChatRoute(tool=ChatTool.OPERATIONAL_OVERVIEW, persona="LINE_MANAGER")
    plan = build_plan("full picture", route)
    assert all(step.tool == ChatTool.SHIFT_READINESS_REPORT for step in plan.steps)


def test_persona_default_horizons():
    assert PERSONA_PERIOD_DAYS[Persona.TRANSPORT_MANAGER] == 7
    assert PERSONA_PERIOD_DAYS[Persona.FACILITIES_HEAD] == 30
    assert PERSONA_PERIOD_DAYS[Persona.LINE_MANAGER] == 1


def test_prior_period_caveat_when_sample_missing():
    from core.sense.kpis import calculate_kpis

    con = create_memory_connection()
    load_dataframe(
        con,
        "mobility_trip_360",
        pd.DataFrame(
            {
                "trip_id": ["t1"],
                "trip_date": [date(2026, 7, 1)],
                "business_unit": ["BU"],
                "office": ["HQ"],
                "shift_type": ["09:00"],
                "vendor_id": ["A"],
                "is_ota_eligible": [True],
                "is_on_time": [True],
                "valid_employee_count": [1],
                "alert_count": [0],
                "billed_cost": [10.0],
            }
        ),
    )
    filters = AnalysisFilters(start_date=date(2026, 7, 1), end_date=date(2026, 7, 1))
    kpi = calculate_kpis(con, filters, 90.0)
    assert kpi["prior_ota_pct"] is None
    assert kpi["ota_pct"] == 100.0


def test_real_line_manager_roster_never_exposes_stwid():
    from core.bootstrap.app_context import bootstrap

    ctx = bootstrap()
    end = ctx.conn.execute("SELECT MAX(trip_date) FROM mobility_trip_360").fetchone()[0]
    busy = ctx.conn.execute(
        """
        SELECT office, shift_type
        FROM employee_legs_clean
        WHERE trip_date = ? AND office IS NOT NULL AND shift_type IS NOT NULL
          AND stwid IS NOT NULL AND stwid <> '0'
        GROUP BY 1, 2
        ORDER BY COUNT(*) DESC
        LIMIT 1
        """,
        [end],
    ).fetchone()
    scope = PersonaScope(
        persona=Persona.LINE_MANAGER,
        office=busy[0] if busy else None,
        shift=busy[1] if busy else None,
        start_date=end,
        end_date=end,
    )
    pulse = build_persona_pulse(ctx.conn, scope, ctx.sla, ctx.thresholds)
    assert_no_raw_stwid(pulse.roster)
    assert "stwid" not in str(pulse.model_dump(mode="json")).lower()
