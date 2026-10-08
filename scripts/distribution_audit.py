"""Read-only scan for what blocks publishing a repository (goal-100).

usage: python scripts/distribution_audit.py <root> [<root> ...] > audit.json

Personal values (home folder, user name, e-mail) are read from this machine at run time and reported masked,
so neither this script nor its output carries them. Nothing is written except standard output.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SKIP_DIRS = {"node_modules", ".venv", ".git", "dist", "build", "target", "__pycache__", "coverage", ".pytest_cache",
             "goal_loop_tool.egg-info", ".goal-loop", ".memory", "artifacts", "workspace", "reports", "findings", "goals"}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json", ".md", ".toml", ".yaml", ".yml", ".rs", ".ps1",
                 ".cmd", ".bat", ".sh", ".css", ".html", ".txt", ".cfg", ".ini"}
SIZE_LIMIT = 2_000_000


def personal_patterns() -> dict[str, re.Pattern]:
    home = Path(os.path.expanduser("~"))
    user = getpass.getuser()
    patterns = {
        "사용자 홈 경로": re.compile(re.escape(str(home)).replace(r"\\", r"[\\/]+"), re.I),
        "사용자 이름": re.compile(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", re.I),
        "개인 작업 폴더 절대 경로": re.compile(r"[A-Za-z]:[\\/]+code_projects", re.I),
    }
    try:
        email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        email = ""
    if email:
        patterns["git 이메일"] = re.compile(re.escape(email), re.I)
    return patterns


SECRETS = {
    "API 키처럼 보이는 값": re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
    "알려진 키 접두어": re.compile(r"\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
}
ENVIRONMENT = {
    "Windows 실행 파일·셸": re.compile(r"(?i)(\.exe\b|powershell|pwsh|cmd(\.exe)? /c|taskkill|Get-CimInstance)"),
    "venv Scripts 경로": re.compile(r"(?i)\.venv[\\/]+Scripts"),
    "외부 에이전트 CLI": re.compile(r"(?i)\b(codex exec|claude -p|codex\.cmd|claude\.cmd)\b"),
    "고정 포트": re.compile(r"\b(localhost|127\.0\.0\.1):(3000|5173|5174|9222)\b"),
}
# Files that should not be published, by name.
PRIVATE_NAMES = [
    (re.compile(r".*\.sqlite3$"), "로컬 데이터베이스"),
    (re.compile(r"^image_\d+\.png$"), "작업용 이미지"),
    (re.compile(r".*\.bak$"), "백업 파일"),
]
PRIVATE_DIRS = {".goal-loop": "Goal Loop 저널(개인 작업 기록)", ".memory": "Memory Tool 기록(개인 지식)", "artifacts": "작업 산출물",
                "workspace": "AO 작업 공간", "reports": "AO 보고서", "findings": "AO 조사 산출물", "goals": "AO Goal 산출물"}
PRESENCE = ["LICENSE", "README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", ".gitignore"]


def walk(root: Path):
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            yield Path(directory) / name


def scan(root: Path, personal: dict[str, re.Pattern]) -> dict:
    findings: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    groups = {**{f"공개 위험: {k}": v for k, v in personal.items()}, **{f"공개 위험: {k}": v for k, v in SECRETS.items()},
              **{f"환경 가정: {k}": v for k, v in ENVIRONMENT.items()}}
    for path in walk(root):
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > SIZE_LIMIT:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root).as_posix()
        for number, line in enumerate(lines, 1):
            for label, pattern in groups.items():
                if pattern.search(line):
                    findings[label][relative].append(number)
    top_level = sorted(item.name for item in root.iterdir())
    private_files = sorted({item.relative_to(root).as_posix() for item in root.iterdir() if item.is_file()
                            for pattern, _ in PRIVATE_NAMES if pattern.match(item.name)})
    private_dirs = {name: label for name, label in PRIVATE_DIRS.items() if (root / name).is_dir()}
    package = {}
    for manifest in ("pyproject.toml", "package.json", "ui/package.json", "ui/src-tauri/tauri.conf.json", "src/desktop/src-tauri/tauri.conf.json"):
        if (root / manifest).is_file():
            text = (root / manifest).read_text(encoding="utf-8")
            package[manifest] = {key: bool(re.search(rf'["\']?{key}["\']?\s*[:=]', text)) for key in ("license", "authors", "author", "repository", "private", "version")}
    return {
        "root_name": root.name,
        "git_repository": (root / ".git").exists(),
        "present": {name: (root / name).is_file() for name in PRESENCE},
        "top_level": top_level,
        "private_files": private_files,
        "private_dirs": private_dirs,
        "package_metadata": package,
        "findings": {label: {"files": len(files), "lines": sum(len(n) for n in files.values()),
                             "examples": [f"{name}:{numbers[0]}" for name, numbers in sorted(files.items())[:12]]}
                     for label, files in sorted(findings.items())},
    }


def main() -> None:
    personal = personal_patterns()
    print(json.dumps({"patterns": sorted(personal), "roots": [scan(Path(arg).resolve(), personal) for arg in sys.argv[1:]]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
