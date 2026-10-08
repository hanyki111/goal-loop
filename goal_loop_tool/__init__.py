"""Minimal domain core for the Goal Loop Tool MVP."""

from .model import AimEvaluation, AimPhase, AttemptOutcome, GoalPhase, ProposalStatus, VerdictResult
from .service import ContractDraft, GoalLoop, GoalLoopError

__all__ = [
    "AimEvaluation",
    "AimPhase",
    "AttemptOutcome",
    "ContractDraft",
    "GoalLoop",
    "GoalLoopError",
    "GoalPhase",
    "ProposalStatus",
    "VerdictResult",
]


