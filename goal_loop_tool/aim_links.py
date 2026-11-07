"""Links from an Aim in one journal (an umbrella journal) to an Aim in another project's journal.

A link is an ordinary reference on the umbrella Aim whose location is ``goal-loop-aim:<project root>#<aim id>``
and whose label is ``Goal Loop Aim <folder name>/<aim id>``, as AO Goal links are (ao_links.py). No Core
concept is added. The linked journal is only read: it is opened read-only and replayed in memory, so
reading it never appends events, receipts, or checkpoints there.
"""
from __future__ import annotations

from pathlib import Path

from .model import GoalPhase, VerdictValidity
from .persistent import replay_journal
from .project import project_database
from .repository import SqliteEventRepository

SCHEME = "goal-loop-aim:"
LABEL_PREFIX = "Goal Loop Aim "
_FINISHED_GOAL_PHASES = {GoalPhase.VERIFIED, GoalPhase.STOPPED}


class AimLinkError(ValueError):
    pass


def link_location(project_root: Path, aim_id: str) -> str:
    return f"{SCHEME}{project_root}#{aim_id}"


def link_label(project_root: Path, aim_id: str) -> str:
    return f"{LABEL_PREFIX}{project_root.name}/{aim_id}"


def parse_location(location: str) -> tuple[Path, str] | None:
    """Return (project root, linked Aim ID) when a reference location is an Aim link."""
    if not location.startswith(SCHEME) or "#" not in location:
        return None
    root, aim_id = location[len(SCHEME):].rsplit("#", 1)
    if not root or not aim_id:
        return None
    return Path(root), aim_id


def read_linked_journal(project_root: Path):
    """Replay the linked project's journal without writing to it; AimLinkError says why it cannot be read."""
    if not project_root.is_dir():
        raise AimLinkError(f"하위 프로젝트 폴더가 없습니다: {project_root}")
    database = project_database(project_root)
    if not database.is_file():
        raise AimLinkError(f"하위 프로젝트에 Goal Loop 저널이 없습니다: {database}")
    try:
        return replay_journal(SqliteEventRepository.read_only_events(database))
    except Exception as error:  # a damaged or foreign file is reported on the link, never raised past it
        raise AimLinkError(f"하위 저널을 읽을 수 없습니다: {database} ({type(error).__name__}: {error})") from error


def _subtree(loop, aim_id: str) -> set[str]:
    scope = {aim_id}
    while True:
        added = {aim.id for aim in loop.aims.values() if aim.parent_aim_id in scope} - scope
        if not added:
            return scope
        scope |= added


def linked_aim_state(loop, aim_id: str) -> dict:
    """The linked Aim's state as the linked journal sees it now."""
    aim = loop.aims[aim_id]
    lineage = [aim_id, *loop.aim_ancestors(aim_id)]
    valid_evaluations = [
        evaluation.id for evaluation in loop.aim_evaluations.values()
        if evaluation.aim_id == aim_id and loop.aim_evaluation_validity.get(evaluation.id) is VerdictValidity.VALID
    ]
    if any(loop.aims[item].phase.value == "abandoned" for item in lineage):
        state = "abandoned"
    elif loop.is_currently_achieved(aim_id):
        state = "achieved"
    elif aim.phase.value == "achieved":
        state = "evaluation_invalidated"  # closed once, but no valid evaluation remains
    elif any(item in loop.paused_aim_ids for item in lineage):
        state = "paused"
    else:
        state = "open"
    scope = _subtree(loop, aim_id)
    live_goals = [
        goal.id for goal in loop.goals.values()
        if goal.identity.primary_aim_id in scope and goal.phase not in _FINISHED_GOAL_PHASES
    ]
    return {
        "external_aim_description": aim.description,
        "state": state,
        "phase": aim.phase.value,
        "paused_by": next((item for item in lineage if item in loop.paused_aim_ids), None),
        "valid_evaluation_ids": valid_evaluations,
        "live_goal_ids": live_goals,
    }


