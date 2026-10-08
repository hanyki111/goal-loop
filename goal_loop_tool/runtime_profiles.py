"""Versioned, bounded Runtime payload executors for durable outbox requests."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .ao_runtime import REQUEST_KIND as AO_SESSION_KIND, AoSessionPayload, run_ao_session
from .ao_cli_runtime import REQUEST_KIND as AO_CLI_KIND, AoCliPayload, run_ao_cli
from .claude_runtime import ClaudePrintProfile
from .codex_runtime import CodexExecProfile
from .repository import RuntimeOutboxRequest
from .subprocess_runtime import RuntimeExecution, RuntimeInvocation, SubprocessRuntime


class RuntimePayloadError(ValueError):
    """A stored Runtime payload is not one of the explicitly supported profiles."""


class InvocationRunner(Protocol):
    def run(self, invocation: RuntimeInvocation, *, delivery_id: str | None = None) -> RuntimeExecution: ...


def execute_profile_request(
    request: RuntimeOutboxRequest, *, runtime: InvocationRunner | None = None
) -> RuntimeExecution:
    """Execute one recognized versioned payload using only its bounded profile."""
    if request.request_kind == "codex_exec.v1":
        invocation = _codex_invocation(request.payload)
    elif request.request_kind == "claude_print.v1":
        invocation = _claude_invocation(request.payload)
    elif request.request_kind == AO_SESSION_KIND:
        # AO runs over HTTP, not as a local process; it raises AoRuntimeError instead of inventing a result.
        return run_ao_session(AoSessionPayload.parse(request.payload))
    elif request.request_kind == AO_CLI_KIND:
        # AO runs through its CLI without the AO server (restructure S3); like ao_session.v1 it refuses to invent a result.
        return run_ao_cli(AoCliPayload.parse(request.payload), request_label=request.request_id)
    else:
        raise RuntimePayloadError(f"Unsupported Runtime request kind: {request.request_kind}")
    return (runtime or SubprocessRuntime()).run(invocation)


def _codex_invocation(payload: dict[str, object]) -> RuntimeInvocation:
    _only(payload, {"prompt", "workdir", "timeout_seconds", "sandbox", "executable", "extra_args"})
    profile = CodexExecProfile(
        executable=_optional_string(payload, "executable", "codex"),
        sandbox=_optional_string(payload, "sandbox", "read-only"),
        extra_args=_optional_string_list(payload, "extra_args"),
    )
    return profile.invocation(
        _required_string(payload, "prompt"), _workdir(payload),
        timeout_seconds=_optional_positive_number(payload, "timeout_seconds", 300.0),
    )


def _claude_invocation(payload: dict[str, object]) -> RuntimeInvocation:
    _only(payload, {"prompt", "workdir", "timeout_seconds", "executable", "model", "max_turns", "max_budget_usd"})
    profile = ClaudePrintProfile(
        executable=_optional_string(payload, "executable", "claude"),
        model=_required_string(payload, "model"),
        max_turns=_required_positive_int(payload, "max_turns"),
        max_budget_usd=_required_positive_number(payload, "max_budget_usd"),
    )
    return profile.invocation(
        _required_string(payload, "prompt"), _workdir(payload),
        timeout_seconds=_optional_positive_number(payload, "timeout_seconds", 300.0),
    )


def _only(payload: dict[str, object], allowed: set[str]) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise RuntimePayloadError(f"Unsupported Runtime payload fields: {', '.join(sorted(unknown))}")


def _required_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimePayloadError(f"Runtime payload requires non-empty string {name!r}.")
    return value


def _optional_string(payload: dict[str, object], name: str, default: str) -> str:
    if name not in payload:
        return default
    return _required_string(payload, name)


def _optional_string_list(payload: dict[str, object], name: str) -> tuple[str, ...]:
    if name not in payload:
        return ()
    value = payload[name]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimePayloadError(f"Runtime payload field {name!r} must be an array of strings.")
    return tuple(value)


def _optional_positive_number(payload: dict[str, object], name: str, default: float) -> float:
    if name not in payload:
        return default
    value = payload[name]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise RuntimePayloadError(f"Runtime payload field {name!r} must be positive.")
    return float(value)


def _required_positive_number(payload: dict[str, object], name: str) -> float:
    if name not in payload:
        raise RuntimePayloadError(f"Runtime payload requires positive number {name!r}.")
    return _optional_positive_number(payload, name, 0.0)


def _required_positive_int(payload: dict[str, object], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RuntimePayloadError(f"Runtime payload field {name!r} must be a positive integer.")
    return value


def _workdir(payload: dict[str, object]) -> Path:
    path = Path(_required_string(payload, "workdir"))
    if not path.is_dir():
        raise RuntimePayloadError(f"Runtime payload workdir must be an existing directory: {path}")
    return path
