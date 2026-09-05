import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st

from app import require_service
from app.ui import status_chip
from core.models.action import ActionStatus

st.set_page_config(page_title="Actions and Audit · Mobility Pulse", page_icon="✅", layout="wide")
st.title("Actions and Audit")
service = require_service()
actions = st.session_state.get("actions", [])
audit = st.session_state.setdefault("audit_trail", [])

if not actions:
    st.info("Run Mobility Pulse from the Command Centre to generate actions.")
    st.stop()

st.warning(
    "Every action here is simulated inside this session. Mobility Pulse never contacts "
    "email, chat or any external service."
)

counts = {status: 0 for status in ActionStatus}
for action in actions:
    counts[action.status] += 1
summary = st.columns(5)
for column, status in zip(summary, ActionStatus):
    column.metric(str(status).replace("_", " ").title(), counts[status])

pending = [a for a in actions if a.status not in {ActionStatus.REJECTED, ActionStatus.SIMULATED_SENT}]
st.caption(f"{len(pending)} action(s) still need a decision from you.")


def transition(index: int, status: ActionStatus) -> None:
    try:
        updated, event = service.transition_action(actions[index], status)
    except ValueError as exc:
        st.error(str(exc))
        return
    actions[index] = updated
    audit.append(event)
    st.session_state["actions"] = actions
    st.rerun()


for index, action in enumerate(actions):
    with st.container(border=True):
        head, badge = st.columns([5, 1])
        head.markdown(f"**{action.title}**")
        head.caption(
            f"{action.action_type} · priority {action.priority} · owner {action.owner_role}"
        )
        badge.markdown(status_chip(str(action.status)), unsafe_allow_html=True)
        st.markdown(action.rationale)

        if action.email_subject is not None:
            with st.expander("Email draft (editable, simulated only)", expanded=False):
                subject = st.text_input(
                    "Subject", action.email_subject, key=f"subject-{action.action_id}"
                )
                body = st.text_area(
                    "Body", action.email_body or "", key=f"body-{action.action_id}", height=200
                )
                actions[index] = action.model_copy(
                    update={"email_subject": subject, "email_body": body}
                )
                action = actions[index]

        buttons = st.columns(4)
        can_decide = action.status in {ActionStatus.PROPOSED, ActionStatus.REVIEWED}
        if buttons[0].button(
            "Approve", key=f"approve-{action.action_id}", disabled=not can_decide, width="stretch"
        ):
            transition(index, ActionStatus.APPROVED)
        if buttons[1].button(
            "Reject",
            key=f"reject-{action.action_id}",
            disabled=action.status in {ActionStatus.REJECTED, ActionStatus.SIMULATED_SENT},
            width="stretch",
        ):
            transition(index, ActionStatus.REJECTED)
        if buttons[2].button(
            "Mark Reviewed",
            key=f"review-{action.action_id}",
            disabled=action.status != ActionStatus.PROPOSED,
            width="stretch",
        ):
            transition(index, ActionStatus.REVIEWED)
        can_send = action.requires_human_approval and action.status == ActionStatus.APPROVED
        if buttons[3].button(
            "Simulate Send",
            key=f"send-{action.action_id}",
            disabled=not can_send,
            width="stretch",
            help="Available only after approval; nothing leaves this app.",
        ):
            transition(index, ActionStatus.SIMULATED_SENT)

st.divider()
st.markdown("#### Audit trail")
if not audit:
    st.caption("No status changes yet in this session.")
for event in reversed(audit):
    st.markdown(
        f"`{event.timestamp:%Y-%m-%d %H:%M:%S}` UTC · **{event.action_id}** · "
        f"{event.previous_status} → {event.new_status}"
    )
