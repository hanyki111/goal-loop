"""aim-criteria-revise: an open Aim's achievement criteria are replaced, the earlier ones kept as history (goal-174)."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.persistent_cli import main
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import GoalLoopError


class CriteriaRevisionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.database = Path(self.directory.name) / "journal.sqlite3"
        self.aim = self.run_cli("aim-create", "기록이 쌓여도 빠르다")
        self.first = self.criteria("달성 기준: (1) 빠르다\n(2) 문서가 있다")
        self.second = self.criteria("달성 기준 보완: (2)는 README만 본다")

    def tearDown(self):
        self.directory.cleanup()

    def run_cli(self, *args: str) -> dict:
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(self.database), *args])
        return json.loads(output.getvalue().splitlines()[-1])

    def fails(self, *args: str) -> str:
        """A refused command: argparse exits, and the Core refuses with GoalLoopError like the other commands."""
        errors = StringIO()
        with redirect_stderr(errors), redirect_stdout(StringIO()):
            try:
                main(["--database", str(self.database), *args])
            except SystemExit:
                return errors.getvalue()
            except GoalLoopError as error:
                return str(error)
        self.fail(f"command succeeded: {args}")

    def criteria(self, text: str) -> dict:
        proposal = self.run_cli("proposal-create", self.aim["id"], text, "--actor", "claude")
        self.run_cli("proposal-close", proposal["id"], "--kind", "criteria", "--actor", "claude", "--reason", "달성 기준")
        return proposal

    def proposals(self) -> dict[str, dict]:
        return {item["id"]: item for item in self.run_cli("ui-projection")["proposals"]}

    def events(self) -> list:
        return SqliteEventRepository(self.database).events()

    def revise(self, *supersedes: str, criteria: str = "달성 기준: (1) 빠르다 (2) README", reason: str = "사용자 결정으로 범위를 좁힘") -> dict:
        args = ["aim-criteria-revise", self.aim["id"], criteria]
        for item in supersedes:
            args += ["--supersedes", item]
        return self.run_cli(*args, "--actor", "owner", "--reason", reason)

    def test_revision_creates_closed_criteria_and_marks_the_replaced_ones(self):
        revised = self.revise(self.first["id"], self.second["id"])
        self.assertEqual((revised["status"], revised["closure_kind"]), ("closed", "criteria"))
        self.assertEqual((revised["proposed_by_actor_id"], revised["closed_by_actor_id"], revised["closure_reason"]),
                         ("owner", "owner", "사용자 결정으로 범위를 좁힘"))
        self.assertEqual(revised["direction"], "달성 기준: (1) 빠르다 (2) README")
        self.assertIsNone(revised["criteria_superseded_by_proposal_id"])
        proposals = self.proposals()
        for previous in (self.first, self.second):
            # The earlier criteria stay readable as closed criteria that point at the new ones.
            self.assertEqual(proposals[previous["id"]]["status"], "closed")
            self.assertEqual(proposals[previous["id"]]["closure_kind"], "criteria")
            self.assertEqual(proposals[previous["id"]]["criteria_superseded_by_proposal_id"], revised["id"])
        self.assertIsNone(proposals[revised["id"]]["criteria_superseded_by_proposal_id"])

    def test_projection_is_version_19_and_every_proposal_carries_the_field(self):
        open_proposal = self.run_cli("proposal-create", self.aim["id"], "접근안")
        projection = self.run_cli("ui-projection")
        self.assertEqual(projection["schema_version"], 19)
        self.assertTrue(projection["proposals"])
        for proposal in projection["proposals"]:
            self.assertIn("criteria_superseded_by_proposal_id", proposal)
            self.assertIsNone(proposal["criteria_superseded_by_proposal_id"])
        self.assertIn(open_proposal["id"], {item["id"] for item in projection["proposals"]})

    def test_the_journal_holds_one_event_that_replays_to_the_same_state(self):
        before = len(self.events())
        revised = self.revise(self.first["id"])
        events = self.events()[before:]
        self.assertEqual([event.event_type for event in events], ["AimCriteriaRevised"])
        self.assertEqual(events[0].payload, {"aim_id": self.aim["id"], "proposal_id": revised["id"],
                                             "criteria": "달성 기준: (1) 빠르다 (2) README",
                                             "superseded_proposal_ids": [self.first["id"]], "actor_id": "owner",
                                             "reason": "사용자 결정으로 범위를 좁힘"})
        expected = self.proposals()
        restarted = PersistentGoalLoop(SqliteEventRepository(self.database))
        restarted.rebuild_from_journal()
        self.assertEqual(self.proposals(), expected)
        self.assertEqual(restarted._loop.proposals[self.first["id"]].criteria_superseded_by_proposal_id, revised["id"])

    def test_the_same_command_id_records_once(self):
        loop = PersistentGoalLoop(SqliteEventRepository(self.database))
        first = loop.revise_aim_criteria(self.aim["id"], "새 기준", [self.first["id"]], "owner", "이유", command_id="revise-1")
        before = len(self.events())
        again = PersistentGoalLoop(SqliteEventRepository(self.database)).revise_aim_criteria(
            self.aim["id"], "새 기준", [self.first["id"]], "owner", "이유", command_id="revise-1")
        self.assertEqual(again.id, first.id)
        self.assertEqual(len(self.events()), before)

    def test_activity_names_the_revision_in_korean(self):
        revised = self.revise(self.first["id"])
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(self.database), "activity"])
        text = output.getvalue()
        self.assertIn("목표 달성 기준 개정", text)
        self.assertIn(f"{self.first['id']} → {revised['id']}", text)

    def test_refusals_record_nothing(self):
        open_proposal = self.run_cli("proposal-create", self.aim["id"], "열린 접근안")
        other_aim = self.run_cli("aim-create", "다른 목표")
        other = self.run_cli("proposal-create", other_aim["id"], "다른 기준")
        self.run_cli("proposal-close", other["id"], "--kind", "criteria", "--actor", "claude", "--reason", "기준")
        declined = self.run_cli("proposal-create", self.aim["id"], "버린 접근안")
        self.run_cli("proposal-close", declined["id"], "--kind", "declined", "--actor", "owner", "--reason", "안 함")
        before = len(self.events())
        aim = self.aim["id"]
        cases = [
            ["aim-criteria-revise", "aim-99", "기준", "--supersedes", self.first["id"], "--actor", "owner", "--reason", "r"],
            ["aim-criteria-revise", aim, "기준", "--actor", "owner", "--reason", "r"],  # no --supersedes
            ["aim-criteria-revise", aim, "기준", "--supersedes", open_proposal["id"], "--actor", "owner", "--reason", "r"],
            ["aim-criteria-revise", aim, "기준", "--supersedes", declined["id"], "--actor", "owner", "--reason", "r"],
            ["aim-criteria-revise", aim, "기준", "--supersedes", other["id"], "--actor", "owner", "--reason", "r"],
            ["aim-criteria-revise", aim, "기준", "--supersedes", "proposal-99", "--actor", "owner", "--reason", "r"],
            ["aim-criteria-revise", aim, " ", "--supersedes", self.first["id"], "--actor", "owner", "--reason", "r"],
            ["aim-criteria-revise", aim, "기준", "--supersedes", self.first["id"], "--actor", " ", "--reason", "r"],
            ["aim-criteria-revise", aim, "기준", "--supersedes", self.first["id"], "--actor", "owner", "--reason", " "],
            ["aim-criteria-revise", aim, "기준", "--supersedes", self.first["id"], "--supersedes", self.first["id"], "--actor", "owner", "--reason", "r"],
        ]
        for args in cases:
            self.fails(*args)
        self.assertEqual(len(self.events()), before)
        # Criteria already replaced cannot be replaced again.
        self.revise(self.first["id"])
        before = len(self.events())
        self.assertIn("already replaced", self.fails("aim-criteria-revise", aim, "기준", "--supersedes", self.first["id"],
                                                     "--actor", "owner", "--reason", "r"))
        self.assertEqual(len(self.events()), before)

    def test_only_an_open_aim_is_revised(self):
        abandoned = self.run_cli("aim-create", "그만둘 목표")
        criteria = self.run_cli("proposal-create", abandoned["id"], "기준")
        self.run_cli("proposal-close", criteria["id"], "--kind", "criteria", "--actor", "claude", "--reason", "기준")
        self.run_cli("aim-abandon", abandoned["id"], "--actor", "owner", "--reason", "그만둠")
        before = len(self.events())
        self.assertIn("open Aim", self.fails("aim-criteria-revise", abandoned["id"], "새 기준", "--supersedes", criteria["id"],
                                              "--actor", "owner", "--reason", "r"))
        self.assertEqual(len(self.events()), before)

    def test_help_and_ui_commands_name_the_command(self):
        from goal_loop_tool import web_ui
        self.assertIn("aim-criteria-revise", web_ui.UI_COMMANDS)
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(self.database), "help", "aim-criteria-revise"])
        self.assertIn("--supersedes", output.getvalue())


if __name__ == "__main__":
    unittest.main()
