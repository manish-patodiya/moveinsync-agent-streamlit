from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import datetime, timezone

from core.act.action_policy import create_actions
from core.bootstrap.app_context import AppContext
from core.models.action import Action, ActionType
from core.models.issue import Severity
from core.models.workflow_state import TraceEvent
from core.reason.reasoning_service import reason_about_issue
from core.sense.anomaly_detector import detect_anomalies
from core.sense.kpis import calculate_kpis
from core.workflow.state import WorkflowState

Progress = Callable[[str], None] | None


def _report(progress: Progress, message: str) -> None:
    if progress is not None:
        progress(message)


def _trace(
    state: WorkflowState,
    node: str,
    status: str,
    description: str,
    started: float | None = None,
) -> list[TraceEvent]:
    return [
        *state.get("workflow_trace", []),
        TraceEvent(
            node=node,
            status=status,
            description=description,
            duration_seconds=None if started is None else round(time.perf_counter() - started, 2),
        ),
    ]


def sense_node(context: AppContext, progress: Progress = None):
    def run(state: WorkflowState) -> dict:
        started = time.perf_counter()
        _report(progress, "Sense — running DuckDB analytics")
        try:
            target = float(context.sla["service_levels"]["trip_end_ota"]["target_pct"])
            kpis = calculate_kpis(context.conn, state["filters"], target)
            issues = detect_anomalies(
                context.conn, state["filters"], context.sla, context.thresholds
            )
            maximum = int(context.settings["app"]["max_issues_for_reasoning"])
            prioritized = [
                issue for issue in issues if issue.severity in {Severity.HIGH, Severity.CRITICAL}
            ][:maximum]
            return {
                "kpi_summary": kpis,
                "candidate_issues": issues,
                "prioritized_issues": prioritized,
                "workflow_trace": _trace(
                    state,
                    "sense_node",
                    "SUCCESS",
                    f"Ran DuckDB analytics over {kpis['trip_count']:,} trips and detected "
                    f"{len(issues)} issue(s); {len(prioritized)} escalated to Reason.",
                    started,
                ),
            }
        except Exception as exc:
            return {
                "errors": [*state.get("errors", []), f"Sense failed: {exc}"],
                "workflow_trace": _trace(state, "sense_node", "FAILED", str(exc), started),
            }

    return run


def reason_node(context: AppContext, progress: Progress = None):
    def run(state: WorkflowState) -> dict:
        started = time.perf_counter()
        caveats = list(context.data_health.errors)
        if context.data_health.invalid_severity_count:
            caveats.append(
                f"{context.data_health.invalid_severity_count} invalid alert severities were normalized."
            )
        issues = state["prioritized_issues"]
        outputs = {}
        for position, issue in enumerate(issues, start=1):
            _report(progress, f"Reason — issue {position} of {len(issues)}: {issue.title}")
            outputs[issue.issue_id] = reason_about_issue(
                issue,
                caveats,
                llm_enabled=bool(context.settings["llm"]["enabled"]),
            )
        by_llm = sum(result.source == "llm" for result in outputs.values())
        detail = next(
            (r.source_detail for r in outputs.values() if r.source == "llm"),
            "deterministic template",
        )
        return {
            "reasoning_outputs": outputs,
            "workflow_trace": _trace(
                state,
                "reason_node",
                "SUCCESS",
                f"Explained {len(outputs)} issue(s): {by_llm} via {detail}, "
                f"{len(outputs) - by_llm} via deterministic template.",
                started,
            ),
        }

    return run


def act_node(context: AppContext, progress: Progress = None):
    def run(state: WorkflowState) -> dict:
        started = time.perf_counter()
        _report(progress, "Act — applying the deterministic action policy")
        actions = create_actions(
            state["prioritized_issues"],
            {key: result.output for key, result in state["reasoning_outputs"].items()},
            context.settings["automation"],
        )
        approvals = sum(action.requires_human_approval for action in actions)
        return {
            "actions": actions,
            "workflow_trace": _trace(
                state,
                "act_node",
                "SUCCESS",
                f"Applied action policy: {len(actions)} action(s) created, "
                f"{approvals} awaiting human approval.",
                started,
            ),
        }

    return run


def daily_brief_node(state: WorkflowState) -> dict:
    started = time.perf_counter()
    issues = state.get("candidate_issues", [])

    kpis = state.get("kpi_summary", {})
    high = sum(issue.severity in {Severity.HIGH, Severity.CRITICAL} for issue in issues)
    if state.get("errors"):
        brief = "Mobility Pulse did not run because data health or analytics failed: " + "; ".join(
            state["errors"]
        )
    else:
        brief = (
            f"Mobility Pulse: {kpis.get('trip_count', 0):,} trips reviewed; "
            f"trip-end OTA {kpis.get('ota_pct', 'N/A')}% against "
            f"{kpis.get('sla_target_pct', 'N/A')}% SLA. "
            f"{len(issues)} issue(s) detected, including {high} high/critical."
        )
    filters = state["filters"]
    key = f"{filters.start_date}|{filters.end_date}|{filters.business_unit}|{filters.office}"
    brief_action = Action(
        action_id=f"brief-{hashlib.sha1(key.encode()).hexdigest()[:10]}",
        issue_id="daily-brief",
        action_type=ActionType.GENERATE_DAILY_MOBILITY_BRIEF,
        priority="LOW",
        title="Daily Mobility Brief",
        rationale=brief,
        requires_human_approval=False,
        created_at=datetime.now(timezone.utc),
    )
    return {
        "daily_brief": brief,
        "actions": [*state.get("actions", []), brief_action],
        "workflow_trace": _trace(
            state, "daily_brief_node", "SUCCESS", "Generated the manager brief.", started
        ),
    }
