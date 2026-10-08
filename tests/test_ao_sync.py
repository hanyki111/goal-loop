from contextlib import chdir, redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.persistent_cli import main


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


class AoSyncTests(unittest.TestCase):
    """Changes on the AO side become corrections of the cited AO Evidence, once per change and Goal."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        base = Path(self.directory.name).resolve()
        self.project = base / "repo"
        self.project.mkdir()
        self.workspace = base / "ao" / "workspace"
        self.ao_project = self.workspace / "projects" / "demo"
        (self.ao_project / "goals").mkdir(parents=True)
        self.write_ao(goal_status="completed", assumption_status="active")
        kept = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        self.env = patch.dict("os.environ", {**kept, "LOCALAPPDATA": str(base / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(base)}, clear=True)
        self.env.start()
        self.cwd = chdir(self.project)
        self.cwd.__enter__()
        run("project-init")
        aim = run("aim-create", "AO 변화 수신")
        proposal = run("proposal-create", aim["id"], "p")
        self.goal = run("goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce", "--scope", "s", "--criterion", "c", "--verification", "v", "--key", "k")
        attempt = run("attempt-start", self.goal["id"], "1", "claude", "AO에서 실행")
        run("attempt-complete", attempt["id"], "succeeded")
        self.evidence = run("ao-import", attempt["id"], "claude", str(self.ao_project), "fix-search")
        run("verification-begin", self.goal["id"], attempt["id"])
        self.verdict = run("verdict-submit", self.goal["id"], attempt["id"], "codex", "satisfied", self.evidence["id"], "--reason", "(1) 충족")

    def tearDown(self):
        self.cwd.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def write_ao(self, *, goal_status: str, assumption_status: str, affected=("fix-search",)) -> None:
        (self.ao_project / "goals" / "goals.json").write_text(json.dumps({"goals": [
            {"id": "fix-search", "title": "검색", "status": goal_status, "abandonReason": "다른 방식으로 대체" if goal_status == "abandoned" else None}]}), encoding="utf-8")
        (self.ao_project / "knowledge.json").write_text(json.dumps({"items": [
            {"id": "a-1", "kind": "assumption", "text": "색인이 정본이다", "status": assumption_status, "affectedGoals": list(affected), "supersededBy": "a-2"},
            {"id": "a-9", "kind": "assumption", "text": "관계없는 가정", "status": "invalidated", "affectedGoals": ["other"]}]}), encoding="utf-8")

    def sync(self, *extra: str) -> dict:
        return run("ao-sync", "--ao-workspace", str(self.workspace), "--actor", "claude", *extra)

    def validity(self) -> str:
        return next(item for item in run("ui-projection")["verdicts"] if item["id"] == self.verdict["id"])["validity"]

    def test_nothing_changed_records_nothing(self):
        self.assertEqual(self.sync()["changes"], [])
        self.assertEqual(self.validity(), "valid")

    def test_invalidated_assumption_puts_the_citing_verdict_under_review_once(self):
        self.write_ao(goal_status="completed", assumption_status="invalidated")
        dry = self.sync("--dry-run")["changes"]
        self.assertEqual([(row["change"], row["status"]) for row in dry], [("[ao-change:assumption-invalidated:demo/a-1]", "would-correct")])
        self.assertEqual(self.validity(), "valid")  # a dry run records nothing
        changes = self.sync()["changes"]
        self.assertEqual(changes[0]["status"], "corrected")
        self.assertEqual(changes[0]["corrections"][0]["evidence_id"], self.evidence["id"])
        self.assertEqual(self.validity(), "under_review")
        correction = run("ui-projection")["evidence_corrections"][0]
        self.assertIn("색인이 정본이다", correction["observation"])
        self.assertIn("대체 항목: a-2", correction["observation"])
        self.assertIn(f"[ao-change:assumption-invalidated:demo/a-1]@{self.goal['id']}", correction["observation"])
        self.assertEqual(self.sync()["changes"][0]["status"], "already-recorded")
        self.assertEqual(len(run("ui-projection")["evidence_corrections"]), 1)

    def test_reverted_or_abandoned_ao_goal_is_a_change(self):
        for status in ("reverted", "abandoned"):
            with self.subTest(status=status):
                self.write_ao(goal_status=status, assumption_status="active")
                row = self.sync("--dry-run")["changes"][0]
                self.assertEqual(row["change"], f"[ao-change:goal-{status}:demo/fix-search]")
                if status == "abandoned":
                    self.assertIn("사유: 다른 방식으로 대체", row["text"])

    def test_change_without_a_citing_verdict_is_reported_not_recorded(self):
        run("evidence-correct", self.evidence["id"], "claude", "다른 이유로 정정")  # the Verdict is now under review, not valid
        self.write_ao(goal_status="reverted", assumption_status="active")
        row = self.sync()["changes"][0]
        self.assertEqual(row["status"], "no-cited-evidence")
        self.assertEqual(len(run("ui-projection")["evidence_corrections"]), 1)

    def test_workspace_must_hold_the_linked_project(self):
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            run("ao-sync", "--ao-workspace", str(self.project), "--actor", "claude")


if __name__ == "__main__":
    unittest.main()
