from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.delegated_judging import judge_aim
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.persistent_cli import main
from goal_loop_tool.project import init_project, project_database
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import GoalLoopError


def run(database, *args):
    output = StringIO()
    with redirect_stdout(output):
        main(["--database", str(database), *args])
    return json.loads(output.getvalue().splitlines()[-1])


def fails(database, *args) -> str:
    errors = StringIO()
    with redirect_stderr(errors), redirect_stdout(StringIO()):
        try:
            main(["--database", str(database), *args])
        except SystemExit as raised:
            if raised.code not in (0, None):
                return errors.getvalue()
    raise AssertionError(f"command succeeded: {args}")


class ExternalEvaluationTests(unittest.TestCase):
    """A linked Aim's evaluation is imported once and cited by the umbrella Aim; its later invalidation is shown (goal-141)."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        root = Path(self.directory.name).resolve()
        self.umbrella_root, self.game_root = root / "notes", root / "game"
        self.umbrella_root.mkdir(); self.game_root.mkdir()
        self.umbrella = init_project(self.umbrella_root)[0]
        self.game = init_project(self.game_root)[0]
        self.side = run(self.umbrella, "aim-create", "사이드 프로젝트")
        self.game_aim = run(self.game, "aim-create", "게임을 만든다")
        self.link = run(self.umbrella, "aim-link-add", self.side["id"], "--project", str(self.game_root),
                        "--external-aim", self.game_aim["id"], "--actor", "claude")

    def tearDown(self):
        self.directory.cleanup()

    def achieve_game(self, key="release"):
        proposal = run(self.game, "proposal-create", self.game_aim["id"], key)
        goal = run(self.game, "goal-accept", proposal["id"], "--target", key, "--responsibility", "produce",
                   "--scope", "s", "--criterion", "c", "--verification", "v", "--key", key)
        attempt = run(self.game, "attempt-start", goal["id"], "1", "claude", "op")
        run(self.game, "attempt-complete", attempt["id"], "succeeded")
        evidence = run(self.game, "evidence-record", attempt["id"], "claude", "observed")
        run(self.game, "verification-begin", goal["id"], attempt["id"])
        verdict = run(self.game, "verdict-submit", goal["id"], attempt["id"], "codex", "satisfied", evidence["id"])
        evaluation = run(self.game, "aim-evaluate", self.game_aim["id"], "codex", "게임을 출시했다", verdict["id"])
        return evidence, verdict, evaluation

    def links(self):
        return run(self.umbrella, "aim-links")["links"]

    def test_import_then_cite_and_replay(self):
        self.assertIn("달성 상태가 아닙니다", fails(self.umbrella, "aim-link-import", self.link["id"], "--actor", "claude"))
        _, _, evaluation = self.achieve_game()
        imported = run(self.umbrella, "aim-link-import", self.link["id"], "--actor", "claude")
        self.assertEqual(imported["id"], "external-evaluation-1")
        self.assertEqual((imported["aim_id"], imported["reference_id"], imported["external_aim_id"], imported["external_evaluation_id"]),
                         (self.side["id"], self.link["id"], self.game_aim["id"], evaluation["id"]))
        self.assertEqual((imported["external_evaluator_actor_id"], imported["conclusion"], imported["imported_by_actor_id"]),
                         ("codex", "게임을 출시했다", "claude"))
        self.assertEqual(imported["project"], str(self.game_root))
        suggestion = next(action for action in run(self.umbrella, "next")["actions"] if action["kind"] == "evaluate_aim")
        self.assertEqual(suggestion["subject"]["id"], self.side["id"])
        self.assertIn(imported["id"], suggestion["command_argv"])
        umbrella_evaluation = run(self.umbrella, "aim-evaluate", self.side["id"], "codex", "사이드 프로젝트를 마쳤다", imported["id"])
        self.assertEqual(umbrella_evaluation["external_evaluation_ids"], [imported["id"]])
        replayed = PersistentGoalLoop(SqliteEventRepository(self.umbrella))
        self.assertEqual(replayed.aim_evaluations[umbrella_evaluation["id"]].external_evaluation_ids, (imported["id"],))
        self.assertTrue(replayed.is_currently_achieved(self.side["id"]))
        self.assertEqual(replayed.external_evaluations[imported["id"]].external_evaluation_id, evaluation["id"])
        projection = run(self.umbrella, "ui-projection")
        self.assertEqual(projection["schema_version"], 19)
        self.assertEqual([item["id"] for item in projection["external_evaluations"]], [imported["id"]])
        self.assertEqual(projection["aim_evaluations"][0]["external_evaluation_ids"], [imported["id"]])
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(self.umbrella), "activity"])
        self.assertIn("연결된 목표의 달성 평가 가져오기", output.getvalue())

    def test_refusals_record_nothing(self):
        _, _, evaluation = self.achieve_game()
        other = run(self.game, "aim-create", "다른 목표")
        self.assertIn("유효한 달성 평가가 아닙니다", fails(self.umbrella, "aim-link-import", self.link["id"], "--actor", "claude", "--evaluation", "aim-evaluation-9"))
        self.assertIn("Aim 연결이 아닙니다", fails(self.umbrella, "aim-link-import", "reference-9", "--actor", "claude"))
        document = run(self.umbrella, "reference-add", self.side["id"], "https://example.com/plan", "--actor", "claude")
        self.assertIn("goal-loop-aim", fails(self.umbrella, "aim-link-import", document["id"], "--actor", "claude"))
        run(self.umbrella, "aim-link-import", self.link["id"], "--actor", "claude", "--evaluation", evaluation["id"])
        self.assertIn("already imported", fails(self.umbrella, "aim-link-import", self.link["id"], "--actor", "claude"))
        removed = run(self.umbrella, "aim-link-add", self.side["id"], "--project", str(self.game_root), "--external-aim", other["id"], "--actor", "claude")
        run(self.umbrella, "reference-remove", removed["id"], "--actor", "owner", "--reason", "잘못 연결")
        self.assertIn("현재 Aim 연결이 아닙니다", fails(self.umbrella, "aim-link-import", removed["id"], "--actor", "claude"))
        shutil.move(str(self.game_root), str(self.game_root) + "-moved")
        self.assertIn("폴더가 없습니다", fails(self.umbrella, "aim-link-import", self.link["id"], "--actor", "claude"))
        self.assertEqual(len(run(self.umbrella, "ui-projection")["external_evaluations"]), 1)

    def test_only_imports_of_the_same_aim_can_be_cited(self):
        self.achieve_game()
        imported = run(self.umbrella, "aim-link-import", self.link["id"], "--actor", "claude")
        hobby = run(self.umbrella, "aim-create", "취미")
        with self.assertRaises(GoalLoopError) as raised, redirect_stdout(StringIO()):
            main(["--database", str(self.umbrella), "aim-evaluate", hobby["id"], "codex", "남의 근거", imported["id"]])
        self.assertIn("imported for that Aim", str(raised.exception))
        self.assertEqual(run(self.umbrella, "ui-projection")["aim_evaluations"], [])

    def test_invalidation_in_the_linked_journal_is_shown_but_changes_nothing_here(self):
        evidence, verdict, evaluation = self.achieve_game()
        imported = run(self.umbrella, "aim-link-import", self.link["id"], "--actor", "claude")
        umbrella_evaluation = run(self.umbrella, "aim-evaluate", self.side["id"], "codex", "마쳤다", imported["id"])
        [record] = self.links()
        self.assertEqual(record["imported_evaluations"], [{"id": imported["id"], "external_evaluation_id": evaluation["id"], "still_valid": True}])
        self.assertEqual(record["stale_imports"], 0)
        run(self.game, "evidence-correct", evidence["id"], "claude", "관찰이 틀렸다")
        review = run(self.game, "reviews")
        impact = review["impact_reviews"][0]["id"]
        run(self.game, "verdict-review-resolve", impact, "invalidate", "owner", "근거가 틀렸다")
        run(self.game, "aim-evaluation-review-resolve", evaluation["id"], impact, "invalidate", "owner", "판정이 무효")
        [record] = self.links()
        self.assertEqual(record["imported_evaluations"][0]["still_valid"], False)
        self.assertEqual(record["stale_imports"], 1)
        self.assertEqual(record["state"], "evaluation_invalidated")
        replayed = PersistentGoalLoop(SqliteEventRepository(self.umbrella))
        self.assertTrue(replayed.is_currently_achieved(self.side["id"]))  # never invalidated automatically
        self.assertIn(umbrella_evaluation["id"], replayed.aim_evaluations)
        shutil.rmtree(self.game_root)
        [record] = self.links()
        self.assertEqual((record["state"], record["imported_evaluations"][0]["still_valid"], record["stale_imports"]), ("broken", None, 0))

    def test_aim_evaluate_without_imports_keeps_its_event_shape(self):
        proposal = run(self.umbrella, "proposal-create", self.side["id"], "직접 한다")
        goal = run(self.umbrella, "goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce",
                   "--scope", "s", "--criterion", "c", "--verification", "v", "--key", "k")
        attempt = run(self.umbrella, "attempt-start", goal["id"], "1", "claude", "op")
        run(self.umbrella, "attempt-complete", attempt["id"], "succeeded")
        evidence = run(self.umbrella, "evidence-record", attempt["id"], "claude", "observed")
        run(self.umbrella, "verification-begin", goal["id"], attempt["id"])
        verdict = run(self.umbrella, "verdict-submit", goal["id"], attempt["id"], "codex", "satisfied", evidence["id"])
        run(self.umbrella, "aim-evaluate", self.side["id"], "codex", "직접 마쳤다", verdict["id"])
        event = next(item for item in SqliteEventRepository(self.umbrella).events() if item.event_type == "AimEvaluated")
        self.assertEqual(set(event.payload), {"aim_id", "evaluator_actor_id", "verdict_ids", "conclusion", "evaluation_id"})

    def test_delegated_aim_judgment_sees_and_may_cite_imports(self):
        self.achieve_game()
        imported = run(self.umbrella, "aim-link-import", self.link["id"], "--actor", "claude")
        seen = []
        def runner(prompt, ws, schema, out, log):
            seen.append(prompt)
            log.write_text("", encoding="utf-8")
            out.write_text(json.dumps({"aim_id": self.side["id"], "achieved": True, "criteria": [], "conclusion": "(1) 충족: 게임 출시",
                                       "cited": [imported["id"]], "checked": [], "not_checked": []}, ensure_ascii=False), encoding="utf-8")
        work = Path(self.directory.name) / "judging"; work.mkdir()
        with patch.dict("os.environ", {"GOAL_LOOP_CEILING_DIRECTORIES": str(Path(self.directory.name).resolve())}):
            result = judge_aim(self.umbrella, self.side["id"], runner=runner, base=work)
        self.assertIn("연결된 목표에서 가져온 달성 평가", seen[0])
        self.assertIn(imported["id"], seen[0])
        self.assertIn("게임을 출시했다", seen[0])
        self.assertTrue(result["recorded"])
        self.assertEqual(result["cited"], [imported["id"]])


if __name__ == "__main__":
    unittest.main()
