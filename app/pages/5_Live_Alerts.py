import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st

from app import require_service
from app.ui import severity_chip
from core.act.action_policy import build_live_alert_action
from core.models.action import Action, ActionType
from core.models.live_alert import AlertActivity, AlertPriority, LiveAlert

st.set_page_config(page_title="Live Alerts · Mobility Pulse", page_icon="🚨", layout="wide")
st.title("🚨 Live Alert Management")
st.caption(
    "A demo watcher replays five synthetic, unacknowledged alerts. Source data stays "
    "read-only; requested responses enter the persistent approval queue."
)
service = require_service()

if "source_live_alerts" not in st.session_state:
    st.session_state.source_live_alerts = service.live_alerts(25)
if "demo_alert_feed" not in st.session_state:
    st.session_state.demo_alert_feed = service.synthetic_alerts()
if "demo_alert_cursor" not in st.session_state:
    st.session_state.demo_alert_cursor = 0
if "demo_alert_running" not in st.session_state:
    st.session_state.demo_alert_running = False
if "demo_alert_last_emit" not in st.session_state:
    st.session_state.demo_alert_last_emit = 0.0
if "alert_updates" not in st.session_state:
    st.session_state.alert_updates = {}
if "alert_activity" not in st.session_state:
    st.session_state.alert_activity = []
if "alert_drafts" not in st.session_state:
    st.session_state.alert_drafts = {}


def all_alerts() -> list[LiveAlert]:
    source = st.session_state.source_live_alerts
    demo = st.session_state.demo_alert_feed[: st.session_state.demo_alert_cursor]
    updates = st.session_state.alert_updates
    return sorted(
        [updates.get(alert.event_id, alert) for alert in [*source, *demo]],
        key=lambda alert: (
            alert.acknowledged_at is not None,
            -alert.priority_score,
            -alert.start_time.timestamp(),
        ),
    )


def record(alert: LiveAlert, action: str, detail: str) -> None:
    st.session_state.alert_activity.append(
        AlertActivity(event_id=alert.event_id, action=action, detail=detail)
    )


def enqueue(action: Action, alert: LiveAlert, detail: str) -> None:
    actions = st.session_state.setdefault("actions", [])
    if not any(existing.action_id == action.action_id for existing in actions):
        actions.append(action)
        service.save_actions([action])
    record(alert, "PROPOSE", detail)
    st.toast("Action proposed for approval")


def acknowledge(alert: LiveAlert, detail: str = "Acknowledged by transport manager") -> None:
    updated = alert.model_copy(
        update={
            "acknowledged_at": datetime.now(timezone.utc),
            "state": "CLOSED",
        }
    )
    st.session_state.alert_updates[alert.event_id] = updated
    record(alert, "ACKNOWLEDGE", detail)


@st.dialog("Dummy contact — demo only")
def call_contact(alert: LiveAlert, role: str) -> None:
    contact = service.dummy_contact(alert, role)
    st.warning("This contact is synthetic. No real call will be placed.")
    st.markdown(f"**{contact.display_name}**")
    st.markdown(f"### {contact.masked_phone}")
    if st.button("Record simulated call", type="primary", width="stretch"):
        action_type = (
            ActionType.REQUEST_DRIVER_CALL
            if role == "driver"
            else ActionType.REQUEST_EMPLOYEE_CALL
        )
        enqueue(
            build_live_alert_action(
                alert,
                action_type,
                call_script=(
                    f"Confirm identity using approved operations procedure. Ask for current "
                    f"trip status, safety condition and required assistance for trip {alert.trip_id}. "
                    "Record the response; do not disclose other employee details."
                ),
            ),
            alert,
            f"Simulated call to {contact.display_name} awaiting approval",
        )
        st.rerun()


@st.dialog("Escalate safety alert", width="large")
def escalate_alert(alert: LiveAlert) -> None:
    key = alert.event_id
    draft_result = st.session_state.alert_drafts.get(key)
    if draft_result is None:
        st.write(
            "Generate a manager-ready escalation from the alert and trip context. "
            "Employee identifiers are not sent to the model."
        )
        if st.button("Generate draft with qwen:14b", type="primary", width="stretch"):
            with st.spinner("Ollama is drafting the escalation…"):
                draft_result = service.alert_escalation_draft(alert)
                st.session_state.alert_drafts[key] = draft_result
    if draft_result is None:
        return
    badge = "LLM" if draft_result.source == "llm" else "TEMPLATE FALLBACK"
    st.caption(f"{badge} · {draft_result.source_detail}")
    subject = st.text_input("Subject", draft_result.draft.subject, key=f"alert-subject-{key}")
    body = st.text_area("Body", draft_result.draft.body, height=260, key=f"alert-body-{key}")
    st.warning("The draft will enter Actions and Audit as PROPOSED. Nothing is sent here.")
    if st.button("Propose escalation", type="primary", width="stretch"):
        action = build_live_alert_action(
            alert,
            ActionType.DRAFT_ALERT_ESCALATION_EMAIL,
            email_subject=subject,
            email_body=body,
        )
        enqueue(action, alert, "Escalation email awaiting approval")
        st.rerun()


