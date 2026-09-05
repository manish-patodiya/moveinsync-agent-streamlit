from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class ActionType(StrEnum):
    CREATE_MANAGER_ALERT = "CREATE_MANAGER_ALERT"
    DRAFT_VENDOR_ESCALATION_EMAIL = "DRAFT_VENDOR_ESCALATION_EMAIL"
    CREATE_SAFETY_ESCALATION = "CREATE_SAFETY_ESCALATION"
    CREATE_BILLING_REVIEW_ALERT = "CREATE_BILLING_REVIEW_ALERT"
    FLAG_DATA_QUALITY_ISSUE = "FLAG_DATA_QUALITY_ISSUE"
    GENERATE_DAILY_MOBILITY_BRIEF = "GENERATE_DAILY_MOBILITY_BRIEF"
    DRAFT_ALERT_ESCALATION_EMAIL = "DRAFT_ALERT_ESCALATION_EMAIL"
    REQUEST_DRIVER_CALL = "REQUEST_DRIVER_CALL"
    REQUEST_EMPLOYEE_CALL = "REQUEST_EMPLOYEE_CALL"


class ActionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVIEWED = "REVIEWED"
    SIMULATED_SENT = "SIMULATED_SENT"


class Action(BaseModel):
    action_id: str
    issue_id: str
    action_type: ActionType
    priority: str
    title: str
    rationale: str
    owner_role: str = "TRANSPORT_MANAGER"
    requires_human_approval: bool
    status: ActionStatus = ActionStatus.PROPOSED
    email_subject: str | None = None
    email_body: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuditEvent(BaseModel):
    action_id: str
    previous_status: ActionStatus
    new_status: ActionStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
