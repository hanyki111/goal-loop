"""A read-only text overview of a journal for people at a terminal (``goal-loop overview``).

It is drawn only from ``ui-projection`` and ``next`` records, so it shows what the desktop app shows without
needing the app or the VS Code extension. Nothing here writes to the journal.
"""
from __future__ import annotations

from collections import Counter
import re

AIM_STATE = {"open": "진행 중", "achieved": "달성됨", "abandoned": "그만둠"}
GOAL_STATE = {"committed": "실행 준비됨", "active": "실행 진행 중", "verifying": "검증 중", "verified": "검증됨", "stopped": "중단됨"}
VERDICT_RESULT = {"satisfied": "충족", "not_satisfied": "미충족", "inconclusive": "판단 불가"}
ACTION_NAMES = {
    "create_aim": "목표 만들기", "create_proposal": "접근안 적기", "accept_proposal": "접근안을 과제로 확정",
    "start_attempt": "실행 시작", "start_repair_attempt": "보완 실행 시작", "start_retry_attempt": "재시도 실행 시작", "start_reverification_attempt": "재검증 실행 시작",
    "complete_attempt": "실행 결과 기록", "record_evidence": "관찰 근거 기록", "record_additional_evidence": "관찰 근거 추가",
    "begin_verification": "검증 시작", "submit_verdict": "검증 판정", "submit_superseding_verdict": "검증 판정 다시 기록",
    "decide_retry": "재시도 결정", "decide_reverification": "재검증 결정", "evaluate_aim": "목표 달성 평가",
    "import_linked_evaluation": "연결된 목표의 평가 가져오기", "clear_goal_hold": "과제 보류 해제", "resume_aim": "목표 재개",
    "resolve_verdict_review": "판정 재검토 결정", "resolve_aim_evaluation_review": "목표 평가 재검토 결정",
    "inspect_goal_dependency": "선행 과제 확인", "inspect_verdict_dependency": "의존하는 판정 확인",
    "reopen_aim_after_closure_invalidation": "목표 다시 열기",
}
FOLDED_AIMS = ("achieved", "abandoned")
FOLDED_GOALS = ("verified", "stopped")
WIDTH = 72


def _number(record_id: str) -> tuple[int, str]:
    match = re.search(r"(\d+)$", record_id)
    return (int(match.group(1)) if match else 0, record_id)


def _ordered(counts: Counter, names: dict) -> list[tuple[str, int]]:
    order = list(names)
    return sorted(counts.items(), key=lambda item: (order.index(item[0]) if item[0] in order else len(order), item[0]))


def _short(text: str, width: int = WIDTH) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def subtree(aims: list[dict], root: str) -> set[str]:
    if root not in {aim["id"] for aim in aims}:
        raise ValueError(f"목표를 찾지 못했습니다: {root}")
    children: dict[str | None, list[str]] = {}
    for aim in aims:
        children.setdefault(aim.get("parent_aim_id"), []).append(aim["id"])
    found, stack = set(), [root]
    while stack:
        current = stack.pop()
        found.add(current)
        stack.extend(children.get(current, []))
    return found


def _aim_state(aim: dict) -> str:
    state = AIM_STATE.get(aim["phase"], aim["phase"])
    return f"{state} · 멈춤" if aim.get("paused") and aim["phase"] == "open" else state


def _goal_details(goal: dict, attempts: list[dict], verdicts: list[dict]) -> str:
    parts = [GOAL_STATE.get(goal["phase"], goal["phase"])]
    running = [item for item in attempts if item["goal_id"] == goal["id"] and item.get("outcome") == "running"]
    if running:
        attempt = max(running, key=lambda item: _number(item["id"]))
        parts.append(f"실행 중 {attempt['id']}({attempt.get('executor')})")
    if goal["phase"] == "verifying" and goal.get("verifying_attempt_id"):
        parts.append(f"검증 대상 {goal['verifying_attempt_id']}")
    valid = [item for item in verdicts if item["goal_id"] == goal["id"] and item.get("validity") == "valid"]
    if valid:
        verdict = max(valid, key=lambda item: _number(item["id"]))
        parts.append(f"{VERDICT_RESULT.get(verdict['result'], verdict['result'])} {verdict['id']}")
    if goal.get("held"):
        parts.append(f"보류: {_short(goal.get('hold_reason') or '', 30)}")
    return " · ".join(parts)


