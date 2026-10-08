from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity
from goal_loop_tool.outbox_runtime import RuntimeOutboxWorker
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft
from goal_loop_tool.subprocess_runtime import RuntimeExecution


class RuntimeOutboxWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.loop = PersistentGoalLoop(
            SqliteEventRepository(Path(self.directory.name) / "goal-loop.sqlite3")
        )
        aim = self.loop.create_aim("Dispatch one request")
        proposal = self.loop.create_proposal(aim.id, "Use a Runtime worker")
        self.goal = self.loop.accept_proposal(
            proposal.id, GoalIdentity(aim.id, "target", "produce"),
            ContractDraft("scope", "criterion", "verification"),
            idempotency_key="outbox-worker",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def enqueue(self):
        return self.loop.start_runtime_attempt(
            self.goal.id, 1, "runtime", "inspect",
            request_kind="test", request_payload={"value": "inspect"},
            request_id="request-1",
        )

    @staticmethod
    def succeeded_execution() -> RuntimeExecution:
        return RuntimeExecution(
            "delivery-1", AttemptOutcome.SUCCEEDED, 0, "done", "", False
        )

    def test_dispatch_delivers_then_acknowledges_one_claimed_request(self) -> None:
        attempt = self.enqueue()
        seen: list[str] = []

        def execute(request):
            seen.append(request.request_id)
            return self.succeeded_execution()

        dispatched = RuntimeOutboxWorker(self.loop, "worker-a").dispatch("request-1", execute)
        self.assertEqual(seen, ["request-1"])
        self.assertEqual(dispatched.attempt.id, attempt.id)
        self.assertEqual(dispatched.attempt.outcome, AttemptOutcome.SUCCEEDED)
        self.assertEqual(dispatched.acknowledged_request.status, "completed")

    def test_executor_failure_keeps_claim_and_never_retries_automatically(self) -> None:
        self.enqueue()
        calls = 0

        def fail(_request):
            nonlocal calls
            calls += 1
            raise RuntimeError("external process failed before delivery")

        worker = RuntimeOutboxWorker(self.loop, "worker-a")
        with self.assertRaisesRegex(RuntimeError, "external process"):
            worker.dispatch("request-1", fail)
        self.assertEqual(calls, 1)
        self.assertIsNone(RuntimeOutboxWorker(self.loop, "worker-b").dispatch("request-1", fail))
        self.assertEqual(calls, 1)
        self.assertEqual(self.loop.repository.requeue_runtime_request("request-1", "worker-a").status, "pending")

    def test_observed_result_can_be_delivered_without_launching_an_executor(self) -> None:
        attempt = self.enqueue()
        self.loop.repository.claim_runtime_request("request-1", "worker-a")
        delivered = RuntimeOutboxWorker(self.loop, "worker-a").deliver(
            "request-1", self.succeeded_execution()
        )
        self.assertEqual(delivered.attempt.id, attempt.id)
        self.assertEqual(delivered.acknowledged_request.status, "completed")