def link_records(loop, aim_id: str | None = None) -> list[dict]:
    """Every current Aim link of this journal (optionally of one Aim) with the linked Aim's state.

    A link that cannot be followed is returned with state ``broken`` and a reason; the others are still read.
    """
    journals: dict[Path, object] = {}
    records = []
    for reference in loop.references.values():
        if reference.subject_kind != "aim" or reference.removed_by_actor_id is not None:
            continue
        if aim_id is not None and reference.subject_id != aim_id:
            continue
        parsed = parse_location(reference.location)
        if parsed is None:
            continue
        project_root, external_aim_id = parsed
        record = {
            "aim_id": reference.subject_id,
            "reference_id": reference.id,
            "project": str(project_root),
            "database": str(project_database(project_root)),
            "external_aim_id": external_aim_id,
            "label": reference.label,
            "added_by": reference.added_by_actor_id,
        }
        imported = [item for item in loop.external_evaluations.values() if item.reference_id == reference.id]
        try:
            if project_root not in journals:
                try:
                    journals[project_root] = read_linked_journal(project_root)
                except AimLinkError as error:
                    journals[project_root] = error
            linked = journals[project_root]
            if isinstance(linked, AimLinkError):
                raise linked
            if external_aim_id not in linked.aims:
                raise AimLinkError(f"하위 저널에 Aim {external_aim_id}이 없습니다.")
            record.update(linked_aim_state(linked, external_aim_id))
            record["reason"] = None
            record["imported_evaluations"] = [_imported(item, linked) for item in imported]
        except AimLinkError as error:
            record.update({"state": "broken", "reason": str(error),
                           "imported_evaluations": [_imported(item, None) for item in imported]})
        record["stale_imports"] = sum(1 for item in record["imported_evaluations"] if item["still_valid"] is False)
        records.append(record)
    return records


def _imported(external, linked) -> dict:
    """An imported evaluation and whether the linked journal still holds it as valid (None when it cannot be read)."""
    still_valid = None
    if linked is not None:
        evaluation = linked.aim_evaluations.get(external.external_evaluation_id)
        still_valid = (evaluation is not None and evaluation.aim_id == external.external_aim_id
                       and linked.aim_evaluation_validity.get(evaluation.id) is VerdictValidity.VALID)
    return {"id": external.id, "external_evaluation_id": external.external_evaluation_id, "still_valid": still_valid}


def import_evaluation(loop, reference_id: str, actor_id: str, evaluation_id: str | None = None):
    """Copy a linked Aim's valid evaluation into this journal so its Aim can cite it; nothing is recorded on refusal."""
    reference = loop.references.get(reference_id)
    if reference is None or reference.subject_kind != "aim" or reference.removed_by_actor_id is not None:
        raise AimLinkError(f"이 저널의 현재 Aim 연결이 아닙니다: {reference_id}")
    parsed = parse_location(reference.location)
    if parsed is None:
        raise AimLinkError(f"{reference_id}는 Aim 연결(goal-loop-aim:)이 아닙니다: {reference.location}")
    project_root, external_aim_id = parsed
    linked = read_linked_journal(project_root)
    if external_aim_id not in linked.aims:
        raise AimLinkError(f"하위 저널에 Aim {external_aim_id}이 없습니다.")
    if not linked.is_currently_achieved(external_aim_id):
        raise AimLinkError(f"하위 Aim {external_aim_id}은 아직 달성 상태가 아닙니다({linked_aim_state(linked, external_aim_id)['state']}).")
    valid = [item for item in linked.aim_evaluations.values()
             if item.aim_id == external_aim_id and linked.aim_evaluation_validity.get(item.id) is VerdictValidity.VALID]
    if evaluation_id is None:
        chosen = valid[-1]
    else:
        chosen = next((item for item in valid if item.id == evaluation_id), None)
        if chosen is None:
            raise AimLinkError(f"{evaluation_id}는 하위 Aim {external_aim_id}의 유효한 달성 평가가 아닙니다. 유효한 평가: "
                               f"{', '.join(item.id for item in valid)}")
    return loop.import_external_evaluation(reference.subject_id, reference.id, str(project_root), external_aim_id, chosen.id,
                                           chosen.evaluator_actor_id, chosen.conclusion, actor_id)


def broken_links(loop) -> list[dict]:
    return [
        {key: record[key] for key in ("aim_id", "reference_id", "project", "external_aim_id", "reason")}
        for record in link_records(loop) if record["state"] == "broken"
    ]


