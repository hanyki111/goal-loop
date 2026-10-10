from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AimPhase, AttemptOutcome, GoalIdentity, VerdictResult, VerdictValidity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.persistent_cli import main
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class AimTreeTests(unittest.TestCase):
    """Sub-Aims split a direction; nothing is achieved or closed implicitly."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "goal.sqlite3"
        self.loop = PersistentGoalLoop(SqliteEventRepository(self.path))
        self.game = self.loop.create_aim("게임을 만든다")
        self.combat = self.loop.create_aim("전투가 재미있다", self.game.id)

    def tearDown(self):
        self.directory.cleanup()

    def restart(self):
        return PersistentGoalLoop(SqliteEventRepository(self.path))

    def verified_goal(self, aim_id, key):
        proposal = self.loop.create_proposal(aim_id, key)
        goal = self.loop.accept_proposal(proposal.id, GoalIdentity(aim_id, key, "produce"), ContractDraft("s", "c", "v"), idempotency_key=key)
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "op")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "executor", "observed")
        self.loop.begin_verification(goal.id, attempt.id)
        verdict = self.loop.submit_verdict(goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,))
        return goal, evidence, verdict

    def test_parent_link_rules_and_replay(self):
        stage = self.loop.create_aim("첫 스테이지")
        self.loop.link_aim_parent(stage.id, self.game.id)
        with self.assertRaises(GoalLoopError):
            self.loop.link_aim_parent(stage.id, self.combat.id)  # one parent, never moved
        with self.assertRaises(GoalLoopError):
            self.loop.link_aim_parent(self.game.id, self.combat.id)  # cycle
        with self.assertRaises(GoalLoopError):
            self.loop.create_aim("missing parent", "aim-99")
        restarted = self.restart()
        self.assertEqual(restarted.aims[self.combat.id].parent_aim_id, self.game.id)
        self.assertEqual(restarted.aims[stage.id].parent_aim_id, self.game.id)
        self.assertEqual(restarted.aim_ancestors(self.combat.id), [self.game.id])

    def test_achieving_sub_aims_does_not_achieve_the_parent_and_parent_cites_sub_evaluations(self):
        _, _, verdict = self.verified_goal(self.combat.id, "combat")
        child_evaluation = self.loop.evaluate_aim_as_achieved(self.combat.id, "owner", (verdict.id,), "전투 완료")
        self.assertIs(self.loop.aims[self.game.id].phase, AimPhase.OPEN)
        with self.assertRaises(GoalLoopError):
            self.loop.evaluate_aim_as_achieved(self.game.id, "owner", (), "no basis")
        with self.assertRaises(GoalLoopError):
            # A Verdict of a sub-Aim's Goal is not the parent's own basis; cite the sub-Aim evaluation.
            self.loop.evaluate_aim_as_achieved(self.game.id, "owner", (verdict.id,), "wrong basis")
        parent_evaluation = self.loop.evaluate_aim_as_achieved(self.game.id, "owner", (), "게임 완성", (child_evaluation.id,))
        restarted = self.restart()
        self.assertEqual(restarted.aim_evaluations[parent_evaluation.id].child_evaluation_ids, (child_evaluation.id,))
        self.assertTrue(restarted.is_currently_achieved(self.game.id))
        with self.assertRaises(GoalLoopError):
            self.loop.create_aim("late child", self.game.id)  # an achieved Aim takes no new sub-Aim

    def test_grandchild_evaluation_cannot_be_cited_directly(self):
        boss = self.loop.create_aim("보스전", self.combat.id)
        _, _, verdict = self.verified_goal(boss.id, "boss")
        boss_evaluation = self.loop.evaluate_aim_as_achieved(boss.id, "owner", (verdict.id,), "보스전 완료")
        with self.assertRaises(GoalLoopError):
            self.loop.evaluate_aim_as_achieved(self.game.id, "owner", (), "skips a level", (boss_evaluation.id,))

    def test_correction_reviews_the_parent_evaluation_that_cites_the_affected_sub_evaluation(self):
        _, evidence, verdict = self.verified_goal(self.combat.id, "combat")
        child_evaluation = self.loop.evaluate_aim_as_achieved(self.combat.id, "owner", (verdict.id,), "전투 완료")
        parent_evaluation = self.loop.evaluate_aim_as_achieved(self.game.id, "owner", (), "게임 완성", (child_evaluation.id,))
        self.loop.correct_evidence(evidence.id, "observer", "actually broken")
        review = next(iter(self.loop.impact_reviews.values()))
        self.assertEqual(review.affected_aim_evaluation_ids, (child_evaluation.id, parent_evaluation.id))
        self.assertIs(self.loop.aim_evaluation_validity[parent_evaluation.id], VerdictValidity.UNDER_REVIEW)
        from goal_loop_tool.model import ReviewDisposition
        self.loop.resolve_verdict_review(review.id, ReviewDisposition.REAFFIRM, "reviewer", "still fine")
        # Reaffirming the parent first keeps it under review while the cited sub-evaluation is not valid.
        self.loop.resolve_aim_evaluation_review(parent_evaluation.id, review.id, ReviewDisposition.REAFFIRM, "reviewer", "early")
        self.assertIs(self.loop.aim_evaluation_validity[parent_evaluation.id], VerdictValidity.UNDER_REVIEW)
        self.loop.resolve_aim_evaluation_review(child_evaluation.id, review.id, ReviewDisposition.REAFFIRM, "reviewer", "fine")
        self.loop.resolve_aim_evaluation_review(parent_evaluation.id, review.id, ReviewDisposition.REAFFIRM, "reviewer", "fine now")
        self.assertIs(self.loop.aim_evaluation_validity[parent_evaluation.id], VerdictValidity.VALID)
        self.assertEqual(len(self.restart().impact_reviews), 1)

    def test_paused_or_abandoned_ancestor_blocks_new_work_below_without_closing_it(self):
        boss = self.loop.create_aim("보스전", self.combat.id)
        proposal = self.loop.create_proposal(boss.id, "boss")
        goal = self.loop.accept_proposal(proposal.id, GoalIdentity(boss.id, "boss", "produce"), ContractDraft("s", "c", "v"), idempotency_key="boss")
        self.loop.pause_aim(self.game.id, "owner", "rethink")
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(goal.id, 1, "executor", "blocked by grandparent pause")
        self.loop.create_proposal(boss.id, "pause still allows proposals")
        self.loop.resume_aim(self.game.id, "owner", "back")
        self.loop.abandon_aim(self.game.id, "owner", "cancelled")
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(goal.id, 1, "executor", "blocked")
        with self.assertRaises(GoalLoopError):
            self.loop.create_proposal(boss.id, "blocked")
        self.assertIs(self.loop.aims[boss.id].phase, AimPhase.OPEN)


class AimTreeCliTests(unittest.TestCase):
    def call(self, path, *args):
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(path), *args])
        return json.loads(output.getvalue().splitlines()[-1])

    def test_cli_tree_next_and_projection(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            game = self.call(path, "aim-create", "게임을 만든다")
            combat = self.call(path, "aim-create", "전투가 재미있다", "--parent", game["id"])
            stage = self.call(path, "aim-create", "첫 스테이지")
            self.call(path, "aim-parent-add", stage["id"], game["id"])
            kinds = [(action["kind"], action["subject"]["id"]) for action in self.call(path, "next")["actions"]]
            self.assertNotIn(("create_proposal", game["id"]), kinds)  # split into sub-Aims
            self.assertIn(("create_proposal", combat["id"]), kinds)
            projection = self.call(path, "ui-projection")
            self.assertEqual(projection["schema_version"], 16)
            self.assertEqual({aim["id"]: aim["parent_aim_id"] for aim in projection["aims"]}[combat["id"]], game["id"])
            self.assertIn({"id": f"aim-parent:{game['id']}:{stage['id']}", "source": game["id"], "target": stage["id"], "kind": "aim-parent"}, projection["edges"])
            proposal = self.call(path, "proposal-create", combat["id"], "전투 프로토타입")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce", "--scope", "s", "--criterion", "c", "--verification", "v", "--key", "k")
            attempt = self.call(path, "attempt-start", goal["id"], "1", "claude", "build")
            self.call(path, "attempt-complete", attempt["id"], "succeeded")
            evidence = self.call(path, "evidence-record", attempt["id"], "claude", "works")
            self.call(path, "verification-begin", goal["id"], attempt["id"])
            verdict = self.call(path, "verdict-submit", goal["id"], attempt["id"], "owner", "satisfied", evidence["id"])
            child = self.call(path, "aim-evaluate", combat["id"], "owner", "전투 완료", verdict["id"])
            suggestion = next(action for action in self.call(path, "next")["actions"] if action["kind"] == "evaluate_aim" and action["subject"]["id"] == game["id"])
            self.assertIn(child["id"], suggestion["command_argv"])
            parent = self.call(path, "aim-evaluate", game["id"], "owner", "게임 완성", child["id"])
            self.assertEqual(parent["child_evaluation_ids"], [child["id"]])

    def test_next_never_suggests_work_that_an_ancestor_pause_or_abandonment_blocks(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            game = self.call(path, "aim-create", "게임을 만든다")
            combat = self.call(path, "aim-create", "전투", "--parent", game["id"])
            goals = []
            for key in ("a", "b"):
                proposal = self.call(path, "proposal-create", combat["id"], key)
                goals.append(self.call(path, "goal-accept", proposal["id"], "--target", key, "--responsibility", "produce", "--scope", "s", "--criterion", "c", "--verification", "v", "--key", key))
            self.call(path, "aim-pause", game["id"], "owner", "rethink")
            actions = [(action["kind"], action["subject"]["id"]) for action in self.call(path, "next")["actions"]]
            self.assertEqual(actions.count(("resume_aim", game["id"])), 1)
            self.assertFalse(any(kind == "start_attempt" for kind, _ in actions))
            self.call(path, "aim-resume", game["id"], "owner", "back")
            self.call(path, "aim-abandon", game["id"], "--actor", "owner", "--reason", "cancelled")
            actions = [(action["kind"], action["subject"]["id"]) for action in self.call(path, "next")["actions"]]
            self.assertFalse(any(subject in {combat["id"], *(goal["id"] for goal in goals)} for _, subject in actions))


if __name__ == "__main__":
    unittest.main()
