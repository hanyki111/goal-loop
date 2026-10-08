from contextlib import chdir, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.model import EvidenceAttachment
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.persistent_cli import main
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import GoalLoop, GoalLoopError


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


class ReferenceAndAttachmentCliTests(unittest.TestCase):
    """References point at living documents; attachments preserve what was observed."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.project = Path(self.directory.name).resolve() / "repo"
        (self.project / "docs").mkdir(parents=True)
        (self.project / "docs" / "design.md").write_text("v1", encoding="utf-8")
        self.env = patch.dict("os.environ", {"LOCALAPPDATA": str(self.project.parent / "appdata")}, clear=True)
        self.env.start()
        self.cwd = chdir(self.project)
        self.cwd.__enter__()
        run("project-init")
        self.aim = run("aim-create", "Track references")
        proposal = run("proposal-create", self.aim["id"], "approach")
        self.goal = run("goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce", "--scope", "s", "--criterion", "c", "--verification", "v", "--key", "k")

    def tearDown(self):
        self.cwd.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def projection(self) -> dict:
        return run("ui-projection")

    def test_reference_add_remove_and_projection(self):
        added = run("reference-add", self.goal["id"], "docs/design.md#결정", "--label", "설계", "--actor", "owner")
        self.assertEqual(added["location"], "docs/design.md#결정")
        aim_reference = run("reference-add", self.aim["id"], "https://example.com/spec", "--actor", "claude")
        goal = next(item for item in self.projection()["goals"] if item["id"] == self.goal["id"])
        self.assertEqual(goal["references"], [{"id": added["id"], "location": "docs/design.md#결정", "label": "설계", "actor": "owner",
                                               "resolved": str(self.project / "docs" / "design.md"), "exists": True}])
        (self.project / "docs" / "design.md").unlink()
        goal = next(item for item in self.projection()["goals"] if item["id"] == self.goal["id"])
        self.assertFalse(goal["references"][0]["exists"])
        run("reference-remove", added["id"], "--actor", "owner", "--reason", "moved")
        projection = self.projection()
        self.assertEqual(next(item for item in projection["goals"] if item["id"] == self.goal["id"])["references"], [])
        self.assertEqual(next(item for item in projection["aims"] if item["id"] == self.aim["id"])["references"][0]["id"], aim_reference["id"])
        self.assertEqual(projection["project_root"], str(self.project))
        with self.assertRaises(GoalLoopError):
            run("reference-remove", added["id"], "--actor", "owner")
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            run("reference-add", self.goal["id"], "docs/typo.md", "--actor", "owner")
        with self.assertRaises(GoalLoopError):
            run("reference-add", "attempt-1", "https://example.com", "--actor", "owner")
        restarted = PersistentGoalLoop(SqliteEventRepository(self.project / ".goal-loop" / "journal.sqlite3"))
        self.assertEqual(restarted.references[added["id"]].removal_reason, "moved")

    def test_evidence_attachment_is_copied_preserved_and_backed_up(self):
        attempt = run("attempt-start", self.goal["id"], "1", "claude", "run tests")
        run("attempt-complete", attempt["id"], "succeeded")
        log = self.project / "pytest.log"
        log.write_text("123 passed", encoding="utf-8")
        evidence = run("evidence-record", attempt["id"], "claude", "123 tests passed", "--attach", "pytest.log")
        log.write_text("rerun output", encoding="utf-8")
        record = next(item for item in self.projection()["evidence"] if item["id"] == evidence["id"])
        attachment = record["attachments"][0]
        self.assertEqual((attachment["name"], attachment["size"], attachment["status"]), ("pytest.log", 10, "preserved"))
        self.assertEqual(Path(attachment["path"]).read_text(encoding="utf-8"), "123 passed")
        restarted = PersistentGoalLoop(SqliteEventRepository(self.project / ".goal-loop" / "journal.sqlite3"))
        self.assertEqual(restarted.evidence[evidence["id"]].attachments[0].sha256, attachment["sha256"])
        backup = run("backup", str(self.project.parent / "backup" / "journal.sqlite3"))
        self.assertTrue((Path(backup["artifacts"]) / attachment["sha256"] / "pytest.log").is_file())
        # A backup made as another project's journal keeps its attachments where that journal looks for them.
        copy = self.project.parent / "copy" / ".goal-loop" / "journal.sqlite3"
        self.assertEqual(Path(run("backup", str(copy))["artifacts"]), copy.parent / "artifacts")
        restored = next(item for item in run("--database", str(copy), "ui-projection")["evidence"] if item["id"] == evidence["id"])
        self.assertEqual(restored["attachments"][0]["status"], "preserved")
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            run("evidence-record", attempt["id"], "claude", "missing file", "--attach", "nope.log")

    def test_evidence_without_attachments_keeps_the_legacy_payload_and_fingerprint(self):
        attempt = run("attempt-start", self.goal["id"], "1", "claude", "run")
        run("attempt-complete", attempt["id"], "succeeded")
        run("evidence-record", attempt["id"], "claude", "plain")
        payloads = [event.payload for event in SqliteEventRepository(self.project / ".goal-loop" / "journal.sqlite3").events() if event.event_type == "EvidenceRecorded"]
        self.assertNotIn("attachments", payloads[0])


class AttachmentCoreRulesTests(unittest.TestCase):
    def test_core_rejects_malformed_or_duplicate_attachments(self):
        from goal_loop_tool.model import AttemptOutcome, GoalIdentity
        from goal_loop_tool.service import ContractDraft
        loop = GoalLoop()
        aim = loop.create_aim("a")
        goal = loop.accept_proposal(loop.create_proposal(aim.id, "p").id, GoalIdentity(aim.id, "t", "produce"), ContractDraft("s", "c", "v"), idempotency_key="k")
        attempt = loop.start_attempt(goal.id, 1, "executor", "op")
        loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        good = EvidenceAttachment("log.txt", "a" * 64, 3)
        with self.assertRaises(GoalLoopError):
            loop.record_evidence(attempt.id, "executor", "obs", (EvidenceAttachment("log.txt", "XYZ", 3),))
        with self.assertRaises(GoalLoopError):
            loop.record_evidence(attempt.id, "executor", "obs", (good, good))
        self.assertEqual(loop.record_evidence(attempt.id, "executor", "obs", (good,)).attachments, (good,))


if __name__ == "__main__":
    unittest.main()
