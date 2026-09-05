from __future__ import annotations

import hashlib
from datetime import datetime

from core.act.draft_factory import make_email_draft
from core.models.action import Action, ActionType, ManagerRole
from core.models.issue import CandidateIssue, IssueType, Severity
from core.models.live_alert import LiveAlert
from core.models.output_schemas import IssueReasoningOutput


def build_action(
    issue: CandidateIssue,
    action_type: ActionType,
    rationale: str,
    *,
    approval: bool = False,
    subject: str | None = None,
    body: str | None = None,
    owner_role: ManagerRole = ManagerRole.TRANSPORT_MANAGER,
    source: str = "WORKFLOW",
    target_type: str | None = None,
    target_name: str | None = None,
    call_script: str | None = None,
    expected_outcome: str | None = None,
    monitoring_condition: str | None = None,
    due_at: datetime | None = None,
) -> Action:
    key = f"{issue.issue_id}|{action_type}"
    return Action(
        action_id=f"action-{hashlib.sha1(key.encode()).hexdigest()[:10]}",
        issue_id=issue.issue_id,
        action_type=action_type,
        priority=issue.severity.value,
        title=f"{action_type.value.replace('_', ' ').title()}: {issue.title}",
        rationale=rationale,
        requires_human_approval=approval,
        email_subject=subject,
        email_body=body,
        owner_role=owner_role,
        requested_by=owner_role,
        source=source,
        target_type=target_type,
        target_name=target_name,
        call_script=call_script,
        expected_outcome=expected_outcome,
        monitoring_condition=monitoring_condition,
        due_at=due_at,
        supporting_evidence=[
            f"{item.label}: {item.value}"
            + (f" ({item.comparison})" if item.comparison else "")
            for item in issue.evidence[:6]
        ],
    )


_action = build_action


def build_live_alert_action(
    alert: LiveAlert,
    action_type: ActionType,
    *,
    email_subject: str | None = None,
    email_body: str | None = None,
    call_script: str | None = None,
) -> Action:
    role = (
        ManagerRole.SHIFT_MANAGER
        if action_type
        in {
            ActionType.ACKNOWLEDGE_ALERT,
            ActionType.REQUEST_DRIVER_CALL,
            ActionType.REQUEST_EMPLOYEE_CALL,
        }
        else ManagerRole.TRANSPORT_MANAGER
    )
    key = f"{alert.event_id}|{action_type}"
    return Action(
        action_id=f"live-{hashlib.sha1(key.encode()).hexdigest()[:10]}",
        issue_id=alert.event_id,
        action_type=action_type,
        priority=str(alert.priority),
        title=f"{action_type.value.replace('_', ' ').title()}: {alert.event_type}",
        rationale=(
            f"{alert.severity} {alert.event_type} on trip {alert.trip_id}; "
            f"current state {alert.state}."
        ),
        owner_role=role,
        requested_by=role,
        requires_human_approval=True,
        email_subject=email_subject,
        email_body=email_body,
        call_script=call_script,
        target_type="ALERT",
        target_name=alert.event_id,
        source="LIVE_ALERTS",
        supporting_evidence=[
            f"Trip: {alert.trip_id}",
            f"Event: {alert.event_type}",
            f"Severity: {alert.severity}",
            f"State: {alert.state}",
        ],
        expected_outcome="The alert has an accountable response and recorded next step.",
        monitoring_condition="Monitor until the alert is acknowledged and closed.",
    )


