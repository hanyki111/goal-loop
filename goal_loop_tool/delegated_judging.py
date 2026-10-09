"""Delegated judging (Decision 6): a separate agent judges a Goal or an Aim on a copy of the project.

The judging agent never runs in the project itself. Each run gets a fresh copy of the source tree and a
backup of the journal, a TEMP folder outside that copy, and a discovery ceiling, so the agent can run tests
and builds freely; on Windows the agent's own sandbox did not reliably keep writes out of the project.
Its answer is fixed by a JSON Schema, attached as Evidence under the agent's name, and recorded unchanged.

    python -m goal_loop_tool.delegated_judging goal goal-7 [--attachments-only] [--note TEXT]
    python -m goal_loop_tool.delegated_judging aim aim-4
    python -m goal_loop_tool.delegated_judging --tree ao=E:/path/to/ao-worktree goal goal-23

goal ... --evidence ID... adds earlier Evidence of the same Goal; the verifying Attempt's Evidence is always shown.

--tree NAME=PATH copies another working tree (for example the AO lane a Goal changed) next to the project copy,
so the judge can rerun that repository's tests without touching the original.

--judge codex|claude|glm picks the judging agent (default codex). claude and glm run a fresh ``claude -p`` session in the
copy with the answer fixed by ``--json-schema``; glm is the same CLI pointed at GLM through connection variables read from
the user's own settings (``.goal-loop/judges.json``), which are passed only to that process and kept out of every record.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stdout
from dataclasses import dataclass, field
from io import StringIO
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable

from .project import CEILING_ENV, PROJECT_DIRNAME, discover_project_database, project_root_for

# Never copied into the judge's workspace: environments, dependencies, build output, and the live journal.
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", "node_modules", PROJECT_DIRNAME, "__pycache__", "*.pyc",
                                     "dist", "build", "target", "*.egg-info", "*.sqlite3", "*.sqlite3-*")
MARK = {"met": "충족", "not_met": "미충족", "cannot_judge": "판단 불가"}
# A judge that keeps running this long after its answer is complete is ended with its process tree: a server it left
# behind kept `codex exec` alive for about 45 minutes after the goal-75 answer was written (goal-78).
ANSWER_GRACE_SECONDS = 60.0
POLL_SECONDS = 1.0

_CRITERIA = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["item", "met", "basis"],
                                       "properties": {"item": {"type": "string"}, "met": {"type": "string", "enum": list(MARK)},
                                                      "basis": {"type": "string"}}}}
_LIST = {"type": "array", "items": {"type": "string"}}
VERDICT_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["goal_id", "result", "criteria", "reason", "checked", "not_checked"],
                  "properties": {"goal_id": {"type": "string"},
                                 "result": {"type": "string", "enum": ["satisfied", "not_satisfied", "inconclusive"]},
                                 "criteria": _CRITERIA, "reason": {"type": "string"}, "checked": _LIST, "not_checked": _LIST}}
AIM_SCHEMA = {"type": "object", "additionalProperties": False,
              "required": ["aim_id", "achieved", "criteria", "conclusion", "cited", "checked", "not_checked"],
              "properties": {"aim_id": {"type": "string"}, "achieved": {"type": "boolean"}, "criteria": _CRITERIA,
                             "conclusion": {"type": "string"}, "cited": _LIST, "checked": _LIST, "not_checked": _LIST}}

RULES = """## 판정 규칙(Decision 5)

- result는 satisfied / not_satisfied / inconclusive 중 하나다. "일부 충족"은 없다.
- 성공 기준의 번호 항목마다 met / not_met / cannot_judge와 근거를 적는다.
- 모든 항목이 met일 때만 satisfied다. 실제로 미충족이 확인되면 not_satisfied, 판단할 근거가 부족하면 inconclusive다.
- Contract 밖의 사항은 판정을 막지 않는다. reason에 "기준 밖:"으로 적는다.
- 기준 문구가 모호하면 가장 합리적인 해석을 고르고 reason에 밝힌다."""

ATTACHMENTS_ONLY = """## 이번 판정의 제약: 첨부만 사용

