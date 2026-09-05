from __future__ import annotations

from core.chat.duckdb_checkpointer import DuckDBSaver
from core.models.action import Action, AuditEvent


class ActionStore:
    """Persist the unified action queue and audit in the chat checkpoint database."""

    def __init__(self, saver: DuckDBSaver) -> None:
        self.saver = saver
        with saver._lock:
            saver.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manager_actions (
                    action_id VARCHAR PRIMARY KEY,
                    created_at TIMESTAMP NOT NULL,
                    action_json VARCHAR NOT NULL
                )
                """
            )
            saver.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manager_action_audit (
                    event_id VARCHAR PRIMARY KEY,
                    action_id VARCHAR NOT NULL,
                    event_ts TIMESTAMP NOT NULL,
                    event_json VARCHAR NOT NULL
                )
                """
            )

    def save(self, action: Action) -> None:
        with self.saver._lock:
            self.saver.conn.execute(
                """
                INSERT OR REPLACE INTO manager_actions VALUES (?, ?, ?)
                """,
                [action.action_id, action.created_at, action.model_dump_json()],
            )

    def save_all(self, actions: list[Action]) -> None:
        for action in actions:
            self.save(action)

    def list(self) -> list[Action]:
        with self.saver._lock:
            rows = self.saver.conn.execute(
                "SELECT action_json FROM manager_actions ORDER BY created_at DESC"
            ).fetchall()
        return [Action.model_validate_json(row[0]) for row in rows]

    def record(self, event: AuditEvent) -> None:
        event_id = f"{event.action_id}:{event.timestamp.isoformat()}:{event.new_status}"
        with self.saver._lock:
            self.saver.conn.execute(
                "INSERT OR REPLACE INTO manager_action_audit VALUES (?, ?, ?, ?)",
                [event_id, event.action_id, event.timestamp, event.model_dump_json()],
            )

    def audit(self) -> list[AuditEvent]:
        with self.saver._lock:
            rows = self.saver.conn.execute(
                "SELECT event_json FROM manager_action_audit ORDER BY event_ts DESC"
            ).fetchall()
        return [AuditEvent.model_validate_json(row[0]) for row in rows]
