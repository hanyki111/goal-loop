from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AdmissionErrorCode, AttemptOutcome, GoalIdentity, VerdictResult
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class PersistentVerdictDependencyAcceptanceTests(unittest.TestCase):
    def test_dependency_restores_and_correction_quarantines_dependent_goal(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("durable dependency")

            source_proposal = first.create_proposal(aim.id, "produce prerequisite")
            source = first.accept_proposal(
                source_proposal.id,
                GoalIdentity(aim.id, "source", "produce"),
                ContractDraft("scope", "criterion", "verify"),
                idempotency_key="dependency-source",
            )
            source_attempt = first.start_attempt(source.id, 1, "executor", "source work")
            first.complete_attempt(source_attempt.id, AttemptOutcome.SUCCEEDED)
            evidence = first.record_evidence(source_attempt.id, "observer", "source observed")
            first.begin_verification(source.id, source_attempt.id)
            verdict = first.submit_verdict(
                source.id, source_attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,)
            )

            dependent_proposal = first.create_proposal(aim.id, "use prerequisite")
            dependent = first.accept_proposal(
                dependent_proposal.id,
                GoalIdentity(aim.id, "dependent", "consume"),
                ContractDraft("scope", "criterion", "verify"),
                idempotency_key="dependency-dependent",
            )
            first.add_verdict_dependency(dependent.id, verdict.id)

            restarted = PersistentGoalLoop(SqliteEventRepository(path))
            admitted = restarted.start_attempt(dependent.id, 1, "executor", "dependent work")
            self.assertEqual(admitted.goal_id, dependent.id)

            restarted.correct_evidence(evidence.id, "auditor", "source observation corrected")
            quarantined = PersistentGoalLoop(SqliteEventRepository(path))
            with self.assertRaises(GoalLoopError) as blocked:
                quarantined.start_attempt(dependent.id, 1, "executor", "another dependent work")
            self.assertEqual(blocked.exception.code, AdmissionErrorCode.DEPENDENCY_VERDICT_UNTRUSTED)