관찰 근거의 본문과 첨부 파일만 근거로 쓴다. 소스 코드, 외부 저장소, git을 판정 근거로 쓰지 말고, 첨부 안에 적힌 경로나
commit을 따라가 원본을 열지 마라(첨부된 patch는 읽어도 된다). 판단할 수 없는 항목은 cannot_judge로 하고 무엇이 있었으면
판단할 수 있었는지 적어라."""


class JudgingError(RuntimeError):
    """The judgment could not be prepared, produced, or recorded."""


def cli(database: Path, *args: str) -> dict:
    """Run one goal-loop command in process and return its JSON result."""
    from .persistent_cli import main
    output = StringIO()
    with redirect_stdout(output):
        main(["--database", str(database), *args])
    lines = output.getvalue().strip().splitlines()
    return json.loads(lines[-1]) if lines else {}


@dataclass(frozen=True)
class Workspace:
    root: Path
    project: Path
    database: Path
    tmp: Path
    trees: dict[str, Path] = field(default_factory=dict)

    def env(self) -> dict[str, str]:
        return {**os.environ, "TEMP": str(self.tmp), "TMP": str(self.tmp), CEILING_ENV: str(self.project)}


def _link_dependencies(source: Path, copy: Path) -> None:
    """Point the copy at the project's installed node_modules instead of copying them."""
    for modules in source.glob("*/node_modules"):
        target = copy / modules.parent.name / "node_modules"
        if target.exists() or not target.parent.is_dir():
            continue
        _link(target, modules)


def _link(target: Path, modules: Path) -> None:
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(modules)], check=True, capture_output=True)
    else:
        target.symlink_to(modules, target_is_directory=True)


def _link_tree_dependencies(source: Path, copy: Path) -> None:
    """Point an extra tree's copy at its installed node_modules (root, and up to two folders deep)."""
    for modules in [source / "node_modules", *source.glob("*/node_modules"), *source.glob("*/*/node_modules")]:
        target = copy / modules.relative_to(source)
        if modules.is_dir() and not target.exists() and target.parent.is_dir():
            _link(target, modules)


def prepare_workspace(project_root: Path, database: Path, base: Path | None = None, trees: dict[str, Path] | None = None) -> Workspace:
    root = Path(tempfile.mkdtemp(prefix="goal-loop-judge-", dir=base))
    copy = root / "project"
    shutil.copytree(project_root, copy, ignore=COPY_IGNORE)
    _link_dependencies(project_root, copy)
    copied_db = copy / PROJECT_DIRNAME / "journal.sqlite3"
    cli(database, "backup", str(copied_db))
    tmp = root / "tmp"
    tmp.mkdir()
    copied_trees: dict[str, Path] = {}
    for name, source in (trees or {}).items():
        if not source.is_dir():
            raise JudgingError(f"추가 작업 트리를 찾을 수 없습니다: {name}={source}")
        target = root / "trees" / name
        shutil.copytree(source, target, ignore=COPY_IGNORE)
        _link_tree_dependencies(source, target)
        copied_trees[name] = target
    return Workspace(root, copy, copied_db, tmp, copied_trees)


def parse_trees(values: list[str]) -> dict[str, Path]:
    trees: dict[str, Path] = {}
    for value in values:
        name, sep, path = value.partition("=")
        if not sep or not name or not path:
            raise JudgingError(f"--tree 는 NAME=PATH 형식입니다: {value}")
        trees[name] = Path(path).resolve()
    return trees


def _evidence_block(item: dict, corrections: list[dict] = ()) -> str:
    attached = "\n".join(f"  - {a['name']}: {a['path']}" for a in item.get("attachments", [])) or "  (없음)"
    fixed = [c for c in corrections if c["corrected_evidence_id"] == item["id"]]
    note = "".join(f"\n\n정정됨({c['id']}, {c['submitted_by_actor_id']}): {c['observation']}" for c in fixed)
    if fixed:
        note += "\n이 근거는 정정되었으므로 판정의 근거로 인용되지 않는다. 본문 대신 정정 문장을 따른다."
    return f"### {item['id']} ({item['attempt_id']}, 기록자 {item['actor']})\n\n본문:\n{item['observation']}{note}\n\n첨부:\n{attached}"


