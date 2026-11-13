"""Prepare a project for the agent-led flow: agent instructions and the default judge (``project-init`` options).

The flow this sets up: the user works with Claude Code or Codex interactively, the agent records through the CLI,
and a separate agent that did not execute the work judges it with ``goal-loop-judge`` (Decision 6). Nothing here is
a journal fact; it only writes the agent files and the judging settings under ``.goal-loop/``.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
from importlib import resources
import json
from pathlib import Path
import re
import shutil

from .memory_view import memory_root
from .project import PROJECT_DIRNAME, ProjectLocationError

AGENTS = ("claude", "codex")
# goal-loop says how to record; goal-loop-planning says what to record (problem type, criteria, cause narrowing).
SKILL_NAMES = ("goal-loop", "goal-loop-planning")
SKILL_DIRS = {"claude": Path(".claude/skills"), "codex": Path(".agents/skills")}
INSTRUCTION_FILES = {"claude": "CLAUDE.md", "codex": "AGENTS.md"}
BEGIN = "<!-- goal-loop:begin (project-init --agents가 관리하는 절입니다. 표지 안을 고치면 다시 실행할 때 바뀝니다) -->"
END = "<!-- goal-loop:end -->"
BEGIN_PREFIX = "<!-- goal-loop:begin"
# Must match delegated_judging.JUDGE_NAMES; kept here so project-init does not import the judging module.
JUDGE_NAMES = {"codex": "codex", "claude": "claude-judge", "glm": "glm"}
JUDGE_CLI = {"codex": "codex", "claude": "claude", "glm": "claude"}
JUDGING_FILE = "judging.json"
JUDGES_FILE = "judges.json"
DEFAULT_JUDGE = "codex"
AGENT_PRODUCTS = {"claude": "Claude Code", "codex": "Codex"}
AGENT_SHORT = {"claude": "Claude", "codex": "Codex"}
# Hashes of every skill file version this tool has shipped, made from git history by
# scripts/update_released_skill_hashes.py. A copy equal to one of them was never edited (goal-172).
RELEASED_HASHES_FILE = "agent_files/released-skill-hashes.json"
# A CLAUDE.md that imports AGENTS.md (goal-170): Claude reads both files, so one shared section goes in AGENTS.md.
IMPORT_LINE = re.compile(r"^\s*@(?:\./)?AGENTS\.md\s*$", re.MULTILINE)
# Goal Loop instructions written by hand are recognised by their commands (goal-171): the executable followed by a
# subcommand or an option ("goal-loop next", "goal-loop-judge --judge codex"). A skill name such as goal-loop-planning,
# "the goal-loop skill" or a heading alone is not; what a second section would repeat is the commands (Contract v3).
# goal-loop-judge's own subcommands; goal-loop's come from its parser so a new command is covered (verdict-252).
JUDGE_SUBCOMMANDS = ("goal", "aim")


@lru_cache(maxsize=1)
def _handwritten_pattern() -> re.Pattern[str]:
    import argparse
    from .persistent_cli import build_parser  # imported here: persistent_cli imports this module lazily too

    names: set[str] = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices)
    alternatives = lambda items: "|".join(sorted((re.escape(item) for item in items), key=len, reverse=True))
    # A subcommand ends where the word ends: "next-generation" or "goal_x" is not the subcommand "next" or "goal".
    end = r"(?![\w-])"
    # The executable starts a word or follows a path separator (.\.venv\Scripts\goal-loop.exe), not "my-goal-loop".
    begin = r"(?<![\w-])"
    return re.compile(rf"{begin}goal-loop(?:\.exe)?[ \t]+(?:--?[a-z]|(?:{alternatives(names)}){end})"
                      rf"|{begin}goal-loop-judge(?:\.exe)?[ \t]+(?:--?[a-z]|(?:{alternatives(JUDGE_SUBCOMMANDS)}){end})", re.IGNORECASE)


def parse_agents(value: str) -> tuple[str, ...]:
    names = [item.strip().lower() for item in value.split(",") if item.strip()]
    unknown = sorted(set(names) - set(AGENTS))
    if not names or unknown:
        raise ProjectLocationError(f"--agents에는 {', '.join(AGENTS)} 중 하나 이상을 쉼표로 적습니다: {value!r}")
    return tuple(dict.fromkeys(names))


def skill_path(agent: str, name: str = "goal-loop") -> Path:
    return SKILL_DIRS[agent] / name / "SKILL.md"


def skill_text(name: str = "goal-loop") -> str:
    return resources.files("goal_loop_tool").joinpath(f"agent_files/{name}-SKILL.md").read_text(encoding="utf-8")


def skill_files(name: str = "goal-loop") -> dict[str, str]:
    """A skill's files by their path inside its folder: SKILL.md and the reference files it points to.

    References live in ``agent_files/<name>-references/`` and are installed as ``references/<file>`` so the agent reads
    them only when SKILL.md sends it there (goal-163).
    """
    files = {"SKILL.md": skill_text(name)}
    folder = resources.files("goal_loop_tool").joinpath(f"agent_files/{name}-references")
    if folder.is_dir():
        for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
            if item.name.endswith(".md"):
                files[f"references/{item.name}"] = item.read_text(encoding="utf-8")
    return files


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProjectLocationError(f"{path}를 읽지 못했습니다: {error}") from error
    if not isinstance(data, dict):
        raise ProjectLocationError(f"{path}는 JSON 객체여야 합니다.")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def configured_judge(project_root: Path) -> str | None:
    judge = _read_json(project_root / PROJECT_DIRNAME / JUDGING_FILE).get("judge")
    return judge if judge in JUDGE_NAMES else None


def judge_for(agent: str, judge: str) -> str:
    """The judge an agent should call: the project's judge, unless it would be recorded under the agent's own name."""
    if JUDGE_NAMES[judge] != agent:
        return judge
    return "claude" if agent == "codex" else "codex"


def _commands(project_root: Path) -> tuple[str, str, str]:
    """How the instructions call goal-loop, goal-loop-judge and Python: the project's .venv when it has the CLI."""
    if (project_root / ".venv/Scripts/goal-loop.exe").is_file():
        return r".\.venv\Scripts\goal-loop.exe", r".\.venv\Scripts\goal-loop-judge.exe", r".\.venv\Scripts\python.exe"
    if (project_root / ".venv/bin/goal-loop").is_file():
        return "./.venv/bin/goal-loop", "./.venv/bin/goal-loop-judge", "./.venv/bin/python"
    return "goal-loop", "goal-loop-judge", "python"


