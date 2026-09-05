from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Evidence(BaseModel):
    label: str
    value: Any
    comparison: str | None = None
    benchmark: Any | None = None
    delta: float | None = None
    denominator: int | None = None
    source: str = "mobility_trip_360"
