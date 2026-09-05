from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import datetime, timezone

from core.act.action_policy import create_actions
from core.benchmark.benchmark_agent import benchmark_issue
from core.benchmark.tools import scope_for_issue
from core.bootstrap.app_context import AppContext
from core.models.action import Action, ActionType
from core.models.issue import Severity
from core.models.workflow_state import TraceEvent
from core.reason.reasoning_service import reason_about_issue
from core.root_cause.root_cause_agent import rank_vendors
from core.sense.anomaly_detector import detect_anomalies
from core.sense.evidence_summary import summarize_run
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


def sensing_node(context: AppContext, progress: Progress = None):
    """Deterministic DuckDB analytics. Flags only suspicious signals: marginal MEDIUM-confidence
    findings and dimension-level rollups already explained by an independently flagged vendor
    are suppressed inside `detect_anomalies` rather than surfaced as noise."""

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
                "sense_summary": summarize_run(kpis, issues, prioritized),
                "workflow_trace": _trace(
                    state,
                    "sensing_node",
                    "SUCCESS",
                    f"Ran DuckDB analytics over {kpis['trip_count']:,} trips and flagged "
                    f"{len(issues)} suspicious signal(s); {len(prioritized)} escalated to Benchmark.",
                    started,
                ),
            }
        except Exception as exc:
            return {
                "errors": [*state.get("errors", []), f"Sense failed: {exc}"],
                "workflow_trace": _trace(state, "sensing_node", "FAILED", str(exc), started),
            }

    return run


def benchmark_node(context: AppContext, progress: Progress = None):
    """Agent node: for each escalated issue, enumerates every vendor in its business_unit/office
    cohort and benchmarks each one (current value, peer, multi-period trend) via the tools in
    `core.benchmark.tools` -- never just the single vendor the issue happened to name."""

    def run(state: WorkflowState) -> dict:
        started = time.perf_counter()
        _report(progress, "Benchmark — comparing candidate vendors against peers and trend")
        issues = state["prioritized_issues"]
        outputs = {}
        for position, issue in enumerate(issues, start=1):
            _report(progress, f"Benchmark — issue {position} of {len(issues)}: {issue.title}")
            outputs[issue.issue_id] = benchmark_issue(
                context.conn, issue, state["filters"], context.sla, context.impact_scoring
            )
        total_vendors = sum(len(vendors) for vendors in outputs.values())
        return {
            "benchmark_outputs": outputs,
            "workflow_trace": _trace(
                state,
                "benchmark_node",
                "SUCCESS",
                f"Benchmarked {total_vendors} vendor(s) across {len(outputs)} issue(s).",
                started,
            ),
        }

    return run


def root_cause_node(context: AppContext, progress: Progress = None):
    """Agent node: ranks each issue's benchmarked vendors by negative impact and attaches
    contributing factors (delay reasons, shift concentration, compliance flags, trend) via
    `core.root_cause.tools`. Writes the ranked culprit vendor IDs back onto the issue so the
    UI can show "who" without needing the full ranking payload."""

    def run(state: WorkflowState) -> dict:
        started = time.perf_counter()
        _report(progress, "Root Cause — ranking vendors by negative impact")
        weights = context.impact_scoring["impact_weights"]
        max_targets = int(context.impact_scoring["escalation"]["max_targets_per_issue"])
        outputs = {}
        for issue in state["prioritized_issues"]:
            benchmarks = state["benchmark_outputs"].get(issue.issue_id, [])
            scope = scope_for_issue(issue, state["filters"])
            attributions = rank_vendors(context.conn, issue, benchmarks, scope, weights)
            outputs[issue.issue_id] = attributions
            issue.culprit_vendor_ids = [a.vendor_id for a in attributions[:max_targets]]
        culprits = sum(len(v) for v in outputs.values())
        return {
            "root_cause_outputs": outputs,
            "workflow_trace": _trace(
                state,
                "root_cause_node",
                "SUCCESS",
                f"Ranked {culprits} vendor(s) by negative impact across {len(outputs)} issue(s).",
                started,
            ),
        }

    return run


def escalation_advisor_node(context: AppContext, progress: Progress = None):
    """Agent node: explains each issue in manager language (LLM with deterministic template
    fallback, unchanged from before) and, using Root Cause's ranking, drafts one escalation per
    top-impact culprit vendor via the fixed action policy. No external communication happens
    here -- actions are proposed, not sent."""

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
            _report(progress, f"Escalation Advisor — issue {position} of {len(issues)}: {issue.title}")
            outputs[issue.issue_id] = reason_about_issue(
                issue,
                caveats,
                llm_enabled=bool(context.settings["llm"]["enabled"]),
            )
        by_llm = sum(result.source == "llm" for result in outputs.values())
        llm_detail = next((r.source_detail for r in outputs.values() if r.source == "llm"), None)
        explained_bits = []
        if by_llm:
            explained_bits.append(f"{by_llm} via {llm_detail}")
        if len(outputs) - by_llm:
            explained_bits.append(f"{len(outputs) - by_llm} via deterministic template")
        explained = ", ".join(explained_bits) or "none"
        actions = create_actions(
            issues,
            {key: result.output for key, result in outputs.items()},
            context.settings["automation"],
            root_cause_outputs=state["root_cause_outputs"],
            benchmark_outputs=state["benchmark_outputs"],
            max_escalation_targets=int(context.impact_scoring["escalation"]["max_targets_per_issue"]),
        )
        approvals = sum(action.requires_human_approval for action in actions)
        vendor_targeted = sum(action.target_vendor_id is not None for action in actions)
        return {
            "reasoning_outputs": outputs,
            "actions": actions,
            "workflow_trace": _trace(
                state,
                "escalation_advisor_node",
                "SUCCESS",
                f"Explained {len(outputs)} issue(s): {explained}. Drafted {len(actions)} "
                f"action(s), {vendor_targeted} vendor-targeted, {approvals} awaiting human approval.",
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
