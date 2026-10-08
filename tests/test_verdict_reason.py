from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity, VerdictResult
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.persistent_cli import main
from goal_loop_tool.repository import CommandConflictError, SqliteEventRepository
from goal_loop_tool.service import ContractDraft


class VerdictReasonTests(unittest.TestCase):
    """A Verdict may carry the verifier's reason; journals written before the field still replay."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.database = Path(self.directory.name) / "journal.sqlite3"

    def tearDown(self):
        self.directory.cleanup()

    def verifying_goal(self, loop: PersistentGoalLoop) -> tuple[str, str, str]:
        aim = loop.create_aim("reason")
        proposal = loop.create_proposal(aim.id, "approach")
        goal = loop.accept_proposal(proposal.id, GoalIdentity(aim.id, "target", "produce"), ContractDraft("scope", "(1) a (2) b", "verify"), idempotency_key="k")
        attempt = loop.start_attempt(goal.id, 1, "claude", "run")
        loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = loop.record_evidence(attempt.id, "claude", "observed")
        loop.begin_verification(goal.id, attempt.id)
        return goal.id, attempt.id, evidence.id

    def call(self, *args: str) -> dict:
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(self.database), *args])
        return json.loads(output.getvalue().splitlines()[-1])

    def test_cli_reason_is_stored_replayed_and_projected_with_line_breaks(self):
        goal_id, attempt_id, evidence_id = self.verifying_goal(PersistentGoalLoop(SqliteEventRepository(self.database)))
        reason = "(1) 충족: 문서에 있다\n(2) 미충족: 첨부가 없다"
        verdict = self.call("verdict-submit", goal_id, attempt_id, "user", "not_satisfied", evidence_id, "--reason", reason)
        self.assertEqual(verdict["reason"], reason)
        restarted = PersistentGoalLoop(SqliteEventRepository(self.database))
        self.assertEqual(restarted.verdicts[verdict["id"]].reason, reason)
        projected = next(item for item in self.call("ui-projection")["verdicts"] if item["id"] == verdict["id"])
        self.assertEqual(projected["reason"], reason)

    def test_verdict_without_reason_keeps_the_earlier_event_shape(self):
        loop = PersistentGoalLoop(SqliteEventRepository(self.database))
        goal_id, attempt_id, evidence_id = self.verifying_goal(loop)
        verdict = loop.submit_verdict(goal_id, attempt_id, "user", VerdictResult.SATISFIED, (evidence_id,), reason="   ", command_id="verdict-command")
        self.assertIsNone(verdict.reason)
        event = [item for item in SqliteEventRepository(self.database).events() if item.event_type == "VerdictSubmitted"][-1]
        self.assertNotIn("reason", event.payload)  # same payload as journals written before the field existed
        restarted = PersistentGoalLoop(SqliteEventRepository(self.database))
        self.assertIsNone(restarted.verdicts[verdict.id].reason)
        # Replaying the same command without a reason is idempotent; adding a reason is a different request.
        self.assertEqual(restarted.submit_verdict(goal_id, attempt_id, "user", VerdictResult.SATISFIED, (evidence_id,), command_id="verdict-command").id, verdict.id)
        with self.assertRaises(CommandConflictError):
            restarted.submit_verdict(goal_id, attempt_id, "user", VerdictResult.SATISFIED, (evidence_id,), reason="new", command_id="verdict-command")


if __name__ == "__main__":
    unittest.main()
