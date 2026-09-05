from __future__ import annotations

from core.models.issue import CandidateIssue

# Deterministic, never LLM-authored: this text must be traceable 1:1 to the evidence
# Sense already attached to the issue, so it can never drift from the underlying numbers.


def summarize_issue_evidence(issue: CandidateIssue) -> str:
    facts = "; ".join(f"{item.label}: {item.value}" for item in issue.evidence)
    comparisons = ", ".join(
        f"{key.replace('_', ' ')} {value}"
        for key, value in issue.comparisons.items()
        if value is not None
    )
    tail = f" Benchmarked against {comparisons}." if comparisons else ""
    return (
        f"Sense flagged '{issue.title}' ({issue.severity} severity, {issue.data_confidence} "
        f"confidence) affecting {issue.affected_trip_count:,} trip(s). {facts}.{tail}"
    )


def summarize_run(kpis: dict, all_issues: list[CandidateIssue], prioritized: list[CandidateIssue]) -> str:
    if not all_issues:
        return (
            f"Sense reviewed {kpis.get('trip_count', 0):,} trip(s) and found no signal "
            "clearing the configured suspicious-signal thresholds."
        )
    by_type: dict[str, int] = {}
    for issue in all_issues:
        by_type[str(issue.issue_type)] = by_type.get(str(issue.issue_type), 0) + 1
    breakdown = ", ".join(f"{count} {name}" for name, count in sorted(by_type.items()))
    return (
        f"Sense reviewed {kpis.get('trip_count', 0):,} trip(s) and flagged {len(all_issues)} "
        f"suspicious signal(s) ({breakdown}); {len(prioritized)} were high/critical enough to "
        "escalate to Benchmark and Root Cause."
    )
