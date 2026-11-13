"""Rebuild goal_loop_tool/agent_files/released-skill-hashes.json from git history (goal-172).

project-init replaces an installed skill file only when it equals a version this tool shipped, so a copy nobody
edited moves to the new template while an edited copy is kept. This script lists every version each template file
has had in git and stores the SHA-256 of its content with line endings normalised to LF.

Run it after changing a file under goal_loop_tool/agent_files/ and commit the result; a test fails until the
current templates are listed.

    python scripts/update_released_skill_hashes.py          # rewrite the JSON file
    python scripts/update_released_skill_hashes.py --check  # exit 1 if the file is out of date
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
AGENT_FILES = REPO / "goal_loop_tool" / "agent_files"
OUTPUT = AGENT_FILES / "released-skill-hashes.json"


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data.decode("utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True).stdout


def template_files() -> dict[str, Path]:
    """Installed key (``<skill>/SKILL.md``, ``<skill>/references/<file>``) -> template path."""
    files: dict[str, Path] = {}
    for skill in sorted(AGENT_FILES.glob("*-SKILL.md")):
        name = skill.name[: -len("-SKILL.md")]
        files[f"{name}/SKILL.md"] = skill
        references = AGENT_FILES / f"{name}-references"
        if references.is_dir():
            for item in sorted(references.glob("*.md")):
                files[f"{name}/references/{item.name}"] = item
    return files


def versions(path: Path) -> list[str]:
    """Hashes of every committed version of one file, following renames, plus the working copy."""
    relative = path.relative_to(REPO).as_posix()
    hashes: list[str] = []
    log = git("log", "--follow", "--format=%H", "--name-only", "--", relative).decode("utf-8").split("\n")
    commit = None
    for line in log:
        line = line.strip()
        if not line:
            continue
        if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
            commit = line
            continue
        if commit is None:
            continue
        try:
            digest = content_hash(git("show", f"{commit}:{line}"))
        except subprocess.CalledProcessError:
            continue  # the commit removed the file
        if digest not in hashes:
            hashes.append(digest)
    current = content_hash(path.read_bytes())
    if current not in hashes:
        hashes.append(current)
    return sorted(hashes)


def build() -> str:
    data = {key: versions(path) for key, path in template_files().items()}
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the committed file differs from git history")
    args = parser.parse_args(argv)
    text = build()
    if args.check:
        same = OUTPUT.is_file() and OUTPUT.read_text(encoding="utf-8") == text
        print("up to date" if same else f"out of date: run python {Path(__file__).relative_to(REPO).as_posix()}")
        return 0 if same else 1
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.relative_to(REPO).as_posix()}: {sum(len(v) for v in json.loads(text).values())} hashes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
