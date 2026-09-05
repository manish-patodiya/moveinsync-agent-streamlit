import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import streamlit as st

from app import require_service
from app.ui import (
    render_action_card,
    render_llm_banner,
    render_pulse_card,
    render_readiness_roster,
    render_rider_drill,
    render_vendor_drill,
    render_vendor_scorecard,
    source_chip,
)
from core.models.action import ManagerRole
from core.models.chat import ChatToolResult
from core.models.persona import PERSONA_LABELS, PERSONA_MISSION, PERSONA_PROMPTS, Persona, PersonaScope


def _restore_history(service, thread_id: str) -> list[dict]:
    history = []
    try:
        messages = service.conversation_history(thread_id)
    except Exception:
        return []
    for message in messages:
        if message["role"] == "assistant" and message.get("result"):
            history.append(
                {"role": "assistant", "result": ChatToolResult.model_validate(message["result"])}
            )
        else:
            history.append({"role": message["role"], "content": message.get("content", "")})
    return history


def _history_key(persona: Persona) -> str:
    return f"copilot_history_{persona}"


def _render_chat_result(result: ChatToolResult, index: int, service, actor: ManagerRole, persona: Persona) -> None:
    st.caption(f"`{result.route_detail}` · `{result.answer_detail}`")
    if result.plan:
        with st.expander(f"Tool plan · {len(result.plan.steps)} step(s)", expanded=False):
            for step in result.plan.steps:
                st.markdown(f"{step.step}. `{step.tool}` — {step.purpose}")
    st.markdown(result.answer)
    if result.grounded_summary and result.grounded_summary != result.answer:
        with st.container(border=True):
            st.markdown("**Verified evidence**")
            for summary in result.evidence_summaries or [result.grounded_summary]:
                st.markdown(summary)
    if result.rows:
        frame = pd.DataFrame(result.rows)
        with st.expander(f"Rows · {len(frame):,}", expanded=False):
            st.dataframe(frame, hide_index=True, width="stretch")
    proposed = result.proposed_actions or ([result.proposed_action] if result.proposed_action else [])
    saved = {action.action_id: action for action in service.saved_actions()}

    def transition(action, status):
        current = saved.get(action.action_id, action)
        service.transition_action(current, status, actor_role=actor, note="Workspace copilot")
        st.rerun()

    for action in proposed:
        render_action_card(
            saved.get(action.action_id, action),
            key_prefix=f"chat-{persona}-{index}",
            on_transition=transition,
        )


def _ensure_persona_scope(service, persona: Persona) -> None:
    prefix = f"tab_{persona}_"
    if st.session_state.get(prefix + "ready"):
        return
    defaults = service.default_scope(persona)
    st.session_state[prefix + "bu"] = defaults.business_unit
    st.session_state[prefix + "office"] = defaults.office
    st.session_state[prefix + "shift"] = defaults.shift
    st.session_state[prefix + "start"] = defaults.start_date
    st.session_state[prefix + "end"] = defaults.end_date
    st.session_state[prefix + "ready"] = True
    st.session_state[_history_key(persona)] = _restore_history(service, defaults.thread_id)


def _scope_from_widgets(persona: Persona, options: dict) -> PersonaScope:
    prefix = f"tab_{persona}_"
    units = [None, *options["business_units"]]
    offices = [None, *options["offices"]]
    n = 5 if persona == Persona.LINE_MANAGER else 4
    cols = st.columns(n)
    bu = cols[0].selectbox(
        "Business unit",
        units,
        index=units.index(st.session_state[prefix + "bu"]) if st.session_state[prefix + "bu"] in units else 0,
        key=prefix + "bu_w",
    )
    office = cols[1].selectbox(
        "Office",
        offices,
        index=offices.index(st.session_state[prefix + "office"]) if st.session_state[prefix + "office"] in offices else 0,
        key=prefix + "office_w",
    )
    shift = None
    if persona == Persona.LINE_MANAGER:
        shifts = [None, *options["shifts"]]
        shift = cols[2].selectbox(
            "Shift",
            shifts,
            index=shifts.index(st.session_state[prefix + "shift"]) if st.session_state[prefix + "shift"] in shifts else 0,
            key=prefix + "shift_w",
        )
        start = cols[3].date_input("From", st.session_state[prefix + "start"], key=prefix + "start_w")
        end = cols[4].date_input("To", st.session_state[prefix + "end"], key=prefix + "end_w")
    else:
        start = cols[2].date_input("From", st.session_state[prefix + "start"], key=prefix + "start_w")
        end = cols[3].date_input("To", st.session_state[prefix + "end"], key=prefix + "end_w")
    st.session_state[prefix + "bu"] = bu
    st.session_state[prefix + "office"] = office
    st.session_state[prefix + "shift"] = shift
    st.session_state[prefix + "start"] = start
    st.session_state[prefix + "end"] = end
    return PersonaScope(persona=persona, business_unit=bu, office=office, shift=shift, start_date=start, end_date=end)


