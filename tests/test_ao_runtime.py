from contextlib import chdir, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from unittest.mock import patch
import unittest

from goal_loop_tool.ao_runtime import AoSessionPayload, contract_payload
from goal_loop_tool.persistent_cli import main


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


class FakeAo:
    """A scripted AO server: Session statuses and Goal statuses are returned in order, the last one repeating."""

    def __init__(self, session_statuses, goal_statuses, *, approval=False, create_status=200):
        self.session_statuses, self.goal_statuses = list(session_statuses), list(goal_statuses)
        self.approval, self.create_status = approval, create_status
        self.calls: list[tuple[str, str, dict | None]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.calls.append(("POST", self.path, body))
                if self.path.endswith("/goals"):
                    if fake.create_status != 200:
                        return self._send(fake.create_status, {"error": {"code": "DUPLICATE", "message": "exists"}})
                    return self._send(200, {"data": {"id": body["id"], "status": "ready"}})
                if self.path.endswith("/sessions"):
                    return self._send(201, {"data": {"id": "run-1", "status": "running"}})
                return self._send(200, {"data": {"status": "started", "sessionId": "run-1"}})

            def do_GET(self):
                fake.calls.append(("GET", self.path, None))
                if self.path.endswith("/pending-approval"):
                    return self._send(200, {"data": {"id": "approval-1"} if fake.approval else None})
                if "/sessions/" in self.path:
                    status = fake.session_statuses.pop(0) if len(fake.session_statuses) > 1 else fake.session_statuses[0]
                    return self._send(200, {"data": {"id": "run-1", "status": status}})
                status = fake.goal_statuses.pop(0) if len(fake.goal_statuses) > 1 else fake.goal_statuses[0]
                return self._send(200, {"data": {"goal": {"id": "fix-search", "status": status}, "workflow": {}}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class AoRuntimeProfileTests(unittest.TestCase):
    """ao_session.v1 runs the Contract as an AO Goal and never invents a result while AO waits for a person."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        base = Path(self.directory.name).resolve()
        self.project = base / "repo"
        self.project.mkdir()
        self.ao_workspace = base / "ao" / "workspace"
        ao_project = self.ao_workspace / "projects" / "demo"
        (ao_project / "goals").mkdir(parents=True)
        (ao_project / "goals" / "goals.json").write_text(json.dumps({"goals": [{"id": "fix-search", "title": "검색 수정", "status": "completed"}]}), encoding="utf-8")
        kept = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        self.env = patch.dict("os.environ", {**kept, "LOCALAPPDATA": str(base / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(base)}, clear=True)
        self.env.start()
        self.cwd = chdir(self.project)
        self.cwd.__enter__()
        run("project-init")
        self.aim = run("aim-create", "AO에서 실행")
        self.goal = self.new_goal("k")
        self.servers: list[FakeAo] = []

    def new_goal(self, key: str) -> dict:
        proposal = run("proposal-create", self.aim["id"], "p")
        return run("goal-accept", proposal["id"], "--target", "검색을 고친다", "--responsibility", "produce", "--scope", "src/search.ts",
                   "--criterion", "(1) 검색이 된다", "--verification", "테스트", "--key", key)

    def tearDown(self):
        for server in self.servers:
            server.close()
        self.cwd.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def start(self, fake: FakeAo, *extra: str, goal: dict | None = None) -> dict:
        self.servers.append(fake)
        return run("ao-attempt-start", (goal or self.goal)["id"], "1", "claude", "AO에서 실행", "--ao-project-id", "demo", "--ao-goal-id", "fix-search",
                   "--ao-url", fake.url, "--poll-seconds", "0.01", "--milestone-id", "m1", "--allowed-write-path", "src/search.ts", *extra)

    def dispatch_refused(self, request_id: str) -> str:
        error = StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(error):
            run("runtime-dispatch", request_id, "worker-a")
        return error.getvalue()

    def request(self, request_id: str) -> dict:
        return run("runtime-request", request_id)["request"]

    def test_completed_ao_goal_succeeds_and_imports_evidence(self):
        fake = FakeAo(["running", "running", "completed"], ["in_progress", "completed"])
        started = self.start(fake, "--ao-workspace", str(self.ao_workspace))
        payload = started["runtime_request"]["payload"]
        self.assertEqual(started["runtime_request"]["request_kind"], "ao_session.v1")
        self.assertEqual((payload["title"], payload["objective"]), ("검색을 고친다 [fix-search]", "(1) 검색이 된다"))
        self.assertIn("범위: src/search.ts", payload["description"])
        result = run("runtime-dispatch", started["runtime_request"]["request_id"], "worker-a")
        self.assertEqual(result["dispatch"]["attempt"]["outcome"], "succeeded")
        self.assertEqual(result["dispatch"]["acknowledged_request"]["status"], "completed")
        self.assertEqual(json.loads(result["dispatch"]["execution"]["stdout"])["ao_goal_status"], "completed")
        created = next(body for method, path, body in fake.calls if method == "POST" and path.endswith("/goals"))
        self.assertEqual((created["id"], created["milestoneId"], created["allowedWritePaths"]), ("fix-search", "m1", ["src/search.ts"]))
        evidence = next(item for item in run("ui-projection")["evidence"] if item["id"] == result["ao_import"]["evidence_id"])
        self.assertEqual(evidence["actor"], "worker-a")
        self.assertIn("AO Goal fix-search", evidence["observation"])
        request_id = started["runtime_request"]["request_id"]
        first_line = evidence["observation"].splitlines()[0]
        for fact in (request_id, "ao_session.v1", "worker-a", started["attempt"]["id"], "succeeded", "delivery ao-session:run-1"):
            self.assertIn(fact, first_line)
        runtime_attachment = evidence["attachments"][0]
        self.assertEqual(runtime_attachment["name"], f"runtime-{request_id}.json")
        record = json.loads(Path(runtime_attachment["path"]).read_text(encoding="utf-8"))
        # Every field the record promises, so a verifier reading only this attachment sees the whole delivery.
        self.assertEqual(record["request"], {"request_id": request_id, "request_kind": "ao_session.v1", "attempt_id": started["attempt"]["id"],
                                             "status": "completed", "claimed_by": "worker-a"})
        self.assertEqual({key: value for key, value in record["execution"].items() if key != "stdout"},
                         {"delivery_id": "ao-session:run-1", "outcome": "succeeded", "exit_code": 0, "timed_out": False})
        self.assertEqual(json.loads(record["execution"]["stdout"]), {"ao_project_id": "demo", "ao_goal_id": "fix-search", "ao_session_id": "run-1",
                                                                     "ao_session_status": "completed", "ao_goal_status": "completed"})
        self.assertEqual(record["attempt"], {"id": started["attempt"]["id"], "outcome": "succeeded"})
        # Delivery is at most once: dispatching again does nothing.
        self.assertIsNone(run("runtime-dispatch", started["runtime_request"]["request_id"], "worker-a")["dispatch"])

    def test_failed_ao_goal_is_delivered_as_a_failed_attempt(self):
        started = self.start(FakeAo(["failed"], ["failed"]))
        result = run("runtime-dispatch", started["runtime_request"]["request_id"], "worker-a")
        self.assertEqual(result["dispatch"]["attempt"]["outcome"], "failed")
        self.assertNotIn("ao_import", result)

    def test_waiting_for_a_person_or_time_leaves_the_request_claimed(self):
        cases = [
            (FakeAo(["running"], ["ready"], approval=True), (), "사람 승인"),
            (FakeAo(["completed"], ["awaiting_review"]), (), "awaiting_review"),
            (FakeAo(["stopped"], ["ready"]), (), "stopped"),
            (FakeAo(["running"], ["ready"]), ("--timeout-seconds", "0.05"), "끝나지 않았습니다"),
            (FakeAo(["running"], ["ready"], create_status=409), (), "HTTP 409"),
        ]
        for index, (fake, extra, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                started = self.start(fake, *extra, goal=self.new_goal(f"case-{index}"))
                request_id = started["runtime_request"]["request_id"]
                message = self.dispatch_refused(request_id)
                self.assertIn(reason, message)
                self.assertIn("remains claimed", message)
                state = self.request(request_id)
                self.assertEqual((state["status"], state["claimed_by"]), ("claimed", "worker-a"))
                attempt = next(item for item in run("ui-projection")["attempts"] if item["id"] == started["attempt"]["id"])
                self.assertEqual(attempt["outcome"], "running")
        refused_create = self.servers[-1]
        self.assertFalse(any(path.endswith("/sessions") for _, path, _ in refused_create.calls))

    def test_payload_is_validated_before_anything_is_enqueued(self):
        with self.assertRaises(ValueError):
            AoSessionPayload.parse({"ao_project_id": "demo", "ao_goal_id": "g", "title": "t", "description": "d", "objective": "o", "shell": "rm"})
        with self.assertRaises(ValueError):
            contract_payload({"id": "goal-1", "target": "t", "current_contract_version": 1,
                              "contract": {"operational_scope": "s", "success_criterion": "c", "verification_specification": "v"}},
                             ao_project_id="demo", ao_goal_id="g", objective="")
        override = contract_payload({"id": "goal-1", "target": "t", "current_contract_version": 1,
                                     "contract": {"operational_scope": "s", "success_criterion": "(1) AO 일 (2) Goal Loop 일", "verification_specification": "v"}},
                                    ao_project_id="demo", ao_goal_id="g", objective="(1) AO 일")
        self.assertEqual(override["objective"], "(1) AO 일")
        # A retry of the same Goal gets a different AO title, so AO's duplicate-goal guard does not return the old one.
        same_goal = {"id": "goal-1", "target": "t", "current_contract_version": 1,
                     "contract": {"operational_scope": "s", "success_criterion": "c", "verification_specification": "v"}}
        first = contract_payload(same_goal, ao_project_id="demo", ao_goal_id="g-1")
        retry = contract_payload(same_goal, ao_project_id="demo", ao_goal_id="g-2")
        self.assertEqual((first["title"], retry["title"]), ("t [g-1]", "t [g-2]"))
        self.assertEqual({key: value for key, value in first.items() if key not in ("title", "ao_goal_id")},
                         {key: value for key, value in retry.items() if key not in ("title", "ao_goal_id")})
        self.assertIn("(2) Goal Loop 일", override["description"])
        with self.assertRaises(ValueError):
            contract_payload({"id": "goal-1", "target": "t", "current_contract_version": 1,
                              "contract": {"operational_scope": "s", "success_criterion": "c", "verification_specification": "v"}},
                             ao_project_id="demo", ao_goal_id="g", timeout_seconds=-1)

    def test_explicit_objective_reaches_the_description(self):
        # AO builds the implementer's checklist from the description, not the objective (goal-67).
        goal = {"id": "goal-1", "target": "t", "current_contract_version": 1,
                "contract": {"operational_scope": "s", "success_criterion": "c", "verification_specification": "v"}}
        plain = contract_payload(goal, ao_project_id="demo", ao_goal_id="g")
        self.assertNotIn("이번 실행 지시", plain["description"])
        self.assertEqual(plain["description"], contract_payload(goal, ao_project_id="demo", ao_goal_id="g", objective="c")["description"])
        override = contract_payload(goal, ao_project_id="demo", ao_goal_id="g", objective="EventFeed.tsx 의 whitespace-nowrap 을 break-keep 으로")
        self.assertTrue(override["description"].startswith(plain["description"]))
        self.assertTrue(override["description"].endswith("이번 실행 지시(objective):\nEventFeed.tsx 의 whitespace-nowrap 을 break-keep 으로"))
        # ao-attempt-start uses the AO CLI payload; the objective must reach its description too.
        from goal_loop_tool.ao_cli_runtime import cli_contract_payload
        cli = cli_contract_payload(goal, ao_root="r", ao_workspace="w", ao_project_id="demo", ao_goal_id="g",
                                   objective="index.ts 에 runnerHostname 줄을 더한다", approval="stop")
        self.assertEqual(cli["objective"], "index.ts 에 runnerHostname 줄을 더한다")
        self.assertTrue(cli["description"].endswith("이번 실행 지시(objective):\nindex.ts 에 runnerHostname 줄을 더한다"))
        self.assertEqual(cli_contract_payload(goal, ao_root="r", ao_workspace="w", ao_project_id="demo", ao_goal_id="g")["description"],
                         plain["description"])


if __name__ == "__main__":
    unittest.main()
