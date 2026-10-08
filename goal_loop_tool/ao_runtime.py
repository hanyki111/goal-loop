"""Runtime profile ``ao_session.v1``: run a Goal Loop Attempt as an AI_ORCHESTRATION (AO) Goal.

The profile creates one AO Goal from the Goal Loop Contract, starts an AO Session for it, waits until the
Session ends and the AO Goal status settles, and returns the outcome as a RuntimeExecution. It never invents
a result: when AO waits for a person (approval, review, PM decision), stops, or exceeds the time limit, it
raises ``AoRuntimeError`` so the outbox request stays claimed for an operator. See docs/AO-ADAPTER-MAPPING.ko.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Callable
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote

from .model import AttemptOutcome
from .subprocess_runtime import RuntimeExecution

REQUEST_KIND = "ao_session.v1"
DEFAULT_URL = "http://127.0.0.1:3000"
PAYLOAD_FIELDS = {"ao_url", "ao_web_url", "ao_project_id", "ao_goal_id", "title", "description", "objective", "milestone_id",
                  "allowed_write_paths", "activity_kind", "ao_workspace", "timeout_seconds", "poll_seconds", "settle_seconds"}
RUNNING_SESSION = {"running", "pending"}
SUCCEEDED_GOAL = {"completed"}
FAILED_GOAL = {"failed", "abandoned", "reverted"}
# AO states that need a person; delivering a result here would invent one.
HUMAN_GOAL = {"awaiting_review", "awaiting_pm_decision", "waiting"}


class AoRuntimeError(RuntimeError):
    """AO did not reach a deliverable result; the Runtime request must stay claimed."""


@dataclass(frozen=True)
class AoSessionPayload:
    ao_project_id: str
    ao_goal_id: str
    title: str
    description: str
    objective: str
    ao_url: str = DEFAULT_URL
    milestone_id: str | None = None
    allowed_write_paths: tuple[str, ...] = ()
    activity_kind: str = "change"
    ao_workspace: str | None = None
    ao_web_url: str | None = None
    timeout_seconds: float = 3600.0
    poll_seconds: float = 30.0
    settle_seconds: float = 600.0

    @classmethod
    def parse(cls, payload: dict[str, object]) -> "AoSessionPayload":
        unknown = set(payload) - PAYLOAD_FIELDS
        if unknown:
            raise ValueError(f"Unsupported ao_session.v1 payload fields: {', '.join(sorted(unknown))}")
        def text(name: str, required: bool = True) -> str | None:
            value = payload.get(name)
            if value is None and not required:
                return None
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ao_session.v1 payload requires non-empty string {name!r}.")
            return value
        def number(name: str, default: float) -> float:
            value = payload.get(name, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"ao_session.v1 payload field {name!r} must be a positive number.")
            return float(value)
        paths = payload.get("allowed_write_paths", [])
        if not isinstance(paths, list) or not all(isinstance(item, str) and item.strip() for item in paths):
            raise ValueError("ao_session.v1 payload field 'allowed_write_paths' must be a list of strings.")
        return cls(
            ao_project_id=text("ao_project_id"), ao_goal_id=text("ao_goal_id"), title=text("title"),
            description=text("description"), objective=text("objective"),
            ao_url=text("ao_url", False) or DEFAULT_URL, milestone_id=text("milestone_id", False),
            allowed_write_paths=tuple(paths), activity_kind=text("activity_kind", False) or "change",
            ao_workspace=text("ao_workspace", False), ao_web_url=text("ao_web_url", False),
            timeout_seconds=number("timeout_seconds", 3600.0), poll_seconds=number("poll_seconds", 30.0),
            settle_seconds=number("settle_seconds", 600.0),
        )


class AoClient:
    """The few AO HTTP routes the profile needs; every body is ``{"data": ...}`` or ``{"error": ...}``."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urlrequest.Request(self.base_url + path, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8"} if data else {})
        try:
            with urlrequest.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urlerror.HTTPError as http:
            detail = http.read().decode("utf-8", errors="replace")
            raise AoRuntimeError(f"AO {method} {path} → HTTP {http.code}: {detail[:300]}") from http
        except (urlerror.URLError, TimeoutError, OSError) as failure:
            raise AoRuntimeError(f"AO 서버에 연결할 수 없습니다({self.base_url}): {failure}") from failure
        return payload.get("data") or {}

    def project(self, project_id: str) -> str:
        return f"/api/projects/{quote(project_id, safe='')}"

    def create_goal(self, project_id: str, body: dict) -> dict:
        return self._call("POST", f"{self.project(project_id)}/goals", body)

    def goal(self, project_id: str, goal_id: str) -> dict:
        return self._call("GET", f"{self.project(project_id)}/goals/{quote(goal_id, safe='')}").get("goal") or {}

    def create_session(self, project_id: str, body: dict) -> dict:
        return self._call("POST", f"{self.project(project_id)}/sessions", body)

    def run_session(self, project_id: str, session_id: str) -> dict:
        return self._call("POST", f"{self.project(project_id)}/sessions/{quote(session_id, safe='')}/run")

    def session(self, project_id: str, session_id: str) -> dict:
        return self._call("GET", f"{self.project(project_id)}/sessions/{quote(session_id, safe='')}")

    def pending_approval(self, project_id: str, session_id: str) -> dict | None:
        return self._call("GET", f"{self.project(project_id)}/sessions/{quote(session_id, safe='')}/pending-approval") or None


