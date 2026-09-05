from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class AlertPriority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class LiveAlert(BaseModel):
    event_id: str
    trip_id: str
    business_unit: str
    office: str | None = None
    vendor: str | None = None
    event_type: str
    severity: str = "UNKNOWN"
    state: str = "NEW"
    source: str | None = None
    start_time: datetime
    acknowledged_at: datetime | None = None
    priority: AlertPriority
    priority_score: int
    allowed_actions: list[str]
    has_employee_contact: bool = False
    synthetic: bool = False
    trip_direction: str | None = None
    delay_minutes: float | None = None
    driver_non_compliant: bool = False
    cab_non_compliant: bool = False


class ContactDetails(BaseModel):
    role: str
    display_name: str
    masked_phone: str
    synthetic: bool = True


class AlertEscalationDraft(BaseModel):
    subject: str
    body: str


class AlertDraftResult(BaseModel):
    draft: AlertEscalationDraft
    source: str
    source_detail: str
    fallback_reason: str | None = None


class AlertActivity(BaseModel):
    event_id: str
    action: str
    detail: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
