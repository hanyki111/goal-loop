from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool import agent_setup
from goal_loop_tool.agent_setup import BEGIN, END, judge_for, skill_text
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
        for relative in (".claude/skills/goal-loop/SKILL.md", ".agents/skills/goal-loop/SKILL.md"):
            self.assertEqual(skill_text(), (REPO / relative).read_text(encoding="utf-8"), relative)

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
        self.assertEqual(statuses, {".claude/skills/goal-loop/SKILL.md": "created", "CLAUDE.md": "appended",
                                    ".agents/skills/goal-loop/SKILL.md": "created", "AGENTS.md": "created"})
        files = self.files()
        self.assertEqual(files[".claude/skills/goal-loop/SKILL.md"], skill_text())
        self.assertEqual(files[".agents/skills/goal-loop/SKILL.md"], skill_text())
        claude = files["CLAUDE.md"]
        self.assertTrue(claude.startswith("# My project\n\nOwn notes.\n\n" + BEGIN))
        self.assertTrue(claude.endswith(END + "\n"))
        self.assertIn("이 에이전트의 이름은 `claude`", claude)
        self.assertIn("goal-loop-judge --judge codex goal <goal-id>", claude)
        self.assertIn("verification-begin", claude)
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
        self.assertEqual(result["agent_files"][0]["status"], "kept")
        self.assertIn("kept", result["note"])
        self.assertEqual(skill.read_text(encoding="utf-8"), "my own skill\n")

    def test_memory_steps_appear_when_memory_tool_folder_exists(self):
        (self.project / ".memory/timeline").mkdir(parents=True)
        run("project-init", str(self.project), "--agents", "claude")
        text = (self.project / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("python -m memory_tool context", text)
        self.assertIn("memory-draft", text)
        self.assertIn("python -m memory_tool record", text)

    def test_venv_commands_are_used_when_the_project_has_the_cli(self):
        (self.project / ".venv/Scripts").mkdir(parents=True)
        (self.project / ".venv/Scripts/goal-loop.exe").write_bytes(b"")
        run("project-init", str(self.project), "--agents", "claude")
        text = (self.project / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(r"`.\.venv\Scripts\goal-loop.exe where`", text)
        self.assertIn(r".\.venv\Scripts\goal-loop-judge.exe --judge codex", text)

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
