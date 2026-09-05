from core.act.action_store import ActionStore
from core.chat.duckdb_checkpointer import DuckDBSaver
from core.models.action import Action, ActionStatus, ActionType, AuditEvent, ManagerRole


def _action() -> Action:
    return Action(
        action_id="a1",
        issue_id="i1",
        action_type=ActionType.REQUEST_DRIVER_CALL,
        priority="HIGH",
        title="Call driver",
        rationale="Open safety event",
        owner_role=ManagerRole.SHIFT_MANAGER,
        requires_human_approval=True,
        call_script="Confirm status.",
        source="LIVE_ALERTS",
    )


def test_actions_and_enriched_audit_survive_reopen(tmp_path):
    path = tmp_path / "manager.duckdb"
    store = ActionStore(DuckDBSaver(path))
    store.save(_action())
    store.record(
        AuditEvent(
            action_id="a1",
            previous_status=ActionStatus.PROPOSED,
            new_status=ActionStatus.APPROVED,
            actor_role=ManagerRole.SHIFT_MANAGER,
            source="LIVE_ALERTS",
            note="Verified with shift desk",
        )
    )

    reopened = ActionStore(DuckDBSaver(path))
    assert reopened.list()[0].call_script == "Confirm status."
    event = reopened.audit()[0]
    assert event.actor_role == ManagerRole.SHIFT_MANAGER
    assert event.note == "Verified with shift desk"
