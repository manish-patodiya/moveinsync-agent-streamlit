from __future__ import annotations

import json
import time

from core.models.issue import CandidateIssue, IssueType
from core.models.output_schemas import IssueReasoningOutput, ReasoningResult
from core.reason.llm_provider import describe_llm, get_llm
from core.reason.prompts import REASONING_PROMPT


def template_reasoning(issue: CandidateIssue) -> IssueReasoningOutput:
    evidence = "; ".join(f"{item.label}: {item.value}" for item in issue.evidence[:4])
    interpretation = {
        IssueType.VENDOR_OTA_BREACH: "Punctuality is below the configured operating benchmark and needs vendor follow-up.",
        IssueType.SAFETY_ESCALATION: "Critical safety signals require immediate transport-manager review.",
        IssueType.BILLING_ANOMALY: "The billing pattern is anomalous and requires validation; it is not evidence of fraud.",
        IssueType.DATA_QUALITY: "Data quality limits reliable operational analysis.",
    }[issue.issue_type]
    email_subject = None
    email_body = None
    if "DRAFT_VENDOR_ESCALATION_EMAIL" in issue.allowed_action_types:
        email_subject = f"Action required: {issue.title}"
        email_body = (
            f"Hello,\n\nPlease review the following transport operations issue: {issue.title}.\n"
            f"Evidence: {evidence}.\n\nPlease confirm findings and corrective actions.\n"
        )
    return IssueReasoningOutput(
        manager_summary=f"{issue.title}. {evidence}.",
        operational_interpretation=interpretation,
        urgency_reason=f"Priority is {issue.severity}; {issue.affected_trip_count} trip(s) are affected.",
        recommended_action_types=issue.allowed_action_types,
        recommended_actions=["Review the evidence and assign an owner.", "Track closure against the relevant SLA."],
        email_subject=email_subject,
        email_body=email_body,
        caveat=f"Data confidence: {issue.data_confidence}.",
    )


def _fallback(issue: CandidateIssue, reason: str) -> ReasoningResult:
    return ReasoningResult(
        output=template_reasoning(issue),
        source="template",
        source_detail="deterministic template",
        fallback_reason=reason,
    )


def reason_about_issue(
    issue: CandidateIssue,
    caveats: list[str],
    *,
    llm_enabled: bool = True,
) -> ReasoningResult:
    if not llm_enabled:
        return _fallback(issue, "LLM disabled in settings.yaml")
    config = describe_llm()
    try:
        llm = get_llm()
    except Exception as exc:
        return _fallback(issue, f"provider init failed: {exc}")
    if llm is None:
        return _fallback(issue, f"no usable {config['provider']} configuration")
    started = time.perf_counter()
    try:
        structured = llm.with_structured_output(IssueReasoningOutput)  # type: ignore[attr-defined]
        messages = REASONING_PROMPT.format_messages(
            issue=json.dumps(issue.model_dump(mode="json"), indent=2),
            allowed_action_types=", ".join(issue.allowed_action_types),
            caveats="; ".join(caveats) or "None reported",
        )
        validated = IssueReasoningOutput.model_validate(structured.invoke(messages))
    except Exception as exc:
        return _fallback(issue, f"{type(exc).__name__}: {exc}")
    allowed = set(issue.allowed_action_types)
    permitted = [item for item in validated.recommended_action_types if item in allowed]
    # A model that ignores the allowed list must not silently produce an empty policy input.
    validated.recommended_action_types = permitted or issue.allowed_action_types
    if not validated.recommended_actions:
        validated.recommended_actions = ["Review the evidence and assign an owner."]
    return ReasoningResult(
        output=validated,
        source="llm",
        source_detail=f"{config['provider']}:{config['model']}",
        latency_seconds=round(time.perf_counter() - started, 2),
    )
