import pytest

from core.application.mobility_service import MobilityService
from core.act.action_policy import create_actions
from core.models.action import ActionStatus, ActionType, ManagerRole
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
        ActionType.REQUEST_CORRECTIVE_PLAN,
        ActionType.INVESTIGATE_DELAY,
        ActionType.MONITOR_KPI,
    }
    email = next(a for a in actions if a.action_type == ActionType.DRAFT_VENDOR_ESCALATION_EMAIL)
    assert email.requires_human_approval is True
    assert email.status == "PROPOSED"
    shift_action = next(a for a in actions if a.action_type == ActionType.INVESTIGATE_DELAY)
    assert shift_action.owner_role == "SHIFT_MANAGER"
    assert shift_action.expected_outcome
    assert shift_action.monitoring_condition
    assert shift_action.supporting_evidence


def test_disabled_alert_and_email_automation_keeps_operational_actions():
    issue = _issue()
    actions = create_actions(
        [issue],
        {issue.issue_id: template_reasoning(issue)},
        {"auto_create_manager_alerts": False, "auto_create_email_drafts": False},
    )
    assert {action.action_type for action in actions} == {
        ActionType.REQUEST_CORRECTIVE_PLAN,
        ActionType.INVESTIGATE_DELAY,
        ActionType.MONITOR_KPI,
    }
    assert all(action.requires_human_approval for action in actions)


def test_action_policy_has_no_external_send_path():
    issue = _issue()
    actions = create_actions(
        [issue],
        {issue.issue_id: template_reasoning(issue)},
        {"auto_create_manager_alerts": False, "auto_create_email_drafts": True},
    )
    assert actions[0].status == "PROPOSED"
    assert not hasattr(actions[0], "recipient_address")


class _Store:
    def __init__(self):
        self.actions = []
        self.events = []

    def save(self, action):
        self.actions.append(action)

    def record(self, event):
        self.events.append(event)


def test_non_email_action_requires_approval_before_simulated_execution():
    service = MobilityService.__new__(MobilityService)
    service.action_store = _Store()
    service.context = type(
        "Context",
        (),
        {"settings": {"automation": {"auto_mark_approved_actions_simulated_sent": False}}},
    )()
    action = next(
        item
        for item in create_actions(
            [_issue()],
            {"issue-1": template_reasoning(_issue())},
            {"auto_create_manager_alerts": True, "auto_create_email_drafts": True},
        )
        if item.action_type == ActionType.INVESTIGATE_DELAY
    )
    with pytest.raises(ValueError):
        service.transition_action(action, ActionStatus.SIMULATED_COMPLETED)
    approved, _ = service.transition_action(
        action, ActionStatus.APPROVED, actor_role=ManagerRole.SHIFT_MANAGER
    )
    completed, event = service.transition_action(
        approved,
        ActionStatus.SIMULATED_COMPLETED,
        actor_role=ManagerRole.SHIFT_MANAGER,
        note="Shift review recorded",
    )
    assert completed.status == ActionStatus.SIMULATED_COMPLETED
    assert event.actor_role == ManagerRole.SHIFT_MANAGER
    assert event.note == "Shift review recorded"
