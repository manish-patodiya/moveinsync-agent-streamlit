from core.models.evidence import Evidence
from core.models.issue import CandidateIssue, IssueType, Severity
from core.reason.llm_provider import describe_llm, get_llm
from core.reason.reasoning_service import reason_about_issue


def _issue() -> CandidateIssue:
    return CandidateIssue(
        issue_id="issue-1",
        issue_type=IssueType.VENDOR_OTA_BREACH,
        severity=Severity.HIGH,
        title="Vendor OTA breach: Vendor A",
        business_scope={"vendor": "Vendor A"},
        current_metric="trip_end_ota_pct",
        current_value=70.0,
        affected_trip_count=20,
        evidence=[Evidence(label="OTA", value=70.0)],
        data_confidence="HIGH",
        allowed_action_types=["CREATE_MANAGER_ALERT"],
    )


def test_openai_without_key_is_unavailable(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4.1-mini")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert describe_llm()["available"] is False
    assert get_llm() is None


def test_ollama_provider_reports_configured_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "qwen:14b")
    config = describe_llm()
    assert (config["provider"], config["model"], config["available"]) == ("ollama", "qwen:14b", True)


def test_disabled_llm_uses_template_without_network(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    result = reason_about_issue(_issue(), [], llm_enabled=False)
    assert result.source == "template"
    assert "disabled" in result.fallback_reason
    assert result.output.manager_summary


def test_unknown_provider_falls_back_to_template(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "does-not-exist")
    result = reason_about_issue(_issue(), ["coverage caveat"])
    assert result.source == "template"
    assert result.output.recommended_action_types == ["CREATE_MANAGER_ALERT"]
