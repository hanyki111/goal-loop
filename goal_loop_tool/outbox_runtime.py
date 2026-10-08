"""Conservative, caller-driven dispatch for durable Runtime outbox requests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .model import ExecutionAttempt
from .persistent import PersistentGoalLoop
from .repository import RuntimeOutboxRequest
from .subprocess_runtime import RuntimeExecution, deliver_execution


@dataclass(frozen=True)
class RuntimeOutboxDispatch:
    """The facts produced by one explicit worker dispatch."""

    request: RuntimeOutboxRequest
    execution: RuntimeExecution
    attempt: ExecutionAttempt
    acknowledged_request: RuntimeOutboxRequest


class RuntimeOutboxWorker:
    """Dispatch one request at most once per claim; never retries automatically.

    The supplied executor owns payload interpretation and any external process launch.
    If it, result delivery, or acknowledgement raises, the worker leaves the request
    claimed.  An operator must inspect it and explicitly acknowledge or requeue it.
    """

    def __init__(self, loop: PersistentGoalLoop, worker_id: str) -> None:
        self.loop = loop
        self.worker_id = worker_id

    def dispatch(
        self,
        request_id: str,
        executor: Callable[[RuntimeOutboxRequest], RuntimeExecution],
    ) -> RuntimeOutboxDispatch | None:
        request = self.loop.repository.claim_runtime_request(request_id, self.worker_id)
        if request is None:
            return None
        execution = executor(request)
        attempt = deliver_execution(self.loop, request.attempt_id, execution)
        acknowledged = self.loop.repository.acknowledge_runtime_request(
            request_id, self.worker_id
        )
        return RuntimeOutboxDispatch(request, execution, attempt, acknowledged)

    def deliver(
        self, request_id: str, execution: RuntimeExecution
    ) -> RuntimeOutboxDispatch:
        """Record an externally observed result for this worker's existing claim.

        This is recovery ingress only; it never launches an executor or reclaims work.
        """
        request = self.loop.repository.runtime_request(request_id)
        if request is None or request.status != "claimed" or request.claimed_by != self.worker_id:
            raise ValueError("Runtime request is not claimed by this worker.")
        attempt = deliver_execution(self.loop, request.attempt_id, execution)
        acknowledged = self.loop.repository.acknowledge_runtime_request(
            request_id, self.worker_id
        )
        return RuntimeOutboxDispatch(request, execution, attempt, acknowledged)
