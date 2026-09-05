from __future__ import annotations

import json
import re
import time

from core.models.chat import ChatRoute, ChatTool, ChatToolResult
from core.models.issue import CandidateIssue, IssueType
from core.models.live_alert import AlertDraftResult, AlertEscalationDraft, LiveAlert
from core.models.output_schemas import IssueReasoningOutput, ReasoningResult
from core.reason.llm_provider import describe_llm, get_llm
from core.reason.prompts import (
    ALERT_ESCALATION_PROMPT,
    CHAT_ANSWER_PROMPT,
    CHAT_ROUTER_PROMPT,
    REASONING_PROMPT,
)


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


def draft_alert_escalation(alert: LiveAlert) -> AlertDraftResult:
    context = {
        "trip_id": alert.trip_id,
        "business_unit": alert.business_unit,
        "office": alert.office,
        "vendor": alert.vendor,
        "event_type": alert.event_type,
        "severity": alert.severity,
        "priority": alert.priority,
        "state": alert.state,
        "start_time": alert.start_time.isoformat(),
        "trip_direction": alert.trip_direction,
        "trip_delay_minutes": alert.delay_minutes,
        "driver_non_compliant": alert.driver_non_compliant,
        "cab_non_compliant": alert.cab_non_compliant,
    }
    fallback = AlertEscalationDraft(
        subject=f"Immediate review required: {alert.event_type} on trip {alert.trip_id}",
        body=(
            f"Hello,\n\nA {alert.severity} {alert.event_type} alert is open for trip "
            f"{alert.trip_id} ({alert.business_unit}, {alert.office or 'office unavailable'}). "
            "Please acknowledge the event, investigate it immediately, and provide closure "
            "with the actions taken.\n\nRegards,\nTransport Operations"
        ),
    )
    config = describe_llm()
    try:
        llm = get_llm()
        if llm is None:
            raise RuntimeError(f"{config['provider']} is unavailable")
        structured = llm.with_structured_output(AlertEscalationDraft)  # type: ignore[attr-defined]
        messages = ALERT_ESCALATION_PROMPT.format_messages(context=json.dumps(context, default=str))
        draft = AlertEscalationDraft.model_validate(structured.invoke(messages))
        return AlertDraftResult(
            draft=draft,
            source="llm",
            source_detail=f"{config['provider']}:{config['model']}",
        )
    except Exception as exc:
        return AlertDraftResult(
            draft=fallback,
            source="template",
            source_detail="deterministic template",
            fallback_reason=f"{type(exc).__name__}: {exc}",
        )


SAFETY_WORDS = ("safety", "alert", "panic", "sos", "overspeed", "incident", "sev-1", "sev1")
SLA_WORDS = ("sla", "breach", "below target", "below the target")
OTA_WORDS = ("ota", "on time", "on-time", "punctual", "late", "delay")
GROUP_WORDS = (("business unit", "business_unit"), ("office", "office"), ("shift", "shift"), ("vendor", "vendor"))
TRIP_ID_PATTERN = re.compile(r"\b(?:trips?\s*(?:id)?\s*[:#-]?\s*)?(\d[\d,]{5,})\b")
PERIOD_PATTERN = re.compile(r"(?:last|past|previous|latest)\s+(\d{1,3})?\s*(day|week|month)s?")
UNIT_DAYS = {"day": 1, "week": 7, "month": 30}
GROUP_CHOICES = {"vendor", "office", "shift", "business_unit", "overall"}
TRIP_TOOLS = {"TRIP_LOOKUP", "TRIP_SAFETY"}
MAX_PERIOD_DAYS = 92
ECHOED_LINES = ("manager's question:", "question:", "recent conversation:")
ECHOED_LABELS = ("answer:", "response:", "verified query result:")


