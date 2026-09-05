from __future__ import annotations

import json
import re
import time

from core.models.chat import ChatRoute, ChatTool, ChatToolResult
from core.models.issue import CandidateIssue, IssueType
from core.models.live_alert import AlertDraftResult, AlertEscalationDraft, LiveAlert
from core.models.output_schemas import (
    IssueReasoningOutput,
    PersonaDecision,
    ReasoningResult,
    RoleRecommendation,
)
from core.reason.llm_provider import describe_llm, get_llm
from core.reason.prompts import (
    ALERT_ESCALATION_PROMPT,
    CHAT_ANSWER_PROMPT,
    CHAT_ROUTER_PROMPT,
    PERSONA_ANSWER_INSTRUCTIONS,
    PERSONA_REASONING_INSTRUCTIONS,
    REASONING_PROMPT,
)


def template_reasoning(issue: CandidateIssue) -> IssueReasoningOutput:
    evidence_items = [f"{item.label}: {item.value}" for item in issue.evidence[:6]]
    evidence = "; ".join(evidence_items)
    comparisons = issue.comparisons
    benchmark_parts = []
    if comparisons.get("sla_target_pct") is not None:
        benchmark_parts.append(
            f"current {issue.current_value}% versus SLA {comparisons['sla_target_pct']}%"
        )
    if comparisons.get("prior_period_ota_pct") is not None:
        benchmark_parts.append(
            f"prior-period OTA {comparisons['prior_period_ota_pct']}% "
            f"({comparisons.get('prior_period_delta_pp')}pp change)"
        )
    if comparisons.get("peer_median_ota_pct") is not None:
        benchmark_parts.append(
            f"peer median {comparisons['peer_median_ota_pct']}% "
            f"(rank {comparisons.get('peer_rank')} of {comparisons.get('peer_count')})"
        )
    if comparisons.get("historical_alert_rate") is not None:
        benchmark_parts.append(
            f"alert rate {comparisons.get('alert_rate_multiplier')}× the matching prior period"
        )
    if comparisons.get("peer_median_cost_per_km") is not None:
        benchmark_parts.append(
            f"{comparisons.get('peer_gap_pct')}% above peer median cost/km"
        )
    benchmark = "; ".join(benchmark_parts) or "No reliable comparison was available"
    interpretation = {
        IssueType.VENDOR_OTA_BREACH: (
            f"Punctuality is materially weak: {benchmark}. Delay-reason counts are signals "
            "for investigation, not proven causes."
        ),
        IssueType.SAFETY_ESCALATION: (
            f"Critical safety signals require immediate review. {benchmark}."
        ),
        IssueType.BILLING_ANOMALY: (
            f"This is a billing anomaly requiring validation, not evidence of fraud. {benchmark}."
        ),
        IssueType.DATA_QUALITY: (
            "Data quality limits reliable operational analysis; repair the named source "
            "before using the affected metric for decisions."
        ),
        IssueType.SHIFT_READINESS: (
            f"Shift readiness is incomplete: {benchmark}. Counts are office and shift scoped. "
            "Lateness is late pickup, not late office arrival. Rider labels are masked."
        ),
    }[issue.issue_type]
    role_actions = {
        IssueType.VENDOR_OTA_BREACH: [
            RoleRecommendation(
                role="TRANSPORT_MANAGER",
                action="Request a time-bound corrective plan from the vendor.",
                rationale="The vendor is below one or more supplied punctuality benchmarks.",
                owner="Transport manager",
                expected_outcome="Vendor commits to corrective steps and an OTA recovery target.",
                monitoring_condition="Review OTA after the next matching reporting window.",
            ),
            RoleRecommendation(
                role="SHIFT_MANAGER",
                action="Review the affected shift's late trips and top recorded delay reasons.",
                rationale="Shift-level evidence can separate dispatch, driver and traffic patterns.",
                owner="Shift manager",
                expected_outcome="Each recurring operational reason has an assigned follow-up.",
                monitoring_condition="Check late-trip count on the next three comparable shifts.",
            ),
        ],
        IssueType.SAFETY_ESCALATION: [
            RoleRecommendation(
                role="TRANSPORT_MANAGER",
                action="Escalate open or critical alerts to the vendor safety owner.",
                rationale="Critical and unacknowledged events require accountable closure.",
                owner="Transport manager",
                expected_outcome="Every critical event has an owner, investigation and closure note.",
                monitoring_condition="Monitor until all critical alerts are acknowledged and closed.",
            ),
            RoleRecommendation(
                role="SHIFT_MANAGER",
                action="Acknowledge the alert, contact the permitted party and record the response.",
                rationale="Immediate shift response reduces time-to-assistance.",
                owner="Shift manager",
                expected_outcome="The affected trip is contacted and its current status verified.",
                monitoring_condition="Recheck open alerts at the configured acknowledgement SLA.",
            ),
        ],
        IssueType.BILLING_ANOMALY: [
            RoleRecommendation(
                role="TRANSPORT_MANAGER",
                action="Open a billing review with the vendor and contract owner.",
                rationale="The supplied invoice pattern differs from peer or prior-period cost.",
                owner="Transport manager",
                expected_outcome="Distance, rate card and exception lines are reconciled.",
                monitoring_condition="Do not approve affected lines until the review is closed.",
            ),
            RoleRecommendation(
                role="SHIFT_MANAGER",
                action="Validate trip execution details for the affected trip set.",
                rationale="Operational evidence is needed before finance resolves the discrepancy.",
                owner="Shift manager",
                expected_outcome="Actual distance and trip completion evidence are attached.",
                monitoring_condition="Return validation before the billing review due date.",
            ),
        ],
        IssueType.DATA_QUALITY: [
            RoleRecommendation(
                role="TRANSPORT_MANAGER",
                action="Assign the source-data correction and rerun Mobility Pulse.",
                rationale="The affected analysis is not decision-safe until corrected.",
                owner="Transport manager",
                expected_outcome="The failed quality check passes on rerun.",
                monitoring_condition="Track the named quality metric to its configured threshold.",
            )
        ],
        IssueType.SHIFT_READINESS: [
            RoleRecommendation(
                role="LINE_MANAGER",
                action="Follow up masked unready riders and assign a shift owner.",
                rationale="No-shows and late pickups reduce start-of-shift coverage.",
                owner="Line manager",
                expected_outcome="Each unready rider has a recorded follow-up.",
                monitoring_condition="Recheck the next comparable office and shift.",
            )
        ],
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
        manager_summary=(
            f"{issue.title}: {issue.affected_trip_count} trip(s) affected. {benchmark}."
        ),
        operational_interpretation=interpretation,
        urgency_reason=f"Priority is {issue.severity}; {issue.affected_trip_count} trip(s) are affected.",
        recommended_action_types=issue.allowed_action_types,
        recommended_actions=[recommendation.action for recommendation in role_actions],
        email_subject=email_subject,
        email_body=email_body,
        caveat=f"Data confidence: {issue.data_confidence}.",
        evidence_citations=evidence_items,
        role_recommendations=role_actions,
    )


