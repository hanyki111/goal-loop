from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AimPhase, AttemptOutcome, GoalIdentity, GoalPhase, ProposalStatus
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class LifecycleClosureTests(unittest.TestCase):
    """Closing approaches, stopping tasks and abandoning Aims are recorded, never reopened, and survive restart."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "goal.sqlite3"
        self.loop = PersistentGoalLoop(SqliteEventRepository(self.path))
        self.aim = self.loop.create_aim("Closure")

    def tearDown(self):
        self.directory.cleanup()

    def goal(self, key):
        proposal = self.loop.create_proposal(self.aim.id, key, "agent-a")
        return proposal, self.loop.accept_proposal(proposal.id, GoalIdentity(self.aim.id, key, "produce"), ContractDraft("scope", "criterion", "verify"), idempotency_key=key)

    def restart(self):
        return PersistentGoalLoop(SqliteEventRepository(self.path))

    def test_proposal_close_records_kind_and_blocks_acceptance(self):
        kept = self.loop.create_proposal(self.aim.id, "kept", "agent-a")
        closed = self.loop.create_proposal(self.aim.id, "alternative", "agent-b")
        self.loop.close_proposal(closed.id, "declined", "owner", "kept was chosen")
        with self.assertRaises(GoalLoopError):
            self.loop.accept_proposal(closed.id, GoalIdentity(self.aim.id, "x", "produce"), ContractDraft("s", "c", "v"), idempotency_key="closed")
        with self.assertRaises(GoalLoopError):
            self.loop.close_proposal(closed.id, "withdrawn", "owner", "again")
        with self.assertRaises(GoalLoopError):
            self.loop.close_proposal(kept.id, "postponed", "owner", "not a closure kind")
        restarted = self.restart()
        record = restarted.proposals[closed.id]
        self.assertIs(record.status, ProposalStatus.CLOSED)
        self.assertEqual((record.closure_kind, record.closed_by_actor_id, record.closure_reason), ("declined", "owner", "kept was chosen"))
        self.assertEqual(restarted.proposals[kept.id].proposed_by_actor_id, "agent-a")

    def test_criteria_closure_keeps_the_proposal_as_the_aims_criteria(self):
        criteria = self.loop.create_proposal(self.aim.id, "Aim criteria\n(1) it works", "agent-a")
        self.loop.close_proposal(criteria.id, "criteria", "agent-a", "adopted as the Aim's achievement criteria")
        with self.assertRaises(GoalLoopError):
            self.loop.accept_proposal(criteria.id, GoalIdentity(self.aim.id, "c", "produce"), ContractDraft("s", "c", "v"), idempotency_key="criteria")
        record = self.restart().proposals[criteria.id]
        self.assertIs(record.status, ProposalStatus.CLOSED)
        self.assertEqual((record.closure_kind, record.direction), ("criteria", "Aim criteria\n(1) it works"))

    def test_goal_stop_blocks_new_work_but_keeps_running_result_recordable(self):
        _, goal = self.goal("stoppable")
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "in flight")
        self.loop.stop_goal(goal.id, "owner", "requirement dropped")
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(goal.id, 1, "executor", "new work")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        with self.assertRaises(GoalLoopError):
            self.loop.begin_verification(goal.id, attempt.id)
        with self.assertRaises(GoalLoopError):
            self.loop.stop_goal(goal.id, "owner", "twice")
        restarted = self.restart()
        self.assertIs(restarted.goals[goal.id].phase, GoalPhase.STOPPED)
        self.assertEqual(restarted.goals[goal.id].stop_reason, "requirement dropped")

    def test_aim_abandon_blocks_new_proposals_acceptance_and_attempts_without_closing_children(self):
        open_proposal = self.loop.create_proposal(self.aim.id, "pending")
        _, goal = self.goal("existing")
        self.loop.abandon_aim(self.aim.id, "owner", "out of scope")
        with self.assertRaises(GoalLoopError):
            self.loop.create_proposal(self.aim.id, "late idea")
        with self.assertRaises(GoalLoopError):
            self.loop.accept_proposal(open_proposal.id, GoalIdentity(self.aim.id, "pending", "produce"), ContractDraft("s", "c", "v"), idempotency_key="late")
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(goal.id, 1, "executor", "blocked")
        restarted = self.restart()
        self.assertIs(restarted.aims[self.aim.id].phase, AimPhase.ABANDONED)
        self.assertIs(restarted.proposals[open_proposal.id].status, ProposalStatus.OPEN)
        self.assertIs(restarted.goals[goal.id].phase, GoalPhase.COMMITTED)

    def test_hold_reason_survives_restart_and_legacy_hold_has_none(self):
        _, held = self.goal("held")
        _, legacy = self.goal("legacy")
        self.loop.add_goal_hold(held.id, "owner", "waiting for approval")
        self.loop.add_goal_hold(legacy.id)
        restarted = self.restart()
        self.assertEqual(restarted.goal_hold_records[held.id], ("owner", "waiting for approval"))
        self.assertEqual(restarted.goal_hold_records[legacy.id], ("system", None))
        restarted.clear_goal_hold(held.id)
        self.assertNotIn(held.id, self.restart().goal_hold_records)


if __name__ == "__main__":
    unittest.main()
