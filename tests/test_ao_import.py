from contextlib import chdir, redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.persistent_cli import main
from goal_loop_tool.service import GoalLoopError


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def git(repo_args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *repo_args], cwd=cwd, check=True, capture_output=True)


class AoImportCliTests(unittest.TestCase):
    """ao-import reads an AO Goal's results and records them as Evidence; AO is never written."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        base = Path(self.directory.name).resolve()
        self.ao_project = base / "ao" / "workspace" / "projects" / "demo"
        write_json(self.ao_project / "goals" / "goals.json", {"goals": [
            {"id": "goal-a", "title": "검색 수정", "status": "awaiting_review", "objective": "테스트가 통과한다",
             "reviewReason": "죽은 단언이 남았다", "path": "goals/goal-a"},
            {"id": "goal-ab", "title": "다른 Goal", "status": "completed", "path": "goals/goal-ab"},
        ], "updatedAt": "2026-10-04T00:00:00Z"})
        write_json(self.ao_project / "goals" / "goal-a" / "goal.json", {"id": "goal-a", "status": "ready"})
        session = self.ao_project / "sessions" / "run-1"
        write_json(session / "session.json", {"id": "run-1", "goalId": "goal-a", "status": "completed", "createdAt": "2026-10-01T00:00:00Z"})
        events = [
            {"type": "agent:output:chunk", "payload": {"text": "verifier:result 처럼 보이는 출력"}},
            {"type": "verifier:result", "timestamp": "t1", "payload": {"verifierType": "command", "passed": False, "reason": "exit 1"}},
            {"type": "verifier:result", "timestamp": "t2", "payload": {"verifierType": "command", "passed": True, "reason": "exit 0"}},
            {"type": "verifier:result", "timestamp": "t3", "payload": {"verifierType": "advisory_review", "passed": False, "reason": "우려"}},
            {"type": "engine:notice", "timestamp": "t4", "payload": {"category": "completion:advisory", "message": "완료 판단을 상위 도구에 맡김",
                                                                      "clause": "objective-achievement", "reason": "판정자가 미달로 봄"}},
            {"type": "engine:notice", "timestamp": "t5", "payload": {"category": "new-test:pre-change-run", "message": "completion:advisory가 아닌 알림"}},
        ]
        (session / "events.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in events), encoding="utf-8")
        write_json(self.ao_project / "sessions" / "run-2" / "session.json", {"id": "run-2", "goalId": "goal-ab", "status": "completed"})
        earlier = self.ao_project / "sessions" / "run-0"
        write_json(earlier / "session.json", {"id": "run-0", "goalId": "goal-a", "status": "failed", "createdAt": "2026-09-30T00:00:00Z"})
        (earlier / "events.jsonl").write_text(json.dumps({"type": "verifier:result", "timestamp": "t0", "payload": {"verifierType": "command", "passed": False, "reason": "exit 2"}}), encoding="utf-8")
        self.before = {path: path.read_bytes() for path in self.ao_project.rglob("*") if path.is_file()}

        self.project = base / "repo"
        self.project.mkdir()
        # Keep PATH (and SYSTEMROOT for Windows subprocesses) so git stays reachable for ao-import.
        kept = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        self.env = patch.dict("os.environ", {**kept, "LOCALAPPDATA": str(base / "appdata")}, clear=True)
        self.env.start()
        self.cwd = chdir(self.project)
        self.cwd.__enter__()
        run("project-init")
        aim = run("aim-create", "AO 결과 추적")
        proposal = run("proposal-create", aim["id"], "approach")
        goal = run("goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce", "--scope", "s", "--criterion", "c", "--verification", "v", "--key", "k")
        self.attempt = run("attempt-start", goal["id"], "1", "claude", "AO에서 실행")

    def tearDown(self):
        self.cwd.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def make_repo(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        work = self.ao_project.parent.parent / "work"
        work.mkdir()
        git(["init", "-q"], work)
        (work / "src" / "__tests__").mkdir(parents=True)
        (work / "src" / "search.ts").write_text("export const find = () => 1\n", encoding="utf-8")
        (work / "src" / "__tests__" / "search.test.ts").write_text("expect(find()).toBe(1)\n", encoding="utf-8")
        git(["add", "-A"], work)
        identity = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
        git([*identity, "commit", "-q", "-m", "수정 A\n\nGoal-Id: goal-a\nGoal-Status: awaiting_review"], work)
        git([*identity, "commit", "-q", "--allow-empty", "-m", "수정 AB\n\nGoal-Id: goal-ab\nGoal-Status: completed"], work)
        git(["clone", "-q", "--bare", str(work), str(self.ao_project.parent.parent / "repos" / "demo.git")], work.parent)

    def test_import_records_summary_and_attachments_without_touching_ao(self):
        self.make_repo()
        run("attempt-complete", self.attempt["id"], "succeeded")
        evidence = run("ao-import", self.attempt["id"], "claude", str(self.ao_project), "goal-a")
        observation = evidence["observation"]
        self.assertIn("AO Goal goal-a", observation)
        self.assertIn("AO 상태 awaiting_review", observation)
        self.assertIn("검증기 2종 3회 실행, 도중 실패 2회(advisory_review, command)", observation)
        self.assertIn("마지막 결과가 실패인 검증기: advisory_review", observation)
        self.assertIn("「수정 A」 (Goal-Status: awaiting_review): 변경 파일 2개, 테스트 파일 src/__tests__/search.test.ts.", observation)
        self.assertIn("이전 세션 run-0(failed): 검증기 1회, 마지막 결과가 실패인 검증기: command.", observation)
        self.assertNotIn("수정 AB", observation)  # --grep matches substrings; only the exact trailer counts.
        self.assertIn("리뷰 사유: 죽은 단언이 남았다", observation)
        attached = {item["name"]: self.project / ".goal-loop" / "artifacts" / item["sha256"] / item["name"] for item in evidence["attachments"]}
        patch_name = next(name for name in attached if name.startswith("ao-commit-"))
        self.assertEqual(list(attached), ["ao-goal-demo-goal-a.json", "ao-index-entry-goal-a.json", "ao-verifier-events-run-0.jsonl",
                                          "ao-verifier-events-run-1.jsonl", patch_name, "goal-goal-a.json", "session-run-0.json", "session-run-1.json"])
        snapshot = json.loads(attached["ao-goal-demo-goal-a.json"].read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in snapshot["sessions"]], ["run-0", "run-1"])
        self.assertEqual(snapshot["goal"]["reviewReason"], "죽은 단언이 남았다")
        self.assertIn("Goal-Id: goal-a", snapshot["commits"][0]["message"])
        self.assertEqual(snapshot["commits"][0]["test_files"], ["src/__tests__/search.test.ts"])
        patch_text = attached[patch_name].read_text(encoding="utf-8")
        self.assertIn("Goal-Id: goal-a", patch_text)
        self.assertIn("+expect(find()).toBe(1)", patch_text)
        self.assertEqual(json.loads(attached["ao-index-entry-goal-a.json"].read_text(encoding="utf-8"))["reviewReason"], "죽은 단언이 남았다")
        self.assertEqual(len(attached["ao-verifier-events-run-1.jsonl"].read_text(encoding="utf-8").splitlines()), 3)
        self.assertEqual({path: path.read_bytes() for path in self.ao_project.rglob("*") if path.is_file()}, self.before)

    def test_external_verdict_advisories_are_kept_apart_from_verifiers_and_summarized(self):
        self.make_repo()
        run("attempt-complete", self.attempt["id"], "succeeded")
        evidence = run("ao-import", self.attempt["id"], "claude", str(self.ao_project), "goal-a")
        self.assertIn("AO 참고 판정(완료를 막지 않음, --external-verdict) 1건: objective-achievement.", evidence["observation"])
        self.assertIn("검증기 2종 3회 실행", evidence["observation"])  # advisories are not counted as verifier runs
        attached = {item["name"]: self.project / ".goal-loop" / "artifacts" / item["sha256"] / item["name"] for item in evidence["attachments"]}
        snapshot = json.loads(attached["ao-goal-demo-goal-a.json"].read_text(encoding="utf-8"))
        sessions = {item["id"]: item for item in snapshot["sessions"]}
        self.assertEqual(sessions["run-1"]["advisories"], [{"at": "t4", "clause": "objective-achievement", "reason": "판정자가 미달로 봄"}])
        self.assertEqual(sessions["run-0"]["advisories"], [])
        self.assertEqual(len(attached["ao-verifier-events-run-1.jsonl"].read_text(encoding="utf-8").splitlines()), 3)
        [summary] = [item for item in run("ui-projection")["ao_runs"] if item["goal_id"] == evidence["goal_id"]]  # ao-import links the Goal
        run_1 = next(item for item in summary["sessions"] if item["id"] == "run-1")
        self.assertEqual(run_1["advisories"], [{"clause": "objective-achievement", "reason": "판정자가 미달로 봄", "at": "t4"}])

    def test_missing_repo_is_reported_in_the_observation(self):
        run("attempt-complete", self.attempt["id"], "succeeded")
        evidence = run("ao-import", self.attempt["id"], "claude", str(self.ao_project), "goal-ab")
        self.assertIn("commit 없음 — AO 저장소가 없습니다", evidence["observation"])
        self.assertIn("최근 run-2(completed): 검증기 0종 0회 실행", evidence["observation"])
        self.assertEqual([item["name"] for item in evidence["attachments"]], ["ao-goal-demo-goal-ab.json", "ao-index-entry-goal-ab.json", "session-run-2.json"])

    def test_large_patch_is_cut_and_says_so(self):
        self.make_repo()
        run("attempt-complete", self.attempt["id"], "succeeded")
        with patch("goal_loop_tool.ao_import.PATCH_LIMIT", 40):
            evidence = run("ao-import", self.attempt["id"], "claude", str(self.ao_project), "goal-a")
        record = next(item for item in evidence["attachments"] if item["name"].startswith("ao-commit-"))
        text = (self.project / ".goal-loop" / "artifacts" / record["sha256"] / record["name"]).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# truncated: original "))
        self.assertIn("kept the first 40 bytes", text.splitlines()[0])

    def test_unknown_goal_or_folder_records_nothing(self):
        run("attempt-complete", self.attempt["id"], "succeeded")
        for args in ((str(self.ao_project), "no-such-goal"), (str(self.project), "goal-a")):
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                run("ao-import", self.attempt["id"], "claude", *args)
        self.assertEqual(run("ui-projection")["evidence"], [])

    def test_running_attempt_is_rejected_by_core(self):
        with self.assertRaises(GoalLoopError):
            run("ao-import", self.attempt["id"], "claude", str(self.ao_project), "goal-a")


if __name__ == "__main__":
    unittest.main()
