"""Prepare a project for the agent-led flow: agent instructions and the default judge (``project-init`` options).

The flow this sets up: the user works with Claude Code or Codex interactively, the agent records through the CLI,
and a separate agent that did not execute the work judges it with ``goal-loop-judge`` (Decision 6). Nothing here is
a journal fact; it only writes the agent files and the judging settings under ``.goal-loop/``.
"""
from __future__ import annotations

from importlib import resources
import json
from pathlib import Path
import shutil

from .memory_view import memory_root
from .project import PROJECT_DIRNAME, ProjectLocationError

AGENTS = ("claude", "codex")
SKILL_PATHS = {"claude": Path(".claude/skills/goal-loop/SKILL.md"), "codex": Path(".agents/skills/goal-loop/SKILL.md")}
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


def parse_agents(value: str) -> tuple[str, ...]:
    names = [item.strip().lower() for item in value.split(",") if item.strip()]
    unknown = sorted(set(names) - set(AGENTS))
    if not names or unknown:
        raise ProjectLocationError(f"--agents에는 {', '.join(AGENTS)} 중 하나 이상을 쉼표로 적습니다: {value!r}")
    return tuple(dict.fromkeys(names))


def skill_text() -> str:
    return resources.files("goal_loop_tool").joinpath("agent_files/goal-loop-SKILL.md").read_text(encoding="utf-8")


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


def instruction_section(agent: str, judge: str, project_root: Path) -> str:
    gl, glj, py = _commands(project_root)
    judge = judge_for(agent, judge)
    judge_cmd = f"{glj} --judge {judge}"
    memory = memory_root(project_root) is not None
    start = []
    if memory:
        start.append(f"1. `{py} -m memory_tool context`를 실행하고 `.claude/memory-context.md`를 읽는다.")
    start.append(f"{len(start) + 1}. `{gl} where`(`source: project` 확인), `{gl} next`, `{gl} status`를 실행하고 "
                 "열린 Aim, 실행 중인 Attempt, 검증 대기를 요약한다.")
    lines = [
        BEGIN,
        "## Goal Loop",
        "",
        "이 프로젝트의 작업은 Goal Loop 기록(`.goal-loop/`)으로 추적한다. 사용자는 대화로 일을 맡기고, 에이전트는 CLI로 기록하며,",
        "판정은 실행하지 않은 별도 판정자에게 맡긴다. 자세한 명령은 `goal-loop` 스킬과 `" + gl + " guide`에 있다.",
        "",
        "### 세션 시작",
        "",
        *start,
        "",
        "### 기록",
        "",
        f"- 이 에이전트의 이름은 `{agent}`이다. Proposal과 참고 문서의 `--actor`, Attempt의 실행자, Evidence의 관찰자로 이 이름을 쓴다.",
        "  `user` 이름으로는 아무것도 기록하지 않는다. 종료, 일시 중지와 재개, 보류, 검토 해소는 사용자가 정한다.",
        "- 각 Aim의 첫 Proposal에 \"무엇을 이루면 달성인가\"를 쓰고(첫 줄은 한 문장 요약) `proposal-close --kind criteria`로 닫는다.",
        "  각 Goal의 완료 기준에는 멈춤 조건을 쓴다.",
        "- Evidence에는 실행한 뒤 관찰한 것만 쓰고, 근거로 삼은 출력은 `--attach`로 붙인다.",
        "- 같은 방향의 Proposal이 두 번째로 나오면 그 방향을 하위 Aim으로 먼저 만들고 사용자 승인을 받는다(Proposal과 Goal은 Aim을 옮길 수 없다).",
        f"- Goal이 앞 Goal의 결과로 시작했거나 중단된 Goal을 이어가면 `goal-follow-add <goal> <앞 goal> --actor {agent} --reason \"…\"`로 남긴다.",
        "",
        "### 판정",
        "",
        f"- `{gl} verification-begin <goal-id> <attempt-id>` 뒤 `{judge_cmd} goal <goal-id>`로 판정을 맡긴다.",
        f"  Aim은 `{judge_cmd} aim <aim-id>`로 맡긴다. `--no-record`를 붙이면 기록하지 않고 결과만 본다.",
        "- 판정자는 프로젝트 사본에서 별도 세션으로 실행되고, 그 답이 그대로 Verdict로 기록된다. 판정에 동의하지 않으면",
        "  의견을 Evidence로 남기고 사용자에게 알린다. 다른 결과를 얻으려고 판정을 다시 실행하지 않는다.",
    ]
    if memory:
        lines.append(f"- 판정이 기록되면 결과의 `memory_draft`(또는 `{gl} memory-draft`)를 다듬어 "
                     f"`{py} -m memory_tool record \"…\"`로 타임라인에 남긴다.")
    lines.append(END)
    return "\n".join(lines) + "\n"


def _place_section(path: Path, section: str) -> str:
    if not path.exists():
        path.write_text(section, encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    start = text.find(BEGIN_PREFIX)
    if start < 0:
        path.write_text((text.rstrip("\n") + "\n\n" if text.strip() else "") + section, encoding="utf-8")
        return "appended"
    end = text.find(END, start)
    if end < 0:
        raise ProjectLocationError(f"{path}에 Goal Loop 절의 시작 표지만 있고 끝 표지({END})가 없습니다. 표지를 고친 뒤 다시 실행하십시오.")
    end += len(END)
    if end < len(text) and text[end] == "\n":
        end += 1
    updated = text[:start] + section + text[end:]
    if updated == text:
        return "unchanged"
    path.write_text(updated, encoding="utf-8")
    return "updated"


def _place_skill(path: Path, text: str) -> str:
    if path.exists():
        return "unchanged" if path.read_text(encoding="utf-8") == text else "kept"
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


def setup_project(project_root: Path, agents: tuple[str, ...], judge: str | None, glm_env_ps1: Path | None) -> dict:
    """Apply the project-init options; ``glm_env_ps1`` is the path check_options returned."""
    result: dict = {}
    if judge is not None or glm_env_ps1 is not None:
        result["judging"] = configure_judging(project_root, judge, glm_env_ps1)
    effective = configured_judge(project_root) or DEFAULT_JUDGE
    if agents:
        text = skill_text()
        files = []
        for agent in agents:
            skill = project_root / SKILL_PATHS[agent]
            files.append({"agent": agent, "path": str(skill), "status": _place_skill(skill, text)})
            instructions = project_root / INSTRUCTION_FILES[agent]
            files.append({"agent": agent, "path": str(instructions),
                          "status": _place_section(instructions, instruction_section(agent, effective, project_root))})
        result["agent_files"] = files
        result["judges_by_agent"] = {agent: judge_for(agent, effective) for agent in agents}
        if any(item["status"] == "kept" for item in files):
            result["note"] = "kept: 이미 있는 스킬 파일의 내용이 달라 덮어쓰지 않았습니다. 새 내용으로 바꾸려면 그 파일을 지우고 다시 실행하십시오."
    if result:
        result["judge"] = effective
        result["warnings"] = judging_warnings(project_root, effective)
    return result