def create_actions(
    issues: list[CandidateIssue],
    reasoning: dict[str, IssueReasoningOutput],
    automation: dict,
) -> list[Action]:
    """Apply fixed policy. No external communication is performed here."""
    actions: list[Action] = []
    for issue in issues:
        output = reasoning.get(issue.issue_id)
        rationale = output.manager_summary if output else issue.title
        if automation["auto_create_manager_alerts"] and issue.severity in {
            Severity.HIGH,
            Severity.CRITICAL,
        }:
            actions.append(_action(issue, ActionType.CREATE_MANAGER_ALERT, rationale))
        if issue.issue_type == IssueType.VENDOR_OTA_BREACH:
            if automation["auto_create_email_drafts"]:
                subject, body = make_email_draft(issue, output) if output else (issue.title, rationale)
                actions.append(
                    _action(
                        issue,
                        ActionType.DRAFT_VENDOR_ESCALATION_EMAIL,
                        rationale,
                        approval=True,
                        subject=subject,
                        body=body,
                        target_type="VENDOR",
                        target_name=issue.business_scope.get("vendor"),
                        expected_outcome="Vendor confirms a corrective plan and recovery target.",
                        monitoring_condition="Recheck OTA after the next matching reporting window.",
                    )
                )
            actions.extend(
                [
                    _action(
                        issue,
                        ActionType.REQUEST_CORRECTIVE_PLAN,
                        rationale,
                        approval=True,
                        target_type="VENDOR",
                        target_name=issue.business_scope.get("vendor"),
                        expected_outcome="A time-bound vendor corrective plan is recorded.",
                        monitoring_condition="Review plan delivery and next-period OTA.",
                    ),
                    _action(
                        issue,
                        ActionType.INVESTIGATE_DELAY,
                        rationale,
                        approval=True,
                        owner_role=ManagerRole.SHIFT_MANAGER,
                        target_type="SHIFT",
                        target_name=issue.business_scope.get("shift"),
                        expected_outcome="Recurring operational delay signals have assigned owners.",
                        monitoring_condition="Review the next three comparable shifts.",
                    ),
                    _action(
                        issue,
                        ActionType.MONITOR_KPI,
                        rationale,
                        approval=True,
                        expected_outcome="OTA returns toward the strongest applicable benchmark.",
                        monitoring_condition="Compare next-period OTA with SLA, prior period and peers.",
                    ),
                ]
            )
        elif issue.issue_type == IssueType.SAFETY_ESCALATION:
            actions.append(_action(issue, ActionType.CREATE_SAFETY_ESCALATION, rationale))
            if automation["auto_create_email_drafts"]:
                subject, body = make_email_draft(issue, output) if output else (issue.title, rationale)
                actions.append(
                    _action(
                        issue,
                        ActionType.DRAFT_VENDOR_ESCALATION_EMAIL,
                        rationale,
                        approval=True,
                        subject=subject,
                        body=body,
                        target_type="VENDOR_SAFETY",
                        target_name=issue.business_scope.get("vendor"),
                        expected_outcome="Critical events receive accountable closure.",
                        monitoring_condition="Monitor until critical alerts are acknowledged and closed.",
                    )
                )
            actions.extend(
                [
                    _action(
                        issue,
                        ActionType.INVESTIGATE_SAFETY,
                        rationale,
                        approval=True,
                        owner_role=ManagerRole.SHIFT_MANAGER,
                        expected_outcome="The trip status and immediate response are verified.",
                        monitoring_condition="Recheck open alerts at the acknowledgement SLA.",
                    ),
                    _action(
                        issue,
                        ActionType.MONITOR_KPI,
                        rationale,
                        approval=True,
                        expected_outcome="Open and late-acknowledged alert counts decline.",
                        monitoring_condition="Monitor acknowledgement SLA until closure.",
                    ),
                ]
            )
        elif issue.issue_type == IssueType.BILLING_ANOMALY:
            actions.append(_action(issue, ActionType.CREATE_BILLING_REVIEW_ALERT, rationale))
            actions.append(
                _action(
                    issue,
                    ActionType.INVESTIGATE_BILLING,
                    rationale,
                    approval=True,
                    expected_outcome="Trip distance, rate card and exception lines are reconciled.",
                    monitoring_condition="Keep affected lines pending until review closure.",
                )
            )
        elif issue.issue_type == IssueType.DATA_QUALITY:
            actions.append(_action(issue, ActionType.FLAG_DATA_QUALITY_ISSUE, rationale))
        elif issue.issue_type == IssueType.SHIFT_READINESS:
            actions.extend(
                [
                    _action(
                        issue,
                        ActionType.REQUEST_RIDER_FOLLOW_UP,
                        rationale,
                        approval=True,
                        owner_role=ManagerRole.LINE_MANAGER,
                        target_type="SHIFT",
                        target_name=issue.business_scope.get("shift"),
                        expected_outcome="Each unready masked rider has a recorded follow-up.",
                        monitoring_condition="Recheck the next comparable office and shift.",
                    ),
                    _action(
                        issue,
                        ActionType.ASSIGN_SHIFT_FOLLOW_UP,
                        rationale,
                        approval=True,
                        owner_role=ManagerRole.LINE_MANAGER,
                        target_type="SHIFT",
                        target_name=issue.business_scope.get("shift"),
                        expected_outcome="A shift owner is accountable for readiness recovery.",
                        monitoring_condition="Confirm boarded counts on the next matching shift.",
                    ),
                    _action(
                        issue,
                        ActionType.ACKNOWLEDGE_READINESS_RISK,
                        rationale,
                        approval=True,
                        owner_role=ManagerRole.LINE_MANAGER,
                        expected_outcome="The readiness risk is acknowledged on the operating record.",
                        monitoring_condition="Monitor until no-show and late-pickup counts fall.",
                    ),
                ]
            )
    return actions


def create_persona_actions(
    persona,
    issues: list[CandidateIssue],
    reasoning: dict[str, IssueReasoningOutput],
    automation: dict,
) -> list[Action]:
    from core.models.persona import Persona, PERSONA_OWNER_ROLE

    owner = PERSONA_OWNER_ROLE[Persona(persona)]
    actions = create_actions(issues, reasoning, automation)
    if Persona(persona) == Persona.FACILITIES_HEAD:
        seed = issues[0] if issues else None
        if seed is not None:
            rationale = reasoning[seed.issue_id].manager_summary if seed.issue_id in reasoning else seed.title
            extras = [
                (ActionType.REVIEW_VENDOR_PERFORMANCE, "Vendor scorecard review is recorded."),
                (ActionType.RECORD_SLA_RECOVERY, "An SLA recovery decision is on the operating record."),
                (ActionType.SHARE_MOBILITY_BRIEF, "Leadership can forward the verified operating brief."),
            ]
            existing = {action.action_type for action in actions}
            for action_type, outcome in extras:
                if action_type in existing:
                    continue
                actions.append(
                    _action(
                        seed,
                        action_type,
                        rationale,
                        approval=True,
                        owner_role=owner,
                        expected_outcome=outcome,
                        monitoring_condition="Review the next 30-day comparable window.",
                    )
                )
    for action in actions:
        if Persona(persona) == Persona.LINE_MANAGER:
            action.owner_role = ManagerRole.LINE_MANAGER
            action.requested_by = ManagerRole.LINE_MANAGER
        elif Persona(persona) == Persona.FACILITIES_HEAD and action.owner_role == ManagerRole.TRANSPORT_MANAGER:
            action.owner_role = ManagerRole.FACILITIES_HEAD
            action.requested_by = ManagerRole.FACILITIES_HEAD
    return actions