def _section_parts(project_root: Path) -> tuple[str, str, str, str]:
    gl, glj, py = _commands(project_root)
    start = f"`{gl} where`(`source: project` 확인), `{gl} next`, `{gl} status`로 열린 Aim, 실행 중인 Attempt, 검증 대기를 요약한다."
    if memory_root(project_root) is not None:
        start = f"`{py} -m memory_tool context`를 실행하고 `.claude/memory-context.md`를 읽은 뒤 " + start
    intro = "이 프로젝트의 작업은 Goal Loop 기록(`.goal-loop/`)으로 추적한다. 명령과 절차는 `goal-loop` 스킬과 `" + gl + " guide`에 있다."
    if gl != "goal-loop":
        intro += f" 이 프로젝트에서는 `goal-loop` 대신 `{gl}`, `goal-loop-judge` 대신 `{glj}`를 쓴다."
    return gl, glj, start, intro


def _section(intro: str, start: str, rules: list[str]) -> str:
    lines = [
        BEGIN,
        "## Goal Loop",
        "",
        intro,
        "Aim을 만들거나 나누기 전, 기준과 Contract를 쓰기 전, 불합격이 나온 뒤에는 `goal-loop-planning` 스킬을 따른다.",
        "",
        "- 세션 시작: " + start,
        *rules,
        "- 판정에 동의하지 않으면 의견을 Evidence로 남기고 사용자에게 알린다. 다른 결과를 얻으려고 판정을 다시 실행하지 않는다.",
        END,
    ]
    return "\n".join(lines) + "\n"