def run_ao_session(payload: AoSessionPayload, *, client: AoClient | None = None,
                   sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> RuntimeExecution:
    """Create the AO Goal, run one Session, wait for a settled AO Goal status, and report it."""
    client = client or AoClient(payload.ao_url)
    project, goal_id = payload.ao_project_id, payload.ao_goal_id
    body = {"id": goal_id, "title": payload.title, "description": payload.description, "objective": payload.objective,
            "activityKind": payload.activity_kind}
    if payload.milestone_id:
        body["milestoneId"] = payload.milestone_id
    if payload.allowed_write_paths:
        body["allowedWritePaths"] = list(payload.allowed_write_paths)
    created = client.create_goal(project, body)
    if created.get("id") != goal_id:
        # AO may rename or merge a proposed Goal; running some other Goal would not be this Attempt.
        raise AoRuntimeError(f"AO가 요청한 ID({goal_id})와 다른 Goal({created.get('id')})을 만들었습니다.")
    session = client.create_session(project, {"goal": payload.title, "goalId": goal_id, "goalPath": f"goals/{goal_id}", "workflowName": "workflow"})
    session_id = session.get("id")
    if not session_id:
        raise AoRuntimeError("AO가 Session ID를 돌려주지 않았습니다.")
    client.run_session(project, session_id)
    deadline = clock() + payload.timeout_seconds

    status = "running"
    while True:
        if client.pending_approval(project, session_id):
            raise AoRuntimeError(f"AO Session {session_id}이 사람 승인을 기다립니다. AO에서 처리한 뒤 runtime-deliver로 결과를 전달하세요.")
        status = client.session(project, session_id).get("status") or "unknown"
        if status not in RUNNING_SESSION:
            break
        if clock() >= deadline:
            raise AoRuntimeError(f"AO Session {session_id}이 {payload.timeout_seconds:.0f}초 안에 끝나지 않았습니다(status {status}). 요청은 claimed로 남습니다.")
        sleep(payload.poll_seconds)
    if status == "stopped":
        raise AoRuntimeError(f"AO Session {session_id}이 멈췄습니다(stopped). AO에서 확인한 뒤 runtime-deliver로 결과를 전달하세요.")

    # The index status settles after post-session work (knowledge pass, commit); in_progress is not a result.
    settle_deadline = clock() + payload.settle_seconds
    while True:
        goal_status = client.goal(project, goal_id).get("status") or "unknown"
        if goal_status in SUCCEEDED_GOAL | FAILED_GOAL:
            break
        if goal_status in HUMAN_GOAL:
            raise AoRuntimeError(f"AO Goal {goal_id}이 사람의 판단을 기다립니다(status {goal_status}). AO에서 처리한 뒤 runtime-deliver로 결과를 전달하세요.")
        if clock() >= settle_deadline:
            raise AoRuntimeError(f"AO Goal {goal_id}의 상태가 확정되지 않았습니다(status {goal_status}, Session {status}).")
        sleep(payload.poll_seconds)

    outcome = AttemptOutcome.SUCCEEDED if goal_status in SUCCEEDED_GOAL else AttemptOutcome.FAILED
    report = json.dumps({"ao_project_id": project, "ao_goal_id": goal_id, "ao_session_id": session_id,
                         "ao_session_status": status, "ao_goal_status": goal_status}, ensure_ascii=False)
    return RuntimeExecution(f"ao-session:{session_id}", outcome, 0 if outcome is AttemptOutcome.SUCCEEDED else 1, report, "", False)


def contract_payload(goal: dict, *, ao_project_id: str, ao_goal_id: str, **options: object) -> dict[str, object]:
    """Build an ao_session.v1 payload from a Goal's current Contract (as the ui-projection shows it)."""
    contract = goal["contract"]
    description = "\n\n".join([
        f"Goal Loop 과제 {goal['id']}(Contract v{goal['current_contract_version']})를 AO에서 실행한다.",
        f"범위: {contract['operational_scope']}",
        f"성공 기준: {contract['success_criterion']}",
        f"검증 방법: {contract['verification_specification']}",
    ])
    # One AO Goal per Attempt: AO's duplicate-goal guard returns an unfinished Goal with the same title instead of
    # creating a new one, so a retry with the bare Goal target would never start. The AO Goal ID keeps titles unique.
    payload: dict[str, object] = {"ao_project_id": ao_project_id, "ao_goal_id": ao_goal_id, "title": f"{goal['target']} [{ao_goal_id}]",
                                  "description": description, "objective": contract["success_criterion"]}
    # An explicit objective replaces the criterion when the criterion also names Goal Loop-only conditions AO cannot meet.
    payload.update({key: value for key, value in options.items() if value not in (None, [], ())})
    # AO builds the implementer's checklist from the description, so an objective that differs from the criterion must
    # also appear there or the implementer never sees it (goal-67).
    if isinstance(payload["objective"], str) and payload["objective"] and payload["objective"] != contract["success_criterion"]:
        payload["description"] = f"{description}\n\n이번 실행 지시(objective):\n{payload['objective']}"
    AoSessionPayload.parse(payload)  # refuse an unusable payload before anything is enqueued
    return payload
