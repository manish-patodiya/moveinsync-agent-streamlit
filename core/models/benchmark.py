from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class TrendPoint(BaseModel):
    period_start: date
    period_end: date
    value: float | None = None
    sla_target: float | None = None
    gap: float | None = None


class VendorBenchmark(BaseModel):
    """Sourced entirely from benchmark_node tool calls; never LLM-authored."""

    vendor_id: str
    metric: str
    current_value: float | None = None
    peer_value: float | None = None
    sla_target: float | None = None
    gap: float | None = None
    affected_trips: int = 0
    affected_employees: int | None = None
    trend: list[TrendPoint] = Field(default_factory=list)
    trend_direction: str = "FLAT"
    trend_slope: float = 0.0


class VendorAttribution(BaseModel):
    """Sourced entirely from root_cause_node tool calls; never LLM-authored."""

    vendor_id: str
    impact_rank: int
    impact_score: float
    primary_factors: list[str] = Field(default_factory=list)
