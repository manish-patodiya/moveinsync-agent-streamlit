from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from core.models.action import Action


class ChatTool(StrEnum):
    OPERATIONAL_OVERVIEW = "OPERATIONAL_OVERVIEW"
    TRIP_LOOKUP = "TRIP_LOOKUP"
    TRIP_SAFETY = "TRIP_SAFETY"
    ALERTS_REPORT = "ALERTS_REPORT"
    OTA_REPORT = "OTA_REPORT"
    SLA_BREACH_REPORT = "SLA_BREACH_REPORT"
    ENTITY_COMPARISON = "ENTITY_COMPARISON"
    DELAY_REPORT = "DELAY_REPORT"
    NO_SHOW_REPORT = "NO_SHOW_REPORT"
    FEEDBACK_REPORT = "FEEDBACK_REPORT"
    UTILIZATION_REPORT = "UTILIZATION_REPORT"
    BILLING_REPORT = "BILLING_REPORT"
    IMPACTED_TRIPS = "IMPACTED_TRIPS"
    SHIFT_READINESS_REPORT = "SHIFT_READINESS_REPORT"
    HELP = "HELP"


class ChatRoute(BaseModel):
    tool: ChatTool
    trip_id: str | None = None
    period_days: int = Field(default=7, ge=1, le=92)
    business_unit: str | None = None
    office: str | None = None
    vendor: str | None = None
    shift: str | None = None
    group_by: str = "vendor"
    manager_role: str = "TRANSPORT_MANAGER"
    persona: str = "TRANSPORT_MANAGER"
    action_intent: str | None = None
    explanation: str = ""


class ChatPlanStep(BaseModel):
    step: int
    tool: ChatTool
    purpose: str
    route: ChatRoute


class ChatPlan(BaseModel):
    goal: str
    steps: list[ChatPlanStep]
    requires_approval: bool = False
    proposed_action_intent: str | None = None


class ChatToolResult(BaseModel):
    answer: str
    tool: ChatTool
    rows: list[dict[str, Any]] = Field(default_factory=list)
    report_name: str | None = None
    proposed_action: Action | None = None
    proposed_actions: list[Action] = Field(default_factory=list)
    route_source: str = "template"
    route_detail: str = "deterministic router"
    interpretation: str = ""
    grounded_summary: str = ""
    row_order: str = ""
    answer_source: str = "template"
    answer_detail: str = "deterministic summary"
    plan: ChatPlan | None = None
    evidence_summaries: list[str] = Field(default_factory=list)