def render_overview(projection: dict, actions: list[dict], aim_scope: str | None = None, show_all: bool = False) -> str:
    aims = sorted(projection.get("aims", []), key=lambda item: _number(item["id"]))
    goals = sorted(projection.get("goals", []), key=lambda item: _number(item["id"]))
    attempts, verdicts = projection.get("attempts", []), projection.get("verdicts", [])
    scope = subtree(aims, aim_scope) if aim_scope else {aim["id"] for aim in aims}
    by_id = {aim["id"]: aim for aim in aims}
    children: dict[str | None, list[dict]] = {}
    for aim in aims:
        if aim["id"] not in scope:
            continue
        parent = aim.get("parent_aim_id")
        # The scope root stands at the top even when it has a parent outside the scope.
        children.setdefault(parent if parent in scope else None, []).append(aim)
    goals_of: dict[str, list[dict]] = {}
    for goal in goals:
        goals_of.setdefault(goal["aim_id"], []).append(goal)

    lines = ["Goal Loop 진행 상태" + (f" ({aim_scope} 범위)" if aim_scope else ""), ""]
    if projection.get("project_root"):
        lines.insert(1, f"프로젝트: {projection['project_root']}")

    def folded(label: str, items: list[dict]) -> str:
        counts = Counter(item["phase"] for item in items)
        names = AIM_STATE if label == "목표" else GOAL_STATE
        summary = ", ".join(f"{names.get(phase, phase)} {count}개" for phase, count in _ordered(counts, names))
        return f"(접힌 {label}: {summary})"

    def draw(aim: dict, depth: int) -> None:
        pad = "  " * depth
        lines.append(f"{pad}{aim['id']} [{_aim_state(aim)}] {_short(aim['description'], WIDTH - len(pad))}")
        own = goals_of.get(aim["id"], [])
        shown = own if show_all else [goal for goal in own if goal["phase"] not in FOLDED_GOALS]
        for goal in shown:
            lines.append(f"{pad}  - {goal['id']} [{_goal_details(goal, attempts, verdicts)}]")
            lines.append(f"{pad}      {_short(goal.get('target', ''), WIDTH - len(pad) - 6)}")
        hidden = [goal for goal in own if goal not in shown]
        if hidden:
            lines.append(f"{pad}  - {folded('과제', hidden)}")
        sub = children.get(aim["id"], [])
        visible = sub if show_all else [item for item in sub if item["phase"] not in FOLDED_AIMS]
        for child in visible:
            draw(child, depth + 1)
        closed = [item for item in sub if item not in visible]
        if closed:
            lines.append(f"{pad}  {folded('목표', closed)}: {', '.join(item['id'] for item in closed)}")

    top = children.get(None, [])
    visible_top = top if show_all or aim_scope else [item for item in top if item["phase"] not in FOLDED_AIMS]
    for aim in visible_top:
        draw(aim, 0)
    closed_top = [item for item in top if item not in visible_top]
    if closed_top:
        lines.append(f"{folded('목표', closed_top)}: {', '.join(item['id'] for item in closed_top)}")
    if not top:
        lines.append("목표가 아직 없습니다. goal-loop aim-create로 첫 목표를 기록하십시오.")

    scoped_actions = actions  # the caller asks next for the same Aim scope
    lines += ["", f"지금 할 일 ({len(scoped_actions)}건)"]
    groups: dict[str, list[dict]] = {}
    for action in scoped_actions:
        groups.setdefault(action["kind"], []).append(action)
    for kind, items in groups.items():
        subjects = ", ".join(dict.fromkeys(item.get("subject", {}).get("id", "?") for item in items))
        lines.append(f"  {ACTION_NAMES.get(kind, kind)} {len(items)}건: {subjects}")
    if not scoped_actions:
        lines.append("  없음")

    in_scope_goals = [goal for goal in goals if goal["aim_id"] in scope]
    aim_counts = Counter(by_id[item]["phase"] for item in scope)
    goal_counts = Counter(goal["phase"] for goal in in_scope_goals)
    lines += ["", "요약: 목표 " + f"{len(scope)}개(" + ", ".join(f"{AIM_STATE.get(k, k)} {v}" for k, v in _ordered(aim_counts, AIM_STATE)) + ")"
              + " · 과제 " + f"{len(in_scope_goals)}개" + ("(" + ", ".join(f"{GOAL_STATE.get(k, k)} {v}" for k, v in _ordered(goal_counts, GOAL_STATE)) + ")" if in_scope_goals else "")]
    return "\n".join(lines) + "\n"
