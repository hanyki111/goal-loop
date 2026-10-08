"""Transaction-first command execution support for persistent GoalLoop slices."""

from __future__ import annotations

from typing import Callable, TypeVar

from .service import GoalLoop

T = TypeVar("T")


class TrialStateExecutor:
    """Run a command against a freshly replayed state and publish it only on commit."""

    def __init__(self, replay: Callable[[], GoalLoop]) -> None:
        self._replay = replay

    def run(self, commit: Callable[[], None], command: Callable[[GoalLoop], T]) -> tuple[GoalLoop, T]:
        trial = self._replay()
        result = command(trial)
        commit()
        return trial, result
