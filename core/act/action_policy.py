from __future__ import annotations

import hashlib

from core.act.draft_factory import make_email_draft
from core.models.action import Action, ActionType
from core.models.issue import CandidateIssue, IssueType, Severity
from core.models.output_schemas import IssueReasoningOutput


def _action(
    issue: CandidateIssue,
    action_type: ActionType,
    rationale: str,
    *,
    approval: bool = False,
    subject: str | None = None,
    body: str | None = None,
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
        if issue.issue_type == IssueType.VENDOR_OTA_BREACH and automation["auto_create_email_drafts"]:
            subject, body = make_email_draft(issue, output) if output else (issue.title, rationale)
            actions.append(
                _action(
                    issue,
                    ActionType.DRAFT_VENDOR_ESCALATION_EMAIL,
                    rationale,
                    approval=True,
                    subject=subject,
                    body=body,
                )
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
                    )
                )
        elif issue.issue_type == IssueType.BILLING_ANOMALY:
            actions.append(_action(issue, ActionType.CREATE_BILLING_REVIEW_ALERT, rationale))
        elif issue.issue_type == IssueType.DATA_QUALITY:
            actions.append(_action(issue, ActionType.FLAG_DATA_QUALITY_ISSUE, rationale))
    return actions
