"""Stable JSON-ready read model shared by future React UI shells."""
from __future__ import annotations

from pathlib import Path

from .persistent import PersistentGoalLoop
from .ao_links import runs as ao_runs
from . import memory_view
from .project import attachment_path, attachment_status, resolve_reference


def goal_graph_projection(loop: PersistentGoalLoop, *, project_root: Path | None = None, artifacts_dir: Path | None = None) -> dict[str, object]:
    """Return read-only UI facts without exposing mutable Core records."""
    def references(kind: str, subject_id: str) -> list[dict[str, object]]:
        # v10: live references only; removed ones remain in the journal and activity history.
        records = []
        for reference in loop.references.values():
            if reference.subject_kind != kind or reference.subject_id != subject_id or reference.removed_by_actor_id is not None:
                continue
            resolved, exists = resolve_reference(reference.location, project_root)
            records.append({"id": reference.id, "location": reference.location, "label": reference.label,
                            "actor": reference.added_by_actor_id, "resolved": resolved, "exists": exists})
        return records

    def attachments(evidence) -> list[dict[str, object]]:
        if artifacts_dir is None:
            return [{**item.__dict__, "path": None, "status": "missing"} for item in evidence.attachments]
        return [{**item.__dict__, "path": str(attachment_path(artifacts_dir, item.name, item.sha256)),
                 "status": attachment_status(artifacts_dir, item.name, item.sha256)} for item in evidence.attachments]

    projection = {
        "schema_version": 16,
        # v15: memory_drafts, timeline lines to suggest for Verdicts and Aim Evaluations the timeline does not cite yet.
        # v14: Memory Tool timeline entries citing record IDs, and excerpts of referenced .memory documents (read-only; empty without .memory).
        # v13: AO Goals linked to Goals, summarized from the AO snapshots attached to their Evidence (read-only).
        # v12: verdicts carry the verifier's optional "reason".
        # v10: the folder relative references resolve against (None for a folderless workspace).
        "project_root": str(project_root) if project_root else None,
        "aims": [
            {"id": aim.id, "description": aim.description, "phase": aim.phase.value,
             "paused": aim.id in loop.paused_aim_ids,
             "currently_achieved": loop.is_currently_achieved(aim.id),
             # v9: who gave up the Aim and why.
             "abandoned_by_actor_id": aim.abandoned_by_actor_id, "abandon_reason": aim.abandon_reason,
             "references": references("aim", aim.id),
             # v11: the Aim tree (one parent, no cycles).
             "parent_aim_id": aim.parent_aim_id}
            for aim in loop.aims.values()
        ],
        "nodes": [
            {"id": aim.id, "aim_id": aim.id, "label": aim.description, "phase": aim.phase.value, "kind": "aim", "dependency_blocked": False}
            for aim in loop.aims.values()
        ] + [
            {
                "id": goal.id,
                "aim_id": goal.identity.primary_aim_id,
                "label": goal.identity.target_and_desired_result,
                "phase": goal.phase.value, "kind": "goal",
                "dependency_blocked": any(
                    not loop._has_current_valid_satisfied_verdict(link.required_goal_id)
                    for link in loop.goal_dependencies.get(goal.id, [])
                ),
            }
            for goal in loop.goals.values()
        ],
        "edges": [
            {"id": f"aim:{goal.identity.primary_aim_id}:{goal.id}", "source": goal.identity.primary_aim_id, "target": goal.id, "kind": "aim"}
            for goal in loop.goals.values()
        ] + [
            {"id": f"parent:{parent}:{child}", "source": parent, "target": child, "kind": "parent"}
            for child, parent in loop.goal_parents.items()
        ] + [
            {"id": f"dependency:{link.required_goal_id}:{goal_id}", "source": link.required_goal_id, "target": goal_id, "kind": "dependency"}
            for goal_id, links in loop.goal_dependencies.items() for link in links
        ] + [
            # A follow link is history (built on / continues), never an admission gate.
            {"id": f"follows:{link.previous_goal_id}:{link.goal_id}", "source": link.previous_goal_id, "target": link.goal_id, "kind": "follows", "actor_id": link.actor_id, "reason": link.reason}
            for link in loop.goal_follows
        ] + [
            {"id": f"aim-parent:{aim.parent_aim_id}:{aim.id}", "source": aim.parent_aim_id, "target": aim.id, "kind": "aim-parent"}
            for aim in loop.aims.values() if aim.parent_aim_id
        ],
        # Detail records are intentionally not graph nodes: they support contextual forms without
        # obscuring the Aim/Goal topology, and remain a read-only projection of Core facts.
        "evidence": [
            {"id": evidence.id, "goal_id": evidence.goal_id, "attempt_id": evidence.attempt_id,
             "observation": evidence.observation, "actor": evidence.submitted_by_actor_id,
             # v10: copies of observed material and whether each copy still matches its recorded digest.
             "attachments": attachments(evidence)}
            for evidence in loop.evidence.values()
        ],
        # Proposals are context records, not graph nodes.  Their Aim relationship lets a UI keep
        # acceptance actions in the correct Aim/Work-Inbox scope without inferring ownership.
        "proposals": [
            {"id": proposal.id, "aim_id": proposal.aim_id, "direction": proposal.direction,
             "status": proposal.status.value, "accepted_goal_id": proposal.accepted_goal_id,
             # v9: proposer and closure facts (None for records made before they were captured).
             "proposed_by_actor_id": proposal.proposed_by_actor_id, "closure_kind": proposal.closure_kind,
             "closed_by_actor_id": proposal.closed_by_actor_id, "closure_reason": proposal.closure_reason,
             "references": references("proposal", proposal.id)}
            for proposal in loop.proposals.values()
        ],
        "attempts": [
            {"id": attempt.id, "goal_id": attempt.goal_id, "outcome": attempt.outcome.value,
             "operation": attempt.operation, "executor": attempt.executor_actor_id,
             "contract_version": attempt.contract_version}
            for attempt in loop.attempts.values()
        ],
        "goals": [
            {"id": goal.id, "aim_id": goal.identity.primary_aim_id, "phase": goal.phase.value,
             "target": goal.identity.target_and_desired_result,
             "responsibility": goal.identity.responsibility_kind,
             "current_contract_version": goal.current_contract_version,
             "verifying_attempt_id": goal.verifying_attempt_id,
             "held": goal.id in loop.goal_holds,
             # v9: Hold and stop facts; a legacy Hold has no reason.
             "hold_actor_id": loop.goal_hold_records.get(goal.id, (None, None))[0] if goal.id in loop.goal_holds else None,
             "hold_reason": loop.goal_hold_records.get(goal.id, (None, None))[1] if goal.id in loop.goal_holds else None,
             "stopped_by_actor_id": goal.stopped_by_actor_id, "stop_reason": goal.stop_reason,
             "references": references("goal", goal.id),
             "contract": loop.contracts[(goal.id, goal.current_contract_version)].__dict__,
             # v6: every immutable Contract version, oldest first, so a UI can show what a revision changed.
             "contract_history": [loop.contracts[(goal.id, version)].__dict__
                                  for version in range(1, goal.current_contract_version + 1)
                                  if (goal.id, version) in loop.contracts]}
            for goal in loop.goals.values()
        ],
        "verdicts": [
            {**verdict.__dict__, "result": verdict.result.value,
             "validity": loop.verdict_validity[verdict.id].value}
            for verdict in loop.verdicts.values()
        ],
        # v8: Evidence corrections and the Impact Reviews they opened, so a UI can show what changed and which Verdict is under review.
        "evidence_corrections": [correction.__dict__ for correction in loop.evidence_corrections.values()],
        "impact_reviews": [{**review.__dict__, "affected_goal_ids": list(review.affected_goal_ids), "affected_aim_evaluation_ids": list(review.affected_aim_evaluation_ids)}
                           for review in loop.impact_reviews.values()],
        # v7: retry and re-verification Decisions, so a UI can show why a run was repeated.
        "decisions": [decision.__dict__ for decision in loop.decisions.values()],
        "aim_evaluations": [
            {**evaluation.__dict__, "child_evaluation_ids": list(evaluation.child_evaluation_ids), "validity": loop.aim_evaluation_validity[evaluation.id].value}
            for evaluation in loop.aim_evaluations.values()
        ],
        "ao_runs": ao_runs(loop, artifacts_dir) if artifacts_dir is not None else [],
        "memory_mentions": memory_view.mentions(project_root),
        "memory_documents": memory_view.documents(project_root, [reference.location for reference in loop.references.values()
                                                                 if reference.removed_by_actor_id is None]),
    }
    projection["memory_drafts"] = memory_view.drafts(projection, project_root)
    return projection
