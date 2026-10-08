"""Codex CLI profile built on the CLI-neutral subprocess runtime."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .subprocess_runtime import RuntimeInvocation


@dataclass(frozen=True)
class CodexExecProfile:
    executable: str = "codex"
    sandbox: str = "read-only"
    extra_args: tuple[str, ...] = ()

    def invocation(self, prompt: str, working_directory: Path, *, timeout_seconds: float = 300.0) -> RuntimeInvocation:
        if self.sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("Codex Runtime permits only read-only or workspace-write sandbox modes.")
        forbidden = {"--danger-full-access", "--full-auto", "--dangerously-bypass-approvals-and-sandbox", "--ignore-rules", "--ignore-user-config"}
        if forbidden.intersection(self.extra_args):
            raise ValueError("Codex Runtime refuses dangerous or configuration-bypassing extra arguments.")
        return RuntimeInvocation(
            argv=(self.executable, "exec", "--sandbox", self.sandbox, *self.extra_args, prompt),
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
        )
