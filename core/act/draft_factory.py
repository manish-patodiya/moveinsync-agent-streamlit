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
