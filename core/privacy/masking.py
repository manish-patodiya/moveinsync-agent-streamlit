from __future__ import annotations

import hashlib


def mask_rider(stwid: str | None) -> str:
    """Stable display label. Raw stwid must never leave this helper into UI or LLM."""
    value = "" if stwid is None else str(stwid).strip()
    if not value or value == "0":
        return "Rider-UNLINKED"
    digest = hashlib.sha1(value.encode()).hexdigest()[:4].upper()
    return f"Rider-{digest}"


def assert_no_raw_stwid(rows: list[dict]) -> None:
    for row in rows:
        if "stwid" in row:
            raise ValueError("Raw stwid leaked into a public result")
