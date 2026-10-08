"""Project-folder storage: where a journal lives, its project root, and the files it points to.

Like memory_tool's ``.memory/``, a project keeps its journal inside the project folder so a session
started anywhere in the repository finds it. None of this is a journal fact; Core only stores the
normalized reference strings and attachment digests computed here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil

CEILING_ENV = "GOAL_LOOP_CEILING_DIRECTORIES"
PROJECT_DIRNAME = ".goal-loop"
JOURNAL_FILENAME = "journal.sqlite3"
ARTIFACTS_DIRNAME = "artifacts"
MAX_SEARCH_DEPTH = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
URL_PREFIXES = ("http://", "https://", "ao:")  # ao: names an AO Goal without a web address


class ProjectLocationError(ValueError):
    """A path, reference, or attachment cannot be used as given."""


def project_database(folder: Path) -> Path:
    return folder / PROJECT_DIRNAME / JOURNAL_FILENAME


def ceiling_directories() -> set[Path]:
    """Folders the upward search must not climb above, like git's GIT_CEILING_DIRECTORIES.

    Tests and agent sandboxes set this so a temporary folder that happens to sit inside another project
    does not pick up that project's journal.
    """
    value = os.environ.get(CEILING_ENV, "")
    return {Path(item).resolve() for item in value.split(os.pathsep) if item.strip()}


def discover_project_database(start: Path) -> Path | None:
    """Return the nearest ``.goal-loop/journal.sqlite3`` at or above ``start``, below any ceiling."""
    current = start.resolve()
    ceilings = ceiling_directories()
    for _ in range(MAX_SEARCH_DEPTH + 1):
        candidate = project_database(current)
        if candidate.is_file():
            return candidate
        if current.parent == current or current in ceilings:
            return None
        current = current.parent
    return None


def init_project(folder: Path) -> tuple[Path, bool]:
    """Create ``.goal-loop/`` with a self-ignoring ``.gitignore``; return the journal path and whether it was new."""
    folder = folder.resolve()
    if not folder.is_dir():
        raise ProjectLocationError(f"프로젝트 폴더가 없습니다: {folder}")
    directory = folder / PROJECT_DIRNAME
    created = not directory.exists()
    directory.mkdir(exist_ok=True)
    ignore = directory / ".gitignore"
    if not ignore.exists():
        # The journal is binary SQLite with WAL sidecars, so the whole folder stays out of Git.
        ignore.write_text("*\n", encoding="utf-8")
    return directory / JOURNAL_FILENAME, created


def project_root_for(database: Path) -> Path | None:
    """A journal inside ``.goal-loop/`` belongs to the folder above it, however it was selected."""
    database = database.resolve()
    return database.parent.parent if database.parent.name == PROJECT_DIRNAME else None


def artifacts_dir_for(database: Path) -> Path:
    database = database.resolve()
    if project_root_for(database) is not None:
        return database.parent / ARTIFACTS_DIRNAME
    return database.parent / f"{database.stem}.artifacts"


def normalize_reference(location: str, project_root: Path | None, cwd: Path) -> str:
    """Store a URL as given, a file inside the project relative to its root, and any other file absolutely."""
    location = location.strip()
    if not location:
        raise ProjectLocationError("참고 문서 위치가 비어 있습니다.")
    if location.lower().startswith(URL_PREFIXES):
        return location
    path_text, hash_mark, anchor = location.partition("#")
    candidate = Path(path_text)
    if not candidate.is_absolute():
        # Relative input is read from where the command runs, which is how a shell user types it.
        candidate = cwd / candidate
    candidate = candidate.resolve()
    if not candidate.exists():
        raise ProjectLocationError(f"참고 문서를 찾지 못했습니다: {candidate}")
    stored = candidate.as_posix()
    if project_root is not None:
        try:
            stored = candidate.relative_to(project_root.resolve()).as_posix()
        except ValueError:
            pass
    return stored + (hash_mark + anchor if hash_mark else "")


def resolve_reference(location: str, project_root: Path | None) -> tuple[str, bool]:
    """Return what a UI should open and whether it currently exists (URLs are not probed)."""
    if location.lower().startswith(URL_PREFIXES):
        return location, True
    path_text = location.partition("#")[0]
    path = Path(path_text)
    if not path.is_absolute():
        if project_root is None:
            return path_text, False
        path = project_root / path
    return str(path), path.exists()


@dataclass(frozen=True)
class StoredAttachment:
    name: str
    sha256: str
    size: int


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def store_attachment(source: Path, artifacts_dir: Path) -> StoredAttachment:
    """Copy ``source`` to ``artifacts/<sha256>/<name>`` so the observed content survives later edits."""
    source = source.resolve()
    if not source.is_file():
        raise ProjectLocationError(f"첨부할 파일이 없습니다: {source}")
    size = source.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ProjectLocationError(f"첨부 파일은 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB 이하여야 합니다: {source} ({size} bytes)")
    sha256 = _digest(source)
    target = artifacts_dir / sha256 / source.name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return StoredAttachment(source.name, sha256, size)


def attachment_path(artifacts_dir: Path, name: str, sha256: str) -> Path:
    return artifacts_dir / sha256 / name


def attachment_status(artifacts_dir: Path, name: str, sha256: str) -> str:
    path = attachment_path(artifacts_dir, name, sha256)
    if not path.is_file():
        return "missing"
    return "preserved" if _digest(path) == sha256 else "changed"