def _strip_prompt_echo(text: str) -> str:
    """Small models restate the prompt scaffolding; keep the prose, drop the labels."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(ECHOED_LINES):
            continue
        for label in ECHOED_LABELS:
            if stripped.lower().startswith(label):
                stripped = stripped[len(label) :].strip()
                break
        kept.append(stripped)
    return "\n".join(kept).strip()


def extract_period_days(question: str) -> int | None:
    """Return an explicitly requested period, or None when the question sets none."""
    match = PERIOD_PATTERN.search(question.lower())
    if not match:
        return None
    count = int(match.group(1)) if match.group(1) else 1
    return max(1, min(count * UNIT_DAYS[match.group(2)], MAX_PERIOD_DAYS))


def extract_trip_id(question: str) -> str | None:
    match = TRIP_ID_PATTERN.search(question)
    return match.group(1).replace(",", "") if match else None


def _deterministic_route(question: str) -> tuple[ChatRoute, bool]:
    """Route on explicit signals. The flag reports whether the match is trustworthy."""
    text = question.lower()
    days = extract_period_days(question)
    period = {"period_days": days} if days else {}
    if trip_id := extract_trip_id(question):
        safety = any(word in text for word in SAFETY_WORDS)
        return (
            ChatRoute(
                tool=ChatTool.TRIP_SAFETY if safety else ChatTool.TRIP_LOOKUP,
                trip_id=trip_id,
                explanation=(
                    f"Trip {trip_id} named explicitly; "
                    + ("the question asks about safety." if safety else "reporting trip details.")
                ),
            ),
            True,
        )
    if any(word in text for word in SLA_WORDS):
        return ChatRoute(tool=ChatTool.SLA_BREACH_REPORT, explanation="SLA breach wording detected.", **period), True
    if any(word in text for word in SAFETY_WORDS):
        return ChatRoute(tool=ChatTool.ALERTS_REPORT, explanation="Safety or alert wording detected.", **period), True
    if any(word in text for word in OTA_WORDS):
        group = next((value for word, value in GROUP_WORDS if word in text), "vendor")
        return (
            ChatRoute(
                tool=ChatTool.OTA_REPORT,
                group_by=group,
                explanation=f"Punctuality wording detected; grouping by {group}.",
                **period,
            ),
            True,
        )
    return ChatRoute(tool=ChatTool.HELP, explanation="No explicit report intent detected.", **period), False


def _continuity_route(question: str, previous: dict | None) -> ChatRoute | None:
    """Keep an unclassifiable follow-up on the trip the manager was already discussing."""
    if not previous or previous.get("tool") not in TRIP_TOOLS or not previous.get("trip_id"):
        return None
    if extract_period_days(question):
        return None
    text = question.lower()
    safety = any(word in text for word in SAFETY_WORDS) or "acknowledg" in text
    trip_id = str(previous["trip_id"])
    return ChatRoute(
        tool=ChatTool.TRIP_SAFETY if safety else ChatTool.TRIP_LOOKUP,
        trip_id=trip_id,
        explanation=f"Follow-up about trip {trip_id} from the previous question.",
    )


def route_chat_question(
    question: str,
    history: list[dict],
    previous: dict | None = None,
) -> tuple[ChatRoute, str, str]:
    """Explicit wording wins; the LLM only resolves questions the rules cannot classify."""
    route, confident = _deterministic_route(question)
    if confident:
        return route, "rules", "deterministic router"
    if followup := _continuity_route(question, previous):
        return followup, "rules", "conversation follow-up"
    config = describe_llm()
    try:
        llm = get_llm()
        if llm is None:
            raise RuntimeError(f"{config['provider']} is unavailable")
        structured = llm.with_structured_output(ChatRoute)  # type: ignore[attr-defined]
        recent = "\n".join(
            f"{message.get('role', 'user')}: {message.get('content', '')}"
            for message in history[-6:]
        )
        resolved = ChatRoute.model_validate(
            structured.invoke(
                CHAT_ROUTER_PROMPT.format_messages(
                    history=recent or "No prior messages.",
                    question=question,
                )
            )
        )
        if resolved.group_by not in GROUP_CHOICES:
            resolved.group_by = "vendor"
        if resolved.tool in {ChatTool.TRIP_LOOKUP, ChatTool.TRIP_SAFETY} and not resolved.trip_id:
            raise ValueError(f"{resolved.tool} requires a trip_id")
        if resolved.trip_id:
            resolved.trip_id = resolved.trip_id.replace(",", "").strip()
        # Explicit numeric filters are deterministic facts; never let model defaults override them.
        if days := extract_period_days(question):
            resolved.period_days = days
        for field in ("business_unit", "office"):
            if not getattr(resolved, field):
                setattr(resolved, field, None)
        return resolved, "llm", f"{config['provider']}:{config['model']}"
    except Exception:
        return route, "template", "deterministic router"


def compose_chat_answer(
    question: str,
    result: ChatToolResult,
    history: list[dict],
) -> tuple[str, str, str]:
    """Turn the tool's rows into a direct answer to the question actually asked."""
    config = describe_llm()
    try:
        llm = get_llm()
        if llm is None:
            raise RuntimeError(f"{config['provider']} is unavailable")
        recent = "\n".join(
            f"{message.get('role', 'user')}: {message.get('content', '')}"
            for message in history[-6:]
        )
        facts = {
            "tool": str(result.tool),
            "verified_summary": result.answer,
            "row_order": result.row_order or "no meaningful order",
            "total_rows": len(result.rows),
            "rows_shown": result.rows[:12],
        }
        response = llm.invoke(
            CHAT_ANSWER_PROMPT.format_messages(
                history=recent or "No prior messages.",
                question=question,
                facts=json.dumps(facts, default=str, indent=2, ensure_ascii=False),
            )
        )
        answer = _strip_prompt_echo(str(getattr(response, "content", "")))
        if not answer:
            raise ValueError("empty completion")
        return answer, "llm", f"{config['provider']}:{config['model']}"
    except Exception:
        return result.answer, "template", "deterministic summary"
