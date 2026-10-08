"""Runtime-result ingress with a delivery identity distinct from command_id."""

from .model import AttemptOutcome, ExecutionAttempt
from .persistent import PersistentGoalLoop


def deliver_runtime_result(loop: PersistentGoalLoop, attempt_id: str, outcome: AttemptOutcome, runtime_delivery_id: str) -> ExecutionAttempt:
    """Durably accept one Runtime delivery, replay it, or conflict on changed payload."""
    return loop.complete_attempt(
        attempt_id,
        outcome,
        command_id=f"runtime_delivery:{runtime_delivery_id}",
    )
