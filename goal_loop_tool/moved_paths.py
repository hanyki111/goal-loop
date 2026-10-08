"""Moved paths: read old AO folder paths stored in runtime requests at their new location.

Runtime request payloads keep the ``ao_workspace`` and ``ao_root`` they were created with. When those folders are
moved (for example the AO folders were regrouped under one parent folder), the journal stays as it was —
it is the record — and Goal Loop rewrites the paths only when it reads them, from ``.goal-loop/moved-paths.json``::

    {"moves": [{"from": "C:/work/ao-ws", "to": "C:/work/ao/workspace"}]}

A stored path that starts with a ``from`` folder (case and separators ignored) gets that prefix replaced by ``to``.
Without the file nothing changes.
"""
from __future__ import annotations

import json
from pathlib import Path

FILENAME = "moved-paths.json"
PATH_FIELDS = ("ao_workspace", "ao_root")


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").casefold()


def load_moves(database: Path | None) -> list[tuple[str, str]]:
    """(from, to) pairs beside the journal, longest ``from`` first; empty when there is no file."""
    if database is None:
        return []
    source = Path(database).parent / FILENAME
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise ValueError(f"{source}를 읽지 못했습니다: {error}") from error
    moves = [(str(item["from"]), str(item["to"])) for item in data.get("moves", [])]
    return sorted(moves, key=lambda move: len(_norm(move[0])), reverse=True)


def relocate(path: str, moves: list[tuple[str, str]]) -> str:
    """The path at its new location, or unchanged when no moved folder contains it."""
    normalized = path.replace("\\", "/")
    for old, new in moves:
        prefix = _norm(old)
        if normalized.casefold() == prefix or normalized.casefold().startswith(prefix + "/"):
            return new.replace("\\", "/").rstrip("/") + normalized[len(prefix):]
    return path


def relocated_payload(payload: dict, database: Path | None) -> dict:
    """A copy of an AO request payload with its folder paths moved; the stored payload is not touched."""
    moves = load_moves(database)
    if not moves:
        return payload
    return {key: relocate(value, moves) if key in PATH_FIELDS and isinstance(value, str) else value
            for key, value in payload.items()}
