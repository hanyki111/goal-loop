from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome
from goal_loop_tool.repository import RuntimeOutboxRequest
from goal_loop_tool.runtime_profiles import RuntimePayloadError, execute_profile_request
from goal_loop_tool.subprocess_runtime import RuntimeExecution


class FakeRuntime:
    def __init__(self) -> None:
        self.invocations = []

    def run(self, invocation, *, delivery_id=None):
        self.invocations.append(invocation)
        return RuntimeExecution("delivery-1", AttemptOutcome.SUCCEEDED, 0, "ok", "", False)


class RuntimeProfilePayloadTests(unittest.TestCase):
    def test_codex_v1_uses_only_the_existing_safe_profile(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = FakeRuntime()
            result = execute_profile_request(
                RuntimeOutboxRequest(
                    "request-1", "attempt-1", "codex_exec.v1",
                    {"prompt": "inspect", "workdir": directory, "sandbox": "read-only"},
                    "pending",
                ), runtime=runtime,
            )
        self.assertEqual(result.outcome, AttemptOutcome.SUCCEEDED)
        self.assertEqual(runtime.invocations[0].argv, ("codex", "exec", "--sandbox", "read-only", "inspect"))

    def test_claude_v1_requires_its_bounded_model_turn_and_budget(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = FakeRuntime()
            execute_profile_request(
                RuntimeOutboxRequest(
                    "request-1", "attempt-1", "claude_print.v1",
                    {"prompt": "inspect", "workdir": directory, "model": "sonnet", "max_turns": 3, "max_budget_usd": 0.25},
                    "pending",
                ), runtime=runtime,
            )
        self.assertIn("--restricted", runtime.invocations[0].argv)
        self.assertIn("--max-budget-usd", runtime.invocations[0].argv)

    def test_unknown_fields_kinds_and_dangerous_codex_options_do_not_run(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = FakeRuntime()
            for kind, payload in (
                ("unknown.v1", {"prompt": "inspect", "workdir": directory}),
                ("codex_exec.v1", {"prompt": "inspect", "workdir": directory, "unexpected": True}),
                ("codex_exec.v1", {"prompt": "inspect", "workdir": directory, "extra_args": ["--danger-full-access"]}),
            ):
                with self.assertRaises((RuntimePayloadError, ValueError)):
                    execute_profile_request(RuntimeOutboxRequest("request", "attempt", kind, payload, "pending"), runtime=runtime)
            self.assertEqual(runtime.invocations, [])
