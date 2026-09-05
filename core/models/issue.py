from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from core.models.evidence import Evidence


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IssueType(StrEnum):
    VENDOR_OTA_BREACH = "VENDOR_OTA_BREACH"
    SAFETY_ESCALATION = "SAFETY_ESCALATION"
    BILLING_ANOMALY = "BILLING_ANOMALY"
    DATA_QUALITY = "DATA_QUALITY"


class CandidateIssue(BaseModel):
    issue_id: str
    issue_type: IssueType
    severity: Severity
    title: str
    business_scope: dict[str, str | None]
    current_metric: str
    current_value: float | int | str
    comparisons: dict[str, float | int | str | None] = Field(default_factory=dict)
    affected_trip_count: int
    affected_employee_count: int | None = None
    evidence: list[Evidence]
    data_confidence: str
    allowed_action_types: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict, exclude=True)
