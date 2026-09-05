from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from core.models.action import Action


class ChatTool(StrEnum):
    TRIP_LOOKUP = "TRIP_LOOKUP"
    TRIP_SAFETY = "TRIP_SAFETY"
    ALERTS_REPORT = "ALERTS_REPORT"
    OTA_REPORT = "OTA_REPORT"
    SLA_BREACH_REPORT = "SLA_BREACH_REPORT"
    HELP = "HELP"


class ChatRoute(BaseModel):
    tool: ChatTool
    trip_id: str | None = None
    period_days: int = Field(default=7, ge=1, le=92)
    business_unit: str | None = None
    office: str | None = None
    group_by: str = "vendor"
    explanation: str = ""


class ChatToolResult(BaseModel):
    answer: str
    tool: ChatTool
    rows: list[dict[str, Any]] = Field(default_factory=list)
    report_name: str | None = None
    proposed_action: Action | None = None
    route_source: str = "template"
    route_detail: str = "deterministic router"
    interpretation: str = ""
    grounded_summary: str = ""
    row_order: str = ""
    answer_source: str = "template"
    answer_detail: str = "deterministic summary"