def _fallback(issue: CandidateIssue, reason: str) -> ReasoningResult:
    return ReasoningResult(
        output=template_reasoning(issue),
        source="template",
        source_detail="deterministic template",
        fallback_reason=reason,
    )


def to_persona_decision(issue: CandidateIssue, result: ReasoningResult, persona: str) -> PersonaDecision:
    output = result.output
    owner = next(
        (item.owner for item in output.role_recommendations if item.role == persona),
        output.role_recommendations[0].owner if output.role_recommendations else persona.replace("_", " ").title(),
    )
    monitor = next(
        (item.monitoring_condition for item in output.role_recommendations if item.role == persona),
        output.role_recommendations[0].monitoring_condition if output.role_recommendations else "Review the next matching window.",
    )
    return PersonaDecision(
        headline=output.manager_summary,
        why_now=output.urgency_reason,
        cited_benchmark_facts=output.evidence_citations,
        operational_impact=output.operational_interpretation,
        recommended_decisions=output.recommended_actions[:4],
        owner=owner,
        due_or_monitor=monitor,
        caveats=[item for item in [output.caveat] if item],
    )


def reason_about_issue(
    issue: CandidateIssue,
    caveats: list[str],
    *,
    llm_enabled: bool = True,
    persona: str = "TRANSPORT_MANAGER",
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
            persona_instructions=PERSONA_REASONING_INSTRUCTIONS.get(
                persona, PERSONA_REASONING_INSTRUCTIONS["TRANSPORT_MANAGER"]
            ),
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
BULLET_MARKER = re.compile(r"^(?:[-*•]|\d+[.)])\s+")
# Managers phrase the same ask many ways ("send a email", "shoot them a mail", "draft this"),
# so match verbs, not fixed phrases. First match wins, so communication verbs precede topics.
ACTION_INTENT_PATTERNS = tuple(
    (intent, re.compile(pattern))
    for intent, pattern in (
        ("draft", r"\b(draft|compose)\b|\b(write|prepare)\b.{0,30}\b(e-?mail|mail|note|letter|message)\b"),
        ("email", r"\be-?mails?\b|\bmails?\b"),
        ("call", r"\b(calls?|phone|dial)\b"),
        ("assign", r"\b(assign|allocate)\b|\b(give|hand)\b.{0,15}\b(to|over)\b"),
        ("acknowledge", r"\backnowledg"),
        ("escalate", r"\bescalat"),
        ("corrective_plan", r"\b(corrective|improvement|recovery)\s+(action\s+)?plan\b"),
        ("investigate", r"\binvestigat|\broot[\s-]?cause\b|\brca\b|\b(look|dig|drill)\s+into\b"),
        ("monitor", r"\b(monitor|track)\b|\bkeep an eye\b|\bwatch\b"),
    )
)


def _strip_prompt_echo(text: str) -> str:
    """Small models restate the prompt scaffolding and bullet everything; keep the prose only."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(ECHOED_LINES):
            continue
        for label in ECHOED_LABELS:
            if stripped.lower().startswith(label):
                stripped = stripped[len(label) :].strip()
                break
        stripped = BULLET_MARKER.sub("", stripped)
        if stripped:
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
    if any(
        word in text
        for word in ("readiness", "who boarded", "late pickup", "who no-show", "roster", "who made it")
    ):
        return ChatRoute(tool=ChatTool.SHIFT_READINESS_REPORT, explanation="Shift readiness requested.", **period), True
    if any(word in text for word in ("overview", "summary of everything", "overall operations", "brief me", "operations brief")):
        return ChatRoute(tool=ChatTool.OPERATIONAL_OVERVIEW, explanation="Operational overview requested.", **period), True
    if ("compare" in text or "comparison" in text or "rank" in text) and any(
        word in text for word, _ in GROUP_WORDS
    ):
        group = next((value for word, value in GROUP_WORDS if word in text), "vendor")
        return ChatRoute(
            tool=ChatTool.ENTITY_COMPARISON,
            group_by=group,
            explanation=f"{group} comparison requested.",
            **period,
        ), True
    if "no show" in text or "no-show" in text or "noshow" in text:
        group = next((value for word, value in GROUP_WORDS if word in text), "shift")
        return ChatRoute(tool=ChatTool.NO_SHOW_REPORT, group_by=group, **period), True
    if "feedback" in text or "rating" in text or "complaint" in text:
        group = next((value for word, value in GROUP_WORDS if word in text), "vendor")
        return ChatRoute(tool=ChatTool.FEEDBACK_REPORT, group_by=group, **period), True
    if "utilization" in text or "occupancy" in text or "capacity" in text:
        group = next((value for word, value in GROUP_WORDS if word in text), "vendor")
        return ChatRoute(tool=ChatTool.UTILIZATION_REPORT, group_by=group, **period), True
    if "billing" in text or "invoice" in text or "cost" in text:
        return ChatRoute(tool=ChatTool.BILLING_REPORT, **period), True
    if "impacted trip" in text or "affected trip" in text or "find trip" in text:
        return ChatRoute(tool=ChatTool.IMPACTED_TRIPS, **period), True
    if "delay" in text or "late" in text or "root cause" in text:
        return ChatRoute(tool=ChatTool.DELAY_REPORT, **period), True
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


def _request_context(question: str, route: ChatRoute, persona: str | None = None) -> ChatRoute:
    text = question.lower()
    if persona:
        route.persona = persona
        route.manager_role = persona
    elif "line manager" in text:
        route.manager_role = "LINE_MANAGER"
        route.persona = "LINE_MANAGER"
    elif "facilities" in text:
        route.manager_role = "FACILITIES_HEAD"
        route.persona = "FACILITIES_HEAD"
    elif "shift manager" in text:
        route.manager_role = "SHIFT_MANAGER"
    else:
        route.manager_role = route.manager_role or "TRANSPORT_MANAGER"
    route.action_intent = next(
        (intent for intent, pattern in ACTION_INTENT_PATTERNS if pattern.search(text)), None
    )
    return route


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


def _clamp_persona_tool(route: ChatRoute, persona: str | None) -> ChatRoute:
    if not persona:
        return route
    from core.models.persona import Persona, allowed_tools

    allowed = allowed_tools(Persona(persona))
    if route.tool in allowed or route.tool == ChatTool.HELP:
        return route
    fallback = next(iter(allowed))
    route.tool = fallback
    route.explanation = f"{persona} cannot use the requested tool; using {fallback}."
    return route


def route_chat_question(
    question: str,
    history: list[dict],
    previous: dict | None = None,
    persona: str | None = None,
) -> tuple[ChatRoute, str, str]:
    """Explicit wording wins; the LLM only resolves questions the rules cannot classify."""
    route, confident = _deterministic_route(question)
    if confident:
        return _clamp_persona_tool(_request_context(question, route, persona), persona), "rules", "deterministic router"
    request = _request_context(question, route, persona)
    if request.action_intent and previous and previous.get("tool") != ChatTool.HELP:
        inherited = ChatRoute.model_validate(previous)
        inherited.action_intent = request.action_intent
        inherited.manager_role = request.manager_role
        inherited.persona = request.persona
        inherited.explanation = (
            f"Action follow-up using the previous {inherited.tool} evidence."
        )
        return _clamp_persona_tool(inherited, persona), "rules", "action follow-up"
    if followup := _continuity_route(question, previous):
        return _clamp_persona_tool(_request_context(question, followup, persona), persona), "rules", "conversation follow-up"
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
        return _clamp_persona_tool(_request_context(question, resolved, persona), persona), "llm", f"{config['provider']}:{config['model']}"
    except Exception:
        return _clamp_persona_tool(_request_context(question, route, persona), persona), "template", "deterministic router"


def compose_chat_answer(
    question: str,
    result: ChatToolResult,
    persona: str | None = None,
) -> tuple[str, str, str]:
    """Turn the tool's rows into a direct answer to the question actually asked."""
    if result.proposed_action and result.plan and result.plan.proposed_action_intent:
        # The draft already exists and is approval-gated. Letting a model narrate it invites
        # invented recipients, deadlines and "sent" claims, so answer from the action itself.
        return (
            f"{result.grounded_summary or result.answer} I have prepared "
            f"**{result.proposed_action.title}** for your approval, quoting exactly that "
            "evidence. Nothing is emailed, called or assigned until you approve and simulate "
            "it here.",
            "template",
            "approval-gated draft summary",
        )
    config = describe_llm()
    try:
        llm = get_llm()
        if llm is None:
            raise RuntimeError(f"{config['provider']} is unavailable")
        # A long payload invites cherry-picking: the summary is the answer, the rows are support.
        facts = {
            "tool": str(result.tool),
            "scope": result.interpretation or "the default reporting window",
            "verified_summary": result.grounded_summary or result.answer,
            "row_order": result.row_order or "no meaningful order",
            "total_rows": len(result.rows),
            "rows_shown": result.rows[:6],
        }
        if len(result.evidence_summaries) > 1:
            facts["evidence_summaries"] = result.evidence_summaries
        response = llm.invoke(
            CHAT_ANSWER_PROMPT.format_messages(
                persona_instructions=PERSONA_ANSWER_INSTRUCTIONS.get(
                    persona or "", PERSONA_ANSWER_INSTRUCTIONS["TRANSPORT_MANAGER"]
                ),
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
