"""project-init with an @AGENTS.md layout, hand-written instructions, and shipped skill copies (aim-22)."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool import agent_setup
from goal_loop_tool.agent_setup import BEGIN, END, SKILL_NAMES, instruction_section, released_hashes, skill_files
from goal_loop_tool.persistent_cli import main

REPO = Path(__file__).resolve().parents[1]
ORIGINAL_ENV = dict(os.environ)  # the tests below clear the environment; git needs PATH


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


class LayoutTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.env = patch.dict("os.environ", {"LOCALAPPDATA": str(self.root / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(self.root)}, clear=True)
        self.env.start()
        self.project = self.root / "repo"
        self.project.mkdir()

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, name: str) -> str:
        return (self.project / name).read_text(encoding="utf-8")

    def statuses(self, result: dict) -> dict[str, str]:
        return {Path(item["path"]).relative_to(self.project).as_posix(): item["status"] for item in result["agent_files"]
                if not Path(item["path"]).relative_to(self.project).as_posix().startswith((".claude/skills", ".agents/skills"))}

    def init(self, *args: str) -> dict:
        return run("project-init", str(self.project), *args)


class SharedSectionTests(LayoutTestCase):
    """goal-170: a CLAUDE.md that imports AGENTS.md gets one shared section in AGENTS.md and none of its own."""

    def test_import_layout_writes_one_shared_section_in_agents_md(self):
        self.write("CLAUDE.md", "@AGENTS.md\n")
        self.write("AGENTS.md", "# Project\n\nRules.\n")
        result = self.init("--agents", "claude,codex", "--judge", "codex")
        self.assertEqual(self.statuses(result), {"AGENTS.md": "appended"})
        self.assertIn("AGENTS.md", result["instructions_layout"])
        self.assertEqual(self.read("CLAUDE.md"), "@AGENTS.md\n")
        agents = self.read("AGENTS.md")
        self.assertTrue(agents.startswith("# Project\n\nRules.\n\n" + BEGIN))
        self.assertTrue(agents.endswith(END + "\n"))
        self.assertEqual(agents.count(BEGIN), 1)
        self.assertIn("기록 이름은 Claude Code가 `claude`, Codex가 `codex`이다.", agents)
        self.assertIn("스킬은 Claude Code가 `.claude/skills/`, Codex가 `.agents/skills/`에서 읽는다.", agents)
        self.assertIn("Claude가 실행한 일은 `goal-loop-judge --judge codex goal <goal-id>`", agents)
        self.assertIn("Codex가 실행한 일은 `goal-loop-judge --judge claude goal <goal-id>`", agents)
        self.assertIn("`aim <aim-id>`", agents)
        self.assertNotIn("이 에이전트의 이름은", agents)
        self.assertEqual(result["judges_by_agent"], {"claude": "codex", "codex": "claude"})
        # The judge in each sentence is the one judges_by_agent reports.
        for agent, judge in result["judges_by_agent"].items():
            self.assertIn(f"{agent_setup.AGENT_SHORT[agent]}가 실행한 일은 `goal-loop-judge --judge {judge} goal", agents)

    def test_dot_slash_import_is_recognised_and_agents_md_is_created(self):
        self.write("CLAUDE.md", "# Claude\n\n@./AGENTS.md\n")
        result = self.init("--agents", "claude,codex")
        self.assertEqual(self.statuses(result), {"AGENTS.md": "created"})
        self.assertEqual(self.read("CLAUDE.md"), "# Claude\n\n@./AGENTS.md\n")
        self.assertTrue(self.read("AGENTS.md").startswith(BEGIN))

    def test_an_inline_mention_is_not_an_import(self):
        self.write("CLAUDE.md", "See @AGENTS.md for the rules.\n")
        result = self.init("--agents", "claude,codex")
        self.assertEqual(self.statuses(result), {"CLAUDE.md": "appended", "AGENTS.md": "created"})
        self.assertNotIn("instructions_layout", result)
        self.assertIn("이 에이전트의 이름은 `claude`", self.read("CLAUDE.md"))

    def test_an_old_generated_section_in_claude_md_is_removed(self):
        old = instruction_section("claude", "codex", self.project)
        self.write("CLAUDE.md", "@AGENTS.md\n\nClaude only.\n\n" + old)
        result = self.init("--agents", "claude,codex")
        self.assertEqual(self.statuses(result), {"AGENTS.md": "created", "CLAUDE.md": "removed"})
        self.assertEqual(self.read("CLAUDE.md"), "@AGENTS.md\n\nClaude only.\n")
        # A section between two blocks of own text leaves the text around it.
        self.write("CLAUDE.md", "@AGENTS.md\n\n" + old + "\nAfter.\n")
        self.assertEqual(self.statuses(self.init("--agents", "claude,codex"))["CLAUDE.md"], "removed")
        self.assertEqual(self.read("CLAUDE.md"), "@AGENTS.md\n\nAfter.\n")

    def test_one_agent_is_named_alone(self):
        self.write("CLAUDE.md", "@AGENTS.md\n")
        self.init("--agents", "claude", "--judge", "codex")
        agents = self.read("AGENTS.md")
        self.assertIn("기록 이름은 Claude Code가 `claude`이다.", agents)
        self.assertIn("스킬은 Claude Code가 `.claude/skills/`에서 읽는다.", agents)
        self.assertIn("Claude가 실행한 일은 `goal-loop-judge --judge codex goal <goal-id>`.", agents)
        self.assertNotIn("Codex", agents)
        self.write("AGENTS.md", "")
        self.init("--agents", "codex", "--judge", "codex")
        agents = self.read("AGENTS.md")
        self.assertIn("기록 이름은 Codex가 `codex`이다.", agents)
        self.assertIn("Codex가 실행한 일은 `goal-loop-judge --judge claude goal <goal-id>`.", agents)
        self.assertNotIn("Claude Code", agents)

    def test_rerun_is_unchanged_and_a_new_judge_changes_only_the_shared_section(self):
        self.write("CLAUDE.md", "@AGENTS.md\n")
        self.write("AGENTS.md", "Before.\n")
        self.init("--agents", "claude,codex", "--judge", "codex")
        self.write("AGENTS.md", self.read("AGENTS.md") + "\nAfter.\n")
        again = self.init("--agents", "claude,codex", "--judge", "codex")
        self.assertEqual(self.statuses(again), {"AGENTS.md": "unchanged"})
        ps1 = self.root / "set-glm.ps1"
        ps1.write_text("$env:ANTHROPIC_BASE_URL = 'https://example.invalid'\n", encoding="utf-8")
        changed = self.init("--agents", "claude,codex", "--judge", "glm", "--glm-env-ps1", str(ps1))
        self.assertEqual(self.statuses(changed), {"AGENTS.md": "updated"})
        agents = self.read("AGENTS.md")
        self.assertTrue(agents.startswith("Before.\n\n" + BEGIN))
        self.assertTrue(agents.endswith(END + "\n\nAfter.\n"))
        self.assertIn("Claude가 실행한 일은 `goal-loop-judge --judge glm goal", agents)
        self.assertIn("Codex가 실행한 일은 `goal-loop-judge --judge glm goal", agents)
        self.assertEqual(self.read("CLAUDE.md"), "@AGENTS.md\n")

    def test_skills_only_leaves_an_import_layout_alone(self):
        self.write("CLAUDE.md", "@AGENTS.md\n")
        result = self.init("--agents", "claude,codex", "--skills-only")
        self.assertEqual(self.statuses(result), {})
        self.assertFalse((self.project / "AGENTS.md").exists())
        self.assertNotIn("instructions_layout", result)


class HandwrittenInstructionTests(LayoutTestCase):
    """goal-171: Goal Loop instructions written by hand stop a second, generated section."""

    def test_handwritten_section_is_not_doubled(self):
        own = "# Project\n\n## Goal Loop\n\nStart with `goal-loop next`.\n"
        self.write("AGENTS.md", own)
        result = self.init("--agents", "codex")
        self.assertEqual(self.statuses(result), {"AGENTS.md": "skipped"})
        self.assertEqual(self.read("AGENTS.md"), own)
        warning = next(item for item in result["warnings"] if "직접 쓴 Goal Loop 지침" in item)
        self.assertIn(str(self.project / "AGENTS.md"), warning)
        self.assertIn("(AGENTS.md 5행)", warning)  # the heading on line 3 alone does not count (Contract v3)
        self.assertIn("--skills-only", warning)
        self.assertIn("지운 뒤 다시 실행", warning)

    def test_commands_count_but_skill_names_headings_and_prose_do_not(self):
        detected = ["goal-loop where", "goal-loop-judge --judge codex goal goal-1", r".\.venv\Scripts\goal-loop.exe status",
                    "- Run `goal-loop next` first.", "    goal-loop -h"]
        # Contract v3: a heading alone is not a hand-written instruction; the commands are what a second section repeats.
        ignored = ["Follow the `goal-loop-planning` skill.", "the goal-loop skill", "Use goal-loop to track work.",
                   "`goal-loop` 스킬을 따른다.", "# My project", "## Goal Loop", "Goal Loop\n=========",
                   "We track work with Goal Loop."]
        for text in detected:
            self.write("AGENTS.md", text + "\n")
            self.assertEqual(agent_setup.handwritten_lines(self.project / "AGENTS.md"), [1], text)
        for text in ignored:
            self.write("AGENTS.md", text + "\n")
            self.assertEqual(agent_setup.handwritten_lines(self.project / "AGENTS.md"), [], text)
        result = self.init("--agents", "codex")
        self.assertEqual(self.statuses(result), {"AGENTS.md": "appended"})
        self.assertFalse(any("직접 쓴" in item for item in result["warnings"]))

    def test_every_goal_loop_subcommand_counts(self):
        # The command names come from the CLI parser, so doctor, reviews and any later command are covered (verdict-252).
        import argparse
        from goal_loop_tool.persistent_cli import build_parser
        names = next(action.choices for action in build_parser()._actions if isinstance(action, argparse._SubParsersAction))
        self.assertIn("doctor", names)
        self.assertIn("reviews", names)
        options = ["-h", "--help", "--judge codex"]
        commands = [*(f"goal-loop {name}" for name in [*names, *options]),
                    *(rf".\.venv\Scripts\goal-loop.exe {name}" for name in [*names, *options]),
                    *(f"goal-loop-judge {name}" for name in ["goal", "aim", *options]),
                    *(rf".\.venv\Scripts\goal-loop-judge.exe {name}" for name in ["goal", "aim", *options])]
        for command in commands:
            self.write("AGENTS.md", f"Run `{command}`.\n")
            self.assertEqual(agent_setup.handwritten_lines(self.project / "AGENTS.md"), [1], command)
        # goal-loop-judge has only goal and aim, and a subcommand ends where the word ends (verdict-259).
        for text in ["goal-loop-judge next", "goal-loop-judge status", "goal-loop goal", "goal-loop next-generation tools",
                     "goal-loop-judge goal_x", "goal-loop status-quo", "goal-loop-judge aim-free",
                     "my-goal-loop next", "xgoal-loop status", "nogoal-loop-judge goal"]:
            self.write("AGENTS.md", text + "\n")
            self.assertEqual(agent_setup.handwritten_lines(self.project / "AGENTS.md"), [], text)
        self.write("AGENTS.md", "Run `goal-loop doctor` and `goal-loop.exe reviews`.\n")
        result = self.init("--agents", "codex")
        self.assertEqual(self.statuses(result), {"AGENTS.md": "skipped"})
        self.assertEqual(self.read("AGENTS.md"), "Run `goal-loop doctor` and `goal-loop.exe reviews`.\n")
        # Next to an existing generated section the same command only warns.
        self.write("AGENTS.md", "")
        self.init("--agents", "codex")
        self.write("AGENTS.md", "Run `goal-loop doctor`.\n\n" + self.read("AGENTS.md"))
        again = self.init("--agents", "codex")
        self.assertEqual(self.statuses(again), {"AGENTS.md": "unchanged"})
        self.assertTrue(any("직접 쓴 Goal Loop 지침" in item and "1행" in item for item in again["warnings"]))

    def test_the_generated_section_itself_is_not_handwritten(self):
        self.init("--agents", "claude,codex")
        again = self.init("--agents", "claude,codex")
        self.assertEqual(set(self.statuses(again).values()), {"unchanged"})
        self.assertFalse(any("직접 쓴" in item for item in again["warnings"]))

    def test_an_existing_section_is_updated_with_a_warning(self):
        self.init("--agents", "codex", "--judge", "codex")
        self.write("AGENTS.md", "Run `goal-loop status` first.\n\n" + self.read("AGENTS.md"))
        result = self.init("--agents", "codex", "--judge", "glm", "--glm-env-ps1", str(self._ps1()))
        self.assertEqual(self.statuses(result), {"AGENTS.md": "updated"})
        self.assertIn("goal-loop-judge --judge glm", self.read("AGENTS.md"))
        warning = next(item for item in result["warnings"] if "직접 쓴 Goal Loop 지침" in item)
        self.assertIn("1행", warning)
        self.assertIn("겹치지 않는지", warning)
        # Both ways out, as when no section exists yet (verdict-260).
        self.assertIn("--skills-only", warning)
        self.assertIn("직접 쓴 절을 지운 뒤 다시 실행", warning)

    def test_import_layout_checks_both_files(self):
        self.write("CLAUDE.md", "@AGENTS.md\n\n- Run `goal-loop where` first.\n")
        result = self.init("--agents", "claude,codex")
        self.assertEqual(self.statuses(result), {"AGENTS.md": "skipped"})
        self.assertFalse((self.project / "AGENTS.md").exists())
        warning = next(item for item in result["warnings"] if "직접 쓴 Goal Loop 지침" in item)
        self.assertIn(f"{self.project / 'CLAUDE.md'} 3행", warning)

    def _ps1(self) -> Path:
        ps1 = self.root / "set-glm.ps1"
        ps1.write_text("$env:ANTHROPIC_BASE_URL = 'https://example.invalid'\n", encoding="utf-8")
        return ps1


class ReleasedSkillCopyTests(LayoutTestCase):
    """goal-172: a copy equal to a version this tool shipped is replaced; an edited copy is kept."""

    def test_every_current_template_is_listed_and_history_is_kept(self):
        released = released_hashes()
        current = {f"{name}/{relative}": agent_setup._content_hash(text)
                   for name in SKILL_NAMES for relative, text in skill_files(name).items()}
        self.assertEqual(set(released), set(current))
        missing = [key for key, digest in current.items() if digest not in released[key]]
        self.assertEqual(missing, [], "run python scripts/update_released_skill_hashes.py after changing a template")
        self.assertGreaterEqual(len(released["goal-loop/SKILL.md"]), 2)

    def test_hash_ignores_line_endings(self):
        self.assertEqual(agent_setup._content_hash("a\r\nb\r\n"), agent_setup._content_hash("a\nb\n"))

    def test_shipped_copy_is_replaced_and_edited_copy_is_kept(self):
        shipped = "an older shipped goal-loop skill\nsecond line\n"
        old_reference = "an older shipped reference\n"
        released = released_hashes()
        released["goal-loop/SKILL.md"].add(agent_setup._content_hash(shipped))
        released["goal-loop-planning/references/when-stuck.md"].add(agent_setup._content_hash(old_reference))
        # The Claude copy was written on Windows with CRLF; the Codex copy was edited by the user.
        claude_skill = self.project / ".claude/skills/goal-loop/SKILL.md"
        claude_skill.parent.mkdir(parents=True)
        claude_skill.write_bytes(shipped.replace("\n", "\r\n").encode("utf-8"))
        self.write(".agents/skills/goal-loop/SKILL.md", shipped + "my own rule\n")
        self.write(".claude/skills/goal-loop-planning/references/when-stuck.md", old_reference)
        with patch("goal_loop_tool.agent_setup.released_hashes", return_value=released):
            result = self.init("--agents", "claude,codex", "--skills-only")
        statuses = {Path(item["path"]).relative_to(self.project).as_posix(): item["status"] for item in result["agent_files"]}
        self.assertEqual(statuses[".claude/skills/goal-loop/SKILL.md"], "replaced")
        self.assertEqual(statuses[".claude/skills/goal-loop-planning/references/when-stuck.md"], "replaced")
        self.assertEqual(statuses[".agents/skills/goal-loop/SKILL.md"], "kept")
        self.assertEqual(claude_skill.read_text(encoding="utf-8"), skill_files("goal-loop")["SKILL.md"])
        self.assertEqual(self.read(".claude/skills/goal-loop-planning/references/when-stuck.md"),
                         skill_files("goal-loop-planning")["references/when-stuck.md"])
        self.assertEqual(self.read(".agents/skills/goal-loop/SKILL.md"), shipped + "my own rule\n")
        self.assertIn("replaced:", result["note"])
        self.assertIn("kept:", result["note"])
        again = self.init("--agents", "claude", "--skills-only")
        self.assertNotIn("replaced", {item["status"] for item in again["agent_files"]})

    def test_hash_list_is_package_data(self):
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"agent_files/*.json"', pyproject)
        self.assertTrue((REPO / "goal_loop_tool" / agent_setup.RELEASED_HASHES_FILE).is_file())

    @unittest.skipUnless((REPO / ".git").exists() and shutil.which("git"), "needs the git history of this repository")
    def test_hash_list_matches_git_history(self):
        script = REPO / "scripts" / "update_released_skill_hashes.py"
        completed = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True, env=ORIGINAL_ENV)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