def environment_notes(ws: Workspace) -> str:
    python = Path(sys.executable)
    return f"""## 작업 환경

- 현재 작업 폴더 {ws.project} 는 프로젝트의 사본이다. 여기서는 테스트와 build를 자유롭게 실행해도 된다. 원본 프로젝트는 읽지도 쓰지도 마라.
- 사본의 .goal-loop\\journal.sqlite3는 journal 백업이다. 상태는 {python.parent / 'goal-loop'} --database .\\.goal-loop\\journal.sqlite3 ui-projection 으로 읽는다. 이 사본에도 판정을 기록하지 마라.
- Python 테스트: {python} -m unittest discover -s tests (사본 폴더에서). UI가 있으면 ui 폴더에서 npx vitest run, npm run build.
- 임시 파일은 {ws.tmp} 에 둔다. 환경 변수 TEMP·TMP·{CEILING_ENV}는 이미 설정되어 있다.
- 서버·watch처럼 오래 도는 프로세스를 띄웠다면 답하기 전에 직접 끝내라. 답을 쓴 뒤에도 남은 프로세스는 판정 도구가 강제로 끝낸다.{_tree_notes(ws)}"""


def _tree_notes(ws: Workspace) -> str:
    if not ws.trees:
        return ""
    lines = [f"\n- 추가 작업 트리 사본: {name} → {path}" for name, path in ws.trees.items()]
    return "".join(lines) + ("\n- 추가 사본은 이 판정을 위해 만든 것이다(.git·node_modules·build 산출물 제외, node_modules는 원본에 연결). "
                             "그 저장소의 시험·typecheck는 이 사본에서 직접 실행하고, 안내문이나 근거에 적힌 원본 경로는 읽지도 쓰지도 마라. "
                             "git 이력이 필요하면 첨부된 patch를 읽어라.")


def goal_prompt(projection: dict, goal_id: str, ws: Workspace, *, attachments_only: bool, note: str, evidence_ids: list[str]) -> tuple[str, str, str | None]:
    """Return (prompt, attempt_id, verdict to supersede) for the Goal's verifying Attempt."""
    goal = next((item for item in projection["goals"] if item["id"] == goal_id), None)
    if goal is None or goal["phase"] != "verifying" or not goal.get("verifying_attempt_id"):
        raise JudgingError(f"{goal_id}는 검증 중인 과제가 아닙니다. verification-begin 뒤에 판정할 수 있습니다.")
    attempt_id = goal["verifying_attempt_id"]
    # The verifying Attempt's Evidence is always shown; --evidence adds earlier Evidence of the same Goal (goal-87).
    shown = [item for item in projection["evidence"] if item["goal_id"] == goal_id and (item["attempt_id"] == attempt_id or item["id"] in evidence_ids)]
    prior = [item for item in projection["verdicts"] if item["goal_id"] == goal_id]
    same_attempt = [item for item in prior if item["attempt_id"] == attempt_id]
    supersedes = same_attempt[-1]["id"] if same_attempt and same_attempt[-1]["result"] == "inconclusive" else None
    contract = goal["contract"]
    history = "\n".join(f"- {v['id']} ({v['attempt_id']}): {v['result']}, 검증자 {v['verifier_actor_id']}\n  이유: {v.get('reason') or ''}" for v in prior) or "없음"
    prompt = f"""당신은 Goal Loop 과제 {goal_id}의 독립 검증자입니다. 이 과제는 다른 agent가 실행했고, 사용자가 당신의 판정을 그대로 검증 판단으로 기록하도록 위임했습니다(Decision 6). 실행자의 요약을 믿지 말고, 파일을 직접 읽고 명령을 직접 실행해 Contract의 성공 기준만으로 판정하십시오.

{RULES}

{ATTACHMENTS_ONLY if attachments_only else ''}

{environment_notes(ws)}

## {goal_id}의 Contract v{goal['current_contract_version']}

- 과제 이름: {goal['target']}
- 범위: {contract['operational_scope']}
- 성공 기준: {contract['success_criterion']}
- 검증 방법: {contract['verification_specification']}

이번 판정 대상은 {attempt_id}이다.

## 이전 검증 판단

{history}

## 관찰 근거

{chr(10).join(_evidence_block(item, projection.get("evidence_corrections") or []) for item in shown) or '없음'}

{('## 추가 안내' + chr(10) + chr(10) + note) if note else ''}

## 출력

최종 응답은 주어진 JSON Schema를 따른다. 한국어로 쓴다. reason은 판정 기록에 남을 문장으로 항목별 판정을 "(1) 충족: …" 형식으로 줄을 나눠 쓰고, 판단할 수 없었던 항목에는 "부족한 근거:"를 덧붙인다. checked에는 직접 읽거나 실행한 것, not_checked에는 확인하지 못한 것과 이유를 적는다.
"""
    return prompt, attempt_id, supersedes


