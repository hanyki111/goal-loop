from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.activity import activity_cards
from goal_loop_tool.graph_view import render_goal_graph
from goal_loop_tool.model import AttemptOutcome, GoalIdentity, VerdictResult
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.persistent_cli import main
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError
from goal_loop_tool.ui_projection import goal_graph_projection


class GoalFollowTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "goal.sqlite3"
        self.repository = SqliteEventRepository(self.path)
        self.loop = PersistentGoalLoop(self.repository)
        self.aim = self.loop.create_aim("Root")
        self.sub_aim = self.loop.create_aim("Sub", self.aim.id)

    def tearDown(self): self.directory.cleanup()

    def goal(self, key, aim=None):
        aim_id = (aim or self.aim).id
        proposal = self.loop.create_proposal(aim_id, key)
        return self.loop.accept_proposal(proposal.id, GoalIdentity(aim_id, key, "produce"), ContractDraft("scope", "criterion", "verify"), idempotency_key=key)

    def satisfy(self, goal):
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "work")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "observer", "observed")
        self.loop.begin_verification(goal.id, attempt.id)
        self.loop.submit_verdict(goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,))

    def test_follow_links_cross_aims_and_closed_goals_and_replay(self):
        research, stopped = self.goal("research"), self.goal("rules")
        self.satisfy(research)
        self.loop.stop_goal(stopped.id, "owner", "rules did not generalize")
        successor = self.goal("llm", self.sub_aim)
        self.loop.add_goal_follow(stopped.id, research.id, "claude", "built on the research conclusion")
        self.loop.add_goal_follow(successor.id, stopped.id, "claude", "continues the stopped rules prototype")
        restarted = PersistentGoalLoop(SqliteEventRepository(self.path))
        self.assertEqual(
            [(item.goal_id, item.previous_goal_id, item.actor_id, item.reason) for item in restarted.goal_follows],
            [(stopped.id, research.id, "claude", "built on the research conclusion"),
             (successor.id, stopped.id, "claude", "continues the stopped rules prototype")],
        )
        cards = [card for card in activity_cards(self.repository) if card.title == "이어받은 과제 연결"]
        self.assertEqual(len(cards), 2)
        self.assertIn("continues the stopped rules prototype", cards[-1].detail)

    def test_follow_link_rules(self):
        first, second, third = self.goal("first"), self.goal("second"), self.goal("third")
        with self.assertRaises(GoalLoopError): self.loop.add_goal_follow(second.id, first.id, "claude", " ")
        with self.assertRaises(GoalLoopError): self.loop.add_goal_follow(second.id, first.id, " ", "reason")
        with self.assertRaises(GoalLoopError): self.loop.add_goal_follow(first.id, first.id, "claude", "self")
        with self.assertRaises(GoalLoopError): self.loop.add_goal_follow(second.id, "goal-99", "claude", "missing")
        self.loop.add_goal_follow(second.id, first.id, "claude", "continues")
        self.loop.add_goal_follow(third.id, second.id, "claude", "continues")
        with self.assertRaises(GoalLoopError): self.loop.add_goal_follow(second.id, first.id, "claude", "duplicate")
        with self.assertRaises(GoalLoopError): self.loop.add_goal_follow(first.id, third.id, "claude", "cycle")
        # Several predecessors are allowed: a Goal may build on more than one earlier result.
        fourth = self.goal("fourth")
        self.loop.add_goal_follow(fourth.id, first.id, "claude", "a")
        self.loop.add_goal_follow(fourth.id, third.id, "claude", "b")
        # Rejected commands append nothing.
        self.assertEqual(len(PersistentGoalLoop(SqliteEventRepository(self.path)).goal_follows), 4)

    def test_follow_link_never_gates_attempts(self):
        earlier, later = self.goal("earlier"), self.goal("later")
        self.loop.add_goal_follow(later.id, earlier.id, "claude", "continues an unverified Goal")
        attempt = self.loop.start_attempt(later.id, 1, "executor", "admitted although earlier is unverified")
        self.assertEqual(attempt.outcome, AttemptOutcome.RUNNING)
        projection = goal_graph_projection(self.loop)
        node = next(item for item in projection["nodes"] if item["id"] == later.id)
        self.assertFalse(node["dependency_blocked"])

    def test_projection_graph_export_and_cli(self):
        earlier, later = self.goal("earlier"), self.goal("later", self.sub_aim)
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(self.path), "goal-follow-add", later.id, earlier.id, "--actor", "claude", "--reason", "continues <earlier>"])
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["action"], "goal_follow_linked")
        loop = PersistentGoalLoop(SqliteEventRepository(self.path))
        projection = goal_graph_projection(loop)
        self.assertEqual(projection["schema_version"], 16)
        self.assertIn(
            {"id": f"follows:{earlier.id}:{later.id}", "source": earlier.id, "target": later.id, "kind": "follows", "actor_id": "claude", "reason": "continues <earlier>"},
            projection["edges"],
        )
        html = render_goal_graph(loop)
        self.assertIn("continues &lt;earlier&gt;", html)
        self.assertIn("Follows Goal", html)
        output = StringIO()
        with redirect_stdout(output): main(["--database", str(self.path), "goal-graph"])
        graph = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(graph["follows"], [{"goal_id": later.id, "previous_goal_id": earlier.id, "actor_id": "claude", "reason": "continues <earlier>"}])
        with self.assertRaises(SystemExit), redirect_stdout(StringIO()):
            from contextlib import redirect_stderr
            with redirect_stderr(StringIO()):
                main(["--database", str(self.path), "goal-follow-add", later.id, earlier.id, "--actor", "claude"])


if __name__ == "__main__":
    unittest.main()
