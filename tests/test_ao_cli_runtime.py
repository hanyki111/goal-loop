from contextlib import chdir, redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.ao_cli_runtime import AoCliPayload
from goal_loop_tool.persistent_cli import main

FAKE_AO = str(Path(__file__).with_name("fake_ao_cli.py"))


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


def run_lines(*args: str) -> list[dict]:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]


class AoCliRuntimeTests(unittest.TestCase):
    """ao_cli.v1 runs the Contract through the AO CLI without a server; while AO waits for a person nothing is invented,
    the person answers with ao-respond, and ao-resume delivers the resumed result to the same claimed request."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        base = Path(self.directory.name).resolve()
        self.project = base / "repo"
        self.project.mkdir()
        self.ao_root = base / "ao"
        self.ao_root.mkdir()
        self.ao_workspace = base / "ao-ws"
        (self.ao_workspace / "projects" / "demo" / "goals").mkdir(parents=True)
        kept = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        self.env = patch.dict("os.environ", {**kept, "LOCALAPPDATA": str(base / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(base)}, clear=True)
        self.env.start()
        self.cwd = chdir(self.project)
        self.cwd.__enter__()
        run("project-init")
        self.aim = run("aim-create", "AO를 서버 없이 실행")
        self.goal = self.new_goal("k")

    def tearDown(self):
        self.cwd.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def new_goal(self, key: str) -> dict:
        proposal = run("proposal-create", self.aim["id"], "p")
        return run("goal-accept", proposal["id"], "--target", "README를 쓴다", "--responsibility", "produce", "--scope", "README.md",
                   "--criterion", "(1) README가 있다", "--verification", "읽어 본다", "--key", key)

    def outcomes(self, *items: str) -> None:
        (self.ao_workspace / "fake-outcomes.json").write_text(json.dumps(list(items)), encoding="utf-8")

    def calls(self) -> list[list[str]]:
        path = self.ao_workspace / "fake-calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def start(self, ao_goal: str = "write-readme", goal: dict | None = None) -> dict:
        started = run("ao-attempt-start", (goal or self.goal)["id"], "1", "claude", "AO CLI로 실행", "--ao-project-id", "demo",
                      "--ao-goal-id", ao_goal, "--ao-root", str(self.ao_root), "--ao-workspace", str(self.ao_workspace),
                      "--milestone-id", "m1", "--allowed-write-path", "README.md")
        # The tests run the scripted AO with this Python instead of npx tsx.
        payload = started["runtime_request"]["payload"]
        self.assertEqual(started["runtime_request"]["request_kind"], "ao_cli.v1")
        self.assertEqual((payload["ao_root"], payload["ao_workspace"]), (str(self.ao_root), str(self.ao_workspace)))
        return started

    def refused(self, *args: str) -> str:
        error = StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(error):
            run(*args)
        return error.getvalue()

    def fake_command(self):
        return patch("goal_loop_tool.ao_cli_runtime.DEFAULT_NODE_COMMAND", (sys.executable, FAKE_AO))

    def test_payload_requires_root_workspace_and_known_approval(self):
        base = {"ao_root": "r", "ao_workspace": "w", "ao_project_id": "p", "ao_goal_id": "g", "title": "t", "description": "d", "objective": "o"}
        self.assertEqual(AoCliPayload.parse(base).approval, "stop")
        for broken in ({**base, "approval": "maybe"}, {k: v for k, v in base.items() if k != "ao_root"}, {**base, "ao_url": "x"}):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                AoCliPayload.parse(broken)

    def test_ao_root_requires_ao_workspace(self):
        message = self.refused("ao-attempt-start", self.goal["id"], "1", "claude", "x", "--ao-project-id", "demo", "--ao-goal-id", "g",
                               "--ao-root", str(self.ao_root))
        self.assertIn("--ao-workspace", message)

    def test_completed_goal_is_created_run_linked_and_imported(self):
        self.outcomes("completed")
        started = self.start()
        with self.fake_command():
            result = run("runtime-dispatch", started["runtime_request"]["request_id"], "worker-a")
        self.assertEqual(result["dispatch"]["attempt"]["outcome"], "succeeded")
        self.assertEqual(result["dispatch"]["acknowledged_request"]["status"], "completed")
        self.assertTrue(result["dispatch"]["execution"]["delivery_id"].startswith("ao-cli:run-"))
        self.assertEqual([call[0] for call in self.calls()], ["goal-create", "run-goal"])
        self.assertIn("--forward-derived-goals", self.calls()[1], "Goal Loop에 연결된 실행은 파생 제안을 넘겨받는다")
        self.assertIn("--external-verdict", self.calls()[1], "Goal Loop에 연결된 실행은 완료 판단을 Goal Loop에 맡긴다")
        created = self.calls()[0]
        self.assertEqual(created[created.index("--milestone") + 1], "m1")
        self.assertEqual(created[created.index("--title") + 1], "README를 쓴다 [write-readme]")
        self.assertIn("ao_link", result)
        self.assertIn("ao_import", result)
        links = run("ao-links", "--goal", self.goal["id"])
        self.assertTrue(any((item["ao_project_id"], item["ao_goal_id"]) == ("demo", "write-readme") for item in links["links"]), links)

    def test_checklist_and_verifiers_reach_ao_goal_create(self):
        self.outcomes("completed")
        started = run("ao-attempt-start", self.goal["id"], "1", "claude", "AO CLI로 실행", "--ao-project-id", "demo", "--ao-goal-id", "checked",
                      "--ao-root", str(self.ao_root), "--ao-workspace", str(self.ao_workspace),
                      "--ao-checklist", "README.md를 쓴다", "--ao-verifier", '{"type": "human", "prompt": "README를 확인하세요"}')
        self.assertEqual(started["runtime_request"]["payload"]["verifiers"], [{"type": "human", "prompt": "README를 확인하세요"}])
        with self.fake_command():
            run("runtime-dispatch", started["runtime_request"]["request_id"], "worker-a")
        created = self.calls()[0]
        self.assertEqual(created[created.index("--checklist") + 1], "README.md를 쓴다")
        self.assertEqual(json.loads(created[created.index("--verifier") + 1]), {"type": "human", "prompt": "README를 확인하세요"})

    def test_derived_goals_become_proposals_under_the_goal_aim_once(self):
        self.outcomes("completed+derived")
        started = self.start()
        with self.fake_command():
            result = run("runtime-dispatch", started["runtime_request"]["request_id"], "worker-a")
        created = result["ao_derived_proposals"]
        self.assertEqual(len(created), 2)
        proposals = {item["id"]: item for item in run("ui-projection")["proposals"]}
        first = proposals[created[0]]
        self.assertEqual(first["aim_id"], self.aim["id"])
        self.assertEqual(first["proposed_by_actor_id"], "ao")
        self.assertTrue(first["direction"].startswith("후속: 실패 commit 시험을 더한다"))
        self.assertIn("AO 실행 중 파생된 제안(AO Goal demo/write-readme", first["direction"])
        self.assertIn("목표: 실패 commit 경로를 단언하는 시험이 통과한다.", first["direction"])
        # A second delivery of the same AO session result records nothing new.
        from goal_loop_tool.persistent_cli import _record_derived_goals
        from goal_loop_tool.persistent import PersistentGoalLoop
        from goal_loop_tool.repository import SqliteEventRepository
        from goal_loop_tool.project import discover_project_database
        loop = PersistentGoalLoop(SqliteEventRepository(discover_project_database(self.project)))

        class Again:
            execution = type("E", (), {"stdout": result["dispatch"]["execution"]["stdout"]})()
            attempt = type("A", (), {"goal_id": self.goal["id"]})()
            request = type("R", (), {"payload": started["runtime_request"]["payload"]})()
        self.assertEqual(_record_derived_goals(loop, Again()), [])

    def test_awaiting_review_is_resolved_with_ao_review_and_delivered(self):
        self.outcomes("review")
        started = self.start()
        request_id = started["runtime_request"]["request_id"]
        with self.fake_command():
            self.assertIn("사람을 기다립니다", self.refused("runtime-dispatch", request_id, "worker-a"))
        status = run("ao-status", "--request-id", request_id)["ao_requests"][0]
        self.assertEqual(status["ao_goal_status"], "awaiting_review")
        self.assertTrue(any(command.startswith(f"ao-review {request_id}") for command in status["next"]), status)
        with self.fake_command():
            kept = run("ao-review", request_id, "worker-a", "--verdict", "concern")
        self.assertFalse(kept["delivered"])
        self.assertEqual(run("runtime-request", request_id)["request"]["status"], "claimed")
        with self.fake_command():
            done = run("ao-review", request_id, "worker-a", "--verdict", "reject", "--reason", "오탐")
        self.assertEqual(done["dispatch"]["attempt"]["outcome"], "succeeded")
        self.assertTrue(done["dispatch"]["execution"]["delivery_id"].endswith(":review-reject"))
        self.assertEqual(self.calls()[-1][self.calls()[-1].index("--reason") + 1], "오탐")

    def test_review_fail_needs_a_reason_and_delivers_a_failed_result(self):
        self.outcomes("review")
        started = self.start()
        request_id = started["runtime_request"]["request_id"]
        with self.fake_command():
            self.refused("runtime-dispatch", request_id, "worker-a")
            before = len(self.calls())
            self.assertIn("--reason", self.refused("ao-review", request_id, "worker-a", "--verdict", "fail"))
            self.assertEqual(len(self.calls()), before)  # AO was not called without a reason
            done = run("ao-review", request_id, "worker-a", "--verdict", "fail", "--reason", "우려가 실제 결함이다")
        self.assertEqual(done["dispatch"]["attempt"]["outcome"], "failed")
        self.assertTrue(done["dispatch"]["execution"]["delivery_id"].endswith(":review-fail"))
        self.assertEqual(done["ao_review"]["goalStatus"], "failed")
        call = self.calls()[-1]
        self.assertEqual(call[call.index("--verdict") + 1], "fail")
        self.assertEqual(call[call.index("--reason") + 1], "우려가 실제 결함이다")
        self.assertEqual(run("runtime-request", request_id)["request"]["status"], "completed")

    def test_respond_passes_write_paths_for_scope_answers(self):
        self.outcomes("waiting", "completed")
        started = self.start()
        request_id = started["runtime_request"]["request_id"]
        with self.fake_command():
            self.refused("runtime-dispatch", request_id, "worker-a")
        status = run("ao-status", "--request-id", request_id)["ao_requests"][0]
        self.assertTrue(any("--write-path" in command for command in status["next"]), status)
        with self.fake_command():
            run("ao-respond", request_id, "--action", "expand_scope", "--write-path", "src/a/**", "--write-path", "src/b.ts",
                "--additional-retries", "2")
        self.assertEqual(self.calls()[-1][3:], ["--action", "expand_scope", "--additional-retries", "2",
                                                "--write-path", "src/a/**", "--write-path", "src/b.ts"])

    def test_waiting_goal_stays_claimed_until_respond_and_resume(self):
        self.outcomes("waiting", "failed")
        started = self.start()
        request_id = started["runtime_request"]["request_id"]
        with self.fake_command():
            message = self.refused("runtime-dispatch", request_id, "worker-a")
        self.assertIn("사람을 기다립니다", message)
        self.assertIn(f"ao-respond {request_id}", message)
        self.assertEqual(run("runtime-request", request_id)["request"]["status"], "claimed")

        status = run("ao-status", "--request-id", request_id)["ao_requests"][0]
        self.assertEqual(status["waiting_for_human"]["kind"], "approval")
        self.assertEqual(status["ao_session_status"], "stopped")
        self.assertIn(f'ao-resume {request_id} "worker-a"', status["next"])

        with self.fake_command():
            self.assertIn("claim", self.refused("ao-resume", request_id, "someone-else"))
            answered = run("ao-respond", request_id, "--reject", "--feedback", "검증을 건너뛰지 않는다")
        self.assertTrue(answered["answered"]["resumeNeeded"])
        self.assertEqual(answered["next"], f'ao-resume {request_id} "worker-a"')
        respond_call = self.calls()[-1]
        self.assertEqual(respond_call[3:], ["--reject", "--feedback", "검증을 건너뛰지 않는다"])

        with self.fake_command():
            resumed = run("ao-resume", request_id, "worker-a")
        resume_call = next(call for call in reversed(self.calls()) if call[0] == "resume")
        self.assertIn("--external-verdict", resume_call, "이어 가는 실행도 완료 판단을 Goal Loop에 맡긴다")
        self.assertEqual(resumed["dispatch"]["attempt"]["outcome"], "failed")
        self.assertEqual(resumed["dispatch"]["acknowledged_request"]["status"], "completed")
        self.assertEqual(self.calls()[-1][0], "resume")
        self.assertIsNone(run("ao-status", "--request-id", request_id)["ao_requests"][0]["waiting_for_human"])

    def test_moved_ao_folders_are_read_through_moved_paths_json(self):
        self.outcomes("waiting", "completed")
        started = self.start()
        request_id = started["runtime_request"]["request_id"]
        with self.fake_command():
            self.refused("runtime-dispatch", request_id, "worker-a")
        # The AO folders move; the journal keeps the old paths.
        moved = Path(self.directory.name).resolve() / "moved"
        moved.mkdir()
        new_root, new_workspace = moved / "runner", moved / "workspace"
        self.ao_root.rename(new_root)
        self.ao_workspace.rename(new_workspace)
        self.assertIsNone(run("ao-status", "--request-id", request_id)["ao_requests"][0].get("ao_goal_status"))
        old_root_spelling = str(self.ao_root).upper().replace("\\", "/")  # case and separators do not matter
        (self.project / ".goal-loop" / "moved-paths.json").write_text(json.dumps({"moves": [
            {"from": old_root_spelling, "to": str(new_root)},
            {"from": str(self.ao_workspace), "to": str(new_workspace)},
        ]}), encoding="utf-8")
        status = run("ao-status", "--request-id", request_id)["ao_requests"][0]
        self.assertEqual(status["ao_session_status"], "stopped")
        self.assertEqual(status["waiting_for_human"]["kind"], "approval")
        self.ao_workspace = new_workspace  # where the scripted AO now writes its calls
        with self.fake_command():
            run("ao-respond", request_id, "--approve")
            resumed = run("ao-resume", request_id, "worker-a")
        self.assertEqual(resumed["dispatch"]["attempt"]["outcome"], "succeeded")
        self.assertEqual([call[0] for call in self.calls()[-2:]], ["respond", "resume"])
        # The stored request still has the paths it was created with.
        stored = run("runtime-request", request_id)["request"]["payload"]
        self.assertEqual((stored["ao_root"], stored["ao_workspace"]), (str(self.ao_root.parent / "ao"), str(self.ao_root.parent / "ao-ws")))

    def test_worker_dispatches_pending_requests_and_leaves_waiting_ones_claimed(self):
        self.outcomes("waiting", "completed")
        first = self.start("first")
        second = self.start("second", goal=self.new_goal("k2"))
        with self.fake_command():
            lines = run_lines("runtime-worker", "worker-a", "--once")
        by_request = {line["request_id"]: line for line in lines}
        self.assertIn("left_claimed", by_request[first["runtime_request"]["request_id"]])
        self.assertEqual(by_request[second["runtime_request"]["request_id"]]["dispatch"]["attempt"]["outcome"], "succeeded")
        self.assertEqual(run("runtime-request", first["runtime_request"]["request_id"])["request"]["status"], "claimed")
        self.assertEqual(run("runtime-requests")["requests"], [])


if __name__ == "__main__":
    unittest.main()