def _ask_copilot(service, persona: Persona, scope: PersonaScope, question: str, vendor: str | None = None) -> None:
    key = _history_key(persona)
    history = st.session_state.setdefault(key, [])
    history.append({"role": "user", "content": question})
    result = service.ask_operations(
        question,
        scope.thread_id,
        persona=str(persona),
        scope={
            "business_unit": scope.business_unit,
            "office": scope.office,
            "shift": scope.shift,
            "vendor": vendor,
            "period_days": scope.period_days,
        },
    )
    history.append({"role": "assistant", "result": result})
    st.session_state[key] = history


def _render_persona_workspace(service, persona: Persona, options: dict) -> None:
    _ensure_persona_scope(service, persona)
    st.info(PERSONA_MISSION[persona])
    scope = _scope_from_widgets(persona, options)
    with st.spinner("Loading pulse…"):
        workspace = service.persona_workspace(scope)
    pulse = workspace["pulse"]
    kpi = pulse.kpi
    actor = ManagerRole(persona)
    vendor_key = f"selected_vendor_{persona}"
    rider_key = f"selected_rider_{persona}"
    pending_key = f"pending_ask_{persona}"

    main, copilot = st.columns([3, 2], gap="large")
    with main:
        st.markdown("### Pulse")
        metrics = st.columns(4)
        metrics[0].metric("OTA %", kpi.get("ota_pct"), delta=kpi.get("ota_delta_pp"))
        metrics[1].metric("Trips", f"{kpi.get('trip_count', 0):,}")
        if persona == Persona.FACILITIES_HEAD:
            metrics[2].metric("Billed spend", f"{kpi.get('total_billed_cost', 0):,.0f}")
            metrics[3].metric("Electric trip %", kpi.get("electric_trip_pct"))
        elif persona == Persona.LINE_MANAGER:
            metrics[2].metric("Boarded", kpi.get("boarded"))
            metrics[3].metric("No-shows / late pickup", f"{kpi.get('no_shows', 0)} / {kpi.get('late_pickups', 0)}")
        else:
            metrics[2].metric("Late trips", kpi.get("late_trips"))
            metrics[3].metric("Safety alerts", kpi.get("safety_alerts"))
        if not pulse.cards:
            st.success("Nothing urgent in this window. Rank vendors or ask Copilot.")
        for card in pulse.cards:
            render_pulse_card(card)
        for caveat in pulse.caveats:
            st.caption(caveat)

        if persona in {Persona.TRANSPORT_MANAGER, Persona.FACILITIES_HEAD}:
            st.markdown("### Vendors")
            st.caption("Sorted by OTA. Click a vendor below to see offices, delay reasons and trips.")
            render_vendor_scorecard(pulse.vendor_scorecard, key=f"score-{persona}")
            names = [row["vendor"] for row in pulse.vendor_scorecard if row.get("vendor")]
            selected = st.selectbox("Inspect vendor", [None, *names], key=vendor_key)
            if selected:
                drill = service.vendor_drill(scope, selected)
                render_vendor_drill(drill)
                buttons = st.columns(2)
                if buttons[0].button("Ask copilot about this vendor", key=f"ask-v-{persona}", width="stretch"):
                    st.session_state[pending_key] = (
                        f"Why is {selected} below OTA SLA in this window? Cite delay reasons and offices."
                    )
                    st.rerun()
                if buttons[1].button("Propose follow-up", key=f"act-v-{persona}", width="stretch"):
                    service.propose_vendor_follow_up(
                        scope,
                        selected,
                        f"Canvas drill on {selected}: {drill['alerts']} alerts, "
                        f"{len(drill['delay_reasons'])} delay reasons.",
                    )
                    st.rerun()
            if persona == Persona.FACILITIES_HEAD:
                st.download_button(
                    "Download leadership brief (Markdown)",
                    workspace["brief_markdown"].encode(),
                    file_name="mobility_leadership_brief.md",
                    mime="text/markdown",
                    key=f"md-{persona}",
                )
                st.download_button(
                    "Download leadership brief (HTML / print)",
                    workspace["brief_html"].encode(),
                    file_name="mobility_leadership_brief.html",
                    mime="text/html",
                    key=f"html-{persona}",
                )
                with st.expander("Brief preview"):
                    st.markdown(workspace["brief_markdown"])

        if persona == Persona.LINE_MANAGER:
            st.markdown("### Readiness roster")
            if pulse.vendor_hotspots:
                st.caption("Late pickups on this shift concentrate on: " + ", ".join(
                    f"{row['vendor']} ({row['late_pickups']})" for row in pulse.vendor_hotspots[:3]
                ))
            render_readiness_roster(pulse.roster)
            labels = [f"{row['rider']} · trip {row['trip_id']}" for row in pulse.roster]
            picked = st.selectbox("Inspect masked rider", [None, *labels], key=rider_key)
            if picked:
                row = next(
                    item
                    for item, label in zip(pulse.roster, labels)
                    if label == picked
                )
                trip = service.rider_trip_context(str(row["trip_id"])) if row.get("trip_id") else {}
                render_rider_drill(row, trip)
                rb = st.columns(2)
                if rb[0].button("Ask copilot about this rider", key=f"ask-r-{persona}", width="stretch"):
                    st.session_state[pending_key] = (
                        f"Shift readiness for {row['rider']} on trip {row.get('trip_id')}. "
                        "Use masked labels only."
                    )
                    st.rerun()
                if rb[1].button("Propose rider follow-up", key=f"act-r-{persona}", width="stretch"):
                    service.propose_rider_follow_up(scope, row["rider"], str(row.get("trip_id")))
                    st.rerun()

        st.markdown("### Evidence")
        for issue in pulse.issues:
            result = workspace["reasoning"].get(issue.issue_id)
            with st.container(border=True):
                st.markdown(f"**{issue.title}**")
                if result:
                    st.markdown(source_chip(result), unsafe_allow_html=True)
                    decision = next(
                        (item for item in workspace["decisions"] if item.headline == result.output.manager_summary),
                        None,
                    )
                    if decision:
                        st.markdown(f"**Why now.** {decision.why_now}")
                        st.markdown(f"**Impact.** {decision.operational_impact}")

        st.markdown("### Next actions")
        st.caption("Approve, then simulate. Nothing is emailed, called or assigned outside this app.")
        queue = [action for action in service.saved_actions() if action.owner_role == actor]
        if not queue:
            st.caption("No open actions for this persona yet.")

        def transition(action, status):
            service.transition_action(action, status, actor_role=actor, note="Workspace")
            st.rerun()

        for action in queue[:8]:
            render_action_card(action, key_prefix=f"pulse-{persona}", on_transition=transition)

    with copilot:
        st.markdown("### Ask Mobility Copilot")
        st.caption("This persona has its own thread. Tools are read-only. Follow-ups stay here.")
        if st.session_state.get(vendor_key):
            st.caption(f"Canvas vendor in scope: **{st.session_state[vendor_key]}**")
        for prompt in PERSONA_PROMPTS[persona]:
            st.caption(f"Try: _{prompt}_")
        hist_key = _history_key(persona)
        if hist_key not in st.session_state:
            st.session_state[hist_key] = _restore_history(service, scope.thread_id)
        for index, message in enumerate(st.session_state[hist_key][-8:]):
            with st.chat_message(message["role"]):
                if message["role"] == "assistant" and message.get("result"):
                    _render_chat_result(message["result"], index, service, actor, persona)
                else:
                    st.markdown(message.get("content", ""))
        pending = st.session_state.pop(pending_key, None)
        question = st.chat_input("Ask about this persona’s scope…")
        asked = pending or question
        if asked:
            vendor = st.session_state.get(vendor_key)
            _ask_copilot(service, persona, scope, asked, vendor if isinstance(vendor, str) else None)
            st.rerun()