controls = st.columns([1, 1, 3])
if controls[0].button(
    "▶ Start demo feed",
    disabled=st.session_state.demo_alert_running
    or st.session_state.demo_alert_cursor >= len(st.session_state.demo_alert_feed),
    width="stretch",
):
    st.session_state.demo_alert_running = True
    st.session_state.demo_alert_last_emit = 0.0
    st.rerun()
if controls[1].button(
    "Reset demo",
    disabled=st.session_state.demo_alert_cursor == 0,
    width="stretch",
):
    st.session_state.demo_alert_running = False
    st.session_state.demo_alert_cursor = 0
    st.session_state.alert_updates = {
        key: value
        for key, value in st.session_state.alert_updates.items()
        if not key.startswith("demo-alert-")
    }
    st.rerun()
controls[2].caption(
    "When started, one new synthetic alert arrives every 5 seconds. "
    "The feed resets with the Streamlit session; proposed actions remain in Actions and Audit."
)


@st.fragment(run_every=5)
def demo_watcher() -> None:
    if not st.session_state.demo_alert_running:
        return
    now = time.time()
    if now - st.session_state.demo_alert_last_emit < 4:
        return
    if st.session_state.demo_alert_cursor < len(st.session_state.demo_alert_feed):
        st.session_state.demo_alert_cursor += 1
        st.session_state.demo_alert_last_emit = now
        st.toast(
            f"New alert: {st.session_state.demo_alert_feed[st.session_state.demo_alert_cursor - 1].event_type}",
            icon="🚨",
        )
        st.rerun()
    else:
        st.session_state.demo_alert_running = False


demo_watcher()
alerts = all_alerts()
open_alerts = [alert for alert in alerts if alert.acknowledged_at is None]

summary = st.columns(5)
summary[0].metric("Open", len(open_alerts))
summary[1].metric("Critical", sum(a.priority == AlertPriority.CRITICAL for a in open_alerts))
summary[2].metric("High", sum(a.priority == AlertPriority.HIGH for a in open_alerts))
summary[3].metric("Synthetic received", st.session_state.demo_alert_cursor)
summary[4].metric("Actions recorded", len(st.session_state.alert_activity))

filters = st.columns(3)
priority_filter = filters[0].multiselect(
    "Priority",
    [str(priority) for priority in AlertPriority],
    default=[str(priority) for priority in AlertPriority],
)
type_filter = filters[1].multiselect(
    "Event type",
    sorted({alert.event_type for alert in alerts}),
    default=sorted({alert.event_type for alert in alerts}),
)
show_closed = filters[2].toggle("Show acknowledged", value=False)

visible = [
    alert
    for alert in alerts
    if str(alert.priority) in priority_filter
    and alert.event_type in type_filter
    and (show_closed or alert.acknowledged_at is None)
]
st.caption(f"Showing {len(visible)} alert(s), highest priority first.")

for alert in visible:
    with st.container(border=True):
        heading, meta = st.columns([4, 1])
        heading.markdown(
            f"{severity_chip(str(alert.priority))} &nbsp; "
            f"{'`SYNTHETIC LIVE`' if alert.synthetic else '`SOURCE DATA`'}",
            unsafe_allow_html=True,
        )
        heading.markdown(f"### {alert.event_type.replace('_', ' ').title()}")
        meta.metric("Priority score", alert.priority_score)
        facts = st.columns(4)
        facts[0].markdown(f"**Trip**  \n`{alert.trip_id}`")
        facts[1].markdown(f"**Business unit**  \n{alert.business_unit}")
        facts[2].markdown(f"**Office**  \n{alert.office or '—'}")
        facts[3].markdown(f"**Severity / state**  \n{alert.severity} · {alert.state}")
        st.caption(
            f"Started {alert.start_time} · vendor {alert.vendor or 'unknown'} · "
            f"source {alert.source or 'unknown'}"
        )
        buttons = st.columns(4)
        if "ACKNOWLEDGE" in alert.allowed_actions and buttons[0].button(
            "Acknowledge", key=f"ack-{alert.event_id}", width="stretch"
        ):
            enqueue(
                build_live_alert_action(alert, ActionType.ACKNOWLEDGE_ALERT),
                alert,
                "Alert acknowledgement awaiting approval",
            )
            st.rerun()
        if "ESCALATE" in alert.allowed_actions and buttons[1].button(
            "Escalate", key=f"esc-{alert.event_id}", width="stretch"
        ):
            escalate_alert(alert)
        if "CALL_DRIVER" in alert.allowed_actions and buttons[2].button(
            "Call driver", key=f"driver-{alert.event_id}", width="stretch"
        ):
            call_contact(alert, "driver")
        if "CALL_EMPLOYEE" in alert.allowed_actions and buttons[3].button(
            "Call employee", key=f"employee-{alert.event_id}", width="stretch"
        ):
            call_contact(alert, "employee")

st.divider()
st.markdown("#### Session activity")
if not st.session_state.alert_activity:
    st.caption("No alert action has been taken yet.")
for activity in reversed(st.session_state.alert_activity):
    st.markdown(
        f"`{activity.timestamp:%H:%M:%S}` · **{activity.action}** · "
        f"{activity.event_id} · {activity.detail}"
    )
