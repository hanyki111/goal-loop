from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import threading
from unittest.mock import patch
import unittest
import urllib.error
import urllib.request

from goal_loop_tool import web_ui
from goal_loop_tool.persistent_cli import main
from goal_loop_tool.web_ui import READ_ONLY, ReadOnlyHost, bundle_dir, make_handler

REPO = Path(__file__).resolve().parents[1]


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


class WebUiTests(unittest.TestCase):
    """goal-loop ui answers the screen's reads for its journal and refuses every change."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.env = patch.dict("os.environ", {"LOCALAPPDATA": str(self.root / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(self.root)})
        self.env.start()
        self.project = self.root / "repo"
        self.project.mkdir()
        self.database = Path(run("project-init", str(self.project))["database"])
        run("--database", str(self.database), "aim-create", "브라우저로 보는 목표")
        self.host = ReadOnlyHost(self.database)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.host, bundle_dir()))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.env.stop()
        self.directory.cleanup()

    def get(self, path: str, headers: dict | None = None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.headers.get("Content-Type"), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Content-Type"), error.read()

    def api(self, command: str, args: dict | None = None, content_type: str = "application/json", headers: dict | None = None):
        body = json.dumps({"command": command, "args": args or {}}).encode("utf-8")
        request = urllib.request.Request(self.base + "/api", data=body, method="POST",
                                         headers={"Content-Type": content_type, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def checkpoint(self) -> int:
        return run("--database", str(self.database), "status")["checkpoint"]

    def test_bundle_is_packaged_and_served(self):
        self.assertTrue((bundle_dir() / "index.js").is_file(), "ui에서 npm run build:web을 실행하십시오")
        status, kind, body = self.get("/")
        self.assertEqual((status, kind), (200, "text/html; charset=utf-8"))
        self.assertIn(b'src="./index.js"', body)
        self.assertIn("읽기 전용".encode("utf-8"), body)
        status, kind, body = self.get("/index.js")
        self.assertEqual((status, kind), (200, "text/javascript; charset=utf-8"))
        self.assertIn(b"./api", body)  # built with web-host.ts, not the VS Code host
        self.assertEqual(self.get("/index.css")[0], 200)
        self.assertEqual(self.get("/../pyproject.toml")[0], 404)

    def test_reads_return_the_journal_cli_output(self):
        status, body = self.api("ui_projection", {"database": str(self.database)})
        self.assertEqual(status, 200)
        self.assertEqual([aim["description"] for aim in body["result"]["aims"]], ["브라우저로 보는 목표"])
        self.assertIn("actions", self.api("next_actions")[1]["result"])
        for command in ("reviews", "admission", "runtime_requests", "activity"):
            self.assertIn("result", self.api(command)[1], command)
        workspaces = self.api("workspaces")[1]["result"]["workspaces"]
        self.assertEqual([item["database"] for item in workspaces], [str(self.database)])
        self.assertEqual(workspaces[0]["name"], "repo")

    def test_every_change_is_refused_and_the_journal_stays(self):
        before = self.checkpoint()
        for command, args in (("goal_loop_command", {"commandName": "aim-create", "args": ["몰래 만든 목표"]}),
                              ("goal_loop_command", {"commandName": "aim-pause", "args": ["aim-1", "--actor", "user"]}),
                              ("pick_file", {}), ("pick_folder", {}), ("workspace_create", {"name": "x"}),
                              ("workspace_rename", {}), ("workspace_delete", {}),
                              ("workspace_open_project", {"folder": str(self.root)})):
            status, body = self.api(command, args)
            self.assertEqual(body.get("error"), READ_ONLY, command)
            self.assertNotIn("result", body)
        self.assertFalse((self.root / ".goal-loop").exists())
        self.assertIn("파일을 열 수 없습니다", self.api("open_location", {"location": "README.md"})[1]["error"])
        self.assertIn("지원하지 않는", self.api("anything_else")[1]["error"])
        self.assertIn("이 화면에서 연 저널이 아닙니다", self.api("ui_projection", {"database": str(self.root / "other.sqlite3")})[1]["error"])
        self.assertEqual(self.checkpoint(), before)

    def test_reads_do_not_move_a_stale_checkpoint(self):
        from goal_loop_tool.repository import SqliteEventRepository
        repo = SqliteEventRepository(self.database)
        repo.mark_projection_checkpoint(0)
        sequence = repo.journal_last_sequence()
        for command in ("ui_projection", "next_actions", "reviews", "admission", "runtime_requests", "activity", "ao_status"):
            self.assertIn("result", self.api(command)[1], command)
        self.api("goal_loop_command", {"commandName": "aim-create", "args": ["x"]})
        self.get("/checkpoint")
        self.assertEqual((repo.projection_checkpoint(), repo.journal_last_sequence()), (0, sequence))

    def test_linked_project_with_a_journal_opens_for_reading(self):
        other = self.root / "linked"
        other.mkdir()
        linked = run("project-init", str(other))["database"]
        status, body = self.api("workspace_open_project", {"folder": str(other)})
        self.assertEqual(body["result"]["database"], linked)
        self.assertIn("result", self.api("ui_projection", {"database": linked})[1])

    def test_checkpoint_follows_writes_from_other_processes(self):
        first = json.loads(self.get("/checkpoint")[2])["checkpoint"]
        self.assertGreater(first, 0)
        run("--database", str(self.database), "aim-create", "다른 프로세스가 기록")
        self.assertGreater(json.loads(self.get("/checkpoint")[2])["checkpoint"], first)

    def test_requests_from_other_sites_are_refused(self):
        self.assertEqual(self.get("/", {"Host": "evil.example:80"})[0], 403)
        self.assertEqual(self.api("ui_projection", headers={"Host": "evil.example"})[0], 403)
        # A forwarded port (VS Code Remote) keeps a local name with another port.
        self.assertEqual(self.get("/", {"Host": "localhost:49999"})[0], 200)
        status, body = self.api("ui_projection", content_type="text/plain")
        self.assertEqual(status, 415)

    def test_change_commands_match_the_extension(self):
        source = (REPO / "vscode/src/host.js").read_text(encoding="utf-8")
        block = re.search(r"const UI_COMMANDS = \[(.*?)\];", source, re.S).group(1)
        self.assertEqual(tuple(re.findall(r"'([a-z-]+)'", block)), web_ui.UI_COMMANDS)
        reads = re.search(r"const READS = \{(.*?)\};", source, re.S).group(1)
        self.assertEqual(dict(re.findall(r"(\w+): '([a-z-]+)'", reads)), web_ui.READS)


class UiCommandTests(unittest.TestCase):
    def test_ui_needs_an_existing_journal(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"GOAL_LOOP_CEILING_DIRECTORIES": directory}):
            from contextlib import redirect_stderr
            with redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit):
                main(["--database", str(Path(directory) / "missing.sqlite3"), "ui", "--no-open"])
            self.assertIn("저널이 없습니다", error.getvalue())


if __name__ == "__main__":
    unittest.main()
