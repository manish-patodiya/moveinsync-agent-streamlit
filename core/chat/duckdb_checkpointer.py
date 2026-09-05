"""LangGraph checkpointer backed by a persistent DuckDB file.

The published `langgraph-checkpoint-duckdb` package pins an older
`langgraph-checkpoint`, so this implements the installed interface directly.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import duckdb
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

CHECKPOINT_COLUMNS = (
    "checkpoint_id, parent_checkpoint_id, checkpoint_type, checkpoint, "
    "metadata_type, metadata"
)


class DuckDBSaver(BaseCheckpointSaver[str]):
    """Persist conversation checkpoints so context survives an app restart."""

    def __init__(self, database_path: str | Path, *, serde=None) -> None:
        super().__init__(serde=serde)
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(self.database_path)
        # ponytail: one process-wide lock. DuckDB connections are not thread-safe and
        # Streamlit serves each browser session on its own thread. Upgrade path is a
        # per-thread cursor pool if checkpoint writes ever become a bottleneck.
        self._lock = threading.Lock()
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self.conn.execute("CREATE SEQUENCE IF NOT EXISTS chat_checkpoint_seq START 1")
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_checkpoints (
                    thread_id VARCHAR NOT NULL,
                    checkpoint_ns VARCHAR NOT NULL,
                    checkpoint_id VARCHAR NOT NULL,
                    parent_checkpoint_id VARCHAR,
                    seq BIGINT NOT NULL,
                    checkpoint_type VARCHAR NOT NULL,
                    checkpoint BLOB NOT NULL,
                    metadata_type VARCHAR NOT NULL,
                    metadata BLOB NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_checkpoint_writes (
                    thread_id VARCHAR NOT NULL,
                    checkpoint_ns VARCHAR NOT NULL,
                    checkpoint_id VARCHAR NOT NULL,
                    task_id VARCHAR NOT NULL,
                    idx BIGINT NOT NULL,
                    channel VARCHAR NOT NULL,
                    task_path VARCHAR NOT NULL,
                    value_type VARCHAR NOT NULL,
                    value BLOB NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                )
                """
            )

    @staticmethod
    def _keys(config: RunnableConfig) -> tuple[str, str]:
        configurable = config["configurable"]
        return configurable["thread_id"], configurable.get("checkpoint_ns", "")

    def _pending_writes(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT task_id, channel, value_type, value
                FROM chat_checkpoint_writes
                WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                ORDER BY task_id, idx
                """,
                [thread_id, checkpoint_ns, checkpoint_id],
            ).fetchall()
        return [
            (task_id, channel, self.serde.loads_typed((value_type, bytes(value))))
            for task_id, channel, value_type, value in rows
        ]

    def _to_tuple(self, thread_id: str, checkpoint_ns: str, row: tuple) -> CheckpointTuple:
        checkpoint_id, parent_id, checkpoint_type, checkpoint, metadata_type, metadata = row

        def config_for(identifier: str) -> RunnableConfig:
            return {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": identifier,
                }
            }

        return CheckpointTuple(
            config=config_for(checkpoint_id),
            checkpoint=self.serde.loads_typed((checkpoint_type, bytes(checkpoint))),
            metadata=self.serde.loads_typed((metadata_type, bytes(metadata))),
            parent_config=config_for(parent_id) if parent_id else None,
            pending_writes=self._pending_writes(thread_id, checkpoint_ns, checkpoint_id),
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id, checkpoint_ns = self._keys(config)
        checkpoint_id = get_checkpoint_id(config)
        with self._lock:
            if checkpoint_id:
                row = self.conn.execute(
                    f"""SELECT {CHECKPOINT_COLUMNS} FROM chat_checkpoints
                        WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?""",
                    [thread_id, checkpoint_ns, checkpoint_id],
                ).fetchone()
            else:
                row = self.conn.execute(
                    f"""SELECT {CHECKPOINT_COLUMNS} FROM chat_checkpoints
                        WHERE thread_id = ? AND checkpoint_ns = ?
                        ORDER BY seq DESC LIMIT 1""",
                    [thread_id, checkpoint_ns],
                ).fetchone()
        return None if row is None else self._to_tuple(thread_id, checkpoint_ns, row)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return
        thread_id, checkpoint_ns = self._keys(config)
        clauses = ["thread_id = ?", "checkpoint_ns = ?"]
        params: list[Any] = [thread_id, checkpoint_ns]
        if before is not None and (before_id := get_checkpoint_id(before)):
            clauses.append(
                "seq < (SELECT seq FROM chat_checkpoints WHERE thread_id = ?"
                " AND checkpoint_ns = ? AND checkpoint_id = ?)"
            )
            params += [thread_id, checkpoint_ns, before_id]
        sql = (
            f"SELECT {CHECKPOINT_COLUMNS} FROM chat_checkpoints "
            f"WHERE {' AND '.join(clauses)} ORDER BY seq DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        for row in rows:
            candidate = self._to_tuple(thread_id, checkpoint_ns, row)
            if filter and not all(candidate.metadata.get(k) == v for k, v in filter.items()):
                continue
            yield candidate

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id, checkpoint_ns = self._keys(config)
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO chat_checkpoints
                (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, seq,
                 checkpoint_type, checkpoint, metadata_type, metadata)
                VALUES (?, ?, ?, ?, nextval('chat_checkpoint_seq'), ?, ?, ?, ?)
                """,
                [
                    thread_id,
                    checkpoint_ns,
                    checkpoint["id"],
                    config["configurable"].get("checkpoint_id"),
                    checkpoint_type,
                    checkpoint_blob,
                    metadata_type,
                    metadata_blob,
                ],
            )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, checkpoint_ns = self._keys(config)
        checkpoint_id = config["configurable"]["checkpoint_id"]
        for position, (channel, value) in enumerate(writes):
            idx = WRITES_IDX_MAP.get(channel, position)
            value_type, value_blob = self.serde.dumps_typed(value)
            with self._lock:
                # Positive indices are task-stable, so the first write of a retried task wins.
                if idx >= 0 and self.conn.execute(
                    """SELECT 1 FROM chat_checkpoint_writes
                       WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                         AND task_id = ? AND idx = ?""",
                    [thread_id, checkpoint_ns, checkpoint_id, task_id, idx],
                ).fetchone():
                    continue
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO chat_checkpoint_writes
                    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel,
                     task_path, value_type, value)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        task_id,
                        idx,
                        channel,
                        task_path,
                        value_type,
                        value_blob,
                    ],
                )

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM chat_checkpoints WHERE thread_id = ?", [thread_id])
            self.conn.execute(
                "DELETE FROM chat_checkpoint_writes WHERE thread_id = ?", [thread_id]
            )
