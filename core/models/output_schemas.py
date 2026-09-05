from pydantic import BaseModel


class IssueReasoningOutput(BaseModel):
    """Schema the LLM must return. `source` is stamped by the service, never the model."""

    manager_summary: str
    operational_interpretation: str
    urgency_reason: str
    recommended_action_types: list[str]
    recommended_actions: list[str]
    email_subject: str | None = None
    email_body: str | None = None
    caveat: str | None = None


class ReasoningResult(BaseModel):
    output: IssueReasoningOutput
    source: str
    source_detail: str
    latency_seconds: float | None = None
    fallback_reason: str | None = None
