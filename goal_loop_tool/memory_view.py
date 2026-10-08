"""Read-only view of a project's Memory Tool files for the app.

The knowledge folder is found as Memory Tool finds it (``base_folder``): ``.memory`` by default, renamed by the
project's ``.memory-tool.yml`` or ``MEMORY_TOOL_BASE``, or the project root itself (``base: "."``).
Goal Loop never writes these files. When the project has no Memory Tool content everything here is empty,
so a project that uses Goal Loop alone sees nothing.
- ``mentions``: timeline entries (``<knowledge folder>/timeline/**/*.md``, lines ``- HH:MM | text`` plus continuation
  lines) with the Goal Loop record IDs each one cites.
- ``documents``: excerpts of the knowledge folder's Markdown files that records reference, from the referenced section.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

# Timeline shorthand: "verdict-1·5·6" lists and "goal-38~53" / "goal-21–24" ranges cite every number they cover.
# ASCII boundaries: Korean particles attach directly to an ID ("goal-12와"), and \w would count them as part of it.
_SHORTHAND = re.compile(r"(?<![A-Za-z0-9_-])((?:aim-evaluation|impact-review|aim|proposal|goal|attempt|evidence|verdict|decision|correction))-([0-9]+)"
                        r"((?:·[0-9]+)*)(?:\s?[~–]\s?([0-9]+))?(?![A-Za-z0-9_-])")
RANGE_LIMIT = 60
_GOAL_VERDICT = re.compile(r"(?<![A-Za-z0-9_-])goal-[0-9]+\([0-9]+\)(?:·[0-9]+\([0-9]+\))*")
_PAIR = re.compile(r"([0-9]+)\(([0-9]+)\)")


def cited_ids(text: str) -> list[str]:
    """Record IDs a timeline entry cites, with its list and range shorthand expanded, in order of appearance."""
    found: list[str] = []
    for match in _SHORTHAND.finditer(text):
        prefix, first = match.group(1), int(match.group(2))
        numbers = [first, *(int(part) for part in match.group(3).split("·") if part)]
        if match.group(4) and 0 < int(match.group(4)) - numbers[-1] <= RANGE_LIMIT:
            numbers += list(range(numbers[-1] + 1, int(match.group(4)) + 1))
        found += [f"{prefix}-{number}" for number in numbers]
    return list(dict.fromkeys(found))


_ENTRY = re.compile(r"^- (\d{1,2}:\d{2}) \| ?(.*)$")
_DATE = re.compile(r"(\d{4})-(\d{2})[/\\](\d{2})\.md$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
EXCERPT_LIMIT = 6000


# Memory Tool's own rules (memory_tool/utils/paths.py), read here without importing Memory Tool so Goal Loop
# still works alone: MEMORY_TOOL_ROOT/MEMORY_TOOL_BASE, then the project's .memory-tool.yml pointer, then .memory.
POINTER_FILENAME = ".memory-tool.yml"
DEFAULT_BASE = ".memory"
CONTENT_SUBDIRS = ("timeline", "modules", "concepts", "plans", "reviews", "summaries", "docs")
_POINTER_BASE = re.compile(r"""^\s*base\s*:\s*(?:"([^"]*)"|'([^']*)'|([^#\s][^#]*?))\s*(?:#.*)?$""", re.M)


