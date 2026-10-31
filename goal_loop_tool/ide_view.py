"""Read-only graph files (Markdown + SVG) that an IDE opens with its built-in previews, no extension needed."""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Callable

STATE_NAMES = {"open": "진행 중", "achieved": "달성됨", "accepted": "확정됨", "committed": "실행 준비됨", "running": "실행 중",
               "active": "실행 진행 중", "verifying": "검증 중", "verified": "검증됨", "stopped": "중단됨", "abandoned": "그만둠"}
# (card fill, card border, badge fill, badge text) per phase; the app's state-badge colors.
PHASE_COLORS = {"verified": ("#f6fef9", "#bbf7d0", "#dcfce7", "#166534"), "achieved": ("#f6fef9", "#86efac", "#dcfce7", "#166534"),
                "active": ("#f5f9ff", "#93c5fd", "#dbeafe", "#1d4ed8"), "verifying": ("#fffbeb", "#fcd34d", "#fef3c7", "#92400e"),
                "accepted": ("#fffbeb", "#fde68a", "#fef3c7", "#92400e"), "committed": ("#fffbeb", "#fde68a", "#fef3c7", "#92400e"),
                "open": ("#f7f7ff", "#a5b4fc", "#e0e7ff", "#3730a3"), "stopped": ("#f8fafc", "#cbd5e1", "#e2e8f0", "#475569"),
                "abandoned": ("#f8fafc", "#cbd5e1", "#e2e8f0", "#475569")}
SETTLED = {"verified", "stopped", "abandoned"}
ROW, CARD_H, WIDTH, ARC_SPACE = 52, 42, 1180, 150


def _number(record_id: str) -> int:
    match = re.search(r"(\d+)$", record_id)
    return int(match.group(1)) if match else 0


def _text_width(text: str, size: float = 13) -> float:
    return sum(size * (1.0 if ord(char) > 0x1100 else 0.58) for char in text)


def _fit(text: str, width: float, size: float = 13) -> str:
    text = " ".join(text.split())
    if _text_width(text, size) <= width:
        return text
    while text and _text_width(text + "…", size) > width:
        text = text[:-1]
    return text + "…"


def _state(node: dict, goal: dict | None) -> str:
    label = STATE_NAMES.get(node["phase"], node["phase"])
    if node.get("dependency_blocked"):
        label += " · 의존성 대기"
    if goal and goal.get("held"):
        label += " · 실행 보류"
    return label


def graph_rows(projection: dict, expand_all: bool = False) -> list[dict]:
    """Aims as an outline (sub-Aims, then Goals nested by Goal parent); an achieved Aim folds into one summary row."""
    nodes = {node["id"]: node for node in projection["nodes"]}
    goals = {goal["id"]: goal for goal in projection["goals"]}
    aims = sorted((node for node in projection["nodes"] if node["kind"] == "aim"), key=lambda node: _number(node["id"]))
    aim_parent = {edge["target"]: edge["source"] for edge in projection["edges"] if edge["kind"] == "aim-parent"}
    goal_parent = {edge["target"]: edge["source"] for edge in projection["edges"] if edge["kind"] == "parent"}
    rows: list[dict] = []

    def subtree_counts(aim_id: str) -> tuple[int, int]:
        children = [aim["id"] for aim in aims if aim_parent.get(aim["id"]) == aim_id]
        goal_count = sum(1 for goal in goals.values() if goal["aim_id"] == aim_id)
        aim_count = len(children)
        for child in children:
            more_aims, more_goals = subtree_counts(child)
            aim_count += more_aims; goal_count += more_goals
        return aim_count, goal_count

    def add_goal(goal_id: str, depth: int, parent_row: int) -> None:
        rows.append({"node": nodes[goal_id], "goal": goals.get(goal_id), "depth": depth, "parent": parent_row})
        index = len(rows) - 1
        for child in sorted((item for item, parent in goal_parent.items() if parent == goal_id and item in nodes), key=_number):
            add_goal(child, depth + 1, index)

    def add_aim(aim: dict, depth: int, parent_row: int | None) -> None:
        rows.append({"node": aim, "goal": None, "depth": depth, "parent": parent_row})
        index = len(rows) - 1
        if aim["phase"] == "achieved" and not expand_all:
            aim_count, goal_count = subtree_counts(aim["id"])
            if aim_count or goal_count:
                parts = ([f"하위 목표 {aim_count}개"] if aim_count else []) + ([f"과제 {goal_count}개"] if goal_count else [])
                rows.append({"fold": " · ".join(parts) + " 접힘 (--all 로 펼치기)", "depth": depth + 1, "parent": index})
            return
        for child in aims:
            if aim_parent.get(child["id"]) == aim["id"]:
                add_aim(child, depth + 1, index)
        for goal_id in sorted((item for item, goal in goals.items() if goal["aim_id"] == aim["id"]), key=_number):
            if goal_parent.get(goal_id) in goals and goals[goal_parent[goal_id]]["aim_id"] == aim["id"]:
                continue
            add_goal(goal_id, depth + 1, index)

    roots = [aim for aim in aims if aim_parent.get(aim["id"]) not in nodes]
    for aim in sorted(roots, key=lambda aim: aim["phase"] == "achieved"):
        add_aim(aim, 0, None)
    return rows


