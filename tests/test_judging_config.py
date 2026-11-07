from contextlib import chdir, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.delegated_judging import (JudgingError, NOTES_RULES, judge_aim, judge_goal, judging_config, main as judging_main,
                                              prepare_workspace)
from goal_loop_tool.persistent_cli import main


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


def answer_for(goal_id: str) -> dict:
    return {"goal_id": goal_id, "result": "satisfied", "criteria": [{"item": "(1) 노트가 있다", "met": "met", "basis": "읽음"}],
            "reason": "(1) 충족: 노트를 읽었다", "checked": ["노트"], "not_checked": []}


def fake_agent(answer: dict, seen: list):
    def runner(prompt, ws, schema, out, log):
        seen.append({"prompt": prompt, "ws": ws})
        log.write_text("agent log", encoding="utf-8")
        out.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return runner


class JudgingConfigTests(unittest.TestCase):
    """.goal-loop/judging.json sets a project's copy exclusions, attachments-only default, and rules (goal-140)."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        base = Path(self.directory.name).resolve()
        self.project = base / "notes"
        for folder in (".memory/modules/AI", ".memory/.npm/_cacache", ".memory/cache", ".memory/.embeddings", ".embeddings", "tools/sub"):
            (self.project / folder).mkdir(parents=True)
        for file in (".memory/modules/AI/AI.md", ".memory/.npm/_cacache/blob", ".memory/cache/notion_pages.json",
                     ".memory/.embeddings/index.json", ".embeddings/e.json", "nvim.appimage", "tools/sub/old.appimage", "tools/keep.txt"):
            (self.project / file).write_text("x", encoding="utf-8")
        self.work = base / "judging"
        self.work.mkdir()
        kept = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        self.env = patch.dict("os.environ", {**kept, "LOCALAPPDATA": str(base / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(base)}, clear=True)
        self.env.start()
        self.cwd = chdir(self.project)
        self.cwd.__enter__()
        run("project-init")
        self.database = self.project / ".goal-loop" / "journal.sqlite3"
        self.aim = run("aim-create", "노트 정리")
        proposal = run("proposal-create", self.aim["id"], "AI 노트를 정리한다")
        self.goal = run("goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce", "--scope", "s",
                        "--criterion", "(1) 노트가 있다", "--verification", "v", "--key", "k")
        attempt = run("attempt-start", self.goal["id"], "1", "claude", "write")
        run("attempt-complete", attempt["id"], "succeeded")
        run("evidence-record", attempt["id"], "claude", "노트를 썼다")
        run("verification-begin", self.goal["id"], attempt["id"])

    def tearDown(self):
        self.cwd.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def configure(self, settings) -> Path:
        path = self.database.parent / "judging.json"
        path.write_text(settings if isinstance(settings, str) else json.dumps(settings), encoding="utf-8")
        return path

    def judge(self, **options):
        seen = []
        result = judge_goal(self.database, self.goal["id"], runner=fake_agent(answer_for(self.goal["id"]), seen), base=self.work,
                            record=False, **options)
        return result, seen[0]

    def test_without_the_file_nothing_changes(self):
        self.assertEqual(judging_config(self.database).summary(), {"judging_config": None, "copy_exclude": [], "attachments_only": False, "rules": "code"})
        result, seen = self.judge()
        self.assertTrue((seen["ws"].project / ".memory/.npm/_cacache/blob").exists())
        self.assertNotIn("코드가 아닌 일", seen["prompt"])
        self.assertNotIn("첨부만 사용", seen["prompt"])
        self.assertEqual((result["judging_config"], result["attachments_only"], result["rules"]), (None, False, "code"))

    def test_copy_exclude_matches_paths_from_the_root_and_bare_names_anywhere(self):
        source = self.configure({"copy_exclude": [".memory/.npm", ".memory/cache", ".memory/.embeddings", ".embeddings", "*.appimage"]})
        result, seen = self.judge()
        copy = seen["ws"].project
        for gone in (".memory/.npm", ".memory/cache", ".memory/.embeddings", ".embeddings", "nvim.appimage", "tools/sub/old.appimage"):
            self.assertFalse((copy / gone).exists(), gone)
        for kept in (".memory/modules/AI/AI.md", "tools/keep.txt"):
            self.assertTrue((copy / kept).exists(), kept)
        self.assertFalse((copy / ".goal-loop" / "judging.json").exists())  # the live journal folder is still never copied
        self.assertEqual(result["judging_config"], str(source))
        self.assertEqual(result["copy_exclude"], [".memory/.npm", ".memory/cache", ".memory/.embeddings", ".embeddings", "*.appimage"])

    def test_a_pattern_with_a_slash_is_anchored_at_the_project_root(self):
        (self.project / "tools/.memory/cache").mkdir(parents=True)
        self.configure({"copy_exclude": [".memory/cache"]})
        _, seen = self.judge()
        self.assertFalse((seen["ws"].project / ".memory/cache").exists())
        self.assertTrue((seen["ws"].project / "tools/.memory/cache").exists())

    def test_attachments_only_default_and_command_line_override(self):
        self.configure({"attachments_only": True})
        result, seen = self.judge()
        self.assertIn("첨부만 사용", seen["prompt"])
        self.assertTrue(result["attachments_only"])
        result, seen = self.judge(attachments_only=False)
        self.assertNotIn("첨부만 사용", seen["prompt"])
        self.assertFalse(result["attachments_only"])
        self.configure({"attachments_only": False})
        result, seen = self.judge(attachments_only=True)
        self.assertIn("첨부만 사용", seen["prompt"])

    def test_command_line_flags_reach_judge_goal(self):
        self.configure({"attachments_only": True})
        calls = []
        def fake_judge_goal(database, goal_id, **options):
            calls.append(options["attachments_only"])
            return {}
        with patch("goal_loop_tool.delegated_judging.judge_goal", fake_judge_goal), redirect_stdout(StringIO()):
            for flags in ([], ["--attachments-only"], ["--no-attachments-only"]):
                judging_main(["goal", self.goal["id"], *flags])
        self.assertEqual(calls, [None, True, False])  # None: follow judging.json

    def test_notes_rules_are_added_to_goal_and_aim_prompts(self):
        self.configure({"rules": "notes"})
        result, seen = self.judge()
        self.assertIn(NOTES_RULES, seen["prompt"])
        self.assertIn("판정 규칙(Decision 5)", seen["prompt"])  # the three results stay
        self.assertEqual(result["rules"], "notes")
        aim_seen = []
        aim_result = judge_aim(self.database, self.aim["id"], runner=fake_agent({"aim_id": self.aim["id"], "achieved": False, "criteria": [],
                               "conclusion": "아직", "cited": [], "checked": [], "not_checked": []}, aim_seen), base=self.work)
        self.assertIn(NOTES_RULES, aim_seen[0]["prompt"])
        self.assertEqual(aim_result["rules"], "notes")

    def test_aim_judgment_reports_attachments_only_as_not_applied(self):
        self.configure({"attachments_only": True})
        seen = []
        result = judge_aim(self.database, self.aim["id"], runner=fake_agent({"aim_id": self.aim["id"], "achieved": False, "criteria": [],
                           "conclusion": "아직", "cited": [], "checked": [], "not_checked": []}, seen), base=self.work)
        self.assertFalse(result["attachments_only"])
        self.assertNotIn("이번 판정의 제약: 첨부만 사용", seen[0]["prompt"])

    def test_mistakes_are_refused_before_any_copy_names_the_file(self):
        for settings, words in (('{"copy_exclude": [', "읽지 못했습니다"), ([], "JSON 객체"), ({"copy_excludes": []}, "copy_excludes"),
                                ({"copy_exclude": ".memory"}, "copy_exclude"), ({"copy_exclude": [""]}, "copy_exclude"),
                                ({"attachments_only": "yes"}, "attachments_only"), ({"rules": "prose"}, "rules")):
            path = self.configure(settings)
            with self.assertRaises(JudgingError) as raised:
                self.judge()
            self.assertIn(str(path), str(raised.exception))
            self.assertIn(words, str(raised.exception))
        self.assertEqual(list(self.work.iterdir()), [])  # refused before a copy was made

    def test_prepare_workspace_alone_applies_the_exclusions(self):
        ws = prepare_workspace(self.project, self.database, self.work, copy_exclude=(".memory/.npm", "*.appimage"))
        self.assertFalse((ws.project / ".memory/.npm").exists())
        self.assertFalse((ws.project / "nvim.appimage").exists())
        self.assertTrue((ws.project / ".memory/cache/notion_pages.json").exists())


if __name__ == "__main__":
    unittest.main()