def instruction_section(agent: str, judge: str, project_root: Path) -> str:
    """Only the rules that always apply; the procedure lives in the goal-loop skill (handoff 2026-11-12)."""
    gl, glj, start, intro = _section_parts(project_root)
    judge_cmd = f"{glj} --judge {judge_for(agent, judge)}"
    return _section(intro, start, [
        f"- 이 에이전트의 이름은 `{agent}`이다. Proposal과 참고 문서의 `--actor`, Attempt의 실행자, Evidence의 관찰자로 이 이름을 쓴다.",
        "- `user` 이름으로는 아무것도 기록하지 않는다. 종료, 일시 중지와 재개, 보류, 검토 해소는 사용자가 정한다.",
        f"- Verdict와 Aim 평가는 직접 기록하지 않고 실행하지 않은 판정자에게 맡긴다: `{gl} verification-begin <goal-id> <attempt-id>` 뒤",
        f"  `{judge_cmd} goal <goal-id>`, Aim은 `{judge_cmd} aim <aim-id>`.",
    ])


def shared_section(agents: tuple[str, ...], judge: str, project_root: Path) -> str:
    """One section for every agent, for AGENTS.md when CLAUDE.md imports it (agreed with agent-workbench, 2026-11-13).

    Claude reads AGENTS.md too, so a section naming a single agent would put two different names in front of it.
    """
    gl, glj, start, intro = _section_parts(project_root)
    names = ", ".join(f"{AGENT_PRODUCTS[agent]}가 `{agent}`" for agent in agents)
    skills = ", ".join(f"{AGENT_PRODUCTS[agent]}가 `{SKILL_DIRS[agent].as_posix()}/`" for agent in agents)
    judges = ", ".join(f"{AGENT_SHORT[agent]}가 실행한 일은 `{glj} --judge {judge_for(agent, judge)} goal <goal-id>`" for agent in agents)
    return _section(intro, start, [
        f"- 기록 이름은 {names}이다. Proposal과 참고 문서의 `--actor`, Attempt의 실행자, Evidence의 관찰자로 자기 이름을 쓴다.",
        f"- 스킬은 {skills}에서 읽는다.",
        "- `user` 이름으로는 아무것도 기록하지 않는다. 종료, 일시 중지와 재개, 보류, 검토 해소는 사용자가 정한다.",
        f"- Verdict와 Aim 평가는 직접 기록하지 않고 실행하지 않은 판정자에게 맡긴다: `{gl} verification-begin <goal-id> <attempt-id>` 뒤",
        f"  {judges}. Aim은 `goal <goal-id>` 대신 `aim <aim-id>`를 쓴다.",
    ])


def imports_agents(project_root: Path) -> bool:
    claude = project_root / INSTRUCTION_FILES["claude"]
    return claude.is_file() and IMPORT_LINE.search(claude.read_text(encoding="utf-8")) is not None


def _managed_span(text: str, path: Path) -> tuple[int, int] | None:
    start = text.find(BEGIN_PREFIX)
    if start < 0:
        return None
    end = text.find(END, start)
    if end < 0:
        raise ProjectLocationError(f"{path}에 Goal Loop 절의 시작 표지만 있고 끝 표지({END})가 없습니다. 표지를 고친 뒤 다시 실행하십시오.")
    end += len(END)
    if end < len(text) and text[end] == "\n":
        end += 1
    return start, end


