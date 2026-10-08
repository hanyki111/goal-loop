"""Append-only persistence primitives for the Goal Loop SQLite MVP.

This module deliberately knows no GoalLoop domain transitions.  It owns the
durable journal/receipt transaction boundary; the domain layer will supply
typed events and rebuild its current-state projections from them.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import hashlib
import uuid
from pathlib import Path
import sqlite3
from typing import Iterator


class CommandConflictError(ValueError):
    """A command id or idempotency fingerprint was reused incompatibly."""


class JournalConflictError(RuntimeError):
    """Another writer appended to the journal after this command read it."""


@dataclass(frozen=True)
class JournalEvent:
    event_id: str
    event_type: str
    actor_id: str
    subject_kind: str
    subject_id: str
    payload: dict[str, object]
    schema_version: int = 1


@dataclass(frozen=True)
class CommandReceipt:
    command_id: str
    command_type: str
    request_fingerprint: str
    status: str
    first_sequence: int | None
    last_sequence: int | None
    result: dict[str, object] | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class ActivityRecord:
    command_id: str
    command_type: str
    status: str
    recorded_at: str
    event_type: str | None = None
    sequence: int | None = None
    actor_id: str | None = None
    subject_kind: str | None = None
    subject_id: str | None = None
    payload: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RuntimeOutboxRequest:
    request_id: str
    attempt_id: str
    request_kind: str
    payload: dict[str, object]
    status: str
    claimed_by: str | None = None


class SqliteEventRepository:
    """SQLite journal and command-receipt store with all-or-nothing appends."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def backup_to(self, destination: str | Path) -> None:
        target_path = Path(destination)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(target_path)
        source = self._connect()
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def integrity_diagnostics(self) -> tuple[str, ...]:
        connection = self._connect()
        try:
            return tuple(row[0] for row in connection.execute("PRAGMA integrity_check"))
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO schema_metadata(key, value) VALUES ('schema_version', '1');
                CREATE TABLE IF NOT EXISTS journal_snapshots (snapshot_id TEXT PRIMARY KEY, journal_sequence INTEGER NOT NULL, journal_digest TEXT NOT NULL, recorded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS projection_checkpoint (name TEXT PRIMARY KEY, last_sequence INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS event_journal (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    command_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_schema_version INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    subject_kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS event_journal_command_idx
                    ON event_journal(command_id, sequence);
                CREATE TABLE IF NOT EXISTS command_receipts (
                    command_id TEXT PRIMARY KEY,
                    command_type TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('committed', 'rejected')),
                    first_sequence INTEGER,
                    last_sequence INTEGER,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_outbox (
                    request_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL UNIQUE,
                    request_kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'claimed', 'completed')),
                    claimed_by TEXT,
                    requested_at TEXT NOT NULL,
                    claimed_at TEXT,
                    completed_at TEXT
                );
                """
            )
            connection.commit()
            version = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()[0]
            if version == "1":
                columns = {row[1] for row in connection.execute("PRAGMA table_info(journal_snapshots)")}
                if "payload_json" not in columns:
                    connection.execute("ALTER TABLE journal_snapshots ADD COLUMN payload_json TEXT")
                connection.execute("UPDATE schema_metadata SET value = '2' WHERE key = 'schema_version'")
                connection.commit()
                version = "2"
            if version == "2":
                connection.execute("UPDATE schema_metadata SET value = '3' WHERE key = 'schema_version'")
                connection.commit()
                version = "3"
            if version != "3":
                raise RuntimeError(f"Unsupported SQLite schema version: {version}")
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _journal_fingerprint(self) -> tuple[int, str]:
        events = self.events()
        digest = hashlib.sha256(json.dumps(
            [(event.event_id, event.event_type, event.payload) for event in events],
            sort_keys=True,
        ).encode()).hexdigest()
        return len(events), digest

    def create_snapshot_metadata(self) -> dict[str, object]:
        sequence, digest = self._journal_fingerprint()
        payload = [event.__dict__ for event in self.events()]
        snapshot = {"id": f"snapshot-{uuid.uuid4().hex}", "sequence": sequence, "digest": digest, "payload": payload}
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO journal_snapshots VALUES (?, ?, ?, ?, ?)",
                (snapshot["id"], sequence, digest, datetime.now(UTC).isoformat(), json.dumps(payload, sort_keys=True)),
            )
        return snapshot

    def snapshot_is_current(self, snapshot_id: str) -> bool:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT journal_sequence, journal_digest FROM journal_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return False
        sequence, digest = self._journal_fingerprint()
        return row["journal_sequence"] == sequence and row["journal_digest"] == digest

    def restore_snapshot_events(self, snapshot_id: str) -> list[JournalEvent]:
        if not self.snapshot_is_current(snapshot_id):
            raise RuntimeError("Snapshot is stale or does not match the journal.")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT payload_json FROM journal_snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
        finally:
            connection.close()
        return [JournalEvent(**event) for event in json.loads(row["payload_json"])]

    def latest_valid_snapshot_events(self) -> list[JournalEvent] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT payload_json FROM journal_snapshots ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        events = [JournalEvent(**event) for event in json.loads(row["payload_json"])]
        current = self.events()
        if current[:len(events)] != events:
            return None
        return events

    def journal_last_sequence(self) -> int:
        connection = self._connect()
        try:
            return connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM event_journal").fetchone()[0]
        finally:
            connection.close()

    def projection_checkpoint(self) -> int:
        connection = self._connect()
        try:
            row = connection.execute("SELECT last_sequence FROM projection_checkpoint WHERE name = 'goal_loop'").fetchone()
            return row[0] if row else 0
        finally:
            connection.close()

    def mark_projection_checkpoint(self, sequence: int) -> None:
        with self._transaction() as connection:
            connection.execute("INSERT INTO projection_checkpoint(name, last_sequence) VALUES ('goal_loop', ?) ON CONFLICT(name) DO UPDATE SET last_sequence=excluded.last_sequence", (sequence,))

    def projection_checkpoint_is_current(self) -> bool:
        return self.projection_checkpoint() == self.journal_last_sequence()

    def schema_version(self) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeError("SQLite schema version is missing.")
        return int(row["value"])

    def commit(
        self,
        *,
        command_id: str,
        command_type: str,
        request_fingerprint: str,
        events: tuple[JournalEvent, ...],
        result: dict[str, object],
        runtime_requests: tuple[RuntimeOutboxRequest, ...] = (),
        expected_last_sequence: int | None = None,
    ) -> CommandReceipt:
        """Append events and a successful receipt, or return a matching receipt.

        With ``expected_last_sequence`` the append succeeds only if no other writer appended since
        the command derived its events, because replay re-executes commands in journal order.
        """
        with self._transaction() as connection:
            existing = self._find_receipt(connection, command_id)
            if existing:
                return self._matching_receipt(existing, request_fingerprint)
            if expected_last_sequence is not None:
                current = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM event_journal").fetchone()[0]
                if current != expected_last_sequence:
                    raise JournalConflictError(f"Journal advanced from {expected_last_sequence} to {current}.")
            if not events:
                raise ValueError("A committed command must append at least one domain event.")
            now = datetime.now(UTC).isoformat()
            first_sequence: int | None = None
            last_sequence: int | None = None
            for event in events:
                cursor = connection.execute(
                    """INSERT INTO event_journal
                    (event_id, command_id, event_type, event_schema_version, recorded_at,
                     actor_id, subject_kind, subject_id, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event.event_id, command_id, event.event_type, event.schema_version, now,
                     event.actor_id, event.subject_kind, event.subject_id,
                     json.dumps(event.payload, sort_keys=True)),
                )
                first_sequence = first_sequence or cursor.lastrowid
                last_sequence = cursor.lastrowid
            connection.execute(
                """INSERT INTO command_receipts
                (command_id, command_type, request_fingerprint, status, first_sequence,
                 last_sequence, result_json, error_code, error_message, recorded_at)
                VALUES (?, ?, ?, 'committed', ?, ?, ?, NULL, NULL, ?)""",
                (command_id, command_type, request_fingerprint, first_sequence, last_sequence,
                 json.dumps(result, sort_keys=True), now),
            )
            for request in runtime_requests:
                connection.execute(
                    "INSERT INTO runtime_outbox(request_id, attempt_id, request_kind, payload_json, status, claimed_by, requested_at) VALUES (?, ?, ?, ?, 'pending', NULL, ?)",
                    (request.request_id, request.attempt_id, request.request_kind, json.dumps(request.payload, sort_keys=True), now),
                )
            return CommandReceipt(command_id, command_type, request_fingerprint, "committed",
                                  first_sequence, last_sequence, result, None, None)

    def pending_runtime_requests(self) -> list[RuntimeOutboxRequest]:
        connection = self._connect()
        try: rows = connection.execute("SELECT * FROM runtime_outbox WHERE status='pending' ORDER BY requested_at").fetchall()
        finally: connection.close()
        return [self._outbox(row) for row in rows]

    def runtime_requests(self) -> list[RuntimeOutboxRequest]:
        """Read every Runtime request in any state, oldest first."""
        connection = self._connect()
        try: rows = connection.execute("SELECT * FROM runtime_outbox ORDER BY requested_at").fetchall()
        finally: connection.close()
        return [self._outbox(row) for row in rows]

    def runtime_request(self, request_id: str) -> RuntimeOutboxRequest | None:
        """Read one Runtime request regardless of pending/claimed/completed state."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM runtime_outbox WHERE request_id=?", (request_id,)
            ).fetchone()
        finally:
            connection.close()
        return self._outbox(row) if row is not None else None

    def claim_runtime_request(self, request_id: str, worker_id: str) -> RuntimeOutboxRequest | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runtime_outbox WHERE request_id=? AND status='pending'", (request_id,)).fetchone()
            if row is None: return None
            connection.execute("UPDATE runtime_outbox SET status='claimed', claimed_by=?, claimed_at=? WHERE request_id=?", (worker_id, datetime.now(UTC).isoformat(), request_id))
            return self._outbox(connection.execute("SELECT * FROM runtime_outbox WHERE request_id=?", (request_id,)).fetchone())

    def acknowledge_runtime_request(self, request_id: str, worker_id: str) -> RuntimeOutboxRequest:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runtime_outbox WHERE request_id=? AND status='claimed' AND claimed_by=?", (request_id, worker_id)).fetchone()
            if row is None: raise ValueError("Runtime request is not claimed by this worker.")
            connection.execute("UPDATE runtime_outbox SET status='completed', completed_at=? WHERE request_id=?", (datetime.now(UTC).isoformat(), request_id))
            return self._outbox(connection.execute("SELECT * FROM runtime_outbox WHERE request_id=?", (request_id,)).fetchone())

    def requeue_runtime_request(self, request_id: str, worker_id: str) -> RuntimeOutboxRequest:
        """Return a worker-owned claim to pending without changing the Attempt."""
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runtime_outbox WHERE request_id=? AND status='claimed' AND claimed_by=?", (request_id, worker_id)).fetchone()
            if row is None:
                raise ValueError("Runtime request is not claimed by this worker.")
            connection.execute("UPDATE runtime_outbox SET status='pending', claimed_by=NULL, claimed_at=NULL WHERE request_id=?", (request_id,))
            return self._outbox(connection.execute("SELECT * FROM runtime_outbox WHERE request_id=?", (request_id,)).fetchone())

    @staticmethod
    def _outbox(row: sqlite3.Row) -> RuntimeOutboxRequest:
        return RuntimeOutboxRequest(row["request_id"], row["attempt_id"], row["request_kind"], json.loads(row["payload_json"]), row["status"], row["claimed_by"])

    def reject(self, *, command_id: str, command_type: str, request_fingerprint: str,
               error_code: str, error_message: str) -> CommandReceipt:
        with self._transaction() as connection:
            existing = self._find_receipt(connection, command_id)
            if existing:
                return self._matching_receipt(existing, request_fingerprint)
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """INSERT INTO command_receipts
                (command_id, command_type, request_fingerprint, status, first_sequence,
                 last_sequence, result_json, error_code, error_message, recorded_at)
                VALUES (?, ?, ?, 'rejected', NULL, NULL, NULL, ?, ?, ?)""",
                (command_id, command_type, request_fingerprint, error_code, error_message, now),
            )
            return CommandReceipt(command_id, command_type, request_fingerprint, "rejected",
                                  None, None, None, error_code, error_message)

    def receipt(self, command_id: str) -> CommandReceipt | None:
        connection = self._connect()
        try:
            return self._find_receipt(connection, command_id)
        finally:
            connection.close()

    def events(self) -> list[JournalEvent]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT * FROM event_journal ORDER BY sequence").fetchall()
        finally:
            connection.close()
        return [JournalEvent(row["event_id"], row["event_type"], row["actor_id"],
                             row["subject_kind"], row["subject_id"],
                             json.loads(row["payload_json"]), row["event_schema_version"])
                for row in rows]

    def activity_records(self) -> list[ActivityRecord]:
        """Read journal facts and rejected receipts for a human-facing projection."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT e.command_id, r.command_type, r.status, e.recorded_at,
                       e.event_type, e.sequence, e.actor_id, e.subject_kind,
                       e.subject_id, e.payload_json, NULL AS error_code,
                       NULL AS error_message
                FROM event_journal e
                JOIN command_receipts r ON r.command_id = e.command_id
                UNION ALL
                SELECT r.command_id, r.command_type, r.status, r.recorded_at,
                       NULL, NULL, NULL, NULL, NULL, NULL,
                       r.error_code, r.error_message
                FROM command_receipts r
                WHERE r.status = 'rejected'
                ORDER BY recorded_at, sequence
                """
            ).fetchall()
        finally:
            connection.close()
        return [
            ActivityRecord(
                command_id=row["command_id"],
                command_type=row["command_type"],
                status=row["status"],
                recorded_at=row["recorded_at"],
                event_type=row["event_type"],
                sequence=row["sequence"],
                actor_id=row["actor_id"],
                subject_kind=row["subject_kind"],
                subject_id=row["subject_id"],
                payload=json.loads(row["payload_json"]) if row["payload_json"] else None,
                error_code=row["error_code"],
                error_message=row["error_message"],
            )
            for row in rows
        ]

    @staticmethod
    def _find_receipt(connection: sqlite3.Connection, command_id: str) -> CommandReceipt | None:
        row = connection.execute(
            "SELECT * FROM command_receipts WHERE command_id = ?", (command_id,)
        ).fetchone()
        if row is None:
            return None
        return CommandReceipt(row["command_id"], row["command_type"],
                              row["request_fingerprint"], row["status"],
                              row["first_sequence"], row["last_sequence"],
                              json.loads(row["result_json"]) if row["result_json"] else None,
                              row["error_code"], row["error_message"])

    @staticmethod
    def _matching_receipt(existing: CommandReceipt, fingerprint: str) -> CommandReceipt:
        if existing.request_fingerprint != fingerprint:
            raise CommandConflictError("Command id conflicts with a different request.")
        return existing
