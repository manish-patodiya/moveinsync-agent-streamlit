from __future__ import annotations

import duckdb

from core.models.benchmark import VendorAttribution, VendorBenchmark
from core.models.issue import CandidateIssue, IssueType
from core.models.workflow_state import AnalysisFilters
from core.root_cause.tools import (
    compute_impact_score,
    get_compliance_flags,
    get_delay_reason_breakdown,
    get_shift_breakdown,
)

SHIFT_CONCENTRATION_PCT_POINTS = 15.0


def rank_vendors(
    con: duckdb.DuckDBPyConnection,
    issue: CandidateIssue,
    benchmarks: list[VendorBenchmark],
    scope: AnalysisFilters,
    weights: dict[str, float],
) -> list[VendorAttribution]:
    """root_cause_node's per-issue orchestration.

    Scores every benchmarked vendor by negative impact (deterministic, via
    `compute_impact_score`) and attaches plain-language contributing factors pulled from
    tool calls -- delay-reason mix, shift concentration, compliance flags. Ranking and
    factor evidence are both tool-sourced; nothing here is invented.
    """
    scored: list[tuple[float, str, list[str]]] = []
    for benchmark in benchmarks:
        score = compute_impact_score(benchmark, weights)
        factors: list[str] = []

        if issue.issue_type == IssueType.VENDOR_OTA_BREACH:
            reasons = get_delay_reason_breakdown(con, benchmark.vendor_id, scope)
            if reasons:
                top = ", ".join(f"{r['reason']} ({r['pct']}%)" for r in reasons)
                factors.append(f"Top delay reasons: {top}")
            shifts = get_shift_breakdown(con, benchmark.vendor_id, scope)
            if len(shifts) > 1:
                worst, best = shifts[0], shifts[-1]
                if best["ota_pct"] - worst["ota_pct"] >= SHIFT_CONCENTRATION_PCT_POINTS:
                    factors.append(
                        f"Breach concentrated in the {worst['shift']} shift "
                        f"({worst['ota_pct']}% OTA vs {best['ota_pct']}% in {best['shift']})"
                    )

        compliance = get_compliance_flags(con, benchmark.vendor_id, scope)
        if (compliance["driver_nc_pct"] or 0) > 0 or (compliance["cab_nc_pct"] or 0) > 0:
            factors.append(
                f"Driver non-compliance {compliance['driver_nc_pct'] or 0}%, "
                f"cab non-compliance {compliance['cab_nc_pct'] or 0}%"
            )

        if benchmark.trend_direction == "WORSENING":
            factors.append(
                f"Trend worsening over the last {len(benchmark.trend)} period(s) "
                f"(slope {benchmark.trend_slope})"
            )
        elif benchmark.trend_direction == "IMPROVING":
            factors.append("Trend improving -- likely already being addressed")

        scored.append((score, benchmark.vendor_id, factors))

    scored.sort(key=lambda entry: -entry[0])
    return [
        VendorAttribution(vendor_id=vendor_id, impact_rank=rank, impact_score=score, primary_factors=factors)
        for rank, (score, vendor_id, factors) in enumerate(scored, start=1)
    ]
