"""A tiny real local-file Runtime adapter for the executable demo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileWriteResult:
    succeeded: bool
    observation: str


class LocalFileRuntime:
    def write_new_text(self, path: Path, content: str) -> FileWriteResult:
        try:
            path.write_text(content, encoding="utf-8", errors="strict")
        except OSError as error:
            return FileWriteResult(False, f"파일 쓰기 실패: {error}")
        return FileWriteResult(True, f"실제 파일 생성: {path.resolve()}")

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")
