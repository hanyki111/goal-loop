from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from unittest.mock import patch
import unittest

from goal_loop_tool.model import AttemptOutcome
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.persistent_cli import main
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.subprocess_runtime import RuntimeExecution


class CodexRunCliTests(unittest.TestCase):
    def call(self, database: Path, *args: str) -> dict:
        output = StringIO()
        with redirect_stdout(output):
            main(["--database", str(database), *args])
        return json.loads(output.getvalue().splitlines()[-1])

    def test_explicit_run_delivers_result_without_real_codex(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory); database = root / "goal.sqlite3"
            aim = self.call(database, "aim-create", "run Codex")
            proposal = self.call(database, "proposal-create", aim["id"], "run a tool")
            goal = self.call(database, "goal-accept", proposal["id"], "--target", "result", "--responsibility", "produce", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "codex-run")
            attempt = self.call(database, "attempt-start", goal["id"], "1", "codex", "inspect")
            execution = RuntimeExecution("delivery-1", AttemptOutcome.SUCCEEDED, 0, "final answer", "", False)
            with patch("goal_loop_tool.persistent_cli.SubprocessRuntime.run", return_value=execution) as run:
                result = self.call(database, "codex-run", attempt["id"], "inspect this", "--workdir", str(root), "--sandbox", "read-only", "--delivery-id", "delivery-1")
            self.assertEqual(result["attempt"]["outcome"], "succeeded")
            self.assertEqual(result["execution"]["stdout"], "final answer")
            self.assertEqual(run.call_count, 1)

    def test_missing_codex_executable_is_a_friendly_error_and_keeps_attempt_running(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory); database = root / "goal.sqlite3"
            aim = self.call(database, "aim-create", "run Codex")
            proposal = self.call(database, "proposal-create", aim["id"], "run a tool")
            goal = self.call(database, "goal-accept", proposal["id"], "--target", "result", "--responsibility", "produce", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "codex-run")
            attempt = self.call(database, "attempt-start", goal["id"], "1", "codex", "inspect")
            error = StringIO()
            with patch("goal_loop_tool.persistent_cli.SubprocessRuntime.run", side_effect=FileNotFoundError()), redirect_stderr(error):
                with self.assertRaises(SystemExit) as exited:
                    main(["--database", str(database), "codex-run", attempt["id"], "inspect this", "--workdir", str(root)])
            self.assertEqual(exited.exception.code, 2)
            self.assertIn("Codex executable was not found", error.getvalue())
            restored = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertEqual(restored.attempts[attempt["id"]].outcome, AttemptOutcome.RUNNING)

    def test_powershell_script_executable_is_a_friendly_error_and_keeps_attempt_running(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory); database = root / "goal.sqlite3"
            aim = self.call(database, "aim-create", "run Codex")
            proposal = self.call(database, "proposal-create", aim["id"], "run a tool")
            goal = self.call(database, "goal-accept", proposal["id"], "--target", "result", "--responsibility", "produce", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "codex-run")
            attempt = self.call(database, "attempt-start", goal["id"], "1", "codex", "inspect")
            error = StringIO()
            with patch("goal_loop_tool.persistent_cli.SubprocessRuntime.run", side_effect=OSError(193, "not a valid application")), redirect_stderr(error):
                with self.assertRaises(SystemExit) as exited:
                    main(["--database", str(database), "codex-run", attempt["id"], "inspect this", "--workdir", str(root), "--executable", "codex.ps1"])
            self.assertEqual(exited.exception.code, 2)
            self.assertIn(".ps1 PowerShell script", error.getvalue())
            restored = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertEqual(restored.attempts[attempt["id"]].outcome, AttemptOutcome.RUNNING)