def _run_persona(persona: Persona) -> None:
    service = require_service()
    health = service.data_health()

    st.title(f"Mobility Pulse · {PERSONA_LABELS[persona]}")
    st.caption(
        "Historical May–July 2026 sample · Sense → Reason → Act · "
        "emails and calls are simulated and approval-gated."
    )
    render_llm_banner(service.llm_status())
    if not health["grain_invariant_ok"]:
        st.error("Trip grain is broken. Open Advanced → Data Health.")
        st.stop()
    _render_persona_workspace(service, persona, service.filter_options())


def transport_manager_page() -> None:
    _run_persona(Persona.TRANSPORT_MANAGER)


def facilities_head_page() -> None:
    _run_persona(Persona.FACILITIES_HEAD)


def line_manager_page() -> None:
    _run_persona(Persona.LINE_MANAGER)


st.set_page_config(page_title="Mobility Pulse", page_icon="🚌", layout="wide")
_pages = st.navigation(
    {
        "Personas": [
            st.Page(
                transport_manager_page,
                title=PERSONA_LABELS[Persona.TRANSPORT_MANAGER],
                icon="🚌",
                url_path="transport-manager",
                default=True,
            ),
            st.Page(
                facilities_head_page,
                title=PERSONA_LABELS[Persona.FACILITIES_HEAD],
                icon="📊",
                url_path="facilities-head",
            ),
            st.Page(
                line_manager_page,
                title=PERSONA_LABELS[Persona.LINE_MANAGER],
                icon="🧭",
                url_path="line-manager",
            ),
        ],
        "Advanced": [
            st.Page("pages/3_Actions_and_Audit.py", title="Actions & Audit", icon="✅"),
            st.Page("pages/4_Data_Health.py", title="Data Health", icon="🧪"),
        ],
    }
)
_pages.run()
