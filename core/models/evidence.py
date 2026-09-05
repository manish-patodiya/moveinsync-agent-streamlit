from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Evidence(BaseModel):
    label: str
    value: Any
    comparison: str | None = None
