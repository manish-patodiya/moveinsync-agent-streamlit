import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st

from app import require_service
from app.ui import role_chip, status_chip
from core.models.action import ActionStatus, ManagerRole

st.set_page_config(page_title="Actions and Audit · Mobility Pulse", page_icon="✅", layout="wide")
st.title("Actions and Audit")
service = require_service()
if "actions" not in st.session_state:
    st.session_state.actions = service.saved_actions()
actions = st.session_state.actions
audit = service.action_audit()

if not actions:
    st.info("Open the Persona workspace to generate actions, then return here for the full audit.")
    st.stop()

st.warning("Every execution is simulated and approval-gated. No email, call, assignment or operational change leaves this app.")

counts = {status: 0 for status in ActionStatus}
for action in actions:
    counts[action.status] += 1
summary = st.columns(len(ActionStatus))
for column, status in zip(summary, ActionStatus):
    column.metric(str(status).replace("_", " ").title(), counts[status])

terminal = {
    ActionStatus.REJECTED,
    ActionStatus.SIMULATED_SENT,
    ActionStatus.SIMULATED_COMPLETED,
}
pending = [a for a in actions if a.status not in terminal]
st.caption(f"{len(pending)} action(s) still need a decision from you.")

filters = st.columns(3)
role_filter = filters[0].multiselect(
    "Owner role",
    [str(role) for role in ManagerRole],
    default=[str(role) for role in ManagerRole],
)
source_filter = filters[1].multiselect(
    "Source",
    sorted({action.source for action in actions}),
    default=sorted({action.source for action in actions}),
)
status_filter = filters[2].multiselect(
    "Status",
    [str(status) for status in ActionStatus],
    default=[str(status) for status in ActionStatus],
)
actor_role = st.selectbox("Acting as", [str(role) for role in ManagerRole])


def transition(index: int, status: ActionStatus, note: str | None = None) -> None:
    try:
        updated, _event = service.transition_action(
            actions[index],
            status,
            actor_role=ManagerRole(actor_role),
            note=note,
        )
    except ValueError as exc:
        st.error(str(exc))
        return
    actions[index] = updated
    st.session_state["actions"] = actions
    st.rerun()


for index, action in enumerate(actions):
    if (
        str(action.owner_role) not in role_filter
        or action.source not in source_filter
        or str(action.status) not in status_filter
    ):
        continue
    with st.container(border=True):
        head, badge = st.columns([5, 1])
        head.markdown(f"**{action.title}**")
        head.markdown(
            f"{role_chip(str(action.owner_role))} &nbsp; `{action.source}` · "
            f"`{action.action_type}` · priority {action.priority}",
            unsafe_allow_html=True,
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

        if action.call_script is not None:
            with st.expander("Call script (editable, simulated only)", expanded=False):
                script = st.text_area(
                    "Call script",
                    action.call_script,
                    key=f"call-{action.action_id}",
                    height=160,
                )
                actions[index] = action.model_copy(update={"call_script": script})
                action = actions[index]
        if action.supporting_evidence:
            with st.expander("Supporting evidence"):
                for item in action.supporting_evidence:
                    st.markdown(f"- {item}")
        details = st.columns(2)
        details[0].caption(f"Expected outcome: {action.expected_outcome or 'Not specified'}")
        details[1].caption(
            f"Monitor: {action.monitoring_condition or 'No monitoring condition'}"
        )
        note = st.text_input(
            "Decision / execution note",
            key=f"note-{action.action_id}",
            placeholder="Optional audit note",
        )
        buttons = st.columns(4)
        can_decide = action.status in {ActionStatus.PROPOSED, ActionStatus.REVIEWED}
        if buttons[0].button(
            "Approve", key=f"approve-{action.action_id}", disabled=not can_decide, width="stretch"
        ):
            transition(index, ActionStatus.APPROVED, note)
        if buttons[1].button(
            "Reject",
            key=f"reject-{action.action_id}",
            disabled=action.status in {ActionStatus.REJECTED, ActionStatus.SIMULATED_SENT},
            width="stretch",
        ):
            transition(index, ActionStatus.REJECTED, note)
        if buttons[2].button(
            "Mark Reviewed",
            key=f"review-{action.action_id}",
            disabled=action.status != ActionStatus.PROPOSED,
            width="stretch",
        ):
            transition(index, ActionStatus.REVIEWED, note)
        can_execute = action.status == ActionStatus.APPROVED
        completion = (
            ActionStatus.SIMULATED_SENT
            if action.email_subject is not None
            else ActionStatus.SIMULATED_COMPLETED
        )
        if buttons[3].button(
            "Simulate execution",
            key=f"execute-{action.action_id}",
            disabled=not can_execute,
            width="stretch",
            help="Available only after approval; nothing leaves this app.",
        ):
            transition(index, completion, note)

st.divider()
st.markdown("#### Audit trail")
if not audit:
    st.caption("No status changes have been recorded.")
for event in reversed(audit):
    st.markdown(
        f"`{event.timestamp:%Y-%m-%d %H:%M:%S}` UTC · **{event.action_id}** · "
        f"{event.previous_status} → {event.new_status} · {event.actor_role} · "
        f"{event.source}"
        + (f" · {event.note}" if event.note else "")
    )
