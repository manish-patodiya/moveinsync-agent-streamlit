from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from core.bootstrap.app_context import AppContext
from core.models.issue import Severity
from core.models.workflow_state import AnalysisFilters, TraceEvent
from core.workflow.nodes import act_node, daily_brief_node, reason_node, sense_node
from core.workflow.state import WorkflowState


def _entry(state: WorkflowState) -> str:
    health = state["data_health"]
    return (
        "sense_node"
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
    return "reason_node" if has_high else "daily_brief_node"


def build_graph(context: AppContext, progress: Callable[[str], None] | None = None):
    graph = StateGraph(WorkflowState)
    graph.add_node("sense_node", sense_node(context, progress))
    graph.add_node("reason_node", reason_node(context, progress))
    graph.add_node("act_node", act_node(context, progress))
    graph.add_node("daily_brief_node", daily_brief_node)
    graph.add_conditional_edges(
        START, _entry, {"sense_node": "sense_node", "daily_brief_node": "daily_brief_node"}
    )
    graph.add_conditional_edges(
        "sense_node",
        _after_sense,
        {"reason_node": "reason_node", "daily_brief_node": "daily_brief_node"},
    )
    graph.add_edge("reason_node", "act_node")
    graph.add_edge("act_node", "daily_brief_node")
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
