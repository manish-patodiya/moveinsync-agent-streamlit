from __future__ import annotations

from html import escape

from core.models.output_schemas import PersonaDecision
from core.models.persona import PERSONA_LABELS, PersonaScope
from core.sense.persona_insights import PersonaPulse


def leadership_brief_markdown(
    scope: PersonaScope,
    pulse: PersonaPulse,
    decisions: list[PersonaDecision],
) -> str:
    kpi = pulse.kpi
    ota = kpi.get("ota_pct")
    prior = kpi.get("prior_ota_pct")
    billed = kpi.get("total_billed_cost")
    prior_billed = kpi.get("prior_billed_cost")
    electric = kpi.get("electric_trip_pct")
    lines = [
        f"# Mobility operating brief · {PERSONA_LABELS[scope.persona]}",
        "",
        f"**Window:** {scope.start_date} → {scope.end_date}"
        + (f" · **BU:** {scope.business_unit}" if scope.business_unit else "")
        + (f" · **Office:** {scope.office}" if scope.office else ""),
        "",
        "## Executive headline",
        decisions[0].headline if decisions else "No high-severity exception in this window; review the scorecard.",
        "",
        "## Trend versus prior period and SLA",
        (
            f"OTA is **{ota}%** versus SLA **{kpi.get('sla_target_pct')}%** "
            f"and prior-period **{prior}%** ({kpi.get('prior_period_start')}–{kpi.get('prior_period_end')})."
            if ota is not None
            else "OTA could not be computed for this window."
        ),
        (
            f"Billed spend is **{billed:,.0f}**"
            + (f" versus prior-period **{prior_billed:,.0f}**." if prior_billed is not None else ".")
            if billed is not None
            else ""
        ),
        "",
        "## Top vendor drivers",
    ]
    for row in pulse.vendor_scorecard[:5]:
        lines.append(
            f"- {row.get('vendor')}: OTA {row.get('ota_pct')}%, billed {row.get('billed_cost')}, "
            f"cost/km {row.get('cost_per_km')}, alerts {row.get('alerts')}"
        )
    if not pulse.vendor_scorecard:
        lines.append("- Vendor scorecard is empty for this scope.")
    lines += [
        "",
        "## Cost",
        "Figures are billed invoice actuals. The dataset has no budget, so this is not budget vs actual.",
        "",
        "## Safety",
        f"Recorded safety alerts in window: **{kpi.get('safety_alerts', 0)}**.",
        "",
        "## Experience",
        "Use Copilot feedback tools for rating detail; this brief does not invent CSAT.",
        "",
        "## Sustainability proxy",
        f"Electric fuel-type trip share: **{electric}%**. This is not an emissions figure.",
        "",
        "## Decisions required",
    ]
    for decision in decisions:
        lines.append(f"- {decision.owner}: {'; '.join(decision.recommended_decisions)}")
        lines.append(f"  Monitor: {decision.due_or_monitor}")
    if not decisions:
        lines.append("- No exception-driven decision; continue SLA monitoring.")
    lines += ["", "## Caveats"]
    for caveat in pulse.caveats:
        lines.append(f"- {caveat}")
    for decision in decisions:
        for caveat in decision.caveats:
            lines.append(f"- {caveat}")
    return "\n".join(line for line in lines if line is not None)


def leadership_brief_html(markdown_text: str) -> str:
    body = escape(markdown_text).replace("\n", "<br>\n")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Mobility operating brief</title>"
        "<style>body{font-family:sans-serif;max-width:720px;margin:2rem auto;line-height:1.45}</style>"
        "</head><body>"
        "<p><button onclick='window.print()'>Print / Save as PDF</button></p>"
        f"<pre style='white-space:pre-wrap;font-family:inherit'>{body}</pre>"
        "</body></html>"
    )
