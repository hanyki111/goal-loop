"""A scripted stand-in for the AO CLI (src/cli/index.ts) used by the ao_cli.v1 tests.

It keeps AO's file layout (goals/goals.json, sessions/<id>/session.json, sessions/<id>/human/pending.json) and its
output contract (diagnostic lines first, the result JSON on the last stdout line, exit 0/1/2/3). The outcomes of
run-goal and resume are taken in order from <workspace>/fake-outcomes.json: "completed", "failed", or "waiting"
(a pending approval and a stopped session).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time


def main(argv: list[str]) -> int:
    workspace = Path(argv[argv.index("-w") + 1])
    args = argv[: argv.index("-w")]
    command, project = args[0], args[1]
    project_dir = workspace / "projects" / project
    index_path = project_dir / "goals" / "goals.json"
    calls = workspace / "fake-calls.jsonl"
    with calls.open("a", encoding="utf-8") as log:
        log.write(json.dumps(args, ensure_ascii=False) + "\n")
    print("[fake-ao] diagnostic line that is not the result")

    def index() -> dict:
        return json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"goals": []}

    def set_goal_status(goal_id: str, status: str) -> None:
        data = index()
        for goal in data["goals"]:
            if goal["id"] == goal_id:
                goal["status"] = status
        index_path.write_text(json.dumps(data), encoding="utf-8")

    def next_outcome() -> str:
        script = workspace / "fake-outcomes.json"
        queue = json.loads(script.read_text(encoding="utf-8"))
        outcome = queue.pop(0)
        script.write_text(json.dumps(queue), encoding="utf-8")
        return outcome

    def finish(goal_id: str, session_id: str) -> int:
        outcome = next_outcome()
        session_dir = project_dir / "sessions" / session_id
        pending_path = session_dir / "human" / "pending.json"
        if outcome == "review":
            (session_dir / "session.json").write_text(json.dumps({"id": session_id, "goalId": goal_id, "status": "awaiting_review",
                                                                  "createdAt": "2026-10-05T00:00:00Z"}), encoding="utf-8")
            set_goal_status(goal_id, "awaiting_review")
            print(json.dumps({"projectId": project, "goalId": goal_id, "sessionId": session_id, "outcome": "waiting_human",
                              "sessionStatus": "awaiting_review", "goalStatus": "awaiting_review", "pending": None, "head": None}))
            return 3
        if outcome == "waiting":
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            pending = {"id": "human-1", "kind": "approval", "key": "approval:Q", "question": "Stage \"impl\" failed. Continue anyway?",
                       "projectId": project, "sessionId": session_id, "requestedAt": "2026-10-05T00:00:00Z"}
            pending_path.write_text(json.dumps(pending), encoding="utf-8")
            session_status, goal_status, result_outcome, code = "stopped", "ready", "waiting_human", 3
        else:
            derived = outcome.endswith("+derived")
            outcome = outcome.removesuffix("+derived")
            pending, session_status, goal_status, result_outcome, code = None, outcome, outcome, outcome, (0 if outcome == "completed" else 1)
        (session_dir / "session.json").write_text(json.dumps({"id": session_id, "goalId": goal_id, "status": session_status,
                                                              "createdAt": "2026-10-05T00:00:00Z"}), encoding="utf-8")
        set_goal_status(goal_id, goal_status)
        result = {"projectId": project, "goalId": goal_id, "sessionId": session_id, "outcome": result_outcome,
                  "sessionStatus": session_status, "goalStatus": goal_status, "pending": pending,
                  "head": {"sha": "abc", "goalId": goal_id, "goalStatus": goal_status}}
        if outcome != "waiting" and derived:
            result["derivedGoals"] = [
                {"title": "후속: 실패 commit 시험을 더한다", "description": "commitGoalFailure 를 덮는 시험이 없다.",
                 "objective": "실패 commit 경로를 단언하는 시험이 통과한다.", "relationship": "derived", "activityKind": "change"},
                {"title": "후속: 문서를 고친다", "relationship": "subgoal"},
            ]
        print(json.dumps(result))
        return code

    if command == "goal-create":
        goal_id = args[args.index("--id") + 1]
        data = index()
        data["goals"].append({"id": goal_id, "title": args[args.index("--title") + 1], "status": "ready", "path": f"goals/{goal_id}",
                              "milestoneId": args[args.index("--milestone") + 1] if "--milestone" in args else None})
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(data), encoding="utf-8")
        print(json.dumps({"goal": {"id": goal_id, "status": "ready"}}))
        return 0
    if command == "run-goal":
        goal_id = args[2]
        session_id = f"run-{time.time_ns()}"
        (project_dir / "sessions" / session_id).mkdir(parents=True, exist_ok=True)
        return finish(goal_id, session_id)
    if command == "resume":
        session_id = args[2]
        session = json.loads((project_dir / "sessions" / session_id / "session.json").read_text(encoding="utf-8"))
        if session["status"] != "stopped":
            print(json.dumps({"error": f"Session {session_id} cannot be resumed from status {session['status']}"}))
            return 2
        return finish(session["goalId"], session_id)
    if command == "pending":
        path = project_dir / "sessions" / args[2] / "human" / "pending.json"
        print(json.dumps({"pending": json.loads(path.read_text(encoding="utf-8")) if path.exists() else None}))
        return 0
    if command == "respond":
        session_dir = project_dir / "sessions" / args[2]
        pending_path = session_dir / "human" / "pending.json"
        if not pending_path.exists():
            print(json.dumps({"error": "기다리는 사람 입력이 없다"}))
            return 2
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        (session_dir / "human" / "answers.json").write_text(json.dumps([{"id": pending["id"], "args": args[3:]}]), encoding="utf-8")
        pending_path.unlink()
        status = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))["status"]
        print(json.dumps({"answered": pending["id"], "kind": pending["kind"], "sessionStatus": status, "resumeNeeded": status == "stopped"}))
        return 0
    if command == "resolve-review":
        goal_id = args[2]
        verdict = args[args.index("--verdict") + 1]
        completed = verdict in ("accept", "reject")
        status = "completed" if completed else "failed" if verdict == "fail" else "awaiting_review"
        if status != "awaiting_review":
            set_goal_status(goal_id, status)
        print(json.dumps({"goalId": goal_id, "verdict": verdict, "completed": completed, "goalStatus": status}))
        return 0
    print(json.dumps({"error": f"unknown command {command}"}))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
