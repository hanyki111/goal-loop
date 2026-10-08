from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.repository import (
    CommandConflictError, JournalEvent, RuntimeOutboxRequest, SqliteEventRepository,
)


class SqliteEventRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "goal-loop.sqlite3"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def event(self, event_id: str = "event-1") -> JournalEvent:
        return JournalEvent(event_id, "AimCreated", "planner", "aim", "aim-1", {"description": "test"})

    def test_commit_survives_repository_restart_and_replays_receipt(self) -> None:
        first = SqliteEventRepository(self.path)
        receipt = first.commit(command_id="command-1", command_type="create_aim",
                               request_fingerprint="same", events=(self.event(),),
                               result={"aim_id": "aim-1"})
        second = SqliteEventRepository(self.path)
        replay = second.commit(command_id="command-1", command_type="create_aim",
                               request_fingerprint="same", events=(self.event(),),
                               result={"aim_id": "aim-1"})
        self.assertEqual(receipt, replay)
        self.assertEqual([event.event_id for event in second.events()], ["event-1"])

    def test_command_id_conflict_does_not_append_another_event(self) -> None:
        repository = SqliteEventRepository(self.path)
        repository.commit(command_id="command-1", command_type="create_aim",
                          request_fingerprint="first", events=(self.event(),), result={})
        with self.assertRaises(CommandConflictError):
            repository.commit(command_id="command-1", command_type="create_aim",
                              request_fingerprint="different", events=(self.event("event-2"),), result={})
        self.assertEqual([event.event_id for event in repository.events()], ["event-1"])

    def test_rejection_is_durable_without_domain_event(self) -> None:
        repository = SqliteEventRepository(self.path)
        rejected = repository.reject(command_id="command-2", command_type="start_attempt",
                                     request_fingerprint="blocked", error_code="aim_paused",
                                     error_message="Aim is paused")
        reopened = SqliteEventRepository(self.path)
        self.assertEqual(reopened.receipt("command-2"), rejected)
        self.assertEqual(reopened.events(), [])

    def test_outbox_insert_failure_rolls_back_its_domain_event_and_receipt(self) -> None:
        repository = SqliteEventRepository(self.path)
        request = RuntimeOutboxRequest("request-1", "attempt-1", "codex", {}, "pending")
        repository.commit(
            command_id="command-1", command_type="start_runtime_attempt",
            request_fingerprint="first", events=(self.event(),), result={},
            runtime_requests=(request,),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            repository.commit(
                command_id="command-2", command_type="start_runtime_attempt",
                request_fingerprint="second", events=(self.event("event-2"),), result={},
                runtime_requests=(RuntimeOutboxRequest("request-1", "attempt-2", "codex", {}, "pending"),),
            )
        self.assertEqual([event.event_id for event in repository.events()], ["event-1"])
        self.assertIsNone(repository.receipt("command-2"))
