from datetime import date
from types import SimpleNamespace

import pandas as pd

from core.chat.agent import OperationsChatAgent, build_plan, scope_phrase
from core.chat.tools import delay_report, requested_action
from core.database.duckdb_connection import create_memory_connection, load_dataframe
from core.models.action import ActionType
from core.models.chat import ChatPlan, ChatRoute, ChatTool, ChatToolResult
from core.reason.reasoning_service import (
    compose_chat_answer,
    extract_period_days,
    route_chat_question,
)


def _trip_frame() -> pd.DataFrame:
    """Vendor A is late five times, only twice with a reason recorded; vendor B is a decoy."""
    return pd.DataFrame(
        {
            "trip_date": [date(2026, 7, 1)] * 8,
            "trip_id": [f"T{i}" for i in range(8)],
            "vendor_id": ["A"] * 6 + ["B"] * 2,
            "business_unit": ["BU"] * 8,
            "office": ["HQ"] * 8,
            "shift_type": ["09:00"] * 8,
            "is_ota_eligible": [True] * 8,
            "is_on_time": [False, False, False, False, False, True, False, False],
            "delay_reason": [
                "NODELAY",
                "NODELAY",
                "NODELAY",
                "TRAFFIC",
                "TRAFFIC",
                "NODELAY",
                "DRIVER",
                "DRIVER",
            ],
            "calculated_delay_minutes": [20.0] * 8,
            "valid_employee_count": [1] * 8,
        }
    )


def _route_without_llm(monkeypatch, question: str, previous: dict | None = None):
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")
    return route_chat_question(question, [], previous)[0]


def test_routes_explicit_trip_lookup(monkeypatch):
    route = _route_without_llm(monkeypatch, "Give details for trip 1,516,906")
    assert route.tool == ChatTool.TRIP_LOOKUP
    assert route.trip_id == "1516906"


def test_trip_question_about_safety_reads_that_trips_alerts(monkeypatch):
    route = _route_without_llm(
        monkeypatch, "what are the safety we are having in this trip 4927479?"
    )
    assert route.tool == ChatTool.TRIP_SAFETY
    assert route.trip_id == "4927479"


def test_routes_ota_period_and_group(monkeypatch):
    route = _route_without_llm(monkeypatch, "Show OTA by office for the last 14 days")
    assert route.tool == ChatTool.OTA_REPORT
    assert route.period_days == 14
    assert route.group_by == "office"


def test_routes_sla_before_general_ota(monkeypatch):
    route = _route_without_llm(monkeypatch, "Which vendors are below the OTA SLA?")
    assert route.tool == ChatTool.SLA_BREACH_REPORT


def test_does_not_generate_sql_for_unsupported_request(monkeypatch):
    route = _route_without_llm(monkeypatch, "Delete all transport records")
    assert route.tool == ChatTool.HELP


def test_followup_stays_on_the_trip_already_discussed(monkeypatch):
    route = _route_without_llm(
        monkeypatch,
        "was anything left unacknowledged?",
        {"tool": "TRIP_SAFETY", "trip_id": "4927479"},
    )
    assert route.tool == ChatTool.TRIP_SAFETY
    assert route.trip_id == "4927479"


def test_weeks_and_months_resolve_to_days():
    assert extract_period_days("OTA for the last 2 weeks") == 14
    assert extract_period_days("alerts in the past month") == 30
    assert extract_period_days("alerts for trip 4927479") is None


def test_period_is_capped_at_the_loaded_window():
    assert extract_period_days("OTA for the last 12 months") == 92


def test_answer_falls_back_to_verified_summary_without_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")
    result = ChatToolResult(answer="**3** alerts were recorded.", tool=ChatTool.ALERTS_REPORT)
    answer, source, _ = compose_chat_answer("how many alerts?", result)
    assert answer == result.answer
    assert source == "template"


def test_routes_cross_domain_manager_questions_without_llm(monkeypatch):
    assert _route_without_llm(monkeypatch, "compare vendors for the last 2 weeks").tool == ChatTool.ENTITY_COMPARISON
    assert _route_without_llm(monkeypatch, "show no-shows by shift").tool == ChatTool.NO_SHOW_REPORT
    assert _route_without_llm(monkeypatch, "review billing cost").tool == ChatTool.BILLING_REPORT
    assert _route_without_llm(monkeypatch, "show feedback by vendor").tool == ChatTool.FEEDBACK_REPORT


def test_operational_overview_builds_bounded_multi_step_plan(monkeypatch):
    route = _route_without_llm(
        monkeypatch, "give me an operational overview and draft follow-up actions"
    )
    plan = build_plan("give me an operational overview and draft follow-up actions", route)
    assert [step.tool for step in plan.steps] == [
        ChatTool.OPERATIONAL_OVERVIEW,
        ChatTool.ENTITY_COMPARISON,
        ChatTool.ALERTS_REPORT,
        ChatTool.SLA_BREACH_REPORT,
        ChatTool.IMPACTED_TRIPS,
    ]
    assert plan.requires_approval is True
    assert len(plan.steps) == 5


