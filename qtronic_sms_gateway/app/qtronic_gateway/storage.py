"""Persistent outbox, identity, and audit history for the gateway."""

from __future__ import annotations

import asyncio
import csv
from io import StringIO
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from time import time
from typing import Any
from uuid import uuid4

from .config import HistoryConfig
from .validation import HistoryQuery


TERMINAL_OUTBOX_STATES = {"sent", "failed", "unknown", "canceled"}
RECOVERABLE_OUTBOX_STATES = {"accepted", "retry"}
_PHONE_KEYS = {
    "phone",
    "recipient",
    "recipients",
    "sender",
    "caller",
    "sender_normalized",
    "caller_normalized",
}
_MESSAGE_KEYS = {
    "message",
    "message_search",
    "message_segments",
    "outgoing_message",
    "sms_message",
}
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d ()-]{3,}\d)(?!\w)")


class PersistentStore:
    """Small SQLite store; public operations are safe to call from the event loop."""

    def __init__(
        self,
        history: HistoryConfig,
        path: str | Path | None = None,
    ) -> None:
        state_dir = Path(os.environ.get("QTRONIC_STATE_DIR", "/data"))
        self.path = Path(path or state_dir / "qtronic_gateway.sqlite3")
        self.history_config = history
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = sqlite3.connect(self.path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS history_timestamp_idx
                    ON history(timestamp DESC);
                CREATE INDEX IF NOT EXISTS history_type_timestamp_idx
                    ON history(type, timestamp DESC);
                CREATE TABLE IF NOT EXISTS outbox (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 1,
                    next_attempt_at REAL NOT NULL,
                    idempotency_key TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT,
                    result_json TEXT,
                    UNIQUE(kind, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS outbox_pending_idx
                    ON outbox(status, next_attempt_at, created_at);
                """
            )
            connection.execute(
                "UPDATE outbox SET status='unknown', updated_at=?, "
                "last_error=COALESCE(last_error, 'Interrupted while delivery outcome was uncertain') "
                "WHERE status='sending' AND kind='sms'",
                (time(),),
            )
            connection.execute(
                "UPDATE outbox SET status='accepted', updated_at=? "
                "WHERE status='sending' AND kind!='sms'",
                (time(),),
            )
            self._redact_terminal_outbox_sync(connection)
            self._prune_outbox_sync(connection)
            connection.commit()
            self._connection = connection
            self._metadata_set_default_sync("gateway_uuid", str(uuid4()))

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Persistent store is not initialized.")
        return self._connection

    def _metadata_set_default_sync(self, key: str, value: str) -> str:
        connection = self._db()
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
            (key, value),
        )
        connection.commit()
        row = connection.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        return str(row["value"])

    async def metadata_get(self, key: str) -> str | None:
        return await asyncio.to_thread(self._metadata_get_sync, key)

    def _metadata_get_sync(self, key: str) -> str | None:
        with self._lock:
            row = (
                self._db()
                .execute("SELECT value FROM metadata WHERE key=?", (key,))
                .fetchone()
            )
            return None if row is None else str(row["value"])

    async def metadata_set(self, key: str, value: str) -> None:
        await asyncio.to_thread(self._metadata_set_sync, key, value)

    def _metadata_set_sync(self, key: str, value: str) -> None:
        with self._lock:
            self._db().execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._db().commit()

    async def gateway_uuid(self) -> str:
        value = await self.metadata_get("gateway_uuid")
        if value is None:
            return await asyncio.to_thread(
                self._metadata_set_default_sync, "gateway_uuid", str(uuid4())
            )
        return value

    async def append_history(self, event: dict[str, Any]) -> None:
        if not self.history_config.enabled:
            return
        await asyncio.to_thread(self._append_history_sync, event)

    def _append_history_sync(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "unknown")[:64]
        timestamp = float(event.get("timestamp") or time())
        payload = self._privacy_payload(event)
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            connection = self._db()
            connection.execute(
                "INSERT INTO history(timestamp, type, payload_json) VALUES (?, ?, ?)",
                (timestamp, event_type, serialized),
            )
            cutoff = time() - self.history_config.retention_days * 86400
            connection.execute("DELETE FROM history WHERE timestamp < ?", (cutoff,))
            connection.execute(
                "DELETE FROM history WHERE id IN ("
                "SELECT id FROM history ORDER BY timestamp DESC, id DESC LIMIT -1 OFFSET ?"
                ")",
                (self.history_config.max_entries,),
            )
            connection.commit()

    def _privacy_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        mode = self.history_config.privacy

        def transform(value: Any, key: str | None = None) -> Any:
            if key in _MESSAGE_KEYS:
                if mode == "metadata":
                    return None
                if mode == "masked":
                    return self._mask_message(value)
            if isinstance(value, dict):
                return {
                    str(child_key): transform(child_value, str(child_key))
                    for child_key, child_value in value.items()
                    if not (mode == "metadata" and str(child_key) in _MESSAGE_KEYS)
                }
            if isinstance(value, (list, tuple)):
                return [transform(item, key) for item in value]
            if mode in {"masked", "metadata"} and isinstance(value, str):
                if key in _PHONE_KEYS:
                    return self._mask_phone(value)
                return _PHONE_RE.sub(self._mask_phone_match, value)
            return value

        return transform(dict(event))

    @staticmethod
    def _mask_message(value: Any) -> str:
        if isinstance(value, str):
            if value.startswith("<masked message"):
                return value
            characters = len(value)
            parts = 1
        elif isinstance(value, (list, tuple)):
            characters = sum(len(item) for item in value if isinstance(item, str))
            parts = len(value)
        else:
            return "<masked message>"
        suffix = f", {parts} parts" if parts > 1 else ""
        return f"<masked message: {characters} characters{suffix}>"

    @staticmethod
    def _mask_phone(value: str) -> str:
        if "*" in value and len(re.sub(r"\D", "", value)) <= 4:
            return value
        digits = re.sub(r"\D", "", value)
        if len(digits) < 5:
            return "***"
        prefix = "+" if value.strip().startswith("+") else ""
        return prefix + "*" * max(3, len(digits) - 4) + digits[-4:]

    @classmethod
    def _mask_phone_match(cls, match: re.Match[str]) -> str:
        value = match.group(0)
        # Avoid turning ISO dates and short numeric identifiers into apparent phones.
        if not value.lstrip().startswith("+") and len(re.sub(r"\D", "", value)) < 9:
            return value
        return cls._mask_phone(value)

    async def query_history(self, query: HistoryQuery) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._query_history_sync, query)

    def _query_history_sync(self, query: HistoryQuery) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if query.event_type:
            if "*" in query.event_type:
                clauses.append("type LIKE ? ESCAPE '\\'")
                values.append(
                    query.event_type.replace("%", "\\%")
                    .replace("_", "\\_")
                    .replace("*", "%")
                )
            else:
                clauses.append("type = ?")
                values.append(query.event_type)
        if query.since is not None:
            clauses.append("timestamp >= ?")
            values.append(query.since)
        if query.until is not None:
            clauses.append("timestamp <= ?")
            values.append(query.until)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.extend((query.limit, query.offset))
        sql = (
            "SELECT id, timestamp, type, payload_json FROM history"
            + where
            + " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
        )
        with self._lock:
            rows = self._db().execute(sql, values).fetchall()
        return [self._history_row(row) for row in rows]

    @staticmethod
    def _history_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        return {
            "id": int(row["id"]),
            "timestamp": float(row["timestamp"]),
            "type": str(row["type"]),
            "payload": payload,
        }

    async def export_history(
        self, query: HistoryQuery, export_format: str
    ) -> tuple[str, str]:
        rows = await self.query_history(query)
        if export_format == "json":
            return json.dumps(rows, ensure_ascii=False, indent=2), "application/json"
        if export_format != "csv":
            raise ValueError("History export format must be 'json' or 'csv'.")
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("id", "timestamp", "type", "payload"))
        for row in rows:
            writer.writerow(
                (
                    row["id"],
                    row["timestamp"],
                    self._csv_safe(row["type"]),
                    self._csv_safe(json.dumps(row["payload"], ensure_ascii=False)),
                )
            )
        return output.getvalue(), "text/csv; charset=utf-8"

    @staticmethod
    def _csv_safe(value: Any) -> Any:
        if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
            return "'" + value
        return value

    async def enqueue_outbox(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        max_attempts: int,
        idempotency_key: str | None = None,
        job_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        return await asyncio.to_thread(
            self._enqueue_outbox_sync,
            kind,
            payload,
            max_attempts,
            idempotency_key,
            job_id,
        )

    def _enqueue_outbox_sync(
        self,
        kind: str,
        payload: dict[str, Any],
        max_attempts: int,
        idempotency_key: str | None,
        job_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        now = time()
        identifier = job_id or uuid4().hex
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            connection = self._db()
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM outbox WHERE kind=? AND idempotency_key=?",
                    (kind, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return self._outbox_row(existing), False
            connection.execute(
                "INSERT INTO outbox(job_id, kind, status, payload_json, attempts, "
                "max_attempts, next_attempt_at, idempotency_key, created_at, updated_at) "
                "VALUES (?, ?, 'accepted', ?, 0, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    kind,
                    serialized,
                    max(1, int(max_attempts)),
                    now,
                    idempotency_key,
                    now,
                    now,
                ),
            )
            self._prune_outbox_sync(connection)
            connection.commit()
            row = connection.execute(
                "SELECT * FROM outbox WHERE job_id=?", (identifier,)
            ).fetchone()
            return self._outbox_row(row), True

    async def claim_next_outbox(
        self, *, kind: str | None = None
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._claim_next_outbox_sync, kind)

    def _claim_next_outbox_sync(self, kind: str | None) -> dict[str, Any] | None:
        now = time()
        with self._lock:
            connection = self._db()
            connection.execute("BEGIN IMMEDIATE")
            if kind is None:
                row = connection.execute(
                    "SELECT * FROM outbox WHERE status IN ('accepted','retry') "
                    "AND next_attempt_at <= ? ORDER BY created_at, rowid LIMIT 1",
                    (now,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM outbox WHERE kind=? AND status IN ('accepted','retry') "
                    "AND next_attempt_at <= ? ORDER BY created_at, rowid LIMIT 1",
                    (kind, now),
                ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                "UPDATE outbox SET status='sending', attempts=attempts+1, updated_at=? "
                "WHERE job_id=?",
                (now, row["job_id"]),
            )
            connection.commit()
            claimed = connection.execute(
                "SELECT * FROM outbox WHERE job_id=?", (row["job_id"],)
            ).fetchone()
            return self._outbox_row(claimed)

    async def update_outbox(
        self,
        job_id: str,
        status: str,
        *,
        last_error: str | None = None,
        result: dict[str, Any] | None = None,
        next_attempt_at: float | None = None,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._update_outbox_sync,
            job_id,
            status,
            last_error,
            result,
            next_attempt_at,
        )

    async def update_outbox_payload(
        self, job_id: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._update_outbox_payload_sync, job_id, payload
        )

    def _update_outbox_payload_sync(
        self, job_id: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            connection = self._db()
            current = connection.execute(
                "SELECT * FROM outbox WHERE job_id=?", (job_id,)
            ).fetchone()
            if current is None:
                return None
            if current["status"] in TERMINAL_OUTBOX_STATES:
                return self._outbox_row(current)
            connection.execute(
                "UPDATE outbox SET payload_json=?, updated_at=? WHERE job_id=?",
                (serialized, time(), job_id),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM outbox WHERE job_id=?", (job_id,)
            ).fetchone()
            return self._outbox_row(row)

    def _update_outbox_sync(
        self,
        job_id: str,
        status: str,
        last_error: str | None,
        result: dict[str, Any] | None,
        next_attempt_at: float | None,
    ) -> dict[str, Any] | None:
        now = time()
        with self._lock:
            connection = self._db()
            current_row = connection.execute(
                "SELECT * FROM outbox WHERE job_id=?", (job_id,)
            ).fetchone()
            if current_row is None:
                return None
            if current_row["status"] == "canceled" and status != "canceled":
                return self._outbox_row(current_row)
            redacted_payload: str | None = None
            redacted_result: dict[str, Any] | None = result
            if (
                status in TERMINAL_OUTBOX_STATES
                and self.history_config.privacy != "full"
            ):
                existing = connection.execute(
                    "SELECT payload_json FROM outbox WHERE job_id=?", (job_id,)
                ).fetchone()
                if existing is not None:
                    try:
                        decoded = json.loads(existing["payload_json"])
                    except (TypeError, json.JSONDecodeError):
                        decoded = {}
                    redacted_payload = json.dumps(
                        self._privacy_payload(decoded),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                if result is not None:
                    redacted_result = self._privacy_payload(result)
            connection.execute(
                "UPDATE outbox SET status=?, last_error=?, result_json=?, "
                "payload_json=COALESCE(?, payload_json), "
                "next_attempt_at=COALESCE(?, next_attempt_at), updated_at=? WHERE job_id=?",
                (
                    status,
                    last_error,
                    None
                    if redacted_result is None
                    else json.dumps(redacted_result, ensure_ascii=False),
                    redacted_payload,
                    next_attempt_at,
                    now,
                    job_id,
                ),
            )
            self._prune_outbox_sync(connection)
            connection.commit()
            row = connection.execute(
                "SELECT * FROM outbox WHERE job_id=?", (job_id,)
            ).fetchone()
            return None if row is None else self._outbox_row(row)

    def _prune_outbox_sync(self, connection: sqlite3.Connection) -> None:
        cutoff = time() - self.history_config.retention_days * 86400
        terminal = "('sent','failed','unknown','canceled')"
        connection.execute(
            f"DELETE FROM outbox WHERE status IN {terminal} AND updated_at < ?",
            (cutoff,),
        )
        connection.execute(
            f"DELETE FROM outbox WHERE job_id IN ("
            f"SELECT job_id FROM outbox WHERE status IN {terminal} "
            "ORDER BY updated_at DESC LIMIT -1 OFFSET ?)",
            (self.history_config.max_entries,),
        )

    def _redact_terminal_outbox_sync(self, connection: sqlite3.Connection) -> None:
        if self.history_config.privacy == "full":
            return
        rows = connection.execute(
            "SELECT job_id, payload_json, result_json FROM outbox "
            "WHERE status IN ('sent','failed','unknown','canceled')"
        ).fetchall()
        for row in rows:
            try:
                decoded = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                decoded = {}
            try:
                decoded_result = json.loads(row["result_json"])
            except (TypeError, json.JSONDecodeError):
                decoded_result = None
            connection.execute(
                "UPDATE outbox SET payload_json=?, result_json=? WHERE job_id=?",
                (
                    json.dumps(
                        self._privacy_payload(decoded),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    None
                    if not isinstance(decoded_result, dict)
                    else json.dumps(
                        self._privacy_payload(decoded_result),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    row["job_id"],
                ),
            )

    async def get_outbox(self, job_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_outbox_sync, job_id)

    def _get_outbox_sync(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = (
                self._db()
                .execute("SELECT * FROM outbox WHERE job_id=?", (job_id,))
                .fetchone()
            )
            return None if row is None else self._outbox_row(row)

    async def list_outbox(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_outbox_sync, limit)

    def _list_outbox_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT * FROM outbox ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(1000, int(limit))),),
                )
                .fetchall()
            )
            return [self._outbox_row(row) for row in rows]

    async def cancel_outbox(
        self, job_id: str | None = None, *, kind: str | None = None
    ) -> int:
        return await asyncio.to_thread(self._cancel_outbox_sync, job_id, kind)

    def _cancel_outbox_sync(self, job_id: str | None, kind: str | None) -> int:
        with self._lock:
            connection = self._db()
            if job_id:
                if kind:
                    cursor = connection.execute(
                        "UPDATE outbox SET status='canceled', updated_at=? "
                        "WHERE job_id=? AND kind=? "
                        "AND status NOT IN ('sent','failed','unknown','canceled')",
                        (time(), job_id, kind),
                    )
                else:
                    cursor = connection.execute(
                        "UPDATE outbox SET status='canceled', updated_at=? "
                        "WHERE job_id=? AND status NOT IN ('sent','failed','unknown','canceled')",
                        (time(), job_id),
                    )
            else:
                if kind:
                    cursor = connection.execute(
                        "UPDATE outbox SET status='canceled', updated_at=? "
                        "WHERE kind=? AND status NOT IN ('sent','failed','unknown','canceled')",
                        (time(), kind),
                    )
                else:
                    cursor = connection.execute(
                        "UPDATE outbox SET status='canceled', updated_at=? "
                        "WHERE status NOT IN ('sent','failed','unknown','canceled')",
                        (time(),),
                    )
            self._redact_terminal_outbox_sync(connection)
            self._prune_outbox_sync(connection)
            connection.commit()
            return max(0, int(cursor.rowcount))

    async def outbox_depth(self) -> int:
        return await asyncio.to_thread(self._outbox_depth_sync)

    def _outbox_depth_sync(self) -> int:
        with self._lock:
            row = (
                self._db()
                .execute(
                    "SELECT COUNT(*) AS count FROM outbox "
                    "WHERE status NOT IN ('sent','failed','unknown','canceled')"
                )
                .fetchone()
            )
            return int(row["count"])

    @staticmethod
    def _outbox_row(row: sqlite3.Row) -> dict[str, Any]:
        def decode(value: Any) -> Any:
            if value is None:
                return None
            try:
                return json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return None

        return {
            "job_id": str(row["job_id"]),
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "payload": decode(row["payload_json"]) or {},
            "attempts": int(row["attempts"]),
            "max_attempts": int(row["max_attempts"]),
            "next_attempt_at": float(row["next_attempt_at"]),
            "idempotency_key": row["idempotency_key"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "last_error": row["last_error"],
            "result": decode(row["result_json"]),
        }
