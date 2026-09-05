from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from core.models.action import ManagerRole
from core.models.chat import ChatTool


class Persona(StrEnum):
    TRANSPORT_MANAGER = "TRANSPORT_MANAGER"
    FACILITIES_HEAD = "FACILITIES_HEAD"
    LINE_MANAGER = "LINE_MANAGER"


class PersonaScope(BaseModel):
    persona: Persona = Persona.TRANSPORT_MANAGER
    business_unit: str | None = None
    office: str | None = None
    shift: str | None = None
    start_date: date
    end_date: date

    @property
    def period_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def thread_id(self) -> str:
        return f"{self.persona.lower()}-copilot"


PERSONA_LABELS = {
    Persona.TRANSPORT_MANAGER: "Transport manager",
    Persona.FACILITIES_HEAD: "Transport & facilities head",
    Persona.LINE_MANAGER: "Team / line manager",
}

PERSONA_MISSION = {
    Persona.TRANSPORT_MANAGER: (
        "Day-to-day operations: vendors, delays, safety and shift coordination."
    ),
    Persona.FACILITIES_HEAD: (
        "Cost, SLA, vendor strategy and a leadership-ready operating story. "
        "Spend is billed actuals, not budget."
    ),
    Persona.LINE_MANAGER: (
        "Shift readiness for the selected office and shift: boarded, late pickup "
        "and no-show. There is no named team hierarchy in this dataset."
    ),
}

PERSONA_PERIOD_DAYS = {
    Persona.TRANSPORT_MANAGER: 7,
    Persona.FACILITIES_HEAD: 30,
    Persona.LINE_MANAGER: 1,
}

PERSONA_OWNER_ROLE = {
    Persona.TRANSPORT_MANAGER: ManagerRole.TRANSPORT_MANAGER,
    Persona.FACILITIES_HEAD: ManagerRole.FACILITIES_HEAD,
    Persona.LINE_MANAGER: ManagerRole.LINE_MANAGER,
}

PERSONA_TOOLS = {
    Persona.TRANSPORT_MANAGER: {
        ChatTool.OPERATIONAL_OVERVIEW,
        ChatTool.TRIP_LOOKUP,
        ChatTool.TRIP_SAFETY,
        ChatTool.ALERTS_REPORT,
        ChatTool.OTA_REPORT,
        ChatTool.SLA_BREACH_REPORT,
        ChatTool.ENTITY_COMPARISON,
        ChatTool.DELAY_REPORT,
        ChatTool.NO_SHOW_REPORT,
        ChatTool.IMPACTED_TRIPS,
        ChatTool.HELP,
    },
    Persona.FACILITIES_HEAD: {
        ChatTool.OPERATIONAL_OVERVIEW,
        ChatTool.OTA_REPORT,
        ChatTool.SLA_BREACH_REPORT,
        ChatTool.ENTITY_COMPARISON,
        ChatTool.BILLING_REPORT,
        ChatTool.FEEDBACK_REPORT,
        ChatTool.ALERTS_REPORT,
        ChatTool.UTILIZATION_REPORT,
        ChatTool.HELP,
    },
    Persona.LINE_MANAGER: {
        ChatTool.SHIFT_READINESS_REPORT,
        ChatTool.NO_SHOW_REPORT,
        ChatTool.DELAY_REPORT,
        ChatTool.ALERTS_REPORT,
        ChatTool.HELP,
    },
}

PERSONA_PROMPTS = {
    Persona.TRANSPORT_MANAGER: [
        "What needs attention in the last 7 days?",
        "Which vendors breached OTA SLA?",
        "Draft an escalation email for the worst vendor.",
    ],
    Persona.FACILITIES_HEAD: [
        "Give me a 30-day operating story for leadership.",
        "Compare vendors on cost per km and OTA.",
        "Where did billed spend concentrate?",
    ],
    Persona.LINE_MANAGER: [
        "Who boarded, no-showed or had a late pickup on this shift?",
        "Show no-shows for this office.",
        "Assign follow-up for the unready riders.",
    ],
}


def allowed_tools(persona: Persona) -> set[ChatTool]:
    return PERSONA_TOOLS[persona]
