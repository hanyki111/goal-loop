from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.persistent_cli import main


class NextAimScopeTests(unittest.TestCase):
    """next --aim keeps only the actions that arise from one Aim and its sub-Aims (goal-138)."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "goal.sqlite3"
        self.life = self.call("aim-create", "모든 영역")
        self.game = self.call("aim-create", "게임을 만든다", "--parent", self.life["id"])
        self.combat = self.call("aim-create", "전투", "--parent", self.game["id"])
        self.music = self.call("aim-create", "음악을 배운다", "--parent", self.life["id"])

    def tearDown(self):
        self.directory.cleanup()

    def call(self, *args):
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(self.path), *args])
        return json.loads(output.getvalue().splitlines()[-1])

    def subjects(self, *scope):
        record = self.call("next", *(("--aim", scope[0]) if scope else ()))
        return record, [(action["kind"], action["subject"]["id"]) for action in record["actions"]]

    def goal(self, aim_id, key):
        proposal = self.call("proposal-create", aim_id, key)
        return self.call("goal-accept", proposal["id"], "--target", key, "--responsibility", "produce",
                         "--scope", "s", "--criterion", "c", "--verification", "v", "--key", key)

    def verified(self, aim_id, key):
        goal = self.goal(aim_id, key)
        attempt = self.call("attempt-start", goal["id"], "1", "claude", "op")
        self.call("attempt-complete", attempt["id"], "succeeded")
        evidence = self.call("evidence-record", attempt["id"], "claude", "observed")
        self.call("verification-begin", goal["id"], attempt["id"])
        verdict = self.call("verdict-submit", goal["id"], attempt["id"], "owner", "satisfied", evidence["id"])
        return goal, evidence, verdict

    def test_scope_covers_every_sub_aim_level_and_excludes_siblings(self):
        combat_goal = self.goal(self.combat["id"], "combat")
        piano = self.call("proposal-create", self.music["id"], "피아노")
        _, everything = self.subjects()
        record, game = self.subjects(self.game["id"])
        self.assertEqual(record["aim_scope"], self.game["id"])
        self.assertIn(("start_attempt", combat_goal["id"]), game)  # two levels down
        self.assertIn(("accept_proposal", piano["id"]), everything)
        self.assertNotIn(("accept_proposal", piano["id"]), game)  # sibling Aim
        self.assertTrue(set(game) <= set(everything))
        _, music = self.subjects(self.music["id"])
        self.assertNotIn(("start_attempt", combat_goal["id"]), music)
        self.assertEqual([kind for kind, _ in music], ["accept_proposal"])
        _, root = self.subjects(self.life["id"])
        self.assertEqual(root, everything)
        self.assertNotIn("aim_scope", self.call("next"))

    def test_an_ancestor_resume_is_kept_when_a_goal_in_scope_caused_it(self):
        combat_goal = self.goal(self.combat["id"], "combat")
        self.call("aim-pause", self.life["id"], "owner", "rest")
        _, combat = self.subjects(self.combat["id"])
        self.assertIn(("resume_aim", self.life["id"]), combat)
        self.assertNotIn(("start_attempt", combat_goal["id"]), combat)
        _, music = self.subjects(self.music["id"])
        self.assertNotIn(("resume_aim", self.life["id"]), music)  # no Goal under music needs it

    def test_reviews_and_evaluations_follow_the_aim_they_touch(self):
        _, evidence, verdict = self.verified(self.combat["id"], "combat")
        _, combat = self.subjects(self.combat["id"])
        self.assertIn(("evaluate_aim", self.combat["id"]), combat)
        self.call("evidence-correct", evidence["id"], "claude", "corrected")
        _, combat = self.subjects(self.combat["id"])
        _, music = self.subjects(self.music["id"])
        self.assertTrue(any(kind == "resolve_verdict_review" for kind, _ in combat), combat)
        self.assertFalse(any(kind == "resolve_verdict_review" for kind, _ in music), music)

    def test_unknown_aim_is_an_error(self):
        errors = StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            main(["--database", str(self.path), "next", "--aim", "aim-99"])
        self.assertNotEqual(raised.exception.code, 0)
        self.assertIn("aim-99", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
