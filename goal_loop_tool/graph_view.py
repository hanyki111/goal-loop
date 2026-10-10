"""Dependency-free static HTML view of the read-only Goal graph."""
from __future__ import annotations

from html import escape
from pathlib import Path

from .persistent import PersistentGoalLoop


def render_goal_graph(loop: PersistentGoalLoop) -> str:
    goals = list(loop.goals.values())
    nodes = []
    edges = []
    for index, goal in enumerate(goals):
        x, y = 40 + (index % 3) * 270, 40 + (index // 3) * 130
        required = loop.goal_dependencies.get(goal.id, [])
        blocked = bool(required) and not all(loop._has_current_valid_satisfied_verdict(item.required_goal_id) for item in required)
        fill = '#fee' if blocked else '#eef'
        label = 'dependency blocked' if blocked else goal.phase.value
        nodes.append(f"<g id='{escape(goal.id)}'><title>{escape(goal.identity.primary_aim_id)} · {escape(label)}</title><rect x='{x}' y='{y}' width='220' height='70' rx='8' fill='{fill}'/><text x='{x+12}' y='{y+28}'>{escape(goal.id)}</text><text x='{x+12}' y='{y+50}'>{escape(label)}</text></g>")
    positions = {goal.id: (40 + (i % 3) * 270 + 110, 40 + (i // 3) * 130 + 35) for i, goal in enumerate(goals)}
    for child, parent in loop.goal_parents.items():
        if child in positions and parent in positions:
            x1, y1 = positions[parent]; x2, y2 = positions[child]
            edges.append(f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='#357' stroke-width='3' marker-end='url(#arrow)'/>")
    for target, links in loop.goal_dependencies.items():
        for link in links:
            if target in positions and link.required_goal_id in positions:
                x1, y1 = positions[link.required_goal_id]; x2, y2 = positions[target]
                edges.append(f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='#b54' stroke-width='2' stroke-dasharray='6 4' marker-end='url(#arrow)'/>")
    for link in loop.goal_follows:
        if link.goal_id in positions and link.previous_goal_id in positions:
            x1, y1 = positions[link.previous_goal_id]; x2, y2 = positions[link.goal_id]
            edges.append(f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='#3a7' stroke-width='2' stroke-dasharray='2 4' marker-end='url(#arrow)'><title>{escape(link.reason)}</title></line>")
    rows = []
    for goal in loop.goals.values():
        parent = loop.goal_parents.get(goal.id, "—")
        dependencies = ", ".join(
            item.required_goal_id for item in loop.goal_dependencies.get(goal.id, [])
        ) or "—"
        follows = ", ".join(
            item.previous_goal_id for item in loop.goal_follows if item.goal_id == goal.id
        ) or "—"
        rows.append(
            "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in (
                goal.id, goal.identity.primary_aim_id, goal.phase.value, parent, dependencies, follows,
            )) + "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan='6'>Goal이 없습니다.</td></tr>"
    return f"""<!doctype html><html lang='ko'><meta charset='utf-8'>
<title>Goal Loop Graph</title><style>body{{font-family:system-ui;margin:2rem}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #bbb;padding:.5rem;text-align:left}}th{{background:#eef}}</style>
<h1>Goal Loop Graph</h1><p>정본 SQLite projection의 읽기 전용 export입니다. 파란선은 parent, 주황 점선은 dependency, 초록 점선은 이어받음입니다.</p>
<svg width='900' height='{max(180, ((len(goals)+2)//3)*130+40)}'><defs><marker id='arrow' markerWidth='8' markerHeight='8' refX='7' refY='4' orient='auto'><path d='M0,0 L8,4 L0,8Z'/></marker></defs>{''.join(edges)}{''.join(nodes)}</svg>
<table><thead><tr><th>Goal</th><th>Aim</th><th>Phase</th><th>Parent</th><th>Requires Goal</th><th>Follows Goal</th></tr></thead><tbody>{body}</tbody></table></html>"""


def export_goal_graph(loop: PersistentGoalLoop, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_goal_graph(loop), encoding="utf-8")
