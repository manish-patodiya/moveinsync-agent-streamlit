from __future__ import annotations

from typing import Any, TypedDict

from core.models.action import Action
from core.models.issue import CandidateIssue
from core.models.output_schemas import ReasoningResult
from core.models.workflow_state import AnalysisFilters, TraceEvent


class WorkflowState(TypedDict):
    filters: AnalysisFilters
    data_health: dict[str, Any]
    kpi_summary: dict[str, Any]
    candidate_issues: list[CandidateIssue]
    prioritized_issues: list[CandidateIssue]
    reasoning_outputs: dict[str, ReasoningResult]
    actions: list[Action]
    daily_brief: str
    workflow_trace: list[TraceEvent]
    errors: list[str]
