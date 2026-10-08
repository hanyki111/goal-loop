from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity, VerdictResult
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class GoalTreeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "goal.sqlite3"
        self.loop = PersistentGoalLoop(SqliteEventRepository(self.path))
        self.aim = self.loop.create_aim("Tree")

    def tearDown(self): self.directory.cleanup()

    def goal(self, key):
        proposal = self.loop.create_proposal(self.aim.id, key)
        return self.loop.accept_proposal(proposal.id, GoalIdentity(self.aim.id, key, "produce"), ContractDraft("scope", "criterion", "verify"), idempotency_key=key)

    def satisfy(self, goal):
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "work")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "observer", "observed")
        self.loop.begin_verification(goal.id, attempt.id)
        self.loop.submit_verdict(goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,))

    def test_parent_and_goal_dependency_replay_and_gate_attempt_start(self):
        parent, child, prerequisite = self.goal("parent"), self.goal("child"), self.goal("prerequisite")
        self.loop.add_goal_parent(child.id, parent.id)
        # A parent verifies only after its children, so waiting for the parent would deadlock.
        with self.assertRaises(GoalLoopError):
            self.loop.add_goal_dependency(child.id, parent.id)
        self.loop.add_goal_dependency(child.id, prerequisite.id)
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(child.id, 1, "executor", "blocked")
        self.satisfy(prerequisite)
        attempt = self.loop.start_attempt(child.id, 1, "executor", "admitted")
        restarted = PersistentGoalLoop(SqliteEventRepository(self.path))
        self.assertEqual(restarted.goal_parents[child.id], parent.id)
        self.assertEqual(restarted.goal_dependencies[child.id][0].required_goal_id, prerequisite.id)
        self.assertEqual(restarted.attempts[attempt.id].outcome, AttemptOutcome.RUNNING)

    def test_parent_and_dependency_cycles_are_rejected(self):
        first, second = self.goal("first"), self.goal("second")
        self.loop.add_goal_parent(second.id, first.id)
        with self.assertRaises(GoalLoopError): self.loop.add_goal_parent(first.id, second.id)
        third = self.goal("third")
        self.loop.add_goal_dependency(second.id, third.id)
        with self.assertRaises(GoalLoopError): self.loop.add_goal_dependency(third.id, second.id)
        # An already-required Goal cannot become an ancestor either.
        with self.assertRaises(GoalLoopError): self.loop.add_goal_parent(second.id, third.id)

    def test_legacy_parent_and_dependency_pair_still_replays(self):
        from goal_loop_tool.service import GoalLoop
        loop = GoalLoop()
        aim = loop.create_aim("legacy")
        goals = []
        for key in ("parent", "child"):
            proposal = loop.create_proposal(aim.id, key)
            goals.append(loop.accept_proposal(proposal.id, GoalIdentity(aim.id, key, "produce"), ContractDraft("scope", "criterion", "verify"), idempotency_key=key))
        parent, child = goals
        loop.add_goal_parent(child.id, parent.id)
        with self.assertRaises(GoalLoopError):
            loop.add_goal_dependency(child.id, parent.id)
        # Journal replay uses the legacy flag so facts recorded before the rule still load.
        loop.add_goal_dependency(child.id, parent.id, allow_legacy_deadlock=True)
        self.assertEqual(loop.goal_dependencies[child.id][0].required_goal_id, parent.id)