def aim_prompt(projection: dict, aim_id: str, ws: Workspace, *, note: str) -> str:
    aim = next((item for item in projection["aims"] if item["id"] == aim_id), None)
    if aim is None:
        raise JudgingError(f"목표를 찾을 수 없습니다: {aim_id}")
    # A Proposal closed as `criteria` carries the Aim's achievement criteria, not an approach.
    criteria = [item for item in projection["proposals"] if item["aim_id"] == aim_id and item.get("closure_kind") == "criteria"]
    proposals = [item for item in projection["proposals"] if item["aim_id"] == aim_id and item not in criteria]
    goals = [item for item in projection["goals"] if item["aim_id"] == aim_id]
    children = [item for item in projection["aims"] if item.get("parent_aim_id") == aim_id]
    lines = [f"## 목표 {aim_id}: {aim['description']}", "", "### 달성 기준(이 목표의 기준으로 채택된 접근안)"]
    lines += [f"- {p['id']}:\n" + "\n".join("    " + line for line in p["direction"].splitlines()) for p in criteria] or ["따로 채택된 기준 없음(목표 설명과 접근안에서 판단)"]
    lines += ["", "### 접근안"]
    lines += [f"- {p['id']} ({p['status']}, 과제 {p.get('accepted_goal_id')}):\n" + "\n".join("    " + line for line in p["direction"].splitlines()) for p in proposals]
    lines += ["", "### 과제와 검증 판단"]
    for goal in goals:
        lines.append(f"- {goal['id']} [{goal['phase']}] {goal['target']}\n  성공 기준: {goal['contract']['success_criterion']}")
        lines += [f"  - {v['id']}: {v['result']} ({v['validity']}, 검증자 {v['verifier_actor_id']})\n    이유: " + (v.get('reason') or '').replace("\n", "\n    ")
                  for v in projection["verdicts"] if v["goal_id"] == goal["id"]]
    lines += ["", "### 하위 목표와 달성 평가"]
    lines += [f"- {child['id']} [{child['phase']}] {child['description']}" for child in children] or ["없음"]
    lines += [f"  - {e['id']} ({e['aim_id']}, {e['validity']}): {e['conclusion'].splitlines()[0]}" for e in projection["aim_evaluations"] if any(e["aim_id"] == c["id"] for c in children)]
    return f"""당신은 Goal Loop 목표 {aim_id}의 달성 평가자입니다. 사용자가 당신의 평가를 그대로 목표 달성 평가로 기록하도록 위임했습니다(Decision 6).

## 평가 규칙

- 목표의 달성 기준은 그 목표의 첫 접근안(또는 그것을 대체한 접근안)에 적혀 있다. 기준 항목마다 met / not_met / cannot_judge와 근거를 적는다.
- 과제가 검증됐다고 목표가 저절로 달성되지는 않는다. 검증 판단과 관찰 근거가 각 기준을 실제로 뒷받침하는지 판단하라.
- 인용할 수 있는 것은 이 목표의 과제에 대한 유효(valid)한 satisfied 검증 판단과, 직접 하위 목표의 유효한 달성 평가뿐이다. achieved가 true일 때 cited에 그 ID를 적는다.
- 모든 기준이 met일 때만 achieved는 true다. conclusion에는 항목별 판단을 "(1) 충족: …" 형식으로 줄을 나눠 쓰고 해석을 밝힌다.

{environment_notes(ws)}

{chr(10).join(lines)}

{('## 추가 안내' + chr(10) + chr(10) + note) if note else ''}

## 출력

최종 응답은 주어진 JSON Schema를 따른다. 한국어로 쓴다.
"""


def _json_file(path: Path) -> Callable[[], bool]:
    """True once ``path`` holds a complete JSON value (the judge's answer is written)."""
    def ready() -> bool:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return True
    return ready


def _end_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass


