"""Restricted Claude Code CLI profile built on the common subprocess runtime."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .subprocess_runtime import RuntimeInvocation


@dataclass(frozen=True)
class ClaudePrintProfile:
    """Run one bounded, non-interactive Claude Code investigation without write tools."""

    executable: str = "claude"
    model: str = ""
    max_turns: int = 0
    max_budget_usd: float = 0.0

    def invocation(self, prompt: str, working_directory: Path, *, timeout_seconds: float = 300.0) -> RuntimeInvocation:
        if not self.model.strip():
            raise ValueError("Claude Runtime requires an explicit model.")
        if self.max_turns <= 0:
            raise ValueError("Claude Runtime max_turns must be positive.")
        if self.max_budget_usd <= 0:
            raise ValueError("Claude Runtime max_budget_usd must be positive.")
        return RuntimeInvocation(
            argv=(
                self.executable,
                "-p",
                "--restricted",
                "--permission-prompts", "none",
                "--output-format", "json",
                "--no-session-persistence",
                "--max-turns", str(self.max_turns),
                "--max-budget-usd", str(self.max_budget_usd),
                "--model", self.model,
                prompt,
            ),
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
        )
