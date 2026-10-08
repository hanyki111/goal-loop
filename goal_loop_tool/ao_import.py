"""Read one AI_ORCHESTRATION (AO) Goal's results as Goal Loop Evidence material.

AO is read only. An AO project folder is ``<ao-workspace>/projects/<project-id>``; the facts come from
``goals/goals.json`` (current status and review reasons), ``sessions/*/session.json`` and the
``verifier:result`` lines of ``sessions/*/events.jsonl`` (verifiers that actually ran), and the bare repo
``<ao-workspace>/repos/<project-id>.git`` whose commits carry ``Goal-Id``/``Goal-Status`` trailers.
See docs/AO-ADAPTER-MAPPING.ko.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import subprocess

# Free text from AO is clipped so one Evidence stays readable; the attached snapshot keeps the full text.
_REASON_LIMIT = 300
# A commit patch is attached so a verifier can judge code without opening AO; very large ones are cut.
PATCH_LIMIT = 256 * 1024
_GOAL_FIELDS = (
    "id", "title", "status", "objective", "parentId", "milestoneId", "workflowRef", "relationship",
    "reviewReason", "reviewSource", "structuralUnmet", "pmDecisionReason", "abandonedAt", "abandonReason",
)


class AoImportError(ValueError):
    """The AO folder or Goal cannot be read; nothing is recorded."""


@dataclass(frozen=True)
class AoGoalSnapshot:
    project_id: str
    goal: dict
    sessions: list[dict]
    commits: list[dict]
    repo_note: str | None
    source_files: list[Path]
    # Raw material written to separate attachments rather than into the snapshot JSON.
    index_entry: dict = field(default_factory=dict)
    verifier_lines: dict[str, list[str]] = field(default_factory=dict)
    patches: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict:
        return {
            "source": "ai_orchestration", "project_id": self.project_id, "goal_entry_from": "goals/goals.json", "goal": self.goal,
            "sessions": self.sessions, "commits": self.commits, "repo_note": self.repo_note,
            "source_files": [str(path) for path in self.source_files],
        }


def _clip(text: object) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= _REASON_LIMIT else value[: _REASON_LIMIT - 1] + "…"


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AoImportError(f"AO 파일을 읽을 수 없습니다: {path} ({error})") from error


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return "__tests__/" in normalized or ".test." in normalized or ".spec." in normalized or normalized.startswith("tests/")


def _sessions_for(project_dir: Path, ao_goal_id: str) -> tuple[list[dict], list[Path], dict[str, list[str]]]:
    sessions: list[dict] = []
    files: list[Path] = []
    raw_lines: dict[str, list[str]] = {}
    for session_file in sorted((project_dir / "sessions").glob("*/session.json")):
        try:
            session = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # A half-written or foreign session must not block reading the others.
        if session.get("goalId") != ao_goal_id:
            continue
        verifiers, advisories = [], []
        session_id = session.get("id", session_file.parent.name)
        raw_lines[session_id] = []
        events = session_file.parent / "events.jsonl"
        if events.is_file():
            with events.open(encoding="utf-8") as lines:
                for line in lines:
                    advisory = '"completion:advisory"' in line
                    if '"verifier:result"' not in line and not advisory:
                        continue  # Agent output chunks dominate this file; skip them without parsing.
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    payload = event.get("payload") or {}
                    if advisory:
                        # An --external-verdict run records the judgments it did not let block completion (goal-96).
                        if event.get("type") == "engine:notice" and payload.get("category") == "completion:advisory":
                            advisories.append({"at": event.get("timestamp"), "clause": payload.get("clause"),
                                               "reason": _clip(payload.get("reason") or payload.get("message"))})
                        continue
                    raw_lines[session_id].append(line.rstrip("\n"))
                    verifiers.append({
                        "at": event.get("timestamp"), "type": payload.get("verifierType"),
                        "passed": payload.get("passed"), "reason": _clip(payload.get("reason")),
                    })
        sessions.append({
            "id": session_id, "status": session.get("status"),
            "activity_kind": session.get("activityKind"), "created_at": session.get("createdAt"),
            "updated_at": session.get("updatedAt"), "verifiers": verifiers, "advisories": advisories,
        })
        files.append(session_file)
    sessions.sort(key=lambda item: item.get("created_at") or "")
    return sessions, files, raw_lines


def _git(git: str, repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([git, "--git-dir", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)


def _commit_details(git: str, repo: Path, sha: str) -> tuple[str, list[str], str]:
    """Full message (with trailers), changed paths, and the patch text of one commit."""
    message = _git(git, repo, "show", "-s", "--format=%B", sha).stdout.strip()
    names = _git(git, repo, "show", "--no-color", "--name-status", "--format=", sha).stdout
    files = [line.split("\t")[-1] for line in names.splitlines() if line.strip()]
    patch = _git(git, repo, "show", "--no-color", "--format=fuller", "--stat", "--patch", sha).stdout
    return message, files, patch


def _commits_for(repo: Path, ao_goal_id: str) -> tuple[list[dict], str | None, dict[str, str]]:
    if not repo.is_dir():
        return [], f"AO 저장소가 없습니다: {repo}", {}
    git = shutil.which("git")
    if git is None:
        return [], "git을 찾을 수 없어 commit을 읽지 못했습니다.", {}
    fmt = "%H%x1f%s%x1f%cI%x1f%(trailers:key=Goal-Id,valueonly,separator=%x2C)%x1f%(trailers:key=Goal-Status,valueonly,separator=%x2C)%x1e"
    result = subprocess.run(
        [git, "--git-dir", str(repo), "log", "--all", "--fixed-strings", f"--grep=Goal-Id: {ao_goal_id}", f"--format={fmt}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode != 0:
        return [], f"git log 실패: {_clip(result.stderr)}", {}
    commits = []
    patches: dict[str, str] = {}
    for record in result.stdout.split("\x1e"):
        fields = record.strip("\n").split("\x1f")
        if len(fields) != 5:
            continue
        sha, subject, committed_at, goal_ids, status = (field.strip() for field in fields)
        # --grep matches substrings (goal-a also matches goal-ab); keep exact trailer matches only.
        if ao_goal_id not in [item.strip() for item in goal_ids.split(",")]:
            continue
        message, files, patch = _commit_details(git, repo, sha)
        commits.append({
            "sha": sha, "subject": subject, "committed_at": committed_at, "goal_status": status or None,
            "message": message, "files": files, "test_files": [path for path in files if _is_test_path(path)],
        })
        patches[sha] = patch
    return commits, None, patches


def read_ao_goal(project_dir: Path, ao_goal_id: str) -> AoGoalSnapshot:
    project_dir = project_dir.resolve()
    index = project_dir / "goals" / "goals.json"
    if not index.is_file():
        raise AoImportError(f"AO 프로젝트 폴더가 아닙니다(goals/goals.json 없음): {project_dir}")
    data = _read_json(index)
    goals = data.get("goals") if isinstance(data, dict) else None
    if not isinstance(goals, list):
        raise AoImportError(f"AO goals.json 형식을 알 수 없습니다: {index}")
    entry = next((item for item in goals if isinstance(item, dict) and item.get("id") == ao_goal_id), None)
    if entry is None:
        raise AoImportError(f"AO Goal을 찾을 수 없습니다: {ao_goal_id} ({index})")
    goal = {key: entry[key] for key in _GOAL_FIELDS if key in entry}
    goal["intent"] = (entry.get("intent") or {}).get("description") if isinstance(entry.get("intent"), dict) else entry.get("intent")
    sessions, session_files, verifier_lines = _sessions_for(project_dir, ao_goal_id)
    repo = project_dir.parent.parent / "repos" / f"{project_dir.name}.git"
    commits, repo_note, patches = _commits_for(repo, ao_goal_id)
    # goals.json is the whole index (MBs); the snapshot carries this Goal's entry, so only per-Goal files are attached.
    goal_file = project_dir / "goals" / ao_goal_id / "goal.json"
    sources = [goal_file] if goal_file.is_file() else []
    return AoGoalSnapshot(project_dir.name, goal, sessions, commits, repo_note, sources + session_files,
                          index_entry=entry, verifier_lines=verifier_lines, patches=patches)


def summarize(snapshot: AoGoalSnapshot) -> str:
    """One Korean observation a verifier can read without opening AO."""
    goal = snapshot.goal
    lines = [f"AO Goal {goal['id']} (프로젝트 {snapshot.project_id}) 「{goal.get('title', '')}」: AO 상태 {goal.get('status')}."]
    if goal.get("objective"):
        lines.append(f"AO 완료 기준(objective): {_clip(goal['objective'])}")
    if len(snapshot.sessions) > 1:
        for session in snapshot.sessions[:-1]:
            passed_last = {str(item["type"]): item["passed"] for item in session["verifiers"]}
            failing = sorted(kind for kind, passed in passed_last.items() if passed is False)
            lines.append(f"이전 세션 {session['id']}({session['status']}): 검증기 {len(session['verifiers'])}회"
                         + (f", 마지막 결과가 실패인 검증기: {', '.join(failing)}." if failing else "."))
    if snapshot.sessions:
        latest = snapshot.sessions[-1]
        runs = latest["verifiers"]
        failures = [item for item in runs if item["passed"] is False]
        # A stage retry re-runs verifiers, so an early failure can be followed by a pass; report both views.
        last_by_type: dict[str, bool | None] = {}
        for item in runs:
            last_by_type[str(item["type"])] = item["passed"]
        still_failing = sorted(kind for kind, passed in last_by_type.items() if passed is False)
        failed_kinds = sorted({str(item["type"]) for item in failures})
        lines.append(
            f"세션 {len(snapshot.sessions)}개, 최근 {latest['id']}({latest['status']}): 검증기 {len(last_by_type)}종 {len(runs)}회 실행, "
            + (f"도중 실패 {len(failures)}회({', '.join(failed_kinds)}), " if failures else "도중 실패 없음, ")
            + (f"마지막 결과가 실패인 검증기: {', '.join(still_failing)}." if still_failing else "모든 검증기의 마지막 결과는 통과.")
        )
    else:
        lines.append("이 AO Goal을 실행한 세션 기록이 없습니다.")
    advisories = [item for session in snapshot.sessions for item in session.get("advisories", [])]
    if advisories:
        clauses = sorted({str(item.get("clause")) for item in advisories})
        lines.append(f"AO 참고 판정(완료를 막지 않음, --external-verdict) {len(advisories)}건: {', '.join(clauses)}. "
                     + "; ".join(_clip(f"{item.get('clause')}: {item.get('reason')}") for item in advisories[:3]))
    if snapshot.commits:
        for item in snapshot.commits:
            tests = item.get("test_files") or []
            lines.append(
                f"commit {item['sha'][:8]} 「{item['subject']}」 (Goal-Status: {item['goal_status']}): 변경 파일 {len(item.get('files') or [])}개"
                + (f", 테스트 파일 {', '.join(tests)}" if tests else ", 테스트 파일 없음") + "."
            )
    else:
        lines.append(f"commit 없음{' — ' + snapshot.repo_note if snapshot.repo_note else ''}.")
    for key, label in (("reviewReason", "리뷰 사유"), ("pmDecisionReason", "PM 결정 실패 사유"), ("abandonReason", "포기 사유")):
        if goal.get(key):
            lines.append(f"{label}: {_clip(goal[key])}")
    if goal.get("structuralUnmet"):
        lines.append("미충족 조항: " + "; ".join(_clip(f"{item.get('clause')}: {item.get('reason')}") for item in goal["structuralUnmet"]))
    lines.append("전체 내용은 첨부한 AO 스냅샷과 원본 파일에 있다.")
    return "\n".join(lines)


def write_snapshot(snapshot: AoGoalSnapshot, directory: Path) -> Path:
    path = directory / f"ao-goal-{snapshot.project_id}-{snapshot.goal['id']}.json"
    path.write_text(json.dumps(snapshot.as_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_extracts(snapshot: AoGoalSnapshot, directory: Path) -> list[Path]:
    """Raw AO material a verifier can recheck: the index entry, verifier event lines, and commit patches."""
    goal_id = snapshot.goal["id"]
    entry = directory / f"ao-index-entry-{goal_id}.json"
    entry.write_text(json.dumps(snapshot.index_entry, ensure_ascii=False, indent=2), encoding="utf-8")
    written = [entry]
    for session_id, lines in snapshot.verifier_lines.items():
        if lines:
            path = directory / f"ao-verifier-events-{session_id}.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            written.append(path)
    for item in snapshot.commits:
        patch = snapshot.patches.get(item["sha"], "")
        data = patch.encode("utf-8")
        if len(data) > PATCH_LIMIT:
            kept = data[:PATCH_LIMIT].decode("utf-8", errors="ignore")
            patch = f"# truncated: original {len(data)} bytes, kept the first {PATCH_LIMIT} bytes\n" + kept
        path = directory / f"ao-commit-{item['sha'][:8]}.patch"
        path.write_text(patch, encoding="utf-8")
        written.append(path)
    return written