def _run_judge(command: list[str], *, prompt: str, env: dict[str, str], stdout: Path, stderr: Path | None = None,
               cwd: Path | None = None, ready: Callable[[], bool]) -> int | None:
    """Run the judge CLI to its end, or until ANSWER_GRACE_SECONDS after ``ready`` reports a complete answer.

    Returns the exit code, or None when the judge was still running after its answer and its process tree was ended.
    """
    with stdout.open("w", encoding="utf-8") as out, (stderr.open("w", encoding="utf-8") if stderr else nullcontext()) as err:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err if stderr else subprocess.STDOUT,
                                   cwd=cwd, env=env, text=True, encoding="utf-8", start_new_session=os.name != "nt")
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except OSError:
            pass  # the judge exited before reading its prompt; its log says why
        answered_at = None
        while process.poll() is None:
            if answered_at is None and ready():
                answered_at = time.monotonic()
            if answered_at is not None and time.monotonic() - answered_at >= ANSWER_GRACE_SECONDS:
                _end_tree(process.pid)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Ending the tree can be refused (a sandbox without process access); end at least the judge itself.
                    process.kill()
                    process.wait()
                return None
            time.sleep(POLL_SECONDS)
        return process.returncode


def _processes() -> list[tuple[int, str]]:
    """(pid, command line) of every process this user can see."""
    if os.name == "nt":
        script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
                  "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
        done = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, check=False)
        try:
            items = json.loads(done.stdout.decode("utf-8", errors="replace") or "[]")
        except ValueError:
            return []
        items = items if isinstance(items, list) else [items]
        return [(int(item["ProcessId"]), str(item["CommandLine"])) for item in items]
    done = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, check=False)
    return [(int(pid), args) for pid, _, args in (line.strip().partition(" ") for line in done.stdout.splitlines()) if pid.isdigit()]


def _end_leftovers(root: Path) -> list[str]:
    """End processes whose command line names the judge's copy (servers the judge left running); return what was ended."""
    def key(text: str) -> str:
        return text.replace("\\", "/").casefold()
    marker = key(str(root))
    ended = []
    for pid, command in _processes():
        if pid != os.getpid() and marker in key(command) and "Get-CimInstance" not in command:
            _end_tree(pid)
            ended.append(f"{pid} {command[:200]}")
    return ended


def codex_runner(prompt: str, ws: Workspace, schema: Path, answer: Path, log: Path) -> None:
    """Run Codex in the copy; its sandbox is not trusted to protect the project, the copy is."""
    codex = shutil.which("codex")
    if codex is None:
        raise JudgingError("codex 명령을 찾을 수 없습니다.")
    command = [codex, "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "--ephemeral",
               "-C", str(ws.project), "--add-dir", str(ws.tmp), *[arg for path in ws.trees.values() for arg in ("--add-dir", str(path))],
               "--output-schema", str(schema), "-o", str(answer), "-"]
    if _run_judge(command, prompt=prompt, env=ws.env(), stdout=log, ready=_json_file(answer)) is None:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"\n[delegated_judging] 답을 쓴 뒤 {ANSWER_GRACE_SECONDS:.0f}초 동안 끝나지 않아 판정 프로세스 트리를 끝냈습니다.\n")


Runner = Callable[[str, Workspace, Path, Path, Path], None]

JUDGES_FILE = "judges.json"
JUDGE_TOOLS = ("Bash", "Read", "Grep", "Glob", "Edit", "Write")
# Names recorded for each judge. They differ from the executor names (claude, codex) so Core's verifier≠executor rule holds.
JUDGE_NAMES = {"codex": "codex", "claude": "claude-judge", "glm": "glm"}
_PS1_ENV = re.compile(r"""^\s*\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:(['"])(.*)\2|([^\s'"#]+))\s*(?:#.*)?$""")
_ALLOWED_ENV = ("ANTHROPIC_", "CLAUDE_CODE_", "API_TIMEOUT_MS")


def env_from_ps1(path: Path) -> dict[str, str]:
    """``$env:NAME = 'value'`` lines of a PowerShell settings file, limited to Claude CLI connection variables."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = _PS1_ENV.match(line)
        if match and match.group(1).startswith(_ALLOWED_ENV):
            values[match.group(1)] = match.group(3) if match.group(2) else match.group(4)
    return values


def judge_settings(database: Path, judge: str) -> dict:
    """This judge's entry in ``.goal-loop/judges.json`` (empty when there is none)."""
    source = Path(database).parent / JUDGES_FILE
    if not source.is_file():
        return {}
    try:
        return dict(json.loads(source.read_text(encoding="utf-8")).get(judge) or {})
    except (OSError, ValueError) as error:
        raise JudgingError(f"{source}를 읽지 못했습니다: {error}") from error


