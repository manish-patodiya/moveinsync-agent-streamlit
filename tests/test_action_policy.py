from core.act.action_policy import create_actions
from core.models.action import ActionType
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
