from core.act.action_policy import create_actions
from core.models.action import ActionType
from core.models.benchmark import VendorAttribution
from core.models.evidence import Evidence
from core.models.issue import CandidateIssue, IssueType, Severity
from core.reason.reasoning_service import template_reasoning


def _issue(issue_type: IssueType = IssueType.VENDOR_OTA_BREACH) -> CandidateIssue:
    allowed = ["CREATE_MANAGER_ALERT", "DRAFT_VENDOR_ESCALATION_EMAIL"]
    return CandidateIssue(
        issue_id="issue-1",
        issue_type=issue_type,
        severity=Severity.HIGH,
        title="Test issue",
        business_scope={"vendor": "Vendor A"},
        current_metric="ota",
        current_value=70.0,
        affected_trip_count=20,
        affected_employee_count=40,
        evidence=[Evidence(label="OTA", value=70.0)],
        data_confidence="HIGH",
        allowed_action_types=allowed,
    )


def test_automatic_alerts_and_emails_follow_config():
    issue = _issue()
    output = template_reasoning(issue)
    actions = create_actions(
        [issue],
        {issue.issue_id: output},
        {"auto_create_manager_alerts": True, "auto_create_email_drafts": True},
    )
    assert {action.action_type for action in actions} == {
        ActionType.CREATE_MANAGER_ALERT,
        ActionType.DRAFT_VENDOR_ESCALATION_EMAIL,
    }
    email = next(a for a in actions if a.action_type == ActionType.DRAFT_VENDOR_ESCALATION_EMAIL)
    assert email.requires_human_approval is True
    assert email.status == "PROPOSED"


def test_disabled_automation_creates_no_vendor_actions():
    issue = _issue()
    actions = create_actions(
        [issue],
        {issue.issue_id: template_reasoning(issue)},
        {"auto_create_manager_alerts": False, "auto_create_email_drafts": False},
    )
    assert actions == []


def test_action_policy_has_no_external_send_path():
    issue = _issue()
    actions = create_actions(
        [issue],
        {issue.issue_id: template_reasoning(issue)},
        {"auto_create_manager_alerts": False, "auto_create_email_drafts": True},
    )
    assert actions[0].status == "PROPOSED"
    assert not hasattr(actions[0], "recipient_address")


def test_root_cause_outputs_fan_out_to_one_action_per_culprit_vendor():
    issue = _issue()
    attributions = [
        VendorAttribution(vendor_id="Vendor A", impact_rank=1, impact_score=100.0, primary_factors=["f1"]),
        VendorAttribution(vendor_id="Vendor B", impact_rank=2, impact_score=50.0, primary_factors=["f2"]),
    ]
    actions = create_actions(
        [issue],
        {issue.issue_id: template_reasoning(issue)},
        {"auto_create_manager_alerts": True, "auto_create_email_drafts": True},
        root_cause_outputs={issue.issue_id: attributions},
    )
    vendor_actions = [a for a in actions if a.action_type == ActionType.DRAFT_VENDOR_ESCALATION_EMAIL]
    assert {a.target_vendor_id for a in vendor_actions} == {"Vendor A", "Vendor B"}
    assert all(a.requires_human_approval for a in vendor_actions)
    assert {a.target_vendor_id: a.impact_rank for a in vendor_actions} == {"Vendor A": 1, "Vendor B": 2}


def test_max_escalation_targets_caps_vendor_fan_out():
    issue = _issue()
    attributions = [
        VendorAttribution(vendor_id=f"Vendor {i}", impact_rank=i, impact_score=100.0 - i, primary_factors=[])
        for i in range(1, 6)
    ]
    actions = create_actions(
        [issue],
        {issue.issue_id: template_reasoning(issue)},
        {"auto_create_manager_alerts": False, "auto_create_email_drafts": True},
        root_cause_outputs={issue.issue_id: attributions},
        max_escalation_targets=2,
    )
    vendor_actions = [a for a in actions if a.action_type == ActionType.DRAFT_VENDOR_ESCALATION_EMAIL]
    assert len(vendor_actions) == 2
    assert {a.target_vendor_id for a in vendor_actions} == {"Vendor 1", "Vendor 2"}


def test_no_root_cause_output_falls_back_to_single_generic_draft():
    """Backward-compatible: when Root Cause found no culprit vendors, keep the old behavior
    of one generic issue-level draft rather than producing zero actions."""
    issue = _issue()
    actions = create_actions(
        [issue],
        {issue.issue_id: template_reasoning(issue)},
        {"auto_create_manager_alerts": False, "auto_create_email_drafts": True},
    )
    vendor_actions = [a for a in actions if a.action_type == ActionType.DRAFT_VENDOR_ESCALATION_EMAIL]
    assert len(vendor_actions) == 1
    assert vendor_actions[0].target_vendor_id is None