def glm_environment(settings: dict) -> dict[str, str]:
    """GLM connection variables from the user's settings; never printed or recorded."""
    values: dict[str, str] = {}
    if settings.get("env_from_ps1"):
        source = Path(settings["env_from_ps1"])
        if not source.is_file():
            raise JudgingError(f"GLM 설정 파일을 찾을 수 없습니다: {source}")
        values.update(env_from_ps1(source))
    for name, ref in (settings.get("env_from_env") or {}).items():
        if ref in os.environ:
            values[name] = os.environ[ref]
    if not values.get("ANTHROPIC_BASE_URL") or not (values.get("ANTHROPIC_AUTH_TOKEN") or values.get("ANTHROPIC_API_KEY")):
        raise JudgingError("GLM 연결 값(ANTHROPIC_BASE_URL과 ANTHROPIC_AUTH_TOKEN)을 .goal-loop/judges.json의 glm 설정에서 찾지 못했습니다.")
    return values


def _scrub(text: str, secrets: dict[str, str]) -> str:
    for value in secrets.values():
        if len(value) >= 8:
            text = text.replace(value, "(숨김)")
    return text


def claude_runner(extra_env: dict[str, str] | None = None, model: str | None = None) -> Runner:
    """Run a fresh Claude CLI session in the copy; ``extra_env`` points it elsewhere (GLM) and is scrubbed from the log."""
    secrets = dict(extra_env or {})

    def run(prompt: str, ws: Workspace, schema: Path, answer: Path, log: Path) -> None:
        claude = shutil.which("claude")
        if claude is None:
            raise JudgingError("claude 명령을 찾을 수 없습니다.")
        command = [claude, "-p", "--output-format", "json", "--json-schema", schema.read_text(encoding="utf-8"),
                   # With the user's auto-mode settings, skipping permissions alone still denied commands outside the copy
                   # (the project's Python); the judge's tools are allowed explicitly. The copy remains the boundary.
                   "--dangerously-skip-permissions", "--allowedTools", *JUDGE_TOOLS, "--add-dir", str(ws.tmp),
                   *[arg for path in ws.trees.values() for arg in ("--add-dir", str(path))],
                   *(["--model", model] if model else [])]
        out, err = ws.root / f"{answer.stem}.stdout", ws.root / f"{answer.stem}.stderr"
        code = _run_judge(command, prompt=prompt, env={**ws.env(), **secrets}, stdout=out, stderr=err, cwd=ws.project, ready=_json_file(out))
        stdout, stderr = out.read_text(encoding="utf-8"), err.read_text(encoding="utf-8")
        out.unlink(missing_ok=True)
        err.unlink(missing_ok=True)
        ended = "" if code is not None else f"\n[delegated_judging] 답을 쓴 뒤 {ANSWER_GRACE_SECONDS:.0f}초 동안 끝나지 않아 판정 프로세스 트리를 끝냈습니다."
        log.write_text(_scrub(f"exit {code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}{ended}", secrets), encoding="utf-8")
        try:
            result = json.loads(stdout)
        except ValueError:
            return
        structured = result.get("structured_output") if isinstance(result, dict) else None
        if isinstance(structured, dict) and not result.get("is_error"):
            answer.write_text(json.dumps(structured, ensure_ascii=False), encoding="utf-8")

    return run


def runner_for(judge: str, database: Path) -> Runner:
    """The runner for a judge name: codex, claude (a fresh Claude CLI session) or glm (the Claude CLI pointed at GLM)."""
    if judge == "codex":
        return codex_runner
    settings = judge_settings(database, judge)
    if judge == "claude":
        return claude_runner(model=settings.get("model"))
    if judge == "glm":
        return claude_runner(glm_environment(settings), model=settings.get("model", "opus"))
    raise JudgingError(f"알 수 없는 판정자입니다: {judge} (codex, claude, glm 중 하나)")


def _refuse_own_work(projection: dict, attempt_id: str, agent: str) -> None:
    attempt = next((item for item in projection.get("attempts", []) if item["id"] == attempt_id), None)
    if attempt is not None and attempt.get("executor") == agent:
        raise JudgingError(f"판정자 {agent}가 {attempt_id}의 실행자와 같습니다. 실행하지 않은 다른 판정자를 고르십시오(Decision 6).")


