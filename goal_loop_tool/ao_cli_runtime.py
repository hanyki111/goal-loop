"""Runtime profile ``ao_cli.v1``: run a Goal Loop Attempt as an AO Goal through the AO CLI, without the AO server.

AO restructure S3 (Decision 8). The profile calls the AO command line of an AO checkout (``ao_root``) against an
AO workspace (``ao_workspace``): ``goal-create`` when the AO Goal does not exist yet, then ``run-goal``. Each AO
command prints its result as JSON on the last stdout line. Like ``ao_session.v1`` it never invents a result: when
AO waits for a person (approval, meeting turn, review, PM decision) or stops, it raises ``AoRuntimeError`` so the
outbox request stays claimed. The person answers with ``ao-respond`` and the claim owner continues with
``ao-resume``, which delivers the resumed result to the same request. See docs/AO-ADAPTER-MAPPING.ko.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Sequence

from .ao_runtime import AoRuntimeError
from .model import AttemptOutcome
from .subprocess_runtime import RuntimeExecution

REQUEST_KIND = "ao_cli.v1"
PAYLOAD_FIELDS = {"ao_root", "ao_workspace", "ao_project_id", "ao_goal_id", "title", "description", "objective", "milestone_id",
                  "allowed_write_paths", "activity_kind", "ao_web_url", "approval", "timeout_seconds", "node_command",
                  "checklist", "verifiers"}
# node runs tsx directly: on Windows npx is a batch file (npx.cmd) and cmd.exe re-parses its arguments, which
# breaks arguments that contain quotes (a JSON --verifier) and drops the ones after them.
DEFAULT_NODE_COMMAND = ("node", "node_modules/tsx/dist/cli.mjs", "src/cli/index.ts")
# AO run-goal exit codes: 0 completed, 1 failed, 2 command error, 3 waiting for a person or stopped.
WAITING_OUTCOMES = {"waiting_human", "stopped"}


@dataclass(frozen=True)
class AoCliPayload:
    ao_root: str
    ao_workspace: str
    ao_project_id: str
    ao_goal_id: str
    title: str
    description: str
    objective: str
    milestone_id: str | None = None
    allowed_write_paths: tuple[str, ...] = ()
    activity_kind: str = "change"
    ao_web_url: str | None = None
    approval: str = "stop"
    timeout_seconds: float = 7200.0
    node_command: tuple[str, ...] = DEFAULT_NODE_COMMAND
    checklist: tuple[str, ...] = ()
    # AO verifier objects (e.g. {"type": "human", "prompt": "…"}) passed to goal-create as JSON.
    verifiers: tuple[dict, ...] = ()

    @classmethod
    def parse(cls, payload: dict[str, object]) -> "AoCliPayload":
        unknown = set(payload) - PAYLOAD_FIELDS
        if unknown:
            raise ValueError(f"Unsupported ao_cli.v1 payload fields: {', '.join(sorted(unknown))}")

        def text(name: str, required: bool = True) -> str | None:
            value = payload.get(name)
            if value is None and not required:
                return None
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ao_cli.v1 payload requires non-empty string {name!r}.")
            return value

        def strings(name: str) -> tuple[str, ...]:
            value = payload.get(name, [])
            if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                raise ValueError(f"ao_cli.v1 payload field {name!r} must be a list of strings.")
            return tuple(value)

        timeout = payload.get("timeout_seconds", 7200.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("ao_cli.v1 payload field 'timeout_seconds' must be a positive number.")
        approval = text("approval", False) or "stop"
        if approval not in {"stop", "wait"}:
            raise ValueError("ao_cli.v1 payload field 'approval' must be 'stop' or 'wait'.")
        node_command = strings("node_command") or DEFAULT_NODE_COMMAND
        verifiers = payload.get("verifiers", [])
        if not isinstance(verifiers, list) or not all(isinstance(item, dict) and isinstance(item.get("type"), str) for item in verifiers):
            raise ValueError("ao_cli.v1 payload field 'verifiers' must be a list of objects with a string 'type'.")
        return cls(
            ao_root=text("ao_root"), ao_workspace=text("ao_workspace"), ao_project_id=text("ao_project_id"),
            ao_goal_id=text("ao_goal_id"), title=text("title"), description=text("description"), objective=text("objective"),
            milestone_id=text("milestone_id", False), allowed_write_paths=strings("allowed_write_paths"),
            activity_kind=text("activity_kind", False) or "change", ao_web_url=text("ao_web_url", False),
            approval=approval, timeout_seconds=float(timeout), node_command=node_command,
            checklist=strings("checklist"), verifiers=tuple(verifiers),
        )


Runner = Callable[..., subprocess.CompletedProcess]


class AoCli:
    """The AO commands the profile needs, run as ``<node_command> <command> … -w <workspace>`` in ``ao_root``."""

    def __init__(self, payload: AoCliPayload, runner: Runner = subprocess.run) -> None:
        self.payload = payload
        self.runner = runner

    def _command(self, args: Sequence[str]) -> list[str]:
        head, *rest = self.payload.node_command
        # Resolve the executable (node.exe on Windows) so the call works without a shell.
        return [shutil.which(head) or head, *rest, *args, "-w", self.payload.ao_workspace]

    def call(self, *args: str, timeout: float | None = None) -> tuple[dict, int]:
        try:
            done = self.runner(self._command(args), cwd=self.payload.ao_root, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as expired:
            raise AoRuntimeError(f"AO 명령 {args[0]}이 {timeout:.0f}초 안에 끝나지 않았습니다. AO 세션 상태를 확인한 뒤 ao-resume 또는 runtime-requeue를 쓰세요.") from expired
        lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
        try:
            result = json.loads(lines[-1]) if lines else None
        except json.JSONDecodeError:
            result = None
        if not isinstance(result, dict):
            raise AoRuntimeError(f"AO 명령 {args[0]}의 결과를 읽지 못했습니다(exit {done.returncode}): {(done.stderr or '')[-400:]}")
        if "error" in result:
            raise AoRuntimeError(f"AO 명령 {args[0]}이 거부했습니다: {result['error']}")
        return result, done.returncode

    def goal_exists(self) -> bool:
        index = Path(self.payload.ao_workspace) / "projects" / self.payload.ao_project_id / "goals" / "goals.json"
        try:
            goals = json.loads(index.read_text(encoding="utf-8")).get("goals", [])
        except (OSError, json.JSONDecodeError):
            return False
        return any(isinstance(goal, dict) and goal.get("id") == self.payload.ao_goal_id for goal in goals)

    def create_goal(self) -> dict:
        p = self.payload
        args = ["goal-create", p.ao_project_id, "--id", p.ao_goal_id, "--title", p.title, "--description", p.description,
                "--objective", p.objective, "--activity-kind", p.activity_kind]
        if p.milestone_id:
            args += ["--milestone", p.milestone_id]
        for path in p.allowed_write_paths:
            args += ["--allowed-write-path", path]
        for item in p.checklist:
            args += ["--checklist", item]
        for verifier in p.verifiers:
            args += ["--verifier", json.dumps(verifier, ensure_ascii=False)]
        result, _ = self.call(*args, timeout=600)
        created = (result.get("goal") or {}).get("id")
        if created != p.ao_goal_id:
            raise AoRuntimeError(f"AO가 요청한 ID({p.ao_goal_id})와 다른 Goal({created})을 만들었습니다.")
        return result

    # Connected to Goal Loop, AO forwards Goals it derives (Proposals here) instead of creating AO Goals (restructure S4),
    # and leaves the completion verdict to Goal Loop: objective-achievement, has-changes and the pre-change guard
    # become completion:advisory records instead of blocking completion (goal-96, docs/AO-INTERNAL-GATES.ko.md).
    def run_goal(self) -> dict:
        return self.call("run-goal", self.payload.ao_project_id, self.payload.ao_goal_id, "--approval", self.payload.approval,
                         "--forward-derived-goals", "--external-verdict", timeout=self.payload.timeout_seconds)[0]

    def resume(self, session_id: str) -> dict:
        return self.call("resume", self.payload.ao_project_id, session_id, "--approval", self.payload.approval,
                         "--forward-derived-goals", "--external-verdict", timeout=self.payload.timeout_seconds)[0]

    def resolve_review(self, verdict: str, reason: str | None = None) -> dict:
        args = ["resolve-review", self.payload.ao_project_id, self.payload.ao_goal_id, "--verdict", verdict]
        if reason:
            args += ["--reason", reason]
        return self.call(*args, timeout=600)[0]

    def pending(self, session_id: str) -> dict | None:
        return self.call("pending", self.payload.ao_project_id, session_id, timeout=120)[0].get("pending")

    def respond(self, session_id: str, answer: Sequence[str]) -> dict:
        return self.call("respond", self.payload.ao_project_id, session_id, *answer, timeout=120)[0]


def latest_session(ao_workspace: str, project_id: str, goal_id: str) -> str | None:
    """The newest AO session that ran this AO Goal (sessions/<id>/session.json carries goalId)."""
    sessions = Path(ao_workspace) / "projects" / project_id / "sessions"
    found: list[tuple[str, str]] = []
    for file in sessions.glob("*/session.json"):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("goalId") == goal_id:
            found.append((str(data.get("createdAt") or ""), file.parent.name))
    return max(found)[1] if found else None


def execution_from_result(result: dict, request_label: str = "") -> RuntimeExecution:
    """Map an AO run-goal/resume result to a RuntimeExecution, or refuse to invent one while AO waits."""
    outcome, session_id = result.get("outcome"), result.get("sessionId")
    if outcome in WAITING_OUTCOMES:
        pending = result.get("pending") or {}
        question = f" 질문: {pending.get('question')}" if pending.get("question") else ""
        raise AoRuntimeError(
            f"AO 세션 {session_id}이 사람을 기다립니다(outcome {outcome}, AO Goal {result.get('goalStatus')}).{question} "
            f"답한 뒤 이어서 실행하세요: goal-loop ao-respond {request_label or '<request-id>'} …, 그다음 goal-loop ao-resume {request_label or '<request-id>'} <worker>."
        )
    if outcome == "completed":
        attempt_outcome, exit_code = AttemptOutcome.SUCCEEDED, 0
    elif outcome == "failed":
        attempt_outcome, exit_code = AttemptOutcome.FAILED, 1
    else:
        raise AoRuntimeError(f"AO 결과의 outcome을 알 수 없습니다: {outcome!r}")
    return RuntimeExecution(f"ao-cli:{session_id}", attempt_outcome, exit_code, json.dumps(result, ensure_ascii=False), "", False)


def run_ao_cli(payload: AoCliPayload, *, cli: AoCli | None = None, request_label: str = "") -> RuntimeExecution:
    """Create the AO Goal if needed, run it once through the AO CLI, and report its outcome."""
    cli = cli or AoCli(payload)
    if not cli.goal_exists():
        cli.create_goal()
    return execution_from_result(cli.run_goal(), request_label)


def resume_ao_cli(payload: AoCliPayload, session_id: str, *, cli: AoCli | None = None, request_label: str = "") -> RuntimeExecution:
    """Continue a stopped AO session after its person answered; the delivery id names the resumed session."""
    cli = cli or AoCli(payload)
    return execution_from_result(cli.resume(session_id), request_label)


def cli_contract_payload(goal: dict, *, ao_root: str, ao_workspace: str, ao_project_id: str, ao_goal_id: str,
                         **options: object) -> dict[str, object]:
    """Build an ao_cli.v1 payload from a Goal's current Contract (same title and description rules as ao_session.v1)."""
    from .ao_runtime import contract_payload
    # The objective goes through contract_payload so it also reaches the description AO builds the checklist from (goal-67).
    base = contract_payload(goal, ao_project_id=ao_project_id, ao_goal_id=ao_goal_id, objective=options.pop("objective", None))
    payload: dict[str, object] = {**base, "ao_root": ao_root, "ao_workspace": ao_workspace}
    payload.update({key: value for key, value in options.items() if value not in (None, [], ())})
    AoCliPayload.parse(payload)
    return payload
