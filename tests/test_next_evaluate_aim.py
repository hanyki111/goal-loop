"""goal-166: next suggests evaluating an Aim only when no open sub-Aim or open Goal is left; open Proposals are listed."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.persistent_cli import main


class NextEvaluateAimTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "goal.sqlite3"

    def tearDown(self):
        self.directory.cleanup()

    def call(self, *args):
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(self.path), *args])
        return json.loads(output.getvalue().splitlines()[-1])

    def verified_goal(self, aim_id, key):
        proposal = self.call("proposal-create", aim_id, f"approach {key}", "--actor", "claude")
        goal = self.call("goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce", "--scope", "s",
                         "--criterion", "c", "--verification", "v", "--key", key)
        attempt = self.call("attempt-start", goal["id"], "1", "claude", "build")
        self.call("attempt-complete", attempt["id"], "succeeded")
        evidence = self.call("evidence-record", attempt["id"], "claude", "works")
        self.call("verification-begin", goal["id"], attempt["id"])
        self.call("verdict-submit", goal["id"], attempt["id"], "owner", "satisfied", evidence["id"])
        return goal

    def evaluate_suggestion(self, aim_id):
        return next((action for action in self.call("next")["actions"]
                     if action["kind"] == "evaluate_aim" and action["subject"]["id"] == aim_id), None)

    def test_nothing_to_cite_means_no_suggestion(self):
        aim = self.call("aim-create", "ship")
        self.call("proposal-create", aim["id"], "an approach", "--actor", "claude")
        self.assertIsNone(self.evaluate_suggestion(aim["id"]))

    def test_suggested_when_every_goal_is_finished_and_no_proposal_is_left(self):
        aim = self.call("aim-create", "ship")
        self.verified_goal(aim["id"], "one")
        suggestion = self.evaluate_suggestion(aim["id"])
        self.assertIsNotNone(suggestion)
        self.assertNotIn("남은 접근안", suggestion["summary"])

    def test_an_open_goal_blocks_the_suggestion_until_it_is_verified_or_stopped(self):
        aim = self.call("aim-create", "ship")
        self.verified_goal(aim["id"], "one")
        proposal = self.call("proposal-create", aim["id"], "second", "--actor", "claude")
        second = self.call("goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce", "--scope", "s",
                           "--criterion", "c", "--verification", "v", "--key", "two")
        self.assertIsNone(self.evaluate_suggestion(aim["id"]))  # accepted, not started
        self.call("goal-stop", second["id"], "--actor", "owner", "--reason", "dropped")
        self.assertIsNotNone(self.evaluate_suggestion(aim["id"]))

    def test_an_open_sub_aim_blocks_the_parent_but_a_finished_one_does_not(self):
        parent = self.call("aim-create", "game")
        self.verified_goal(parent["id"], "parent-goal")
        child = self.call("aim-create", "combat", "--parent", parent["id"])
        self.assertIsNone(self.evaluate_suggestion(parent["id"]))
        self.call("aim-abandon", child["id"], "--actor", "owner", "--reason", "out of scope")
        self.assertIsNotNone(self.evaluate_suggestion(parent["id"]))

    def test_open_proposals_do_not_block_and_are_listed(self):
        aim = self.call("aim-create", "ship")
        self.verified_goal(aim["id"], "one")
        parked = self.call("proposal-create", aim["id"], "later idea", "--actor", "claude")
        suggestion = self.evaluate_suggestion(aim["id"])
        self.assertIsNotNone(suggestion)
        self.assertIn(f"남은 접근안 1개({parked['id']})", suggestion["summary"])


if __name__ == "__main__":
    unittest.main()
