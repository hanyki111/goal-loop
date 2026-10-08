"""Tally the AO internal judgments that kept a Goal Loop-run AO Goal from completing (goal-95, read-only).

For every AO Goal linked to a Goal Loop Goal (``ao_runs`` in ui-projection), collect:
  A. completion blocks: AO commits whose ``Goal-Status`` trailer is awaiting_review or failed, with the reason;
  B. stage-failure approvals: "Stage ... failed after N attempts" questions in the sessions' human/answers.json;
  C. scope approvals: dynamic stages asking for write paths outside the Goal (permission, not a judgment).
Each block is matched with the human resolution and the Goal Loop Verdict of the Attempt that ran that session,
and marked 맞음 / 틀림 / 판단 불가 by the rules printed in the report. Nothing is written except the output files.

usage: python scripts/ao_gate_tally.py <ao-workspace> <out-dir>
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from goal_loop_tool.ao_links import runs  # noqa: E402
from goal_loop_tool.persistent import PersistentGoalLoop  # noqa: E402
from goal_loop_tool.project import artifacts_dir_for  # noqa: E402
from goal_loop_tool.repository import SqliteEventRepository  # noqa: E402

PROJECT = "ai-orchestration"

# Completion-block kinds, matched on the reason the AO commit carries.
KINDS = [
    ("목표 달성 재확인", re.compile(r"grounded 목표달성 재확인|목표달성 재확인")),
    ("변경 없음 판정", re.compile(r"파일을 하나도 생산하지|변경 파일 수를 세지 못해")),
    ("완료 경계 게이트(커버리지·참조)", re.compile(r"완료 경계 구조 게이트")),
    ("계획된 검증 stage 미실행", re.compile(r"계획된 검증 stage")),
    ("변경 전 실행 가드", re.compile(r"변경 전\(고치기 전\) 코드에서 실패하는 새")),
    ("리뷰어", re.compile(r"^\[리뷰어\]")),
    ("stage 게이트", re.compile(r"\[stage-gate\]")),
]
# failed commits that are not a judgment about the change.
NOT_JUDGMENT = [
    ("승인 대기 멈춤(goal-58 결함)", re.compile(r"stopped by user")),
    ("사람이 단계 거부", re.compile(r"Stage \"[^\"]+\" rejected")),
    ("환경·엔진 오류", re.compile(r"ENOENT|No available adapter|Invalid session status|Cannot read properties|workflow-repair")),
]


def kind_of(reason: str, table) -> str | None:
    return next((name for name, pattern in table if pattern.search(reason)), None)


def git_commits(ao_workspace: Path) -> list[dict]:
    out = subprocess.run(["git", f"--git-dir={ao_workspace / 'repos' / (PROJECT + '.git')}", "log", "--all",
                          "--format=%H%x1f%cI%x1f%B%x1e"], capture_output=True, encoding="utf-8", check=True).stdout
    commits = []
    for record in out.split("\x1e"):
        if not record.strip():
            continue
        sha, at, body = record.strip().split("\x1f", 2)
        goal = re.search(r"^Goal-Id:\s*(\S+)", body, re.M)
        status = re.search(r"^Goal-Status:\s*(\S+)", body, re.M)
        if not goal or not status:
            continue
        reason = re.search(r"^(?:검토 사유|실패 사유)\s*:\s*(.+)$", body, re.M)
        subject = body.splitlines()[0]
        text = reason.group(1) if reason else re.sub(r"^chore\(goal\): failed — ", "", subject)
        commits.append({"sha": sha, "at": at, "ao_goal": goal.group(1), "status": status.group(1), "reason": text.strip()})
    return commits


def main() -> None:
    ao_workspace, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    database = ROOT / ".goal-loop" / "journal.sqlite3"
    loop = PersistentGoalLoop(SqliteEventRepository(database))
    linked = [run for run in runs(loop, artifacts_dir_for(database)) if run["ao_project_id"] == PROJECT]
    by_ao_goal = defaultdict(list)
    for run in linked:
        by_ao_goal[run["ao_goal_id"]].append(run)

    # Session -> Attempt, and the human resolution delivered for that session, from Goal Loop Evidence.
    session_attempt, resolution = {}, {}
    for evidence in loop.evidence.values():
        for match in re.finditer(r"delivery ao-cli:(run-\d+)(?::([\w-]+))?", evidence.observation):
            session_attempt.setdefault(match.group(1), evidence.attempt_id)
            if match.group(2) and match.group(2).startswith("review-"):
                resolution[match.group(1)] = match.group(2)
        for match in re.finditer(r"(run-\d{13})", evidence.observation):
            session_attempt.setdefault(match.group(1), evidence.attempt_id)
    verdict_of = {}
    for verdict in loop.verdicts.values():
        if loop.verdict_validity[verdict.id].value != "invalidated":
            verdict_of[verdict.attempt_id] = verdict.result.value  # later Verdicts supersede earlier ones

    sessions_dir = ao_workspace / "projects" / PROJECT / "sessions"
    session_goal, session_times = {}, {}
    for session_file in sessions_dir.glob("*/session.json"):
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        session_goal[data.get("id")] = data.get("goalId")
        session_times[data.get("id")] = (data.get("createdAt") or "", data.get("updatedAt") or "")

    def session_for(ao_goal: str, at: str) -> str | None:
        """The session of this AO Goal running when the commit was made (latest one started before it)."""
        from datetime import datetime
        when = datetime.fromisoformat(at).timestamp()
        best = None
        for sid, goal in session_goal.items():
            if goal != ao_goal:
                continue
            started = session_times[sid][0]
            if started and datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp() <= when + 1:
                if best is None or session_times[sid][0] > session_times[best][0]:
                    best = sid
        return best

    rows = []
    for commit in git_commits(ao_workspace):
        if commit["ao_goal"] not in by_ao_goal or commit["status"] not in ("awaiting_review", "failed"):
            continue
        judgment = kind_of(commit["reason"], KINDS)
        other = None if judgment else kind_of(commit["reason"], NOT_JUDGMENT)
        session = session_for(commit["ao_goal"], commit["at"])
        attempt = session_attempt.get(session) if session else None
        verdict = verdict_of.get(attempt) if attempt else None
        human = resolution.get(session) if session else None
        if judgment is None:
            verdict_mark, why = "제외", f"판정이 아님: {other or '분류 안 됨'}"
        elif human == "review-reject" and verdict == "satisfied":
            verdict_mark, why = "틀림", "사람이 오탐으로 넘겼고(review-reject) 그 실행의 Goal Loop 판정이 satisfied"
        elif human in ("review-fail", "review-accept"):
            verdict_mark, why = "맞음", f"사람이 우려를 받아들임({human})"
        elif human == "review-reject":
            verdict_mark, why = "판단 불가", f"사람은 오탐으로 넘겼으나 Goal Loop 판정이 {verdict or '없음'}"
        else:
            verdict_mark, why = "판단 불가", "해소 기록이 Goal Loop에 없음(ao_session.v1 시기이거나 세션을 다시 실행함)"
        rows.append({"section": "A", "ao_goal": commit["ao_goal"], "goal_ids": sorted({r["goal_id"] for r in by_ao_goal[commit["ao_goal"]]}),
                     "kind": judgment or other or "분류 안 됨", "status": commit["status"], "commit": commit["sha"][:8],
                     "session": session, "attempt": attempt, "human": human, "goal_loop_verdict": verdict,
                     "mark": verdict_mark, "why": why, "reason": commit["reason"][:220]})

    for answers_file in sessions_dir.glob("*/human/answers.json"):
        sid = answers_file.parent.parent.name
        ao_goal = session_goal.get(sid)
        if ao_goal not in by_ao_goal:
            continue
        try:
            answers = json.loads(answers_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for answer in answers if isinstance(answers, list) else []:
            key = answer.get("key") or ""
            approval = answer.get("approval") or {}
            action = approval.get("action") or ("approve" if approval.get("approved") else "reject")
            attempt = session_attempt.get(sid)
            base = {"ao_goal": ao_goal, "goal_ids": sorted({r["goal_id"] for r in by_ao_goal[ao_goal]}), "session": sid,
                    "attempt": attempt, "human": action, "goal_loop_verdict": verdict_of.get(attempt) if attempt else None,
                    "reason": key.replace("approval:", "")[:220], "commit": None, "status": "approval"}
            if "failed after" in key:
                verdict = base["goal_loop_verdict"]
                if action in ("retry", "mark_failed", "reject"):
                    mark, why = "맞음", f"사람이 실패를 인정하고 {action}로 답함"
                elif action in ("approve", "bypass_gate") and verdict == "satisfied":
                    mark, why = "틀림", f"사람이 실패를 넘기고({action}) 그 실행의 Goal Loop 판정이 satisfied"
                else:
                    mark, why = "판단 불가", f"답 {action}, Goal Loop 판정 {verdict or '없음'}: 실패를 반박했다고도 인정했다고도 볼 수 없음"
                rows.append({**base, "section": "B", "kind": "단계 실패 소진(검증기)", "mark": mark, "why": why})
            elif "쓰기 범위를 넘는" in key:
                rows.append({**base, "section": "C", "kind": "범위 확장 요청(권한)", "mark": "제외", "why": f"판정이 아닌 권한 요청, 답 {action}"})

    rows.sort(key=lambda row: (row["section"], row["kind"], row["ao_goal"]))
    (out_dir / "ao-gate-tally.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = defaultdict(Counter)
    for row in rows:
        summary[(row["section"], row["kind"])][row["mark"]] += 1
    lines = ["| 구역 | 종류 | 건수 | 맞음 | 틀림 | 판단 불가 | 제외 |", "|---|---|---|---|---|---|---|"]
    for (section, kind), counts in sorted(summary.items()):
        lines.append(f"| {section} | {kind} | {sum(counts.values())} | {counts['맞음']} | {counts['틀림']} | {counts['판단 불가']} | {counts['제외']} |")
    lines += ["", "| 구역 | 종류 | AO Goal | 과제 | 해소 | Goal Loop 판정 | 판단 | 근거 |", "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['section']} | {row['kind']} | {row['ao_goal']} | {','.join(row['goal_ids'])} | {row['human'] or '-'} | "
                     f"{row['goal_loop_verdict'] or '-'} | {row['mark']} | {row['why']} |")
    (out_dir / "ao-gate-tally.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[: len(summary) + 2]))
    print(f"linked AO Goals: {len(by_ao_goal)}, rows: {len(rows)}")


if __name__ == "__main__":
    main()
