"""Receive changes on the AI_ORCHESTRATION (AO) side as Evidence corrections (aim-6, Decision 7).

For each Goal Loop Goal ↔ AO Goal link, AO is read (never written) for two kinds of change that undermine a
judgment already made: the AO Goal was reverted or abandoned, or an assumption naming that AO Goal in
``affectedGoals`` was invalidated. Each change is recorded once as a correction of the AO Evidence that a valid
Verdict cites, which puts those Verdicts under review; a person resolves the review.
"""
from __future__ import annotations

import json
from pathlib import Path

from .ao_links import links as ao_goal_links

UNDONE_GOAL = {"reverted", "abandoned"}


class AoSyncError(ValueError):
    """The AO workspace cannot be read."""


def _read(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise AoSyncError(f"AO 파일을 읽을 수 없습니다: {path} ({error})") from error


def change_key(kind: str, project: str, subject: str) -> str:
    return f"[ao-change:{kind}:{project}/{subject}]"


def find_changes(ao_workspace: Path, project: str, ao_goal_id: str) -> list[dict]:
    """Changes on the AO side that concern one AO Goal."""
    project_dir = ao_workspace / "projects" / project
    if not (project_dir / "goals" / "goals.json").is_file():
        raise AoSyncError(f"AO 프로젝트 폴더가 아닙니다(goals/goals.json 없음): {project_dir}")
    changes = []
    index = _read(project_dir / "goals" / "goals.json") or {}
    entry = next((item for item in index.get("goals", []) if item.get("id") == ao_goal_id), None)
    if entry is not None and entry.get("status") in UNDONE_GOAL:
        reason = entry.get("abandonReason") or ""
        changes.append({"key": change_key(f"goal-{entry['status']}", project, ao_goal_id), "kind": f"goal-{entry['status']}",
                        "text": f"AO Goal {project}/{ao_goal_id}의 상태가 {entry['status']}이다." + (f" 사유: {reason}" if reason else "")})
    knowledge = _read(project_dir / "knowledge.json") or {}
    for item in knowledge.get("items", []):
        if item.get("kind") == "assumption" and item.get("status") == "invalidated" and ao_goal_id in (item.get("affectedGoals") or []):
            superseded = f" 대체 항목: {item['supersededBy']}." if item.get("supersededBy") else ""
            changes.append({"key": change_key("assumption-invalidated", project, item["id"]), "kind": "assumption-invalidated",
                            "text": f"AO 가정 {item['id']}(「{item.get('text', '')}」)이 invalidated되었다. 이 가정은 AO Goal {ao_goal_id}에 걸려 있었다.{superseded}"})
    return changes


def sync(loop, artifacts_dir: Path, ao_workspace: Path, actor: str, *, dry_run: bool = False) -> list[dict]:
    """Find AO changes for every link and record each once as a correction; return one report row per change."""
    corrected_keys = {key for correction in loop.evidence_corrections.values()
                      for key in _keys_in(correction.observation)}
    rows = []
    for link in ao_goal_links(loop, artifacts_dir):
        project, ao_goal_id, goal_id = link["ao_project_id"], link["ao_goal_id"], link["goal_id"]
        for change in find_changes(ao_workspace, project, ao_goal_id):
            row = {"goal_id": goal_id, "ao_goal": f"{project}/{ao_goal_id}", "change": change["key"], "text": change["text"],
                   "corrections": [], "status": ""}
            # A change already recorded for this Goal is not recorded again (another Goal linked to the same AO Goal still gets one).
            key = f"{change['key']}@{goal_id}"
            if key in corrected_keys:
                row["status"] = "already-recorded"
                rows.append(row)
                continue
            cited = [evidence_id for evidence_id in link["evidence_ids"] if _cited_by_valid_verdict(loop, evidence_id)]
            if not cited:
                row["status"] = "no-cited-evidence"
                rows.append(row)
                continue
            if dry_run:
                row["status"] = "would-correct"
                row["corrections"] = [{"evidence_id": evidence_id} for evidence_id in cited]
                rows.append(row)
                continue
            for evidence_id in cited:
                observation = (f"{change['text']} 이 근거가 전제한 AO 쪽 결과가 바뀌었으므로, 이 근거를 인용한 판정을 다시 확인해야 한다. "
                               f"{change['key']}@{goal_id}")
                correction = loop.correct_evidence(evidence_id, actor, observation)
                row["corrections"].append({"evidence_id": evidence_id, "correction_id": correction.id})
            corrected_keys.add(key)
            row["status"] = "corrected"
            rows.append(row)
    return rows


def _keys_in(text: str) -> list[str]:
    found, start = [], 0
    while (index := text.find("[ao-change:", start)) != -1:
        end = text.find("]", index)
        if end == -1:
            break
        goal_part = text[end + 1:].split()[0] if text[end + 1:].startswith("@") else ""
        found.append(text[index:end + 1] + goal_part)
        start = end + 1
    return found


def _cited_by_valid_verdict(loop, evidence_id: str) -> bool:
    return any(evidence_id in verdict.evidence_ids and getattr(loop.verdict_validity.get(verdict.id), "value", None) == "valid"
               for verdict in loop.verdicts.values())
