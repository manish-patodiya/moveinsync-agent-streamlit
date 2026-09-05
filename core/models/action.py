from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class ManagerRole(StrEnum):
    TRANSPORT_MANAGER = "TRANSPORT_MANAGER"
    SHIFT_MANAGER = "SHIFT_MANAGER"
    FACILITIES_HEAD = "FACILITIES_HEAD"
    LINE_MANAGER = "LINE_MANAGER"


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
    REQUEST_VENDOR_CALL = "REQUEST_VENDOR_CALL"
    ASSIGN_OWNER = "ASSIGN_OWNER"
    INVESTIGATE_SAFETY = "INVESTIGATE_SAFETY"
    INVESTIGATE_DELAY = "INVESTIGATE_DELAY"
    INVESTIGATE_BILLING = "INVESTIGATE_BILLING"
    REQUEST_CORRECTIVE_PLAN = "REQUEST_CORRECTIVE_PLAN"
    MONITOR_KPI = "MONITOR_KPI"
    ACKNOWLEDGE_ALERT = "ACKNOWLEDGE_ALERT"
    SHARE_MOBILITY_BRIEF = "SHARE_MOBILITY_BRIEF"
    REQUEST_RIDER_FOLLOW_UP = "REQUEST_RIDER_FOLLOW_UP"
    ASSIGN_SHIFT_FOLLOW_UP = "ASSIGN_SHIFT_FOLLOW_UP"
    ACKNOWLEDGE_READINESS_RISK = "ACKNOWLEDGE_READINESS_RISK"
    REVIEW_VENDOR_PERFORMANCE = "REVIEW_VENDOR_PERFORMANCE"
    RECORD_SLA_RECOVERY = "RECORD_SLA_RECOVERY"


class ActionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVIEWED = "REVIEWED"
    SIMULATED_SENT = "SIMULATED_SENT"
    SIMULATED_COMPLETED = "SIMULATED_COMPLETED"


class Action(BaseModel):
    action_id: str
    issue_id: str
    action_type: ActionType
    priority: str
    title: str
    rationale: str
    owner_role: ManagerRole = ManagerRole.TRANSPORT_MANAGER
    requires_human_approval: bool
    status: ActionStatus = ActionStatus.PROPOSED
    email_subject: str | None = None
    email_body: str | None = None
    call_script: str | None = None
    target_type: str | None = None
    target_name: str | None = None
    source: str = "WORKFLOW"
    requested_by: ManagerRole = ManagerRole.TRANSPORT_MANAGER
    supporting_evidence: list[str] = Field(default_factory=list)
    expected_outcome: str | None = None
    monitoring_condition: str | None = None
    due_at: datetime | None = None
    execution_note: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuditEvent(BaseModel):
    action_id: str
    previous_status: ActionStatus
    new_status: ActionStatus
    actor_role: ManagerRole = ManagerRole.TRANSPORT_MANAGER
    source: str = "WORKFLOW"
    note: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
