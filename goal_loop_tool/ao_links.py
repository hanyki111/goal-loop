"""Links between Goal Loop Goals and AI_ORCHESTRATION (AO) Goals.

A link is an ordinary reference on the Goal Loop Goal whose location is the AO web page of the AO Goal
(``<web>/projects/<project>/goals/<ao-goal-id>``) and whose label is ``AO Goal <project>/<ao-goal-id>``.
No Core concept is added: references are already recorded, shown in the app, and survive replay.
Sessions and commits are read from the AO Evidence snapshots attached to the Goal's Attempts.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .project import attachment_path

# Without a web address an AO Goal is named by an "ao:" location. AO's resident web server (localhost:5173) was
# retired (aim-10); links stored with that address are still read.
LOCAL_PREFIX = "ao:"
LABEL_PREFIX = "AO Goal "
_PATH = re.compile(r"/projects/(?P<project>[^/]+)/goals/(?P<goal>[^/?#]+)/?$")
_SNAPSHOT = re.compile(r"^ao-goal-(?P<rest>.+)\.json$")


def ao_goal_url(web_url: str | None, project_id: str, ao_goal_id: str) -> str:
    return f"{(web_url or LOCAL_PREFIX).rstrip('/')}/projects/{project_id}/goals/{ao_goal_id}"


def parse_link(reference) -> tuple[str, str] | None:
    """Return (project, AO Goal ID) when a reference is an AO Goal link."""
    if not (reference.label or "").startswith(LABEL_PREFIX):
        return None
    match = _PATH.search(reference.location)
    return (match["project"], match["goal"]) if match else None


def _live_references(loop, goal_id: str):
    return [item for item in loop.references.values()
            if item.subject_kind == "goal" and item.subject_id == goal_id and item.removed_by_actor_id is None]


def link_goal(loop, goal_id: str, project_id: str, ao_goal_id: str, actor: str, web_url: str | None = None):
    """Add the AO Goal link to a Goal Loop Goal unless it already has one; return it or None.

    Without a web address any live link to the same AO Goal counts, whatever address it was stored with. With a
    web address the address decides: the same AO Goal reached through another AO web address gets its own link.
    """
    location = ao_goal_url(web_url, project_id, ao_goal_id)
    for item in _live_references(loop, goal_id):
        if web_url is None and parse_link(item) == (project_id, ao_goal_id):
            return None
        if parse_link(item) is not None and item.location == location:
            return None
    return loop.add_reference("goal", goal_id, location, actor, f"{LABEL_PREFIX}{project_id}/{ao_goal_id}")


# Only Evidence written by ao-import or AO dispatch says "this Goal's Attempt is about that AO Goal". A snapshot
# attached by hand (for example as a sample of the import format) must not create a link.
AO_EVIDENCE_PREFIXES = ("AO Goal ", "runtime-dispatch:")


def _snapshots(loop, goal_id: str, artifacts_dir: Path) -> list[dict]:
    """AO snapshots attached to this Goal's ao-import or AO dispatch Evidence."""
    found = []
    for evidence in loop.evidence.values():
        if evidence.goal_id != goal_id or not evidence.observation.startswith(AO_EVIDENCE_PREFIXES):
            continue
        for attachment in evidence.attachments:
            if not _SNAPSHOT.match(attachment.name):
                continue
            path = attachment_path(artifacts_dir, attachment.name, attachment.sha256)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("source") == "ai_orchestration":
                found.append({**data, "_evidence_id": evidence.id})
    return found


def links(loop, artifacts_dir: Path, *, goal_id: str | None = None, ao_goal: tuple[str, str] | None = None) -> list[dict]:
    """Every Goal Loop ↔ AO Goal link, with the AO Sessions and commits seen in that Goal's snapshots."""
    rows = []
    for reference in loop.references.values():
        if reference.subject_kind != "goal" or reference.removed_by_actor_id is not None:
            continue
        parsed = parse_link(reference)
        if parsed is None or (goal_id and reference.subject_id != goal_id) or (ao_goal and parsed != ao_goal):
            continue
        project, ao_goal_id = parsed
        sessions, commits, evidence_ids = [], [], []
        for snapshot in _snapshots(loop, reference.subject_id, artifacts_dir):
            if snapshot.get("project_id") != project or (snapshot.get("goal") or {}).get("id") != ao_goal_id:
                continue
            evidence_ids.append(snapshot["_evidence_id"])
            sessions += [item["id"] for item in snapshot.get("sessions", []) if item["id"] not in sessions]
            commits += [item["sha"] for item in snapshot.get("commits", []) if item["sha"] not in commits]
        rows.append({"goal_id": reference.subject_id, "ao_project_id": project, "ao_goal_id": ao_goal_id,
                     "reference_id": reference.id, "location": reference.location,
                     "ao_session_ids": sessions, "ao_commits": commits, "evidence_ids": evidence_ids})
    return sorted(rows, key=lambda row: (row["goal_id"], row["ao_goal_id"]))


