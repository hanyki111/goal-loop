from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft
from goal_loop_tool.model import GoalIdentity


class RuntimeOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "goal-loop.sqlite3"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def goal(self, loop: PersistentGoalLoop):
        aim = loop.create_aim("Run a bounded task")
        proposal = loop.create_proposal(aim.id, "Use the runtime")
        return loop.accept_proposal(
            proposal.id,
            GoalIdentity(aim.id, "target", "complete task"),
            ContractDraft("scope", "criterion", "verification"),
            idempotency_key="runtime-outbox-goal",
        )

    def test_start_is_atomic_with_outbox_and_survives_restart(self) -> None:
        first = PersistentGoalLoop(SqliteEventRepository(self.path))
        goal = self.goal(first)
        attempt = first.start_runtime_attempt(
            goal.id, 1, "runtime", "inspect files",
            request_kind="codex",
            request_payload={"argv": ["codex", "exec", "inspect"]},
            request_id="request-1",
            command_id="start-runtime-1",
        )
        self.assertEqual([event.event_type for event in first.repository.events()][-1], "AttemptStarted")
        self.assertEqual(
            first.repository.pending_runtime_requests()[0].attempt_id, attempt.id
        )

        restarted = PersistentGoalLoop(SqliteEventRepository(self.path))
        self.assertEqual(restarted.attempts[attempt.id].id, attempt.id)
        pending = restarted.repository.pending_runtime_requests()
        self.assertEqual([(item.request_id, item.status) for item in pending], [("request-1", "pending")])
        self.assertEqual(
            restarted.start_runtime_attempt(
                goal.id, 1, "runtime", "inspect files",
                request_kind="codex",
                request_payload={"argv": ["codex", "exec", "inspect"]},
                request_id="request-1",
                command_id="start-runtime-1",
            ),
            attempt,
        )
        self.assertEqual(len(restarted.repository.pending_runtime_requests()), 1)

    def test_claim_requeue_and_ack_require_the_claiming_worker(self) -> None:
        loop = PersistentGoalLoop(SqliteEventRepository(self.path))
        goal = self.goal(loop)
        loop.start_runtime_attempt(
            goal.id, 1, "runtime", "inspect files",
            request_kind="codex", request_payload={"prompt": "inspect"},
            request_id="request-1",
        )
        claimed = loop.repository.claim_runtime_request("request-1", "worker-a")
        self.assertEqual((claimed.status, claimed.claimed_by), ("claimed", "worker-a"))
        self.assertEqual(loop.repository.runtime_request("request-1").status, "claimed")
        restarted = SqliteEventRepository(self.path)
        self.assertEqual(restarted.pending_runtime_requests(), [])
        self.assertIsNone(loop.repository.claim_runtime_request("request-1", "worker-b"))
        with self.assertRaises(ValueError):
            loop.repository.acknowledge_runtime_request("request-1", "worker-b")
        pending = loop.repository.requeue_runtime_request("request-1", "worker-a")
        self.assertEqual((pending.status, pending.claimed_by), ("pending", None))
        loop.repository.claim_runtime_request("request-1", "worker-b")
        completed = loop.repository.acknowledge_runtime_request("request-1", "worker-b")
        self.assertEqual((completed.status, completed.claimed_by), ("completed", "worker-b"))
        self.assertEqual(loop.repository.pending_runtime_requests(), [])
