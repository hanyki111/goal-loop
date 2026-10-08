"""Safe, CLI-neutral subprocess execution for Runtime adapters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from uuid import uuid4

from .model import AttemptOutcome, ExecutionAttempt
from .persistent import PersistentGoalLoop
from .runtime import deliver_runtime_result


@dataclass(frozen=True)
class RuntimeInvocation:
    """One explicit subprocess request; no shell interpolation is performed."""

    argv: tuple[str, ...]
    working_directory: Path
    timeout_seconds: float = 300.0
    stdin_text: str | None = None


@dataclass(frozen=True)
class RuntimeExecution:
    delivery_id: str
    outcome: AttemptOutcome
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


class SubprocessRuntime:
    """Run an argv-only process and return facts for separate durable ingress."""

    def run(self, invocation: RuntimeInvocation, *, delivery_id: str | None = None) -> RuntimeExecution:
        if not invocation.argv:
            raise ValueError("Runtime invocation requires a non-empty argv.")
        if invocation.timeout_seconds <= 0:
            raise ValueError("Runtime timeout must be positive.")
        effective_delivery_id = delivery_id or f"subprocess-{uuid4().hex}"
        try:
            completed = subprocess.run(
                invocation.argv,
                cwd=invocation.working_directory,
                input=invocation.stdin_text,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=invocation.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return RuntimeExecution(
                effective_delivery_id, AttemptOutcome.FAILED, None,
                _text(error.stdout), _text(error.stderr), True,
            )
        return RuntimeExecution(
            effective_delivery_id,
            AttemptOutcome.SUCCEEDED if completed.returncode == 0 else AttemptOutcome.FAILED,
            completed.returncode, completed.stdout, completed.stderr, False,
        )


def deliver_execution(
    loop: PersistentGoalLoop, attempt_id: str, execution: RuntimeExecution
) -> ExecutionAttempt:
    """Keep process execution outside SQLite transactions, then durably deliver its outcome."""
    return deliver_runtime_result(loop, attempt_id, execution.outcome, execution.delivery_id)


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value