def render_svg(projection: dict, expand_all: bool = False) -> str:
    rows = graph_rows(projection, expand_all)
    top = 20
    position: dict[str, int] = {}
    parts: list[str] = []
    card_right = WIDTH - ARC_SPACE
    for index, row in enumerate(rows):
        x = 20 + row["depth"] * 26
        y = top + index * ROW
        if row["parent"] is not None:
            px = 20 + rows[row["parent"]]["depth"] * 26 + 12
            py = top + row["parent"] * ROW + CARD_H
            parts.append(f"<path d='M{px},{py} V{y + CARD_H / 2} H{x}' fill='none' stroke='#94a3b8' stroke-width='1.5'/>")
        if "fold" in row:
            parts.append(f"<text x='{x + 6}' y='{y + 26}' font-size='12' fill='#64748b' font-style='italic'>{escape(row['fold'])}</text>")
            continue
        node, goal = row["node"], row["goal"]
        position[node["id"]] = index
        fill, border, badge_fill, badge_text = PHASE_COLORS.get(node["phase"], PHASE_COLORS["open"])
        if node.get("dependency_blocked"):
            fill, border = "#fff5f5", "#fca5a5"
        is_aim = node["kind"] == "aim"
        width = card_right - x
        state = _state(node, goal)
        state_width = _text_width(state, 11) + 16
        kind = f"{'목표' if is_aim else '과제'} {node['id']}"
        kind_width = _text_width(kind, 11) + 10
        label_width = width - kind_width - state_width - 34
        dim = " opacity='0.65'" if node["phase"] in ("stopped", "abandoned") else ""
        tooltip = f"{node['id']} · {state}\n{node['label']}"
        if goal:
            tooltip += f"\n완료 기준: {goal['contract']['success_criterion']}" if goal.get("contract") else ""
        parts.append(
            f"<g><title>{escape(tooltip)}</title>"
            f"<rect x='{x}' y='{y}' width='{width}' height='{CARD_H}' rx='8' fill='{fill}' stroke='{border}' stroke-width='{2 if is_aim else 1}'/>"
            f"<text x='{x + 10}' y='{y + 26}' font-size='11' font-weight='700' fill='{'#3730a3' if is_aim else '#526077'}'>{escape(kind)}</text>"
            f"<text x='{x + 14 + kind_width}' y='{y + 26}' font-size='13' font-weight='{700 if is_aim else 400}' fill='#182235'{dim}>"
            f"{escape(_fit(node['label'], label_width))}</text>"
            f"<rect x='{card_right - state_width - 8}' y='{y + 11}' width='{state_width}' height='20' rx='10' fill='{badge_fill}'/>"
            f"<text x='{card_right - state_width / 2 - 8}' y='{y + 25}' font-size='11' text-anchor='middle' fill='{badge_text}'>{escape(state)}</text></g>"
        )
    styles = {"follows": ("#16a34a", "4 4", "이어받음"), "dependency": ("#c2410c", "7 4", "선행 과제")}
    for edge in projection["edges"]:
        if edge["kind"] not in styles or edge["source"] not in position or edge["target"] not in position:
            continue
        color, dash, name = styles[edge["kind"]]
        y1 = top + position[edge["source"]] * ROW + CARD_H / 2
        y2 = top + position[edge["target"]] * ROW + CARD_H / 2
        bend = card_right + min(ARC_SPACE - 20, 24 + abs(y2 - y1) / 8)
        title = f"{name}: {edge['source']} → {edge['target']}" + (f"\n{edge['reason']}" if edge.get("reason") else "")
        parts.append(f"<path d='M{card_right},{y1} C{bend},{y1} {bend},{y2} {card_right + 6},{y2}' fill='none' stroke='{color}' "
                     f"stroke-width='2' stroke-dasharray='{dash}' marker-end='url(#arrow-{edge['kind']})'><title>{escape(title)}</title></path>")
    height = top + len(rows) * ROW + 20
    markers = "".join(f"<marker id='arrow-{kind}' markerWidth='8' markerHeight='8' refX='7' refY='4' orient='auto'><path d='M0,0 L8,4 L0,8Z' fill='{color}'/></marker>"
                      for kind, (color, _, _) in styles.items())
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{WIDTH}' height='{height}' viewBox='0 0 {WIDTH} {height}' "
            f"font-family='Segoe UI, Malgun Gothic, Apple SD Gothic Neo, sans-serif'><defs>{markers}</defs>"
            f"<rect width='100%' height='100%' fill='#ffffff'/>{''.join(parts)}</svg>\n")