def _run_agent(prompt: str, schema: dict, ws: Workspace, name: str, runner: Runner) -> tuple[dict, list[Path]]:
    prompt_path, schema_path = ws.root / f"prompt-{name}.md", ws.root / f"schema-{name}.json"
    answer, log = ws.root / f"answer-{name}.json", ws.root / f"agent-{name}.log"
    prompt_path.write_text(prompt, encoding="utf-8")
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        runner(prompt, ws, schema_path, answer, log)
    finally:
        ended = _end_leftovers(ws.root)
        if ended:
            with log.open("a", encoding="utf-8") as stream:
                stream.write("\n[delegated_judging] 판정 사본에서 남은 프로세스를 끝냈습니다:\n" + "\n".join(ended) + "\n")
    if not answer.is_file():
        raise JudgingError(f"판정 agent가 답을 남기지 않았습니다. 로그: {log}")
    try:
        result = json.loads(answer.read_text(encoding="utf-8"))
    except ValueError as error:
        raise JudgingError(f"판정 agent의 답이 JSON이 아닙니다: {answer}") from error
    return result, [answer, prompt_path, *([log] if log.is_file() else [])]


def _observation(header: str, answer: dict) -> str:
    return "\n".join([
        header,
        *[f"- {MARK.get(item['met'], item['met'])}: {item['item']} — {item['basis']}" for item in answer["criteria"]],
        "직접 확인: " + ("; ".join(answer["checked"]) or "없음"),
        "확인하지 못함: " + ("; ".join(answer["not_checked"]) or "없음"),
    ])


def judge_goal(database: Path, goal_id: str, *, agent: str = "codex", recorder: str = "claude", attachments_only: bool = False,
               note: str = "", evidence_ids: list[str] | None = None, record: bool = True, runner: Runner = codex_runner,
               base: Path | None = None, trees: dict[str, Path] | None = None) -> dict:
    root = project_root_for(database)
    if root is None:
        raise JudgingError("프로젝트 폴더의 journal(.goal-loop/journal.sqlite3)에서만 위임 판정을 실행합니다.")
    ws = prepare_workspace(root, database, base, trees)
    projection = cli(ws.database, "ui-projection")
    prompt, attempt_id, supersedes = goal_prompt(projection, goal_id, ws, attachments_only=attachments_only, note=note, evidence_ids=evidence_ids or [])
    _refuse_own_work(projection, attempt_id, agent)
    answer, files = _run_agent(prompt, VERDICT_SCHEMA, ws, goal_id, runner)
    if answer.get("result") not in VERDICT_SCHEMA["properties"]["result"]["enum"]:
        raise JudgingError(f"판정 결과를 알 수 없습니다: {answer.get('result')}")
    summary = {"workspace": str(ws.root), "goal": goal_id, "attempt": attempt_id, "result": answer["result"], "recorded": False}
    if not record:
        return summary
    header = f"{agent} 독립 판정(delegated_judging, 프로젝트 사본 {ws.root}{', 첨부만 사용' if attachments_only else ''}). 결과: {answer['result']}. 기록은 {recorder}가 대행."
    attach = [arg for path in files for arg in ("--attach", str(path))]
    evidence = cli(database, "evidence-record", attempt_id, agent, _observation(header, answer), *attach)
    after = cli(database, "ui-projection")
    corrected = {item["corrected_evidence_id"] for item in after.get("evidence_corrections") or []}  # Core refuses them as support
    cited = [item["id"] for item in after["evidence"] if item["attempt_id"] == attempt_id and item["id"] != evidence["id"] and item["id"] not in corrected]
    extra = ["--supersedes-verdict", supersedes] if supersedes else []
    verdict = cli(database, "verdict-submit", goal_id, attempt_id, agent, answer["result"], *cited, evidence["id"], *extra, "--reason", answer["reason"])
    return {**summary, "recorded": True, "evidence": evidence["id"], "verdict": verdict["id"], "supersedes": supersedes,
            **_memory_draft(database, verdict["id"])}


def _memory_draft(database: Path, record_id: str) -> dict:
    """The Memory Tool timeline line suggested for a record just made (only when the project has .memory)."""
    found = cli(database, "memory-draft", "--record", record_id).get("memory_drafts") or []
    return {"memory_draft": {"text": found[0]["text"], "command": found[0]["command"]}} if found else {}