def add_link(loop, aim_id: str, own_database: Path, target_database: Path, external_aim_id: str, actor_id: str):
    """Validate and record one Aim link; nothing is recorded when the target cannot be followed."""
    if aim_id not in loop.aims:
        raise AimLinkError(f"Aim을 찾을 수 없습니다: {aim_id}")
    if not actor_id.strip():
        raise AimLinkError("연결을 기록한 사람(--actor)이 필요합니다.")
    if target_database.resolve() == own_database.resolve():
        raise AimLinkError("같은 저널의 Aim에는 연결할 수 없습니다. 같은 저널 안에서는 하위 Aim을 쓰세요.")
    project_root = target_database.resolve().parent.parent
    if project_database(project_root).resolve() != target_database.resolve():
        raise AimLinkError(f"프로젝트 폴더의 .goal-loop 저널만 연결할 수 있습니다: {target_database}")
    linked = read_linked_journal(project_root)
    if external_aim_id not in linked.aims:
        raise AimLinkError(f"하위 저널에 Aim {external_aim_id}이 없습니다: {target_database}")
    location = link_location(project_root, external_aim_id)
    for reference in loop.references.values():
        if (reference.subject_kind == "aim" and reference.subject_id == aim_id
                and reference.removed_by_actor_id is None and reference.location == location):
            raise AimLinkError(f"Aim {aim_id}은 이미 {location}에 연결되어 있습니다({reference.id}).")
    return loop.add_reference("aim", aim_id, location, actor_id, link_label(project_root, external_aim_id))


def current_links(loop):
    """(reference, project root, linked Aim ID) for every current Aim link of this journal."""
    for reference in loop.references.values():
        if reference.subject_kind != "aim" or reference.removed_by_actor_id is not None:
            continue
        parsed = parse_location(reference.location)
        if parsed is not None:
            yield reference, parsed[0], parsed[1]


def _skip_reason(loop, aim_id: str) -> str | None:
    """Why this journal's Aim takes no linked work now, or None when it does."""
    lineage = [aim_id, *loop.aim_ancestors(aim_id)]
    abandoned = next((item for item in lineage if loop.aims[item].phase.value == "abandoned"), None)
    if abandoned is not None:
        return f"Aim {abandoned}이 폐기되었습니다."
    if loop.aims[aim_id].phase.value == "achieved":
        return f"Aim {aim_id}은 이미 달성되었습니다."
    paused = next((item for item in lineage if item in loop.paused_aim_ids), None)
    if paused is not None:
        return f"Aim {paused}이 멈춤 상태입니다."
    return None


def umbrella_next(loop, child_next, scope: set[str] | None = None) -> dict | None:
    """The linked journals' next actions for this (umbrella) journal; None when it has no Aim links.

    ``child_next(linked_loop, database, aim_id)`` returns the linked journal's actions for one Aim's subtree.
    Returns ``linked`` (one entry per followed link, actions carrying their source), ``skipped`` (links whose
    Aim here is paused, abandoned, or achieved), and ``imports`` (aim-link-import suggestions for this journal).
    Nothing is written to any journal.
    """
    links = list(current_links(loop))
    if not links:
        return None
    journals: dict[Path, object] = {}
    linked, skipped, imports = [], [], []
    for reference, project_root, external_aim_id in links:
        if scope is not None and reference.subject_id not in scope:
            continue
        source = {"reference_id": reference.id, "aim_id": reference.subject_id, "project": str(project_root),
                  "database": str(project_database(project_root)), "external_aim_id": external_aim_id}
        reason = _skip_reason(loop, reference.subject_id)
        if reason is not None:
            skipped.append({**source, "reason": reason})
            continue
        if project_root not in journals:
            try:
                journals[project_root] = read_linked_journal(project_root)
            except AimLinkError as error:
                journals[project_root] = error
        child = journals[project_root]
        if isinstance(child, AimLinkError) or external_aim_id not in child.aims:
            linked.append({**source, "state": "broken", "actions": [],
                           "reason": str(child) if isinstance(child, AimLinkError) else f"하위 저널에 Aim {external_aim_id}이 없습니다."})
            continue
        actions = [{**action, "source": source} for action in child_next(child, project_database(project_root), external_aim_id)]
        state = linked_aim_state(child, external_aim_id)
        linked.append({**source, "state": state["state"], "reason": None, "actions": actions})
        imported = {item.external_evaluation_id for item in loop.external_evaluations.values() if item.reference_id == reference.id}
        waiting = [item for item in state["valid_evaluation_ids"] if item not in imported]
        if state["state"] == "achieved" and waiting:
            imports.append({"reference": reference, "external_aim_id": external_aim_id, "evaluation_ids": waiting, "project": project_root})
    return {"linked": linked, "skipped": skipped, "imports": imports}
