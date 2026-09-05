from core.models.benchmark import VendorAttribution, VendorBenchmark
from core.models.issue import CandidateIssue
from core.models.output_schemas import IssueReasoningOutput


def make_email_draft(
    issue: CandidateIssue,
    reasoning: IssueReasoningOutput,
) -> tuple[str, str]:
    subject = reasoning.email_subject or f"Action required: {issue.title}"
    body = reasoning.email_body or (
        f"Hello,\n\nPlease review this transport operations issue: {issue.title}.\n"
        f"{reasoning.manager_summary}\n\nPlease provide findings and corrective actions.\n"
    )
    return subject, body


def make_vendor_email_draft(
    issue: CandidateIssue,
    attribution: VendorAttribution,
    benchmark: VendorBenchmark | None,
    reasoning: IssueReasoningOutput | None,
) -> tuple[str, str]:
    """escalation_advisor_node's per-vendor draft: addressed to the specific vendor Root
    Cause ranked as responsible, quoting only that vendor's own benchmark and factors."""
    subject = f"Action required: {issue.title} -- {attribution.vendor_id}"
    lines = [
        "Hello,",
        "",
        f"Please review the following transport operations issue: {issue.title}.",
        (
            f"{attribution.vendor_id} is ranked #{attribution.impact_rank} by negative impact "
            f"(impact score {attribution.impact_score})."
        ),
    ]
    if benchmark is not None and benchmark.current_value is not None:
        comparison = (
            f"SLA target {benchmark.sla_target}%"
            if benchmark.sla_target is not None
            else f"peer benchmark {benchmark.peer_value}"
        )
        lines.append(
            f"Current {benchmark.metric.replace('_', ' ')}: {benchmark.current_value} "
            f"(vs {comparison}); trend is {benchmark.trend_direction.lower()}."
        )
    if attribution.primary_factors:
        lines.append("Contributing factors: " + "; ".join(attribution.primary_factors) + ".")
    if reasoning is not None:
        lines.append(reasoning.manager_summary)
    lines += ["", "Please confirm findings and corrective actions for this vendor specifically.", ""]
    return subject, "\n".join(lines)