def backfill(loop, repository, artifacts_dir: Path, actor: str, web_url: str | None = None) -> list[dict]:
    """Add missing links from AO requests (ao_session.v1, ao_cli.v1) and AO snapshots already in the journal."""
    wanted: list[tuple[str, str, str]] = []
    for request in repository.runtime_requests():
        if request.request_kind in ("ao_session.v1", "ao_cli.v1"):
            attempt = loop.attempts.get(request.attempt_id)
            if attempt is not None:
                wanted.append((attempt.goal_id, str(request.payload["ao_project_id"]), str(request.payload["ao_goal_id"])))
    for goal_id in loop.goals:
        for snapshot in _snapshots(loop, goal_id, artifacts_dir):
            wanted.append((goal_id, str(snapshot["project_id"]), str((snapshot.get("goal") or {})["id"])))
    added = []
    for goal_id, project, ao_goal_id in dict.fromkeys(wanted):
        reference = link_goal(loop, goal_id, project, ao_goal_id, actor, web_url)
        if reference is not None:
            added.append({"goal_id": goal_id, "ao_project_id": project, "ao_goal_id": ao_goal_id, "reference_id": reference.id})
    return added


def runs(loop, artifacts_dir: Path) -> list[dict]:
    """Read-only summary of each AO Goal linked to a Goal Loop Goal, for the app.

    Built only from the journal and the AO snapshots attached to that Goal's Evidence: the AO Goal's
    latest title, status and review reason, its Sessions with verifier results, and its commits.
    """
    rows = []
    for link in links(loop, artifacts_dir):
        snapshots = [snapshot for snapshot in _snapshots(loop, link["goal_id"], artifacts_dir)
                     if snapshot.get("project_id") == link["ao_project_id"]
                     and (snapshot.get("goal") or {}).get("id") == link["ao_goal_id"]]
        goal = (snapshots[-1].get("goal") or {}) if snapshots else {}
        sessions: dict[str, dict] = {}
        commits: dict[str, dict] = {}
        for snapshot in snapshots:  # later snapshots describe the same Session more completely
            for session in snapshot.get("sessions", []):
                verifiers = session.get("verifiers") or []
                failed = [item for item in verifiers if item.get("passed") is False]
                sessions[session["id"]] = {
                    "id": session["id"], "status": session.get("status"),
                    "created_at": session.get("created_at"), "updated_at": session.get("updated_at"),
                    "verifiers_passed": sum(1 for item in verifiers if item.get("passed") is True),
                    "verifiers_failed": len(failed),
                    # Judgments an --external-verdict run recorded instead of blocking completion (goal-96).
                    "advisories": [{"clause": item.get("clause"), "reason": item.get("reason"), "at": item.get("at")}
                                   for item in session.get("advisories") or []],
                    "recent_failures": [{"type": item.get("type"), "reason": item.get("reason"), "at": item.get("at")} for item in failed[-3:]],
                }
            for commit in snapshot.get("commits", []):
                commits[commit["sha"]] = {"sha": commit["sha"], "subject": commit.get("subject"), "committed_at": commit.get("committed_at")}
        attempt_ids = list(dict.fromkeys(loop.evidence[evidence_id].attempt_id for evidence_id in link["evidence_ids"] if evidence_id in loop.evidence))
        rows.append({"goal_id": link["goal_id"], "ao_project_id": link["ao_project_id"], "ao_goal_id": link["ao_goal_id"],
                     "title": goal.get("title"), "status": goal.get("status"), "review_reason": goal.get("reviewReason"),
                     "sessions": sorted(sessions.values(), key=lambda item: item.get("created_at") or ""),
                     "commits": sorted(commits.values(), key=lambda item: item.get("committed_at") or ""),
                     "evidence_ids": link["evidence_ids"], "attempt_ids": attempt_ids})
    return rows
