from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from core.bootstrap.app_context import AppContext
from core.models.issue import Severity
from core.models.workflow_state import AnalysisFilters, TraceEvent
from core.workflow.nodes import (
    benchmark_node,
    daily_brief_node,
    escalation_advisor_node,
    root_cause_node,
    sensing_node,
)
from core.workflow.state import WorkflowState


def _entry(state: WorkflowState) -> str:
    health = state["data_health"]
    return (
        "sensing_node"
        if health.get("grain_invariant_ok") and not health.get("errors")
        else "daily_brief_node"
    )


def _after_sense(state: WorkflowState) -> str:
    if state.get("errors"):
        return "daily_brief_node"
    has_high = any(
        issue.severity in {Severity.HIGH, Severity.CRITICAL}
        for issue in state.get("candidate_issues", [])
    )
    return "benchmark_node" if has_high else "daily_brief_node"


def build_graph(context: AppContext, progress: Callable[[str], None] | None = None):
    graph = StateGraph(WorkflowState)
    graph.add_node("sensing_node", sensing_node(context, progress))
    graph.add_node("benchmark_node", benchmark_node(context, progress))
    graph.add_node("root_cause_node", root_cause_node(context, progress))
    graph.add_node("escalation_advisor_node", escalation_advisor_node(context, progress))
    graph.add_node("daily_brief_node", daily_brief_node)
    graph.add_conditional_edges(
        START, _entry, {"sensing_node": "sensing_node", "daily_brief_node": "daily_brief_node"}
    )
    graph.add_conditional_edges(
        "sensing_node",
        _after_sense,
        {"benchmark_node": "benchmark_node", "daily_brief_node": "daily_brief_node"},
    )
    graph.add_edge("benchmark_node", "root_cause_node")
    graph.add_edge("root_cause_node", "escalation_advisor_node")
    graph.add_edge("escalation_advisor_node", "daily_brief_node")
    graph.add_edge("daily_brief_node", END)
    return graph.compile()


def run_graph(
    context: AppContext,
    filters: AnalysisFilters,
    progress: Callable[[str], None] | None = None,
) -> WorkflowState:
    valid = context.data_health.grain_invariant_ok and not context.data_health.errors
    initial: WorkflowState = {
        "filters": filters,
        "data_health": context.data_health.to_dict(),
        "kpi_summary": {},
        "candidate_issues": [],
        "prioritized_issues": [],
        "sense_summary": "",
        "benchmark_outputs": {},
        "root_cause_outputs": {},
        "reasoning_outputs": {},
        "actions": [],
        "daily_brief": "",
        "workflow_trace": [
            TraceEvent(
                node="bootstrap",
                status="SUCCESS" if valid else "FAILED",
                description="Data health validated." if valid else "Data health is invalid; analytics skipped.",
            )
        ],
        "errors": list(context.data_health.errors),
    }
    return build_graph(context, progress).invoke(initial)
