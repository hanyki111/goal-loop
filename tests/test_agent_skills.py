from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AgentSkillTests(unittest.TestCase):
    def test_claude_and_codex_skill_copies_stay_identical(self):
        # .claude/skills is the source; .agents/skills is the Codex copy and must not drift.
        source = (ROOT / ".claude" / "skills" / "goal-loop" / "SKILL.md").read_text(encoding="utf-8")
        copy = (ROOT / ".agents" / "skills" / "goal-loop" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(source, copy)


if __name__ == "__main__":
    unittest.main()