def _cell(text: str, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return escape(text, quote=False).replace("|", "\\|")


def render_markdown(projection: dict, svg_name: str, database: str, expand_all: bool = False) -> str:
    nodes = {node["id"]: node for node in projection["nodes"]}
    goals = projection["goals"]
    aims = sorted((node for node in projection["nodes"] if node["kind"] == "aim"), key=lambda node: _number(node["id"]))
    lines = [
        "# Goal Loop 그래프",
        "",
        f"생성 시각 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 저널 `{database}` · 읽기 전용입니다. "
        "기록을 바꾸는 작업은 CLI나 데스크톱 앱에서 합니다.",
        "",
        "> 미리보기: VS Code에서 이 파일을 연 상태로 `Ctrl+Shift+V`(옆에 열기는 `Ctrl+K V`)를 누릅니다. "
        f"`goal-loop ide-view --watch`를 켜 두면 저널이 바뀔 때 다시 생성되어 미리보기가 갱신됩니다. 그림만 보려면 `{svg_name}` 파일을 엽니다.",
        "",
        "범례: 회색 선은 상위→하위(목표·과제), 초록 점선은 이어받음, 주황 점선은 선행 과제입니다. 카드에 마우스를 올리면 전체 문구가 보입니다(SVG를 직접 열었을 때).",
        "",
        f"![Goal Loop 그래프]({svg_name})",
        "",
    ]
    working = [goal for goal in goals if goal["phase"] not in SETTLED]
    lines += ["## 진행 중인 과제", ""]
    if working:
        lines += ["| 과제 | 목표 | 상태 | 완료 기준 |", "|---|---|---|---|"]
        for goal in sorted(working, key=lambda goal: _number(goal["id"])):
            criterion = goal["contract"]["success_criterion"] if goal.get("contract") else ""
            lines.append(f"| {goal['id']} {_cell(goal['target'], 80)} | {goal['aim_id']} | {_cell(_state(nodes[goal['id']], goal))} | {_cell(criterion)} |")
    else:
        lines.append("진행 중인 과제가 없습니다.")
    lines += ["", "## 목표별 과제", ""]
    for aim in aims:
        aim_goals = sorted((goal for goal in goals if goal["aim_id"] == aim["id"]), key=lambda goal: _number(goal["id"]))
        open_attr = "" if aim["phase"] == "achieved" and not expand_all else " open"
        lines += [f"<details{open_attr}><summary><b>{aim['id']}</b> {escape(aim['label'])} · {STATE_NAMES.get(aim['phase'], aim['phase'])} · 과제 {len(aim_goals)}개</summary>", ""]
        if aim_goals:
            lines += ["| 과제 | 상태 | 내용 |", "|---|---|---|"]
            lines += [f"| {goal['id']} | {_cell(_state(nodes[goal['id']], goal))} | {_cell(goal['target'], 120)} |" for goal in aim_goals]
        else:
            lines.append("과제가 없습니다.")
        lines += ["", "</details>", ""]
    return "\n".join(lines)


def _write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def write_ide_view(projection: dict, destination: Path, database: str, expand_all: bool = False) -> dict[str, str]:
    destination.mkdir(parents=True, exist_ok=True)
    svg_path, markdown_path = destination / "graph.svg", destination / "graph.md"
    _write(svg_path, render_svg(projection, expand_all))
    _write(markdown_path, render_markdown(projection, svg_path.name, database, expand_all))
    return {"markdown": str(markdown_path), "svg": str(svg_path)}


def watch_ide_view(build: Callable[[], dict], destination: Path, database: Path, expand_all: bool = False,
                   interval: float = 2.0, emit: Callable[[dict], None] = print) -> None:
    """Regenerate whenever the journal (or its WAL) changes; stops on Ctrl+C."""
    def stamp() -> tuple:
        return tuple(os.stat(path).st_mtime_ns if path.exists() else 0 for path in (database, Path(f"{database}-wal")))
    last = None
    try:
        while True:
            if stamp() != last:
                emit({"ide_view": write_ide_view(build(), destination, str(database), expand_all), "at": datetime.now().isoformat(timespec="seconds")})
                sys.stdout.flush()
                # Reading the journal can touch its WAL; compare against the state after this read.
                last = stamp()
            time.sleep(interval)
    except KeyboardInterrupt:
        return
