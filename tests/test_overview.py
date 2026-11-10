from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.overview import ACTION_NAMES, render_overview
from goal_loop_tool.persistent_cli import main


def capture(*args: str) -> str:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return output.getvalue()


def run(*args: str) -> dict:
    return json.loads(capture(*args).splitlines()[-1])


class OverviewTests(unittest.TestCase):
    """goal-loop overview draws the journal as Korean text without writing to it."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.env = patch.dict("os.environ", {"LOCALAPPDATA": str(self.root / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(self.root)}, clear=True)
        self.env.start()
        self.db = str(self.root / "journal.sqlite3")
        self.cli("aim-create", "상위 목표")                                    # aim-1
        self.cli("aim-create", "진행 중인 하위 목표", "--parent", "aim-1")       # aim-2
        self.cli("aim-create", "달성할 하위 목표", "--parent", "aim-1")         # aim-3
        self.goal("aim-2", "실행 중인 과제", "running")                          # goal-1, attempt-1
        self.goal("aim-2", "검증된 과제", "verified")                           # goal-2, attempt-2
        self.goal("aim-3", "달성 근거 과제", "verified")                         # goal-3, attempt-3
        self.cli("aim-evaluate", "aim-3", "codex", "달성", "verdict-2")
        self.cli("goal-hold-add", "goal-1", "--actor", "user", "--reason", "사용자 확인 대기")

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def cli(self, *args: str) -> dict:
        return run("--database", self.db, *args)

    def goal(self, aim: str, target: str, state: str) -> None:
        proposal = self.cli("proposal-create", aim, f"{target} 접근", "--actor", "claude")["id"]
        goal = self.cli("goal-accept", proposal, "--target", target, "--responsibility", "produce", "--scope", "s",
                        "--criterion", "c", "--verification", "v", "--key", target)["id"]
        attempt = self.cli("attempt-start", goal, "1", "claude", "실행")["id"]
        if state == "running":
            return
        self.cli("attempt-complete", attempt, "succeeded")
        evidence = self.cli("evidence-record", attempt, "claude", "관찰")["id"]
        self.cli("verification-begin", goal, attempt)
        self.cli("verdict-submit", goal, attempt, "codex", "satisfied", evidence, "--reason", "충족")

    def overview(self, *args: str) -> str:
        return capture("--database", self.db, "overview", *args)

    def test_default_folds_finished_records_and_lists_actions(self):
        checkpoint = self.cli("status")["checkpoint"]
        text = self.overview()
        self.assertEqual(self.cli("status")["checkpoint"], checkpoint)
        self.assertIn("aim-1 [진행 중] 상위 목표", text)
        self.assertIn("  aim-2 [진행 중] 진행 중인 하위 목표", text)
        self.assertIn("goal-1 [실행 진행 중 · 실행 중 attempt-1(claude) · 보류: 사용자 확인 대기]", text)
        self.assertNotIn("goal-2 [", text)
        self.assertIn("(접힌 과제: 검증됨 1개)", text)
        self.assertIn("(접힌 목표: 달성됨 1개): aim-3", text)
        self.assertNotIn("달성 근거 과제", text)
        self.assertIn("지금 할 일 (", text)
        self.assertIn("요약: 목표 3개(진행 중 2, 달성됨 1) · 과제 3개(실행 진행 중 1, 검증됨 2)", text)

    def test_all_expands_and_aim_scope_limits(self):
        text = self.overview("--all")
        self.assertIn("goal-2 [검증됨 · 충족 verdict-1]", text)
        self.assertIn("aim-3 [달성됨] 달성할 하위 목표", text)
        self.assertIn("goal-3 [검증됨 · 충족 verdict-2]", text)
        scoped = self.overview("--aim", "aim-3")
        self.assertIn("(aim-3 범위)", scoped)
        self.assertTrue(any(line.startswith("aim-3 [달성됨]") for line in scoped.splitlines()))
        self.assertNotIn("aim-2", scoped)
        self.assertIn("요약: 목표 1개(달성됨 1) · 과제 1개(검증됨 1)", scoped)

    def test_unknown_aim_scope_is_an_error(self):
        with redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit):
            self.overview("--aim", "aim-99")
        self.assertIn("aim-99", error.getvalue())

    def test_watch_redraws_until_interrupted(self):
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise KeyboardInterrupt

        with patch("goal_loop_tool.persistent_cli.time.sleep", sleep):
            text = self.overview("--watch", "2")
        self.assertEqual(sleeps, [2.0, 2.0])
        self.assertEqual(text.count("\x1b[2J\x1b[H"), 2)
        self.assertIn("2초마다 다시 그림", text)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            self.overview("--watch", "0")

    def test_stale_checkpoint_is_not_moved(self):
        from goal_loop_tool.repository import SqliteEventRepository
        repo = SqliteEventRepository(self.db)
        repo.mark_projection_checkpoint(0)
        self.overview()
        self.overview("--all")
        self.assertEqual(repo.projection_checkpoint(), 0)
        # Other reads keep their behavior: they bring the checkpoint up to date (the extension's change check relies on it).
        capture("--database", self.db, "ui-projection")
        self.assertEqual(repo.projection_checkpoint(), repo.journal_last_sequence())

    def test_every_next_action_kind_has_a_korean_name(self):
        source = Path(main.__code__.co_filename).read_text(encoding="utf-8")
        kinds = set(re.findall(r'\badd\(\s*"([a-z_]+)"', source)) | set(re.findall(r'"kind":\s*"([a-z_]+)",\s*\n\s*"subject"', source))
        self.assertTrue({"start_repair_attempt", "import_linked_evaluation"} <= kinds)
        self.assertEqual(sorted(kinds - set(ACTION_NAMES)), [])

    def test_repair_attempt_is_named_after_not_satisfied(self):
        proposal = self.cli("proposal-create", "aim-2", "보완 접근", "--actor", "claude")["id"]
        goal = self.cli("goal-accept", proposal, "--target", "보완할 과제", "--responsibility", "produce", "--scope", "s",
                        "--criterion", "c", "--verification", "v", "--key", "repair")["id"]
        attempt = self.cli("attempt-start", goal, "1", "claude", "실행")["id"]
        self.cli("attempt-complete", attempt, "succeeded")
        evidence = self.cli("evidence-record", attempt, "claude", "관찰")["id"]
        self.cli("verification-begin", goal, attempt)
        self.cli("verdict-submit", goal, attempt, "codex", "not_satisfied", evidence, "--reason", "미충족")
        text = self.overview()
        self.assertIn(f"보완 실행 시작 1건: {goal}", text)
        self.assertNotIn("start_repair_attempt", text)

    def test_empty_projection(self):
        text = render_overview({"aims": [], "goals": []}, [])
        self.assertIn("목표가 아직 없습니다", text)
        self.assertIn("지금 할 일 (0건)\n  없음", text)


if __name__ == "__main__":
    unittest.main()
