from __future__ import annotations

from core.models.live_alert import AlertPriority

PANIC_EVENTS = {"PANIC_FIXED_DEVICE", "PANIC_DEVICE", "PANIC_MOBILE"}

ACTION_MAP = {
    **{event: ["ACKNOWLEDGE", "ESCALATE", "CALL_DRIVER", "CALL_EMPLOYEE"] for event in PANIC_EVENTS},
    "OVER_SPEEDING": ["ACKNOWLEDGE", "ESCALATE", "CALL_DRIVER"],
    "DEVICE_NOT_REACHABLE": ["ACKNOWLEDGE", "ESCALATE", "CALL_DRIVER"],
    "VEHICLE_STOPPAGE": ["ACKNOWLEDGE", "ESCALATE", "CALL_DRIVER"],
    "EMPLOYEE_GEOFENCE_VIOLATION": ["ACKNOWLEDGE", "ESCALATE", "CALL_EMPLOYEE"],
    "WOMAN_TRAVELLING_ALONE": ["ACKNOWLEDGE", "ESCALATE", "CALL_DRIVER", "CALL_EMPLOYEE"],
}


def prioritize_alert(severity: str | None, event_type: str, state: str) -> tuple[AlertPriority, int]:
    score = {"Sev-1": 100, "Sev-2": 60, "Sev-3": 20}.get(severity or "", 10)
    if event_type in PANIC_EVENTS:
        # Panic is critical even when the source omitted severity.
        score += 110
    elif event_type == "OVER_SPEEDING":
        score += 35
    elif event_type in {"EMPLOYEE_GEOFENCE_VIOLATION", "WOMAN_TRAVELLING_ALONE"}:
        score += 30
    elif event_type in {"DEVICE_NOT_REACHABLE", "VEHICLE_STOPPAGE"}:
        score += 20
    if state == "NEW":
        score += 10
    if score >= 120:
        return AlertPriority.CRITICAL, score
    if score >= 75:
        return AlertPriority.HIGH, score
    if score >= 40:
        return AlertPriority.MEDIUM, score
    return AlertPriority.LOW, score


def allowed_actions(event_type: str, has_employee: bool) -> list[str]:
    actions = list(ACTION_MAP.get(event_type, ["ACKNOWLEDGE", "ESCALATE"]))
    if not has_employee and "CALL_EMPLOYEE" in actions:
        actions.remove("CALL_EMPLOYEE")
    return actions
