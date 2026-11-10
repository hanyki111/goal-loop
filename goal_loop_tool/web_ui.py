"""``goal-loop ui``: the app's screen in a browser, read only, served by the Python package alone.

It is the third host of the same screen (after the Tauri app and the VS Code extension) for places where neither can
be installed. The screen bundle is built from ``ui/`` with ``npm run build:web`` into ``web_files/`` and shipped as
package data, so serving it needs no Node. Requests mirror ``vscode/src/host.js``: reads are answered with the
journal's own CLI output, and every change is refused because a screen anyone on this computer can reach must not
record under the user's name (an agent here could call it too).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

from .project import JOURNAL_FILENAME, PROJECT_DIRNAME, project_root_for

# The extension's change commands (vscode/src/host.js UI_COMMANDS); a test keeps the two lists equal.
UI_COMMANDS = (
    "aim-create", "proposal-create", "proposal-revise", "goal-accept", "attempt-start", "attempt-complete",
    "evidence-record", "evidence-correct", "verification-begin", "verdict-submit",
    "retry-decide", "reverification-decide", "aim-evaluate", "contract-revise",
    "verdict-review-resolve", "aim-evaluation-review-resolve", "aim-closure-invalidate",
    "aim-pause", "aim-resume", "goal-hold-add", "goal-hold-clear", "goal-parent-add", "goal-follow-add",
    "goal-dependency-add", "proposal-close", "goal-stop", "aim-abandon",
    "reference-add", "reference-remove", "aim-parent-add", "aim-link-import",
)
READS = {"ui_projection": "ui-projection", "next_actions": "next", "reviews": "reviews", "admission": "admission",
         "runtime_requests": "runtime-requests"}
REFUSED = ("goal_loop_command", "pick_file", "pick_folder", "workspace_create", "workspace_rename", "workspace_delete")
READ_ONLY = "이 화면은 읽기 전용입니다. 기록 변경은 데스크톱 앱이나 goal-loop CLI에서 하십시오."
NO_FILES = "브라우저 화면에서는 서버의 파일을 열 수 없습니다. 경로를 복사해 편집기에서 여십시오."
CLI_TIMEOUT_SECONDS = 120
AO_STATUS_TTL_SECONDS = 60
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
BUNDLE_FILES = {"index.js": "text/javascript; charset=utf-8", "index.css": "text/css; charset=utf-8"}


class WebUiError(RuntimeError):
    """A request this screen does not answer; the message is shown on the screen."""


def journal_record(database: Path) -> dict:
    """The same record the extension makes (journalRecord), so the screen's saved selection matches."""
    database = Path(database)
    root = project_root_for(database) or database.parent
    return {"id": "journal-" + hashlib.sha1(str(database).encode("utf-8")).hexdigest()[:12],
            "name": root.name, "project": str(root), "database": str(database)}


def bundle_dir() -> Path:
    return Path(str(resources.files("goal_loop_tool").joinpath("web_files")))


def screen_html() -> str:
    # The narrow-layout rule matches the extension's page; storage is the browser's own localStorage here.
    return """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Goal Loop (읽기 전용)</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'">
<link rel="icon" href="data:,"><link rel="stylesheet" href="./index.css">
<style>html,body,#root{height:100%}body{margin:0;padding:0}@media(max-width:1100px){html{overflow:auto}body{overflow:visible}.workspace{overflow:visible}}</style>
</head><body><div id="root"></div><script type="module" src="./index.js"></script></body></html>"""


