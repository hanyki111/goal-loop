from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from unittest.mock import patch
import unittest

from goal_loop_tool.claude_runtime import ClaudePrintProfile
from goal_loop_tool.model import AttemptOutcome
from goal_loop_tool.persistent_cli import main
from goal_loop_tool.subprocess_runtime import RuntimeExecution


class ClaudeRunCliTests(unittest.TestCase):
    def call(self, database: Path, *args: str) -> dict:
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(database), *args])
        return json.loads(output.getvalue().splitlines()[-1])

    def test_profile_is_restricted_noninteractive_json_and_bounded(self) -> None:
        invocation = ClaudePrintProfile(model="sonnet", max_turns=3, max_budget_usd=0.25).invocation("inspect", Path("."))
        self.assertEqual(
            invocation.argv,
            ("claude", "-p", "--restricted", "--permission-prompts", "none", "--output-format", "json", "--no-session-persistence", "--max-turns", "3", "--max-budget-usd", "0.25", "--model", "sonnet", "inspect"),
        )
        with self.assertRaises(ValueError):
            ClaudePrintProfile(model="sonnet", max_turns=0, max_budget_usd=0.25).invocation("inspect", Path("."))
        with self.assertRaises(ValueError):
            ClaudePrintProfile(model="", max_turns=3, max_budget_usd=0.25).invocation("inspect", Path("."))
        with self.assertRaises(ValueError):
            ClaudePrintProfile(model="sonnet", max_turns=3, max_budget_usd=0).invocation("inspect", Path("."))

    def test_explicit_run_delivers_result_without_real_claude(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory); database = root / "goal.sqlite3"
            aim = self.call(database, "aim-create", "run Claude")
            proposal = self.call(database, "proposal-create", aim["id"], "run a tool")
            goal = self.call(database, "goal-accept", proposal["id"], "--target", "result", "--responsibility", "investigate", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "claude-run")
            attempt = self.call(database, "attempt-start", goal["id"], "1", "claude", "inspect")
            execution = RuntimeExecution("delivery-1", AttemptOutcome.SUCCEEDED, 0, '{"result":"final answer"}', "", False)
            with patch("goal_loop_tool.persistent_cli.SubprocessRuntime.run", return_value=execution) as run:
                result = self.call(database, "claude-run", attempt["id"], "inspect this", "--workdir", str(root), "--model", "sonnet", "--max-turns", "3", "--max-budget-usd", "0.25", "--delivery-id", "delivery-1")
            self.assertEqual(result["attempt"]["outcome"], "succeeded")
            self.assertEqual(result["execution"]["stdout"], '{"result":"final answer"}')
            self.assertEqual(run.call_count, 1)

    def test_run_rejects_missing_model_turn_or_budget_before_starting_a_process(self) -> None:
        with TemporaryDirectory() as directory:
            error = StringIO()
            with redirect_stderr(error), self.assertRaises(SystemExit) as exited:
                main(["claude-run", "attempt-1", "inspect this", "--workdir", directory])
            self.assertEqual(exited.exception.code, 2)
            self.assertIn("--model", error.getvalue())
            self.assertIn("--max-turns", error.getvalue())
            self.assertIn("--max-budget-usd", error.getvalue())