def handwritten_lines(path: Path) -> list[int]:
    """Line numbers outside the managed section that already carry Goal Loop instructions."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    span = _managed_span(text, path)
    if span:
        # Blank out the generated section, keeping its line count so reported numbers match the file.
        text = text[:span[0]] + "\n" * text.count("\n", span[0], span[1]) + text[span[1]:]
    return [number for number, row in enumerate(text.split("\n"), 1) if _handwritten_pattern().search(row)]


def _remove_section(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    span = _managed_span(text, path)
    if span is None:
        return None
    before, after = text[:span[0]], text[span[1]:]
    if before.endswith("\n\n") and (not after or after.startswith("\n")):
        before = before[:-1]
    remaining = before + after
    path.write_text(remaining if remaining.strip() else "", encoding="utf-8")
    return "removed"


def _place_section(path: Path, section: str) -> str:
    if not path.exists():
        path.write_text(section, encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    span = _managed_span(text, path)
    if span is None:
        path.write_text((text.rstrip("\n") + "\n\n" if text.strip() else "") + section, encoding="utf-8")
        return "appended"
    start, end = span
    updated = text[:start] + section + text[end:]
    if updated == text:
        return "unchanged"
    path.write_text(updated, encoding="utf-8")
    return "updated"


def _place_guarded(path: Path, section: str, checked: list[Path], warnings: list[str]) -> str:
    """Place the section unless Goal Loop instructions written by hand would sit next to a new one (goal-171)."""
    found = [(item, lines) for item in checked if (lines := handwritten_lines(item))]
    if not found:
        return _place_section(path, section)
    where = "; ".join(f"{item.name if item == path else item} {', '.join(map(str, lines))}행" for item, lines in found)
    if path.is_file() and BEGIN_PREFIX in path.read_text(encoding="utf-8"):
        warnings.append(f"표지 밖에 직접 쓴 Goal Loop 지침이 있습니다({where}). 생성 절과 겹치지 않는지 확인하십시오. "
                        "직접 쓴 지침만 쓰려면 생성 절을 지우고 --skills-only로 실행하고, 생성 절만 쓰려면 직접 쓴 절을 지운 뒤 다시 실행하십시오.")
        return _place_section(path, section)
    warnings.append(f"표지 밖에 직접 쓴 Goal Loop 지침이 있어 {path}에 절을 덧붙이지 않았습니다({where}). "
                    "직접 쓴 지침을 계속 쓰려면 --skills-only로 실행하고, 생성 절을 쓰려면 직접 쓴 절을 지운 뒤 다시 실행하십시오.")
    return "skipped"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def released_hashes() -> dict[str, set[str]]:
    """Hashes of the shipped versions of each skill file, keyed like ``goal-loop/SKILL.md``."""
    data = json.loads(resources.files("goal_loop_tool").joinpath(RELEASED_HASHES_FILE).read_text(encoding="utf-8"))
    return {key: set(values) for key, values in data.items()}


def _place_skill(path: Path, text: str, released: set[str] | frozenset[str] = frozenset()) -> str:
    """Write a skill file. An existing copy that differs is replaced only when it equals a version this tool shipped."""
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == text:
            return "unchanged"
        if _content_hash(current) not in released:
            return "kept"
        path.write_text(text, encoding="utf-8")
        return "replaced"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return "created"


def check_options(judge: str | None, glm_env_ps1: Path | None) -> Path | None:
    """Refuse a wrong option before anything is written."""
    if judge is not None and judge not in JUDGE_NAMES:
        raise ProjectLocationError(f"--judge는 {', '.join(JUDGE_NAMES)} 중 하나여야 합니다: {judge!r}")
    if glm_env_ps1 is None:
        return None
    resolved = glm_env_ps1.expanduser().resolve()
    if not resolved.is_file():
        raise ProjectLocationError(f"GLM 설정 파일이 없습니다: {resolved}")
    return resolved


def configure_judging(project_root: Path, judge: str | None, glm_env_ps1: Path | None) -> dict:
    """Write the default judge to judging.json and the GLM connection to judges.json, keeping every other key."""
    directory = project_root / PROJECT_DIRNAME
    result: dict = {}
    if judge is not None:
        path = directory / JUDGING_FILE
        data = _read_json(path)
        data["judge"] = judge
        _write_json(path, data)
        result["judging_config"] = str(path)
    if glm_env_ps1 is not None:
        path = directory / JUDGES_FILE
        data = _read_json(path)
        entry = data.get("glm") if isinstance(data.get("glm"), dict) else {}
        data["glm"] = {**entry, "env_from_ps1": glm_env_ps1.as_posix()}
        _write_json(path, data)
        result["judges_config"] = str(path)
    return result


def judging_warnings(project_root: Path, judge: str) -> list[str]:
    warnings = []
    if shutil.which(JUDGE_CLI[judge]) is None:
        warnings.append(f"판정자 {judge}가 쓰는 CLI({JUDGE_CLI[judge]})를 PATH에서 찾지 못했습니다. 설치하고 로그인해야 판정할 수 있습니다.")
    if judge == "glm":
        glm = _read_json(project_root / PROJECT_DIRNAME / JUDGES_FILE).get("glm") or {}
        if not (isinstance(glm, dict) and (glm.get("env_from_ps1") or glm.get("env_from_env"))):
            warnings.append("판정자 glm의 연결 설정이 없습니다. --glm-env-ps1 <파일>로 .goal-loop/judges.json에 연결 정보를 두십시오.")
    return warnings


def setup_project(project_root: Path, agents: tuple[str, ...], judge: str | None, glm_env_ps1: Path | None,
                  *, skills_only: bool = False) -> dict:
    """Apply the project-init options; ``glm_env_ps1`` is the path check_options returned.

    ``skills_only`` installs the skills and never reads or writes CLAUDE.md or AGENTS.md, for a folder whose
    instructions already carry their own Goal Loop section (for example an umbrella journal's folder).
    """
    result: dict = {}
    if judge is not None or glm_env_ps1 is not None:
        result["judging"] = configure_judging(project_root, judge, glm_env_ps1)
    effective = configured_judge(project_root) or DEFAULT_JUDGE
    setup_warnings: list[str] = []
    if agents:
        texts = {name: skill_files(name) for name in SKILL_NAMES}
        released = released_hashes()
        files = []
        for agent in agents:
            for name in SKILL_NAMES:
                folder = project_root / SKILL_DIRS[agent] / name
                for relative, text in texts[name].items():
                    path = folder / relative
                    files.append({"agent": agent, "path": str(path),
                                  "status": _place_skill(path, text, released.get(f"{name}/{relative}", set()))})
        if not skills_only:
            claude_file = project_root / INSTRUCTION_FILES["claude"]
            agents_file = project_root / INSTRUCTION_FILES["codex"]
            if imports_agents(project_root):
                # Claude reads AGENTS.md through @AGENTS.md: one shared section there, none in CLAUDE.md (goal-170).
                result["instructions_layout"] = "CLAUDE.md imports AGENTS.md: one shared section in AGENTS.md"
                files.append({"agent": ",".join(agents), "path": str(agents_file),
                              "status": _place_guarded(agents_file, shared_section(agents, effective, project_root),
                                                       [claude_file, agents_file], setup_warnings)})
                if _remove_section(claude_file):
                    files.append({"agent": "claude", "path": str(claude_file), "status": "removed"})
            else:
                for agent in agents:
                    instructions = project_root / INSTRUCTION_FILES[agent]
                    files.append({"agent": agent, "path": str(instructions),
                                  "status": _place_guarded(instructions, instruction_section(agent, effective, project_root),
                                                           [instructions], setup_warnings)})
        result["agent_files"] = files
        if skills_only:
            result["instructions"] = ("--skills-only: CLAUDE.md·AGENTS.md는 건드리지 않았습니다. 직접 쓴 지침에 에이전트 이름, "
                                      "user 이름으로 기록하지 않는다는 규칙, judges_by_agent의 판정자로 맡기는 판정 명령이 있는지 확인하십시오.")
        result["judges_by_agent"] = {agent: judge_for(agent, effective) for agent in agents}
        notes = []
        if any(item["status"] == "replaced" for item in files):
            notes.append("replaced: 이 도구가 예전에 배포한 판 그대로인 스킬 파일을 새 판으로 바꿨습니다.")
        if any(item["status"] == "kept" for item in files):
            notes.append("kept: 이미 있는 스킬 파일이 고쳐져 있어(예전에 배포한 어느 판과도 달라) 덮어쓰지 않았습니다. "
                         "새 내용으로 바꾸려면 그 파일을 지우고 다시 실행하십시오.")
        if notes:
            result["note"] = " ".join(notes)
    if result:
        result["judge"] = effective
        result["warnings"] = setup_warnings + judging_warnings(project_root, effective)
    return result