def run_cli(database: Path, args: list[str]) -> str:
    """The journal's own CLI in this Python, like the extension's host runs the goal-loop executable."""
    root = project_root_for(database) or Path(database).parent
    completed = subprocess.run([sys.executable, "-m", "goal_loop_tool", "persistent", "--keep-checkpoint", "--database", str(database), *args],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=CLI_TIMEOUT_SECONDS, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if completed.returncode != 0:
        raise WebUiError((completed.stderr or completed.stdout).strip() or f"goal-loop {' '.join(args)} 실패")
    return completed.stdout


def read_checkpoint(database: Path) -> int:
    """The journal's last event sequence, read without writing; it grows with every record another process makes."""
    connection = sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM event_journal").fetchone()[0]
    finally:
        connection.close()


@dataclass
class ReadOnlyHost:
    """Answers the screen's requests for the journal it was started with and linked journals opened from it."""
    database: Path
    run: object = run_cli
    records: dict = field(default_factory=dict)
    _ao_status: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.database = Path(self.database).resolve()
        self.records[str(self.database)] = journal_record(self.database)

    def journal(self, requested) -> Path:
        selected = str(requested) if requested else str(self.database)
        if selected not in self.records:
            raise WebUiError(f"이 화면에서 연 저널이 아닙니다: {selected}")
        return Path(selected)

    def read(self, database: Path, args: list[str]):
        return json.loads(self.run(database, args))

    def handle(self, command: str, args: dict | None = None):
        args = args or {}
        if command in READS:
            return self.read(self.journal(args.get("database")), [READS[command]])
        if command == "workspaces":
            return {"workspaces": list(self.records.values())}
        if command == "activity":
            output = self.run(self.journal(args.get("database")), ["activity"])
            return [json.loads(line) for line in output.splitlines() if line.strip()]
        if command == "ao_status":
            return self.ao_status(self.journal(args.get("database")))
        if command == "workspace_open_project":
            return self.open_linked(args.get("folder"))
        if command == "open_location":
            raise WebUiError(NO_FILES)
        if command in REFUSED:
            raise WebUiError(READ_ONLY)
        raise WebUiError(f"지원하지 않는 요청입니다: {command}")

    def ao_status(self, database: Path):
        # ao-status reads AO workspace files and takes seconds; the screen asks for it on every reload.
        with self._lock:
            cached = self._ao_status.get(str(database))
            if cached and time.monotonic() - cached[0] < AO_STATUS_TTL_SECONDS:
                return cached[1]
        result = self.read(database, ["ao-status"])
        with self._lock:
            self._ao_status[str(database)] = (time.monotonic(), result)
        return result

    def open_linked(self, folder) -> dict:
        """A folder that already holds a journal (a linked project) is only read; nothing is created there."""
        if not isinstance(folder, str) or not folder:
            raise WebUiError(READ_ONLY)
        existing = (Path(folder) / PROJECT_DIRNAME / JOURNAL_FILENAME).resolve()
        if not existing.is_file():
            raise WebUiError(READ_ONLY)
        record = journal_record(existing)
        with self._lock:
            self.records[record["database"]] = record
        return record

    def checkpoint(self, requested=None) -> int:
        return read_checkpoint(self.journal(requested))


def _local_host(header: str | None) -> bool:
    if not header:
        return False
    name = header.rsplit(":", 1)[0] if not header.endswith("]") else header
    return name.lower() in LOCAL_HOSTS


def make_handler(host: ReadOnlyHost, bundle: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "goal-loop-ui"

        def log_message(self, format, *args):  # keep the terminal for the address line
            pass

        def send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, value, status: int = HTTPStatus.OK) -> None:
            self.send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def guarded(self) -> bool:
            # Another site in the same browser must not read this journal (DNS rebinding): only local host names.
            # The port is not checked, because a forwarded port (VS Code Remote) can differ from the server's.
            if not _local_host(self.headers.get("Host")):
                self.send_json({"error": "로컬 주소로만 열 수 있습니다."}, HTTPStatus.FORBIDDEN)
                return False
            return True

        def do_GET(self):
            if not self.guarded():
                return
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self.send(HTTPStatus.OK, screen_html().encode("utf-8"), "text/html; charset=utf-8")
            if path.lstrip("/") in BUNDLE_FILES:
                name = path.lstrip("/")
                file = bundle / name
                if not file.is_file():
                    return self.send_json({"error": f"화면 파일이 없습니다: {file} (ui에서 npm run build:web)"}, HTTPStatus.NOT_FOUND)
                return self.send(HTTPStatus.OK, file.read_bytes(), BUNDLE_FILES[name])
            if path == "/checkpoint":
                try:
                    return self.send_json({"checkpoint": host.checkpoint()})
                except (WebUiError, sqlite3.Error) as error:
                    return self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_json({"error": "없는 주소입니다."}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            if not self.guarded():
                return
            if self.path.split("?", 1)[0] != "/api":
                return self.send_json({"error": "없는 주소입니다."}, HTTPStatus.NOT_FOUND)
            # A plain form from another page cannot send JSON without a CORS preflight, which this server never allows.
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send_json({"error": "JSON 요청만 받습니다."}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                request = json.loads(self.rfile.read(length) or b"{}")
                result = host.handle(str(request.get("command")), request.get("args") or {})
            except (WebUiError, ValueError, subprocess.TimeoutExpired) as error:
                return self.send_json({"error": str(error)})
            self.send_json({"result": result})

    return Handler


def _announce(line: str) -> None:
    # Flushed at once: a terminal reading this through a pipe (or VS Code's port detection) needs the address now.
    print(line, flush=True)


def serve(database: Path, port: int = 0, open_browser: bool = True, announce=_announce) -> None:
    bundle = bundle_dir()
    if not (bundle / "index.js").is_file():
        raise WebUiError(f"화면 파일이 없습니다: {bundle}. 저장소의 ui 폴더에서 npm run build:web을 실행하십시오.")
    host = ReadOnlyHost(Path(database))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(host, bundle))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    announce(json.dumps({"url": url, "database": str(host.database), "read_only": True}, ensure_ascii=False))
    announce(f"Goal Loop 화면(읽기 전용): {url}  · Ctrl+C로 끝냄. 원격 접속이면 이 포트를 포워딩해 클라이언트 브라우저로 여십시오.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