def test_action_followup_reuses_previous_verified_scope(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")
    previous = {
        "tool": "SLA_BREACH_REPORT",
        "period_days": 14,
        "group_by": "vendor",
        "manager_role": "TRANSPORT_MANAGER",
    }
    route, source, detail = route_chat_question(
        "draft an escalation email for these vendors", [], previous
    )
    assert route.tool == ChatTool.SLA_BREACH_REPORT
    assert route.period_days == 14
    assert route.action_intent == "draft"
    assert (source, detail) == ("rules", "action follow-up")


def test_asking_to_send_a_mail_produces_an_approval_gated_draft(monkeypatch):
    route = _route_without_llm(
        monkeypatch,
        "send a email to this vendor for do the root cause ananlysis by sending the statics we have",
    )
    assert route.action_intent in {"draft", "email"}
    action = requested_action(route, "Vendor A missed the OTA SLA.")
    assert action.action_type == ActionType.DRAFT_VENDOR_ESCALATION_EMAIL
    assert action.requires_human_approval
    assert action.email_body and "Vendor A missed the OTA SLA." in action.email_body


def test_scope_phrase_says_when_no_vendor_filter_was_applied():
    route = ChatRoute(tool=ChatTool.DELAY_REPORT, period_days=7, business_unit="BU")
    assert "all vendors" in scope_phrase(route)
    assert "vendor A" in scope_phrase(route.model_copy(update={"vendor": "A"}))


def test_delay_report_scopes_to_the_vendor_and_never_ranks_missing_reasons():
    con = create_memory_connection()
    load_dataframe(con, "mobility_trip_360", _trip_frame())
    result = delay_report(
        SimpleNamespace(conn=con),
        ChatRoute(tool=ChatTool.DELAY_REPORT, period_days=1, vendor="A"),
    )
    assert "5 late trips" in result.answer
    assert "TRAFFIC" in result.answer and "DRIVER" not in result.answer
    assert "NODELAY" not in result.answer
    assert "**3** late trips carry no recorded reason" in result.answer
    assert result.rows[0]["delay_reason"] == "TRAFFIC"


def _routed(monkeypatch, question: str, previous: dict | None, scope: dict, persona: str) -> ChatRoute:
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")
    agent = SimpleNamespace(_vendors=[])
    agent._named_vendor = lambda text: OperationsChatAgent._named_vendor(agent, text)
    state = {
        "messages": [{"role": "user", "content": question}],
        "route": previous,
        "scope": scope,
        "persona": persona,
    }
    return ChatRoute.model_validate(OperationsChatAgent._route(agent, state)["route"])


def test_workspace_filters_outrank_a_subject_inherited_from_the_conversation(monkeypatch):
    route = _routed(
        monkeypatch,
        "who boarded or no-showed on this shift?",
        {"tool": "ALERTS_REPORT", "period_days": 7, "vendor": "Stale Vendor", "office": "Stale Office"},
        {"business_unit": None, "office": "Cedar Ridge Office", "shift": "09:00", "vendor": None, "period_days": 1},
        "LINE_MANAGER",
    )
    assert route.tool == ChatTool.SHIFT_READINESS_REPORT
    assert (route.vendor, route.office, route.period_days) == (None, "Cedar Ridge Office", 1)


def test_ranking_question_drops_the_single_vendor_filter(monkeypatch):
    route = _routed(
        monkeypatch,
        "compare vendors for the last 7 days",
        None,
        {"vendor": "Pooja Sokolov Travel", "period_days": 7},
        "TRANSPORT_MANAGER",
    )
    assert route.tool == ChatTool.ENTITY_COMPARISON
    assert route.vendor is None


def test_action_request_is_answered_by_the_draft_not_by_a_written_email(monkeypatch):
    route = _route_without_llm(monkeypatch, "send an email to Vendor A about this")
    result = ChatToolResult(
        answer="Found **82 late trips**.",
        tool=ChatTool.DELAY_REPORT,
        grounded_summary="Found **82 late trips**. Scope: the latest 7 day(s), vendor A.",
        proposed_action=requested_action(route, "Found **82 late trips**."),
        plan=ChatPlan(goal="", steps=[], proposed_action_intent=route.action_intent),
    )
    answer, source, detail = compose_chat_answer("send an email to Vendor A about this", result)
    assert source == "template" and detail == "approval-gated draft summary"
    assert "82 late trips" in answer and "approval" in answer
    assert "Dear" not in answer and "[Your Name]" not in answer


def test_vendor_named_in_the_question_scopes_the_query():
    con = create_memory_connection()
    load_dataframe(
        con,
        "mobility_trip_360",
        pd.DataFrame({"vendor_id": ["Pooja Sokolov Travel", "Meera Lebedev Travel"]}),
    )
    agent = SimpleNamespace(_vendors=None, context=SimpleNamespace(conn=con))
    named = OperationsChatAgent._named_vendor(agent, "email pooja sokolov travel about OTA")
    assert named == "Pooja Sokolov Travel"
    assert OperationsChatAgent._named_vendor(agent, "email this vendor about OTA") is None
