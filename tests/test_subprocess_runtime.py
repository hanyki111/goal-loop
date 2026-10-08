from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

from goal_loop_tool.codex_runtime import CodexExecProfile
from goal_loop_tool.model import AttemptOutcome, GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft
from goal_loop_tool.subprocess_runtime import RuntimeInvocation, SubprocessRuntime, deliver_execution


class SubprocessRuntimeTests(unittest.TestCase):
    def test_success_failure_and_timeout_are_plain_process_facts(self) -> None:
        runtime = SubprocessRuntime()
        with TemporaryDirectory() as directory:
            cwd = Path(directory)
            success = runtime.run(RuntimeInvocation((sys.executable, "-c", "print('ok')"), cwd), delivery_id="success")
            failure = runtime.run(RuntimeInvocation((sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(4)"), cwd), delivery_id="failure")
            timeout = runtime.run(RuntimeInvocation((sys.executable, "-c", "import time; time.sleep(1)"), cwd, 0.01), delivery_id="timeout")
            self.assertEqual(success.outcome, AttemptOutcome.SUCCEEDED)
            self.assertEqual(success.stdout.strip(), "ok")
            self.assertEqual(failure.outcome, AttemptOutcome.FAILED)
            self.assertEqual(failure.exit_code, 4)
            self.assertIn("bad", failure.stderr)
            self.assertTrue(timeout.timed_out)

    def test_utf8_child_output_is_not_decoded_using_windows_console_code_page(self) -> None:
        with TemporaryDirectory() as directory:
            source = (
                "import sys; "
                "sys.stdout.buffer.write('한글 출력'.encode('utf-8')); "
                "sys.stderr.buffer.write('오류 출력'.encode('utf-8'))"
            )
            execution = SubprocessRuntime().run(
                RuntimeInvocation((sys.executable, "-c", source), Path(directory)),
                delivery_id="utf8-output",
            )
            self.assertEqual(execution.outcome, AttemptOutcome.SUCCEEDED)
            self.assertEqual(execution.stdout, "한글 출력")
            self.assertEqual(execution.stderr, "오류 출력")

    def test_delivery_uses_durable_runtime_delivery_identity(self) -> None:
        with TemporaryDirectory() as directory:
            loop = PersistentGoalLoop(SqliteEventRepository(Path(directory) / "goal.sqlite3"))
            aim = loop.create_aim("runtime")
            proposal = loop.create_proposal(aim.id, "run command")
            goal = loop.accept_proposal(proposal.id, GoalIdentity(aim.id, "target", "produce"), ContractDraft("scope", "criterion", "verify"), idempotency_key="runtime-subprocess")
            attempt = loop.start_attempt(goal.id, 1, "runtime", "command")
            execution = SubprocessRuntime().run(RuntimeInvocation((sys.executable, "-c", "pass"), Path(directory)), delivery_id="delivery-1")
            self.assertEqual(deliver_execution(loop, attempt.id, execution).outcome, AttemptOutcome.SUCCEEDED)

    def test_codex_profile_is_explicit_and_never_uses_danger_full_access(self) -> None:
        invocation = CodexExecProfile(sandbox="workspace-write").invocation("inspect", Path("."))
        self.assertEqual(invocation.argv[:4], ("codex", "exec", "--sandbox", "workspace-write"))
        with self.assertRaises(ValueError):
            CodexExecProfile(sandbox="danger-full-access").invocation("inspect", Path("."))
        with self.assertRaises(ValueError):
            CodexExecProfile(extra_args=("--dangerously-bypass-approvals-and-sandbox",)).invocation("inspect", Path("."))
