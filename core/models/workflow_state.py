from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from core.models.action import Action
from core.models.benchmark import VendorAttribution, VendorBenchmark
from core.models.issue import CandidateIssue
from core.models.output_schemas import ReasoningResult


class AnalysisFilters(BaseModel):
    business_unit: str | None = None
    office: str | None = None
    start_date: date
    end_date: date


class TraceEvent(BaseModel):
    node: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str
    description: str
    duration_seconds: float | None = None


class WorkflowStateModel(BaseModel):
    filters: AnalysisFilters
    data_health: dict[str, Any] = Field(default_factory=dict)
    kpi_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_issues: list[CandidateIssue] = Field(default_factory=list)
    prioritized_issues: list[CandidateIssue] = Field(default_factory=list)
    sense_summary: str = ""
    benchmark_outputs: dict[str, list[VendorBenchmark]] = Field(default_factory=dict)
    root_cause_outputs: dict[str, list[VendorAttribution]] = Field(default_factory=dict)
    reasoning_outputs: dict[str, ReasoningResult] = Field(default_factory=dict)
    actions: list[Action] = Field(default_factory=list)
    daily_brief: str = ""
    workflow_trace: list[TraceEvent] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
