from contextlib import chdir, redirect_stdout
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


TIMELINE = """# 2026-10-07 Timeline
- 09:10 | goal-1 verified(verdict-2). goal-12와 aim-evaluation-3도 인용한다.
  이어지는 줄에서 attempt-4를 말한다.
- 10:00 | 기록 ID가 없는 항목
- 11:30 | proposal-5 등록. goal-1x나 my-goal-1, goal-1_b는 ID가 아니다.
"""
MODULE = """# 모듈

## 결정

결정 본문 첫 줄.

### 세부

세부 내용.

## 다른 절

다른 내용.
"""


class MemoryViewTests(unittest.TestCase):
    """Memory Tool files are shown read-only beside the records they cite, and only when the project has them."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        base = Path(self.directory.name).resolve()
        self.project = base / "repo"
        self.project.mkdir()
        kept = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        self.env = patch.dict("os.environ", {**kept, "LOCALAPPDATA": str(base / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(base)}, clear=True)
        self.env.start()
        self.cwd = chdir(self.project)
        self.cwd.__enter__()
        run("project-init")

    def tearDown(self):
        self.cwd.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def write_memory(self):
        timeline = self.project / ".memory" / "timeline" / "daily" / "2026-10"
        timeline.mkdir(parents=True)
        (timeline / "07.md").write_text(TIMELINE, encoding="utf-8")
        (timeline.parent / "2026-09").mkdir()
        (timeline.parent / "2026-09" / "30.md").write_text("# 2026-09-30 Timeline\n- 23:59 | goal-1 시작\n", encoding="utf-8")
        modules = self.project / ".memory" / "modules" / "core"
        modules.mkdir(parents=True)
        (modules / "core.md").write_text(MODULE, encoding="utf-8")

    def test_without_memory_folder_the_projection_has_nothing_and_no_error(self):
        projection = run("ui-projection")
        self.assertEqual(projection["schema_version"], 15)
        self.assertEqual((projection["memory_mentions"], projection["memory_documents"]), ([], {}))

    def test_timeline_entries_cite_whole_record_ids_newest_first(self):
        self.write_memory()
        mentions = run("ui-projection")["memory_mentions"]
        self.assertEqual([(item["date"], item["time"]) for item in mentions], [("2026-10-07", "11:30"), ("2026-10-07", "09:10"), ("2026-09-30", "23:59")])
        self.assertEqual(mentions[0]["ids"], ["proposal-5"])
        self.assertEqual(mentions[1]["ids"], ["goal-1", "verdict-2", "goal-12", "aim-evaluation-3", "attempt-4"])
        self.assertIn("이어지는 줄에서 attempt-4를 말한다.", mentions[1]["text"])
        self.assertEqual(mentions[1]["file"], ".memory/timeline/daily/2026-10/07.md")

    def test_referenced_memory_documents_are_excerpted_from_their_section(self):
        self.write_memory()
        aim = run("aim-create", "메모리 연결")
        (self.project / ".memory" / "modules" / "core" / "missing.md").write_text("# 곧 지운다\n", encoding="utf-8")
        (self.project / "docs").mkdir()
        (self.project / "docs" / "other.md").write_text("# 다른 문서\n", encoding="utf-8")
        for location in (".memory/modules/core/core.md#결정", ".memory/modules/core/core.md#없는 절", ".memory/modules/core/missing.md", "docs/other.md"):
            run("reference-add", aim["id"], location, "--actor", "claude")
        (self.project / ".memory" / "modules" / "core" / "missing.md").unlink()  # linked, then removed
        documents = run("ui-projection")["memory_documents"]
        section = documents[".memory/modules/core/core.md#결정"]
        self.assertTrue(section["exists"] and section["anchor_found"])
        self.assertIn("결정 본문 첫 줄.", section["excerpt"])
        self.assertIn("세부 내용.", section["excerpt"])
        self.assertNotIn("다른 내용.", section["excerpt"])
        whole = documents[".memory/modules/core/core.md#없는 절"]
        self.assertEqual((whole["exists"], whole["anchor_found"]), (True, False))
        self.assertTrue(whole["excerpt"].startswith("# 모듈"))
        self.assertFalse(documents[".memory/modules/core/missing.md"]["exists"])
        self.assertNotIn("docs/other.md", documents)
        self.assertFalse((self.project / ".memory" / "modules" / "core" / "core.md.bak").exists())

    def write_base(self, folder: Path):
        """Memory Tool content in an arbitrary knowledge folder (as Memory Tool would lay it out)."""
        timeline = folder / "timeline" / "daily" / "2026-10"
        timeline.mkdir(parents=True)
        (timeline / "07.md").write_text("# 2026-10-07 Timeline\n- 09:10 | goal-1 verified(verdict-2).\n", encoding="utf-8")
        (folder / "modules" / "core").mkdir(parents=True)
        (folder / "modules" / "core" / "core.md").write_text(MODULE, encoding="utf-8")

    def test_a_pointer_file_renames_the_knowledge_folder(self):
        self.write_base(self.project / "memory")
        (self.project / ".memory-tool.yml").write_text('# Memory Tool base folder pointer.\nbase: "memory"\n', encoding="utf-8")
        aim = run("aim-create", "이름을 바꾼 지식 폴더")
        run("reference-add", aim["id"], "memory/modules/core/core.md#결정", "--actor", "claude")
        projection = run("ui-projection")
        self.assertEqual([(item["time"], item["file"]) for item in projection["memory_mentions"]], [("09:10", "memory/timeline/daily/2026-10/07.md")])
        self.assertTrue(projection["memory_documents"]["memory/modules/core/core.md#결정"]["anchor_found"])
        self.assertTrue(run("memory-draft")["memory"])

    def test_the_project_root_itself_can_be_the_knowledge_folder(self):
        self.write_base(self.project)
        (self.project / ".memory-tool.yml").write_text("base: .   # the vault is the project root\n", encoding="utf-8")
        (self.project / "notes").mkdir()
        (self.project / "notes" / "plain.md").write_text("# 일반 문서\n", encoding="utf-8")
        aim = run("aim-create", "루트가 지식 폴더")
        run("reference-add", aim["id"], "modules/core/core.md", "--actor", "claude")
        run("reference-add", aim["id"], "notes/plain.md", "--actor", "claude")
        projection = run("ui-projection")
        self.assertEqual([item["file"] for item in projection["memory_mentions"]], ["timeline/daily/2026-10/07.md"])
        self.assertIn("modules/core/core.md", projection["memory_documents"])
        self.assertNotIn("notes/plain.md", projection["memory_documents"])  # not a Memory Tool content folder

    def test_environment_variables_choose_the_folder_only_for_this_project(self):
        self.write_base(self.project / "kb")
        with patch.dict("os.environ", {"MEMORY_TOOL_BASE": "kb"}):
            self.assertEqual(len(run("ui-projection")["memory_mentions"]), 1)
        with patch.dict("os.environ", {"MEMORY_TOOL_BASE": "kb", "MEMORY_TOOL_ROOT": str(self.project.parent / "elsewhere")}):
            self.assertEqual(run("ui-projection")["memory_mentions"], [])  # Memory Tool is pointed at another project
        with patch.dict("os.environ", {"MEMORY_TOOL_BASE": str(self.project / "kb")}):
            self.assertEqual(len(run("ui-projection")["memory_mentions"]), 1)

    def test_an_empty_or_foreign_folder_is_not_memory(self):
        (self.project / ".memory").mkdir()
        self.assertFalse(run("memory-draft")["memory"])  # a folder without Memory Tool content
        (self.project / ".memory-tool.yml").write_text('base: "."\n', encoding="utf-8")
        self.assertFalse(run("memory-draft")["memory"])  # the project root has no timeline/modules

    def judged(self, target: str, *results: tuple[str, str]) -> tuple[dict, list[dict]]:
        """A Goal judged once per (result, reason), each on its own Attempt."""
        aim = getattr(self, "aim", None) or run("aim-create", "초안 목표")
        self.aim = aim
        proposal = run("proposal-create", aim["id"], target)
        goal = run("goal-accept", proposal["id"], "--target", target, "--responsibility", "produce", "--scope", "s", "--criterion", "c",
                   "--verification", "v", "--key", target)
        verdicts = []
        for result, reason in results:
            attempt = run("attempt-start", goal["id"], "1", "claude", "실행")
            run("attempt-complete", attempt["id"], "succeeded")
            evidence = run("evidence-record", attempt["id"], "claude", "관찰")
            run("verification-begin", goal["id"], attempt["id"])
            verdicts.append(run("verdict-submit", goal["id"], attempt["id"], "codex", result, evidence["id"], "--reason", reason))
        return goal, verdicts

    def test_timeline_shorthand_lists_and_ranges_cite_every_number(self):
        from goal_loop_tool.memory_view import cited_ids
        self.assertEqual(cited_ids("verdict-1·5·6 판정, goal-38~40 과제, goal-21–22와 aim-evaluation-3을 본다"),
                         ["verdict-1", "verdict-5", "verdict-6", "goal-38", "goal-39", "goal-40", "goal-21", "goal-22", "aim-evaluation-3"])
        self.assertEqual(cited_ids("goal-5~4는 범위가 아니고 goal-1~999는 너무 넓다"), ["goal-5", "goal-1"])
        self.assertEqual(cited_ids("my-goal-1과 goal-1x는 ID가 아니다"), [])

    def test_drafts_cover_the_latest_uncited_verdict_of_each_goal_and_aim_evaluations(self):
        self.write_memory()
        first, (old, latest) = self.judged("첫 과제", ("not_satisfied", "(1) 충족: a\n(2) 미충족: 시험이 없다"), ("not_satisfied", "(1) 충족: a\n(2) 미충족: 문서가 없다"))
        cited_goal, (cited,) = self.judged("둘째 과제", ("satisfied", "(1) 충족"))
        wrong_goal, (wrong,) = self.judged("셋째 과제", ("satisfied", "(1) 충족"))
        number = lambda record: record["id"].rsplit("-", 1)[1]
        timeline = self.project / ".memory" / "timeline" / "daily" / "2026-10" / "07.md"
        timeline.write_text("# 2026-10-07 Timeline\n"
                            f"- 12:00 | {cited_goal['id']}({number(cited)}) verified, {first['id']}({number(wrong)})는 다른 과제의 판정 번호다.\n", encoding="utf-8")
        (timeline.parent.parent / "2026-09" / "30.md").unlink()
        evaluation = run("aim-evaluate", self.aim["id"], "codex", "달성", cited["id"])
        drafts = run("memory-draft", "--limit", "50")
        self.assertTrue(drafts["memory"])
        by_record = {item["record_id"]: item for item in drafts["memory_drafts"]}
        self.assertNotIn(old["id"], by_record)  # an earlier Verdict of the same Goal is history
        self.assertNotIn(cited["id"], by_record)  # goal-N(M) names this Goal's Verdict
        self.assertIn(wrong["id"], by_record)  # the number belongs to another Goal, so it does not count
        self.assertEqual(by_record[latest["id"]]["text"], f"{first['id']} not_satisfied({latest['id']}, codex): 첫 과제 — (2) 미충족: 문서가 없다")
        self.assertEqual(by_record[latest["id"]]["command"], f'python -m memory_tool record "{by_record[latest["id"]]["text"]}"')
        self.assertEqual(drafts["memory_drafts"][0]["record_id"], evaluation["id"])
        self.assertEqual(drafts["memory_drafts"][0]["text"], f"{self.aim['id']} 달성({evaluation['id']}, codex): 초안 목표")
        self.assertEqual(run("memory-draft", "--record", wrong["id"])["memory_drafts"][0]["record_id"], wrong["id"])
        self.assertEqual(run("ui-projection")["memory_drafts"], run("memory-draft", "--limit", "50")["memory_drafts"])
        self.assertEqual(sorted(path.name for path in (self.project / ".memory" / "timeline" / "daily" / "2026-10").iterdir()), ["07.md"])

    def test_without_memory_there_are_no_drafts(self):
        self.judged("과제", ("satisfied", "(1) 충족"))
        drafts = run("memory-draft")
        self.assertEqual((drafts["memory_drafts"], drafts["memory"]), ([], False))
        self.assertFalse((self.project / ".memory").exists())


if __name__ == "__main__":
    unittest.main()
