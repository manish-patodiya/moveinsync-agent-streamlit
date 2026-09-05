from pydantic import BaseModel, Field


class RoleRecommendation(BaseModel):
    role: str
    action: str
    rationale: str
    owner: str
    expected_outcome: str
    monitoring_condition: str


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
    evidence_citations: list[str] = Field(default_factory=list)
    role_recommendations: list[RoleRecommendation] = Field(default_factory=list)


class ReasoningResult(BaseModel):
    output: IssueReasoningOutput
    source: str
    source_detail: str
    latency_seconds: float | None = None
    fallback_reason: str | None = None


class PersonaDecision(BaseModel):
    headline: str
    why_now: str
    cited_benchmark_facts: list[str] = Field(default_factory=list)
    operational_impact: str
    recommended_decisions: list[str] = Field(default_factory=list)
    owner: str
    due_or_monitor: str
    caveats: list[str] = Field(default_factory=list)