def judge_aim(database: Path, aim_id: str, *, agent: str = "codex", note: str = "", record: bool = True,
              runner: Runner = codex_runner, base: Path | None = None, trees: dict[str, Path] | None = None) -> dict:
    root = project_root_for(database)
    if root is None:
        raise JudgingError("프로젝트 폴더의 journal(.goal-loop/journal.sqlite3)에서만 위임 판정을 실행합니다.")
    ws = prepare_workspace(root, database, base, trees)
    projection = cli(ws.database, "ui-projection")
    answer, files = _run_agent(aim_prompt(projection, aim_id, ws, note=note), AIM_SCHEMA, ws, aim_id, runner)
    summary = {"workspace": str(ws.root), "aim": aim_id, "achieved": bool(answer.get("achieved")), "recorded": False,
               "conclusion": answer.get("conclusion", ""), "answer": str(files[0])}
    if not (record and answer.get("achieved")):
        return summary
    # Only citations Core can accept: valid satisfied Verdicts of this Aim's Goals and valid evaluations of direct sub-Aims.
    goals = {item["id"] for item in projection["goals"] if item["aim_id"] == aim_id}
    children = {item["id"] for item in projection["aims"] if item.get("parent_aim_id") == aim_id}
    allowed = {v["id"] for v in projection["verdicts"] if v["goal_id"] in goals and v["result"] == "satisfied" and v["validity"] == "valid"}
    allowed |= {e["id"] for e in projection["aim_evaluations"] if e["aim_id"] in children and e["validity"] == "valid"}
    cited = [item for item in answer.get("cited", []) if item in allowed]
    if not cited:
        return {**summary, "note": "인용할 수 있는 판정이 없어 기록하지 않았습니다."}
    evaluation = cli(database, "aim-evaluate", aim_id, agent, answer["conclusion"], *cited)
    return {**summary, "recorded": True, "evaluation": evaluation["id"], "cited": cited, **_memory_draft(database, evaluation["id"])}


def main(argv: list[str] | None = None) -> None:
    # One name whether it runs as the goal-loop-judge console script or as python -m goal_loop_tool.delegated_judging.
    parser = argparse.ArgumentParser(prog="goal-loop-judge", description="Run a separate agent on a project copy and record its judgment unchanged (Decision 6)")
    parser.add_argument("--database", type=Path, help="project journal (default: the nearest .goal-loop/journal.sqlite3)")
    parser.add_argument("--judge", choices=sorted(JUDGE_NAMES), default="codex",
                        help="judging agent: codex (codex exec), claude (a fresh Claude CLI session), glm (Claude CLI pointed at GLM via .goal-loop/judges.json)")
    parser.add_argument("--agent", default=None, help="name recorded for the judging agent (default: codex, claude-judge, or glm by --judge)")
    parser.add_argument("--note", default="", help="extra guidance appended to the prompt")
    parser.add_argument("--no-record", action="store_true", help="run the judgment but do not record it")
    parser.add_argument("--tree", action="append", default=[], metavar="NAME=PATH",
                        help="also copy another working tree for the judge (e.g. the AO lane a Goal changed); repeatable")
    sub = parser.add_subparsers(dest="kind", required=True)
    goal = sub.add_parser("goal", help="judge the verifying Attempt of a Goal")
    goal.add_argument("id")
    goal.add_argument("--attachments-only", action="store_true", help="judge from Evidence and attachments only")
    goal.add_argument("--evidence", nargs="*", default=[], help="Earlier Evidence of the same Goal to show in addition to the verifying Attempt's (always shown)")
    aim = sub.add_parser("aim", help="judge whether an Aim is achieved")
    aim.add_argument("id")
    args = parser.parse_args(argv)
    database = args.database or discover_project_database(Path.cwd())
    if database is None:
        parser.error("프로젝트 journal을 찾을 수 없습니다. --database를 지정하세요.")
    try:
        trees = parse_trees(args.tree)
        agent = args.agent or JUDGE_NAMES[args.judge]
        runner = runner_for(args.judge, database)
        if args.kind == "goal":
            result = judge_goal(database, args.id, agent=agent, attachments_only=args.attachments_only, note=args.note,
                                evidence_ids=args.evidence, record=not args.no_record, trees=trees, runner=runner)
        else:
            result = judge_aim(database, args.id, agent=agent, note=args.note, record=not args.no_record, trees=trees, runner=runner)
    except JudgingError as error:
        parser.exit(2, f"delegated_judging: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
