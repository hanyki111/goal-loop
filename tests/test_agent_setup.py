from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool import agent_setup
from goal_loop_tool.agent_setup import BEGIN, END, SKILL_NAMES, judge_for, skill_text
from goal_loop_tool.delegated_judging import JUDGE_NAMES, JudgingError, judging_config, main as judging_main
from goal_loop_tool.persistent_cli import main

REPO = Path(__file__).resolve().parents[1]


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


class AgentSetupTests(unittest.TestCase):
    """project-init prepares the agent-led flow: agent instructions, the default judge, and the GLM connection."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.env = patch.dict("os.environ", {"LOCALAPPDATA": str(self.root / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(self.root)}, clear=True)
        self.env.start()
        self.project = self.root / "repo"
        self.project.mkdir()
        self.ps1 = self.root / "set-glm.ps1"
        self.ps1.write_text("$env:ANTHROPIC_BASE_URL = 'https://example.invalid'\n$env:ANTHROPIC_AUTH_TOKEN = 'secret-token-1'\n", encoding="utf-8")

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def files(self) -> dict[str, str]:
        return {path.relative_to(self.project).as_posix(): path.read_text(encoding="utf-8")
                for path in sorted(self.project.rglob("*")) if path.is_file() and path.suffix in (".md", ".json")}

    def test_packaged_skill_matches_repository_skills(self):
        for name in SKILL_NAMES:
            for relative in (f".claude/skills/{name}/SKILL.md", f".agents/skills/{name}/SKILL.md"):
                self.assertEqual(skill_text(name), (REPO / relative).read_text(encoding="utf-8"), relative)

    def test_goal_loop_skill_calls_the_cli_from_path_and_holds_the_procedure(self):
        text = skill_text()
        self.assertNotIn(".venv", text)
        self.assertNotIn("In this repository", text)
        self.assertIn("Invoke `goal-loop` (and `goal-loop-judge`) from PATH", text)
        # What the generated section no longer carries lives in the skill.
        for usage in ("proposal-close --kind criteria", "--attach", "sub-Aim", "goal-follow-add", "memory_draft",
                      "goal-loop-judge --judge <judge> goal <goal-id>", "--no-record", "`goal-loop-planning` skill"):
            self.assertIn(usage, text)

    def test_section_keeps_only_the_rules_that_always_apply(self):
        (self.project / ".memory/timeline").mkdir(parents=True)
        for agent, judge in (("claude", "codex"), ("codex", "claude")):
            section = agent_setup.instruction_section(agent, "codex", self.project)
            body = section.splitlines()[1:-1]
            self.assertLessEqual(len(body), 15, section)
            self.assertIn(f"이 에이전트의 이름은 `{agent}`", section)
            self.assertIn("`user` 이름으로는 아무것도 기록하지 않는다", section)
            self.assertIn(f"goal-loop-judge --judge {judge} goal <goal-id>", section)
            self.assertIn(f"goal-loop-judge --judge {judge} aim <aim-id>", section)
            self.assertIn("goal-loop where", section)
            self.assertIn("memory_tool context", section)
            self.assertIn("`goal-loop-planning` 스킬", section)
            for moved in ("proposal-close", "--attach", "goal-follow-add", "memory_draft", "하위 Aim"):
                self.assertNotIn(moved, section)

    def test_skills_only_installs_both_skills_and_leaves_instructions_alone(self):
        claude = self.project / "CLAUDE.md"
        own = "# Umbrella\n\n## Goal Loop (hand-written)\n"
        claude.write_text(own, encoding="utf-8")
        result = run("project-init", str(self.project), "--agents", "claude,codex", "--skills-only")
        statuses = {Path(item["path"]).relative_to(self.project).as_posix(): item["status"] for item in result["agent_files"]}
        self.assertEqual(statuses, {f"{folder}/{name}/SKILL.md": "created"
                                    for folder in (".claude/skills", ".agents/skills") for name in SKILL_NAMES})
        self.assertEqual(claude.read_text(encoding="utf-8"), own)
        self.assertFalse((self.project / "AGENTS.md").exists())
        self.assertEqual(result["judges_by_agent"], {"claude": "codex", "codex": "claude"})
        self.assertIn("--skills-only", result["instructions"])
        files = self.files()
        for name in SKILL_NAMES:
            self.assertEqual(files[f".claude/skills/{name}/SKILL.md"], skill_text(name))
        # Rerunning stays unchanged and still never touches the instruction files.
        again = run("project-init", str(self.project), "--agents", "claude", "--skills-only")
        self.assertEqual({item["status"] for item in again["agent_files"]}, {"unchanged"})
        self.assertFalse((self.project / "AGENTS.md").exists())

    def test_skills_only_without_agents_writes_nothing(self):
        with redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit):
            run("project-init", str(self.project), "--skills-only")
        self.assertIn("--agents", error.getvalue())
        self.assertFalse((self.project / ".goal-loop").exists())

    def test_planning_skill_has_front_matter_and_every_part(self):
        text = skill_text("goal-loop-planning")
        self.assertTrue(text.startswith("---\nname: goal-loop-planning\ndescription: "))
        for heading in ("## 이 스킬의 목적과 한계", "## 1. 두 질문", "## 2. 유형: 지금 아는 정도", "## 3. Aim을 정할 때",
                        "## 4. 나눌 때", "## 5. 원인을 좁힐 때", "## 6. 확인 결과가 '아니오'일 때",
                        "## 7. 개선이 오르지 않을 때", "## 8. 판정과 기록", "## 근거의 수준"):
            self.assertIn(heading, text)
        # A not_satisfied Verdict on a succeeded Attempt is repaired, not retried (retry-decide needs a failed Attempt).
        self.assertIn("`retry-decide`를 쓸 수 없습니다", text)
        self.assertNotEqual(skill_text("goal-loop-planning"), skill_text())

    def test_judge_names_match_delegated_judging(self):
        self.assertEqual(agent_setup.JUDGE_NAMES, JUDGE_NAMES)

    def test_plain_project_init_output_is_unchanged(self):
        result = run("project-init", str(self.project))
        self.assertEqual(set(result), {"project_root", "database", "created", "schema_version"})
        self.assertEqual(self.files(), {})

    def test_agents_install_skills_and_marked_sections(self):
        (self.project / "CLAUDE.md").write_text("# My project\n\nOwn notes.\n", encoding="utf-8")
        result = run("project-init", str(self.project), "--agents", "claude,codex", "--judge", "codex")
        statuses = {Path(item["path"]).relative_to(self.project).as_posix(): item["status"] for item in result["agent_files"]}
        self.assertEqual(statuses, {".claude/skills/goal-loop/SKILL.md": "created", ".claude/skills/goal-loop-planning/SKILL.md": "created",
                                    "CLAUDE.md": "appended", ".agents/skills/goal-loop/SKILL.md": "created",
                                    ".agents/skills/goal-loop-planning/SKILL.md": "created", "AGENTS.md": "created"})
        files = self.files()
        for name in SKILL_NAMES:
            self.assertEqual(files[f".claude/skills/{name}/SKILL.md"], skill_text(name))
            self.assertEqual(files[f".agents/skills/{name}/SKILL.md"], skill_text(name))
        claude = files["CLAUDE.md"]
        self.assertTrue(claude.startswith("# My project\n\nOwn notes.\n\n" + BEGIN))
        self.assertTrue(claude.endswith(END + "\n"))
        self.assertIn("이 에이전트의 이름은 `claude`", claude)
        self.assertIn("goal-loop-judge --judge codex goal <goal-id>", claude)
        self.assertIn("verification-begin", claude)
        self.assertIn("`goal-loop-planning` 스킬", claude)
        self.assertNotIn("memory_tool", claude)  # no Memory Tool knowledge folder in this project
        # Codex judging its own work would be recorded under its own name, so AGENTS.md hands judging to Claude.
        self.assertIn("이 에이전트의 이름은 `codex`", files["AGENTS.md"])
        self.assertIn("goal-loop-judge --judge claude goal", files["AGENTS.md"])
        self.assertEqual(result["judges_by_agent"], {"claude": "codex", "codex": "claude"})
        self.assertEqual(result["judge"], "codex")

    def test_rerun_is_idempotent_and_keeps_user_content(self):
        run("project-init", str(self.project), "--agents", "claude", "--judge", "codex")
        claude = self.project / "CLAUDE.md"
        claude.write_text("Before.\n\n" + claude.read_text(encoding="utf-8") + "\nAfter.\n", encoding="utf-8")
        before = self.files()
        again = run("project-init", str(self.project), "--agents", "claude", "--judge", "codex")
        self.assertEqual({item["status"] for item in again["agent_files"]}, {"unchanged"})
        self.assertEqual(self.files(), before)
        # Changing the judge replaces only the marked section.
        changed = run("project-init", str(self.project), "--agents", "claude", "--judge", "glm", "--glm-env-ps1", str(self.ps1))
        self.assertIn("updated", {item["status"] for item in changed["agent_files"]})
        text = claude.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("Before.\n\n" + BEGIN))
        self.assertTrue(text.endswith(END + "\n\nAfter.\n"))
        self.assertEqual(text.count(BEGIN), 1)
        self.assertIn("goal-loop-judge --judge glm goal", text)

    def test_existing_different_skill_is_kept(self):
        skill = self.project / ".claude/skills/goal-loop/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("my own skill\n", encoding="utf-8")
        result = run("project-init", str(self.project), "--agents", "claude")
        statuses = {Path(item["path"]).relative_to(self.project).as_posix(): item["status"] for item in result["agent_files"]}
        self.assertEqual(statuses[".claude/skills/goal-loop/SKILL.md"], "kept")
        self.assertEqual(statuses[".claude/skills/goal-loop-planning/SKILL.md"], "created")
        self.assertIn("kept", result["note"])
        self.assertEqual(skill.read_text(encoding="utf-8"), "my own skill\n")

    def test_memory_steps_appear_when_memory_tool_folder_exists(self):
        (self.project / ".memory/timeline").mkdir(parents=True)
        run("project-init", str(self.project), "--agents", "claude")
        text = (self.project / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("python -m memory_tool context", text)
        self.assertIn("memory_tool record", skill_text())  # the memory_draft step moved to the skill

    def test_venv_commands_are_used_when_the_project_has_the_cli(self):
        (self.project / ".venv/Scripts").mkdir(parents=True)
        (self.project / ".venv/Scripts/goal-loop.exe").write_bytes(b"")
        run("project-init", str(self.project), "--agents", "claude")
        text = (self.project / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(r"`.\.venv\Scripts\goal-loop.exe where`", text)
        self.assertIn(r".\.venv\Scripts\goal-loop-judge.exe --judge codex", text)
        self.assertIn(r"`goal-loop` 대신 `.\.venv\Scripts\goal-loop.exe`", text)

    def test_section_without_end_marker_is_refused(self):
        (self.project / "CLAUDE.md").write_text("x\n" + BEGIN + "\nbroken\n", encoding="utf-8")
        with redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit):
            run("project-init", str(self.project), "--agents", "claude")
        self.assertIn("끝 표지", error.getvalue())

    def test_judge_and_glm_settings_merge_with_existing_keys(self):
        run("project-init", str(self.project))
        directory = self.project / ".goal-loop"
        (directory / "judging.json").write_text(json.dumps({"rules": "notes", "copy_exclude": ["data"]}), encoding="utf-8")
        (directory / "judges.json").write_text(json.dumps({"claude": {"model": "sonnet"}, "glm": {"model": "opus"}}), encoding="utf-8")
        with patch("goal_loop_tool.agent_setup.shutil.which", return_value="claude"):
            result = run("project-init", str(self.project), "--judge", "glm", "--glm-env-ps1", str(self.ps1))
        self.assertEqual(result["warnings"], [])
        self.assertNotIn("agent_files", result)
        self.assertEqual(json.loads((directory / "judging.json").read_text(encoding="utf-8")),
                         {"rules": "notes", "copy_exclude": ["data"], "judge": "glm"})
        self.assertEqual(json.loads((directory / "judges.json").read_text(encoding="utf-8")),
                         {"claude": {"model": "sonnet"}, "glm": {"model": "opus", "env_from_ps1": self.ps1.as_posix()}})
        config = judging_config(directory / "journal.sqlite3")
        self.assertEqual((config.judge, config.rules), ("glm", "notes"))

    def test_glm_without_connection_and_missing_cli_warn(self):
        with patch("goal_loop_tool.agent_setup.shutil.which", return_value=None):
            result = run("project-init", str(self.project), "--judge", "glm")
        self.assertEqual(len(result["warnings"]), 2)
        self.assertIn("--glm-env-ps1", result["warnings"][1])

    def test_wrong_options_write_nothing(self):
        for args in (("--agents", "cursor"), ("--glm-env-ps1", str(self.root / "missing.ps1"))):
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                run("project-init", str(self.project), *args)
        self.assertFalse((self.project / ".goal-loop").exists())

    def test_judge_for_avoids_the_agents_own_name(self):
        self.assertEqual([judge_for("claude", judge) for judge in ("codex", "claude", "glm")], ["codex", "claude", "glm"])
        self.assertEqual([judge_for("codex", judge) for judge in ("codex", "claude", "glm")], ["claude", "claude", "glm"])


class JudgeDefaultTests(unittest.TestCase):
    """goal-loop-judge follows judging.json's judge when --judge is not given."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        (self.root / ".goal-loop").mkdir()
        self.database = self.root / ".goal-loop/journal.sqlite3"

    def tearDown(self):
        self.directory.cleanup()

    def judge_used(self, *args: str) -> tuple[str, str]:
        seen = {}

        def runner_for(judge, database):
            seen["judge"] = judge
            return object()

        def judge_goal(database, goal_id, *, agent, **kwargs):
            seen["agent"] = agent
            return {}

        with patch("goal_loop_tool.delegated_judging.runner_for", runner_for), \
             patch("goal_loop_tool.delegated_judging.judge_goal", judge_goal), redirect_stdout(StringIO()):
            judging_main(["--database", str(self.database), *args, "goal", "goal-1"])
        return seen["judge"], seen["agent"]

    def test_default_is_codex_without_config(self):
        self.assertEqual(self.judge_used(), ("codex", "codex"))

    def test_config_judge_is_used_and_flag_overrides_it(self):
        (self.root / ".goal-loop/judging.json").write_text(json.dumps({"judge": "claude"}), encoding="utf-8")
        self.assertEqual(self.judge_used(), ("claude", "claude-judge"))
        self.assertEqual(self.judge_used("--judge", "glm"), ("glm", "glm"))

    def test_wrong_config_judge_stops_before_a_copy(self):
        (self.root / ".goal-loop/judging.json").write_text(json.dumps({"judge": "gemini"}), encoding="utf-8")
        with self.assertRaises(JudgingError):
            judging_config(self.database)
        with redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit):
            self.judge_used()
        self.assertIn("judge", error.getvalue())


if __name__ == "__main__":
    unittest.main()
