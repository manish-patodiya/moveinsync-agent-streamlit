from __future__ import annotations

import hashlib

from core.act.draft_factory import make_email_draft, make_vendor_email_draft
from core.models.action import Action, ActionType
from core.models.benchmark import VendorAttribution, VendorBenchmark
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
    target_vendor_id: str | None = None,
    impact_rank: int | None = None,
) -> Action:
    key = f"{issue.issue_id}|{action_type}|{target_vendor_id or ''}"
    title = f"{action_type.value.replace('_', ' ').title()}: {issue.title}"
    if target_vendor_id:
        title = f"{title} -- {target_vendor_id}"
    return Action(
        action_id=f"action-{hashlib.sha1(key.encode()).hexdigest()[:10]}",
        issue_id=issue.issue_id,
        action_type=action_type,
        priority=issue.severity.value,
        title=title,
        rationale=rationale,
        requires_human_approval=approval,
        target_vendor_id=target_vendor_id,
        impact_rank=impact_rank,
        email_subject=subject,
        email_body=body,
    )


def _vendor_escalation_actions(
    issue: CandidateIssue,
    output: IssueReasoningOutput | None,
    rationale: str,
    attributions: list[VendorAttribution],
    benchmarks: dict[str, VendorBenchmark],
    max_targets: int,
) -> list[Action]:
    """One DRAFT_VENDOR_ESCALATION_EMAIL per top-impact culprit vendor, so the escalation is
    addressed to whoever is actually causing the most damage instead of the issue in the
    abstract. Falls back to a single generic draft when Root Cause found no culprit vendors
    (e.g. the feature found no benchmarkable vendors in scope)."""
    targets = attributions[:max_targets]
    if not targets:
        subject, body = make_email_draft(issue, output) if output else (issue.title, rationale)
        return [
            _action(
                issue,
                ActionType.DRAFT_VENDOR_ESCALATION_EMAIL,
                rationale,
                approval=True,
                subject=subject,
                body=body,
            )
        ]
    actions = []
    for attribution in targets:
        subject, body = make_vendor_email_draft(
            issue, attribution, benchmarks.get(attribution.vendor_id), output
        )
        vendor_rationale = (
            f"{rationale} {attribution.vendor_id} ranked #{attribution.impact_rank} by "
            f"negative impact (score {attribution.impact_score})."
        )
        actions.append(
            _action(
                issue,
                ActionType.DRAFT_VENDOR_ESCALATION_EMAIL,
                vendor_rationale,
                approval=True,
                subject=subject,
                body=body,
                target_vendor_id=attribution.vendor_id,
                impact_rank=attribution.impact_rank,
            )
        )
    return actions


def create_actions(
    issues: list[CandidateIssue],
    reasoning: dict[str, IssueReasoningOutput],
    automation: dict,
    *,
    root_cause_outputs: dict[str, list[VendorAttribution]] | None = None,
    benchmark_outputs: dict[str, list[VendorBenchmark]] | None = None,
    max_escalation_targets: int = 3,
) -> list[Action]:
    """Apply fixed policy. No external communication is performed here."""
    root_cause_outputs = root_cause_outputs or {}
    benchmark_outputs = benchmark_outputs or {}
    actions: list[Action] = []
    for issue in issues:
        output = reasoning.get(issue.issue_id)
        rationale = output.manager_summary if output else issue.title
        attributions = root_cause_outputs.get(issue.issue_id, [])
        benchmarks_by_vendor = {b.vendor_id: b for b in benchmark_outputs.get(issue.issue_id, [])}
        if automation["auto_create_manager_alerts"] and issue.severity in {
            Severity.HIGH,
            Severity.CRITICAL,
        }:
            actions.append(_action(issue, ActionType.CREATE_MANAGER_ALERT, rationale))
        if issue.issue_type == IssueType.VENDOR_OTA_BREACH and automation["auto_create_email_drafts"]:
            actions.extend(
                _vendor_escalation_actions(
                    issue, output, rationale, attributions, benchmarks_by_vendor, max_escalation_targets
                )
            )
        elif issue.issue_type == IssueType.SAFETY_ESCALATION:
            actions.append(_action(issue, ActionType.CREATE_SAFETY_ESCALATION, rationale))
            if automation["auto_create_email_drafts"]:
                actions.extend(
                    _vendor_escalation_actions(
                        issue, output, rationale, attributions, benchmarks_by_vendor, max_escalation_targets
                    )
                )
        elif issue.issue_type == IssueType.BILLING_ANOMALY:
            actions.append(_action(issue, ActionType.CREATE_BILLING_REVIEW_ALERT, rationale))
        elif issue.issue_type == IssueType.DATA_QUALITY:
            actions.append(_action(issue, ActionType.FLAG_DATA_QUALITY_ISSUE, rationale))
    return actions