def _pointer_base(project_root: Path) -> str | None:
    try:
        text = (project_root / POINTER_FILENAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _POINTER_BASE.search(text)
    if match is None:
        return None
    name = next(group for group in match.groups() if group is not None).strip().rstrip("/\\")
    return name or None


def base_folder(project_root: Path | None) -> Path | None:
    """The Memory Tool knowledge folder for this project root, whether or not it exists."""
    if project_root is None:
        return None
    env_base, env_root = os.environ.get("MEMORY_TOOL_BASE"), os.environ.get("MEMORY_TOOL_ROOT")
    if env_root and Path(env_root).expanduser().resolve() != Path(project_root).resolve():
        env_base = None  # the environment points Memory Tool at another project
    name = env_base or _pointer_base(project_root) or DEFAULT_BASE
    if Path(name).expanduser().is_absolute():
        return Path(name).expanduser()
    return project_root if name in (".", "./") else project_root / name


def memory_root(project_root: Path | None) -> Path | None:
    """The knowledge folder when it holds Memory Tool content; None for a project without Memory Tool."""
    base = base_folder(project_root)
    if base is None or not base.is_dir():
        return None
    return base if any((base / name).is_dir() for name in CONTENT_SUBDIRS) else None


def mentions(project_root: Path | None) -> list[dict]:
    """Timeline entries that cite at least one Goal Loop record ID, newest first."""
    root = memory_root(project_root)
    if root is None or not (root / "timeline").is_dir():
        return []
    entries: list[dict] = []
    for path in sorted((root / "timeline").rglob("*.md")):
        dated = _DATE.search(path.as_posix())
        date = f"{dated.group(1)}-{dated.group(2)}-{dated.group(3)}" if dated else path.stem
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        current: dict | None = None
        for line in lines:
            match = _ENTRY.match(line)
            if match:
                current = {"date": date, "time": match.group(1), "text": match.group(2).strip(), "file": path.relative_to(project_root).as_posix()}
                entries.append(current)
            elif current is not None and line.strip() and not line.startswith("#"):
                current["text"] += "\n" + line.strip()
            elif not line.strip():
                current = None
    cited = []
    for entry in entries:
        ids = cited_ids(entry["text"])
        if ids:
            cited.append({**entry, "ids": ids})
    return sorted(cited, key=lambda entry: (entry["date"], entry["time"]), reverse=True)


DRAFT_REASON_LIMIT = 120


def _first_line(text: str | None, limit: int) -> str:
    line = next((part.strip() for part in (text or "").splitlines() if part.strip()), "")
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _unmet_line(reason: str, limit: int) -> str:
    """The line of a numbered reason that says what was not met, else its first line."""
    lines = [part.strip() for part in reason.splitlines() if part.strip()]
    chosen = next((line for line in lines if "미충족" in line or "부족한 근거" in line), lines[0] if lines else "")
    return chosen if len(chosen) <= limit else chosen[: limit - 1] + "…"


def _record_command(text: str) -> str:
    return 'python -m memory_tool record "' + text.replace('"', "'") + '"'


def drafts(projection: dict, project_root: Path | None) -> list[dict]:
    """Timeline lines to suggest for valid Verdicts and Aim Evaluations no timeline entry cites yet, newest first.

    Goal Loop only proposes the line and the ``memory_tool record`` command; recording stays the agent's or
    person's choice. Without Memory Tool content there is nothing to propose.
    """
    if memory_root(project_root) is None:
        return []
    entries = mentions(project_root)
    cited = {record_id for entry in entries for record_id in entry["ids"]}
    # "goal-60(132)·68(131)" names each Goal's Verdict by number; count it only when the journal agrees.
    goal_of = {item["id"]: item["goal_id"] for item in projection.get("verdicts", [])}
    for entry in entries:
        for chain in _GOAL_VERDICT.finditer(entry["text"]):
            for goal_number, verdict_number in _PAIR.findall(chain.group(0)):
                if goal_of.get(f"verdict-{verdict_number}") == f"goal-{goal_number}":
                    cited.add(f"verdict-{verdict_number}")
    goals = {item["id"]: item for item in projection.get("goals", [])}
    aims = {item["id"]: item for item in projection.get("aims", [])}
    number = re.compile(r"-(\d+)$")
    order = lambda record_id: int(number.search(record_id).group(1))
    latest: dict[str, dict] = {}  # an earlier Verdict of the same Goal is history once a later one exists
    for verdict in projection.get("verdicts", []):
        if verdict["goal_id"] not in latest or order(verdict["id"]) > order(latest[verdict["goal_id"]]["id"]):
            latest[verdict["goal_id"]] = verdict
    found = []
    for verdict in latest.values():
        if verdict["id"] in cited or verdict.get("validity") == "invalidated":
            continue
        goal = goals.get(verdict["goal_id"], {})
        text = f"{verdict['goal_id']} {verdict['result']}({verdict['id']}, {verdict['verifier_actor_id']}): {goal.get('target', '')}"
        if verdict["result"] != "satisfied" and verdict.get("reason"):
            text += f" — {_unmet_line(verdict['reason'], DRAFT_REASON_LIMIT)}"
        found.append({"record_id": verdict["id"], "subject_id": verdict["goal_id"], "kind": "verdict", "text": text,
                      "command": _record_command(text)})
    for evaluation in projection.get("aim_evaluations", []):
        if evaluation["id"] in cited or evaluation.get("validity") == "invalidated":
            continue
        aim = aims.get(evaluation["aim_id"], {})
        text = f"{evaluation['aim_id']} 달성({evaluation['id']}, {evaluation['evaluator_actor_id']}): {aim.get('description', '')}"
        found.append({"record_id": evaluation["id"], "subject_id": evaluation["aim_id"], "kind": "aim_evaluation", "text": text,
                      "command": _record_command(text)})
    return sorted(found, key=lambda item: (item["kind"] != "aim_evaluation", -order(item["record_id"])))


def _section(text: str, anchor: str) -> tuple[str, bool]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading and anchor.strip().lower() in heading.group(2).strip().lower():
            level = len(heading.group(1))
            end = next((later for later in range(index + 1, len(lines))
                        if (other := _HEADING.match(lines[later])) and len(other.group(1)) <= level), len(lines))
            return "\n".join(lines[index:end]), True
    return text, False


def documents(project_root: Path | None, locations: list[str]) -> dict[str, dict]:
    """Excerpts of referenced Markdown documents inside the Memory Tool knowledge folder, keyed by the reference location."""
    root = memory_root(project_root)
    if root is None:
        return {}
    base = root.resolve()
    found: dict[str, dict] = {}
    for location in locations:
        path_text, _, anchor = location.partition("#")
        if not path_text.endswith(".md") or "://" in path_text:
            continue
        path = (Path(project_root) / path_text).resolve()
        if base not in path.parents:
            continue
        # When the project root itself is the knowledge folder, only Memory Tool's content folders count.
        if path.relative_to(base).parts[0] not in CONTENT_SUBDIRS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            found[location] = {"exists": False, "excerpt": None, "anchor": anchor or None, "anchor_found": False}
            continue
        excerpt, anchor_found = _section(text, anchor) if anchor else (text, False)
        clipped = len(excerpt) > EXCERPT_LIMIT
        found[location] = {"exists": True, "excerpt": excerpt[:EXCERPT_LIMIT] + ("\n…(잘림)" if clipped else ""),
                           "anchor": anchor or None, "anchor_found": anchor_found}
    return found
