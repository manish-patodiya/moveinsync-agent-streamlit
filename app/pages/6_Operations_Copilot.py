import sys
import uuid
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import streamlit as st

from app import require_service
from app.ui import status_chip
from core.models.action import ActionStatus
from core.models.chat import ChatToolResult

DEFAULT_THREAD = "operations-copilot"

st.set_page_config(page_title="Operations Copilot · Mobility Pulse", page_icon="💬", layout="wide")
st.title("💬 Operations Copilot")
st.caption(
    "Ask for trip details, trip safety, alerts, OTA or SLA reports. Your question is routed "
    "to fixed read-only tools — the model never writes or executes SQL — and the answer is "
    "then written from the rows that query returned."
)
service = require_service()


def restore(thread_id: str) -> list[dict]:
    """Rebuild the visible conversation from the DuckDB checkpoint."""
    history = []
    for message in service.conversation_history(thread_id):
        if message["role"] == "assistant" and message.get("result"):
            history.append(
                {"role": "assistant", "result": ChatToolResult.model_validate(message["result"])}
            )
        else:
            history.append({"role": message["role"], "content": message.get("content", "")})
    return history


if "copilot_thread_id" not in st.session_state:
    st.session_state.copilot_thread_id = DEFAULT_THREAD
if "copilot_history" not in st.session_state:
    st.session_state.copilot_history = restore(st.session_state.copilot_thread_id)
if "actions" not in st.session_state:
    st.session_state.actions = []
if "audit_trail" not in st.session_state:
    st.session_state.audit_trail = []

with st.sidebar:
    st.markdown("#### Copilot tools")
    st.markdown(
        "- Trip lookup\n"
        "- Trip safety alerts\n"
        "- Alert report\n"
        "- OTA report\n"
        "- SLA breach report"
    )
    st.caption("Checkpoints persist in DuckDB, so this thread survives a restart.")
    st.code(st.session_state.copilot_thread_id, language=None)
    if st.button("New conversation", width="stretch"):
        st.session_state.copilot_thread_id = str(uuid.uuid4())[:8]
        st.session_state.copilot_history = []
        st.rerun()
    if st.button("Forget this conversation", width="stretch"):
        service.forget_conversation(st.session_state.copilot_thread_id)
        st.session_state.copilot_history = []
        st.rerun()

st.info(
    "**Try:** “What safety alerts are on trip 4927479?” · then follow up with "
    "“was anything left unacknowledged?” · “Show OTA by office for the last 14 days” · "
    "“And which vendors breached the SLA?”"
)


def current_action(action_id: str):
    return next(
        (action for action in st.session_state.actions if action.action_id == action_id),
        None,
    )


def decide(action_id: str, requested: ActionStatus) -> None:
    for index, action in enumerate(st.session_state.actions):
        if action.action_id != action_id:
            continue
        updated, event = service.transition_action(action, requested)
        st.session_state.actions[index] = updated
        st.session_state.audit_trail.append(event)
        st.rerun()


def render_result(result, index: int) -> None:
    route_badge = (
        f"`ROUTED BY {result.route_detail.upper()}`"
        if result.route_source == "llm"
        else f"`{result.route_detail.upper()}`"
    )
    answer_badge = (
        f"`ANSWER BY {result.answer_detail.upper()}`"
        if result.answer_source == "llm"
        else "`DETERMINISTIC SUMMARY`"
    )
    st.caption(f"{route_badge} · {answer_badge}")
    if result.interpretation:
        st.caption(f"↳ {result.interpretation}")
    st.markdown(result.answer)
    if result.grounded_summary and result.grounded_summary != result.answer:
        with st.expander("Verified figures behind this answer", expanded=False):
            st.markdown(result.grounded_summary)
            st.caption(
                "Computed in SQL before the model wrote the answer above. If the two "
                "disagree, trust this one."
            )
    if result.rows:
        frame = pd.DataFrame(result.rows)
        with st.expander(f"Report data · {len(frame):,} row(s)", expanded=False):
            st.dataframe(frame, hide_index=True, width="stretch")
        st.download_button(
            "Download CSV report",
            frame.to_csv(index=False).encode(),
            file_name=result.report_name or f"mobility_report_{index}.csv",
            mime="text/csv",
            key=f"download-{index}",
        )
    if result.proposed_action:
        action = current_action(result.proposed_action.action_id)
        if action is None:
            st.session_state.actions.append(result.proposed_action)
            action = result.proposed_action
        with st.container(border=True):
            st.markdown(
                f"{status_chip(str(action.status))} &nbsp; "
                f"**Proposed action: {action.title}**",
                unsafe_allow_html=True,
            )
            st.caption(action.rationale)
            st.caption("Human-in-the-loop: this action does nothing until you approve it.")
            buttons = st.columns(2)
            if buttons[0].button(
                "Approve",
                key=f"chat-approve-{index}-{action.action_id}",
                disabled=action.status not in {ActionStatus.PROPOSED, ActionStatus.REVIEWED},
                width="stretch",
            ):
                decide(action.action_id, ActionStatus.APPROVED)
            if buttons[1].button(
                "Reject",
                key=f"chat-reject-{index}-{action.action_id}",
                disabled=action.status in {ActionStatus.APPROVED, ActionStatus.REJECTED},
                width="stretch",
            ):
                decide(action.action_id, ActionStatus.REJECTED)


for index, message in enumerate(st.session_state.copilot_history):
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            render_result(message["result"], index)
        else:
            st.markdown(message["content"])

question = st.chat_input("Ask about trips, alerts, OTA or SLA performance…")
if question:
    st.session_state.copilot_history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.status("Routing, querying, then writing the answer…", expanded=True) as status:
            result = service.ask_operations(question, st.session_state.copilot_thread_id)
            status.update(
                label=f"Report ready · {result.tool}",
                state="complete",
                expanded=False,
            )
        render_result(result, len(st.session_state.copilot_history))
    st.session_state.copilot_history.append({"role": "assistant", "result": result})
