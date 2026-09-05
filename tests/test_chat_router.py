from core.models.chat import ChatTool, ChatToolResult
from core.reason.reasoning_service import (
    compose_chat_answer,
    extract_period_days,
    route_chat_question,
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
    answer, source, _ = compose_chat_answer("how many alerts?", result, [])
    assert answer == result.answer
    assert source == "template"
