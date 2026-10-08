from contextlib import chdir, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.persistent_cli import main
from goal_loop_tool.model import AttemptOutcome
from goal_loop_tool.subprocess_runtime import RuntimeExecution


class PersistentCliAcceptanceTests(unittest.TestCase):
    def call(self, path: Path, *args: str) -> dict:
        output = StringIO()
        with redirect_stdout(output): main(["--database", str(path), *args])
        return json.loads(output.getvalue().splitlines()[-1])

    def test_next_respects_closed_proposals_stopped_goals_and_abandoned_aims(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Closure paths")
            first = self.call(path, "proposal-create", aim["id"], "first approach", "--actor", "agent-a")
            self.assertEqual(first["proposed_by_actor_id"], "agent-a")
            self.call(path, "proposal-close", first["id"], "--kind", "declined", "--actor", "owner", "--reason", "not promising")
            kinds = {action["kind"] for action in self.call(path, "next")["actions"]}
            self.assertIn("create_proposal", kinds)
            self.assertNotIn("accept_proposal", kinds)
            second = self.call(path, "proposal-create", aim["id"], "second approach")
            goal = self.call(path, "goal-accept", second["id"], "--target", "target", "--responsibility", "produce", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "closure-cli")
            attempt = self.call(path, "attempt-start", goal["id"], "1", "executor", "in flight")
            self.assertEqual(self.call(path, "goal-stop", goal["id"], "--actor", "owner", "--reason", "dropped")["phase"], "stopped")
            goal_actions = [action for action in self.call(path, "next")["actions"] if action["subject"]["id"] in (goal["id"], attempt["id"])]
            self.assertEqual([action["kind"] for action in goal_actions], ["complete_attempt"])
            self.call(path, "attempt-complete", attempt["id"], "failed")
            kinds = {action["kind"] for action in self.call(path, "next")["actions"]}
            self.assertNotIn("decide_retry", kinds)
            self.assertIn("create_proposal", kinds)
            self.assertEqual(self.call(path, "aim-abandon", aim["id"], "--actor", "owner", "--reason", "out of scope")["phase"], "abandoned")
            self.assertEqual(self.call(path, "next")["actions"], [])
            projection = self.call(path, "ui-projection")
            self.assertEqual(projection["aims"][0]["abandon_reason"], "out of scope")
            self.assertEqual(projection["proposals"][0]["closure_kind"], "declined")
            self.assertEqual(projection["goals"][0]["stop_reason"], "dropped")

    def test_proposal_close_accepts_criteria_and_next_stops_offering_it(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Criteria")
            criteria = self.call(path, "proposal-create", aim["id"], "what counts as achieved", "--actor", "agent-a")
            closed = self.call(path, "proposal-close", criteria["id"], "--kind", "criteria", "--actor", "agent-a", "--reason", "Aim criteria")
            self.assertEqual((closed["status"], closed["closure_kind"]), ("closed", "criteria"))
            self.assertNotIn("accept_proposal", {action["kind"] for action in self.call(path, "next")["actions"]})
            self.assertEqual(self.call(path, "ui-projection")["proposals"][0]["closure_kind"], "criteria")
            other = self.call(path, "proposal-create", aim["id"], "approach")
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(["--database", str(path), "proposal-close", other["id"], "--kind", "postponed", "--actor", "owner", "--reason", "no such kind"])

    def test_lifecycle_status_activity_backup_and_doctor(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"; backup = Path(directory) / "backup.sqlite3"
            self.assertEqual(self.call(path, "init")["schema_version"], 3)
            aim = self.call(path, "aim-create", "CLI aim")
            proposal = self.call(path, "proposal-create", aim["id"], "CLI work")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "produce", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "cli")
            attempt = self.call(path, "attempt-start", goal["id"], "1", "executor", "work")
            self.call(path, "attempt-complete", attempt["id"], "succeeded")
            evidence = self.call(path, "evidence-record", attempt["id"], "observer", "observed")
            self.call(path, "verification-begin", goal["id"], attempt["id"])
            verdict = self.call(path, "verdict-submit", goal["id"], attempt["id"], "verifier", "satisfied", evidence["id"])
            self.assertEqual(verdict["result"], "satisfied")
            self.assertIn(goal["id"], self.call(path, "status")["goals"])
            output = StringIO()
            with redirect_stdout(output): main(["--database", str(path), "activity"])
            self.assertIn("Aim 생성", output.getvalue())
            self.call(path, "backup", str(backup))
            self.assertEqual(self.call(path, "doctor")["integrity"], ["ok"])

    def test_proposal_revise_exposes_successor_without_overwriting_history(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "CLI aim")
            original = self.call(path, "proposal-create", aim["id"], "initial direction")
            replacement = self.call(
                path, "proposal-revise", original["id"], "revised direction",
                "--actor", "owner", "--reason", "new observation",
            )
            self.assertEqual(replacement["direction"], "revised direction")

    def test_retry_decision_is_explicit_and_is_consumed_by_the_next_attempt(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "CLI retry")
            proposal = self.call(path, "proposal-create", aim["id"], "retry work")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "investigate", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "retry-cli")
            failed = self.call(path, "attempt-start", goal["id"], "1", "claude", "inspect")
            self.call(path, "attempt-complete", failed["id"], "failed")
            decision = self.call(path, "retry-decide", goal["id"], failed["id"], "user", "turn limit reached")
            retried = self.call(path, "attempt-start", goal["id"], "1", "claude", "inspect again", "--retry-decision", decision["id"])
            self.assertEqual(retried["outcome"], "running")

    def test_runtime_outbox_cli_is_explicit_and_never_runs_a_process(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Runtime CLI")
            proposal = self.call(path, "proposal-create", aim["id"], "queue work")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "investigate", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "runtime-cli")
            started = self.call(
                path, "runtime-attempt-start", goal["id"], "1", "runtime", "inspect",
                "codex", '{"prompt": "inspect"}', "--request-id", "request-1",
                "--command-id", "runtime-start-1",
            )
            self.assertEqual(started["attempt"]["outcome"], "running")
            self.assertEqual(started["runtime_request"]["status"], "pending")
            self.assertEqual(self.call(path, "runtime-requests")["requests"][0]["request_id"], "request-1")
            claimed = self.call(path, "runtime-claim", "request-1", "worker-a")
            self.assertEqual(claimed["claimed"]["claimed_by"], "worker-a")
            self.assertEqual(self.call(path, "runtime-requests")["requests"], [])
            self.assertEqual(self.call(path, "runtime-request", "request-1")["request"]["status"], "claimed")
            pending = self.call(path, "runtime-requeue", "request-1", "worker-a")
            self.assertEqual(pending["status"], "pending")
            self.call(path, "runtime-claim", "request-1", "worker-a")
            completed = self.call(path, "runtime-ack", "request-1", "worker-a")
            self.assertEqual(completed["status"], "completed")

    def test_runtime_dispatch_uses_only_the_stored_supported_profile(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Dispatch profile")
            proposal = self.call(path, "proposal-create", aim["id"], "queue profile")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "investigate", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "dispatch-profile")
            self.call(
                path, "runtime-attempt-start", goal["id"], "1", "runtime", "inspect",
                "codex_exec.v1", json.dumps({"prompt": "inspect", "workdir": directory}),
                "--request-id", "request-1",
            )
            execution = RuntimeExecution("delivery-1", AttemptOutcome.SUCCEEDED, 0, "done", "", False)
            with patch("goal_loop_tool.persistent_cli.execute_profile_request", return_value=execution) as execute:
                result = self.call(path, "runtime-dispatch", "request-1", "worker-a")
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(result["dispatch"]["attempt"]["outcome"], "succeeded")
            self.assertEqual(result["dispatch"]["acknowledged_request"]["status"], "completed")

    def test_runtime_dispatch_refusal_leaves_the_request_claimed(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Refused profile")
            proposal = self.call(path, "proposal-create", aim["id"], "queue unsafe profile")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "investigate", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "refused-profile")
            self.call(
                path, "runtime-attempt-start", goal["id"], "1", "runtime", "inspect",
                "codex_exec.v1", json.dumps({"prompt": "inspect", "workdir": directory, "extra_args": ["--danger-full-access"]}),
                "--request-id", "request-1",
            )
            error = StringIO()
            with redirect_stderr(error), self.assertRaises(SystemExit) as exited:
                main(["--database", str(path), "runtime-dispatch", "request-1", "worker-a"])
            self.assertEqual(exited.exception.code, 2)
            self.assertIn("Runtime profile refused execution", error.getvalue())
            self.assertEqual(self.call(path, "runtime-request", "request-1")["request"]["status"], "claimed")

    def test_runtime_deliver_recovers_an_observed_result_without_running_a_process(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Recover delivery")
            proposal = self.call(path, "proposal-create", aim["id"], "queue work")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "investigate", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "recover-delivery")
            self.call(path, "runtime-attempt-start", goal["id"], "1", "runtime", "inspect", "codex_exec.v1", json.dumps({"prompt": "inspect", "workdir": directory}), "--request-id", "request-1")
            self.call(path, "runtime-claim", "request-1", "worker-a")
            delivered = self.call(path, "runtime-deliver", "request-1", "worker-a", "succeeded", "delivery-1")
            self.assertEqual(delivered["attempt"]["outcome"], "succeeded")
            self.assertEqual(delivered["acknowledged_request"]["status"], "completed")

    def test_workspace_option_resolves_desktop_registry_by_id_or_name(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "GoalLoopTool"
            root.mkdir()
            database = root / "workspaces" / "workspace-1.sqlite3"
            registry = [{"id": "workspace-1", "name": "개발", "database": str(database)},
                        {"id": "workspace-2", "name": "중복", "database": str(root / "a.sqlite3")},
                        {"id": "workspace-3", "name": "중복", "database": str(root / "b.sqlite3")}]
            (root / "workspaces.json").write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
            with patch.dict("os.environ", {"LOCALAPPDATA": directory}, clear=True):
                output = StringIO()
                with redirect_stdout(output): main(["workspaces"])
                self.assertEqual([item["id"] for item in json.loads(output.getvalue())["workspaces"]], ["workspace-1", "workspace-2", "workspace-3"])
                self.assertFalse((root / "goal-loop.sqlite3").exists(), "listing must not create a journal")
                for selector in ("개발", "workspace-1"):
                    output = StringIO()
                    with redirect_stdout(output): main(["--workspace", selector, "init"])
                    self.assertEqual(Path(json.loads(output.getvalue())["database"]), database)
                output = StringIO()
                with redirect_stdout(output): main(["--workspace", "workspace-3", "init"])
                self.assertEqual(Path(json.loads(output.getvalue())["database"]), root / "b.sqlite3")
                for selector in ("없음", "중복"):
                    with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                        main(["--workspace", selector, "status"])

    def test_default_database_is_user_global_and_environment_override_is_respected(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory):
            with patch.dict("os.environ", {"LOCALAPPDATA": directory, "GOAL_LOOP_CEILING_DIRECTORIES": directory}, clear=True):
                output = StringIO()
                with redirect_stdout(output):
                    main(["init"])
            result = json.loads(output.getvalue())
            expected = Path(directory) / "GoalLoopTool" / "goal-loop.sqlite3"
            self.assertEqual(Path(result["database"]), expected)
            self.assertTrue(expected.exists())
            override = Path(directory) / "separate.sqlite3"
            with patch.dict("os.environ", {"GOAL_LOOP_DATABASE": str(override), "GOAL_LOOP_CEILING_DIRECTORIES": directory}, clear=True):
                output = StringIO()
                with redirect_stdout(output):
                    main(["init"])
            self.assertEqual(Path(json.loads(output.getvalue())["database"]), override)

    def test_guide_and_detailed_help_are_self_service_and_do_not_create_a_database(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "guide.sqlite3"
            output = StringIO()
            with redirect_stdout(output):
                main(["--database", str(path), "guide"])
            self.assertIn("Goal Loop CLI 자습서", output.getvalue())
            self.assertIn("attempt-complete", output.getvalue())
            self.assertIn("runtime-attempt-start", output.getvalue())
            self.assertFalse(path.exists())
            output = StringIO()
            with redirect_stdout(output):
                main(["--database", str(path), "help", "--all"])
            self.assertIn("상세 명령 도움말", output.getvalue())
            self.assertIn("admission:", output.getvalue())

    def test_graph_export_writes_a_local_html_view(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            destination = Path(directory) / "graph.html"
            result = self.call(path, "graph-export", str(destination))
            self.assertEqual(Path(result["graph_export"]), destination)
            self.assertIn("<svg", destination.read_text(encoding="utf-8"))

    def test_ui_projection_is_read_only_versioned_json(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            projection = self.call(path, "ui-projection")
            self.assertEqual(projection, {"schema_version": 15, "project_root": None, "aims": [], "nodes": [], "edges": [], "evidence": [], "proposals": [], "attempts": [], "goals": [], "verdicts": [], "evidence_corrections": [], "impact_reviews": [], "decisions": [], "aim_evaluations": [], "ao_runs": [], "memory_mentions": [], "memory_documents": {}, "memory_drafts": []})

    def test_next_is_read_only_and_projects_lifecycle_actions_for_agents(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            fresh = self.call(path, "next")
            self.assertEqual(fresh["actions"][0]["kind"], "create_aim")
            self.assertTrue(fresh["actions"][0]["requires_user_authorization"])
            self.assertEqual(self.call(path, "status")["checkpoint"], 0)

            aim = self.call(path, "aim-create", "Agent next")
            self.assertEqual(self.call(path, "next")["actions"][0]["kind"], "create_proposal")
            proposal = self.call(path, "proposal-create", aim["id"], "inspect state")
            self.assertEqual(self.call(path, "next")["actions"][0]["kind"], "accept_proposal")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "investigate", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "next-cli")
            first = self.call(path, "next")["actions"][0]
            self.assertEqual(first["kind"], "start_attempt")
            self.assertEqual(first["command_argv"][-4:], [goal["id"], "1", "<executor>", "<operation>"])

            attempt = self.call(path, "attempt-start", goal["id"], "1", "agent", "inspect")
            self.assertEqual(self.call(path, "next")["actions"][0]["kind"], "complete_attempt")
            self.call(path, "attempt-complete", attempt["id"], "failed")
            retry = self.call(path, "next")["actions"][0]
            self.assertEqual(retry["kind"], "decide_retry")
            decision = self.call(path, "retry-decide", goal["id"], attempt["id"], "user", "need another run")
            pending = self.call(path, "next")["actions"][0]
            self.assertEqual(pending["kind"], "start_retry_attempt")
            self.assertEqual(pending["subject"]["id"], decision["id"])

    def test_admission_commands_project_pause_hold_and_dependency_blockers(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Control admission")
            proposal = self.call(path, "proposal-create", aim["id"], "controlled work")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "produce", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "admission-cli")
            self.call(path, "aim-pause", aim["id"], "owner", "waiting for review")
            paused = next(action for action in self.call(path, "next")["actions"] if action["subject"]["id"] == aim["id"])
            self.assertEqual(paused["kind"], "resume_aim")
            self.assertIn(aim["id"], self.call(path, "admission")["paused_aim_ids"])
            self.call(path, "aim-resume", aim["id"], "owner", "review complete")
            self.call(path, "goal-hold-add", goal["id"], "--actor", "owner", "--reason", "external approval pending")
            hold_record = next(item for item in self.call(path, "ui-projection")["goals"] if item["id"] == goal["id"])
            self.assertEqual(hold_record["hold_reason"], "external approval pending")
            held = next(action for action in self.call(path, "next")["actions"] if action["subject"]["id"] == goal["id"])
            self.assertEqual(held["kind"], "clear_goal_hold")
            self.call(path, "goal-hold-clear", goal["id"])
            self.call(path, "execution-grant", goal["id"], "executor")
            self.assertEqual(self.call(path, "admission")["execution_grants"]["executor"], [goal["id"]])

    def test_aim_evaluate_requires_an_explicit_valid_satisfied_verdict(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Close an Aim explicitly")
            proposal = self.call(path, "proposal-create", aim["id"], "verify result")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "produce", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "aim-cli")
            attempt = self.call(path, "attempt-start", goal["id"], "1", "executor", "work")
            self.call(path, "attempt-complete", attempt["id"], "succeeded")
            evidence = self.call(path, "evidence-record", attempt["id"], "observer", "observed")
            self.call(path, "verification-begin", goal["id"], attempt["id"])
            verdict = self.call(path, "verdict-submit", goal["id"], attempt["id"], "verifier", "satisfied", evidence["id"])

            actions = self.call(path, "next")["actions"]
            evaluation = next(action for action in actions if action["kind"] == "evaluate_aim")
            self.assertEqual(evaluation["subject"]["id"], aim["id"])
            self.assertIn(verdict["id"], evaluation["command_argv"])
            record = self.call(path, "aim-evaluate", aim["id"], "evaluator", "The Aim is achieved.", verdict["id"])
            self.assertEqual(record["aim_id"], aim["id"])
            later_proposal = self.call(path, "proposal-create", aim["id"], "This must not reopen the achieved Aim")
            self.assertNotIn(
                later_proposal["id"],
                [action["subject"]["id"] for action in self.call(path, "next")["actions"] if action["kind"] == "accept_proposal"],
            )

    def test_evidence_correction_exposes_reviews_and_orders_review_resolutions_in_next(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Correct an observation")
            proposal = self.call(path, "proposal-create", aim["id"], "verify result")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "produce", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "correction-cli")
            attempt = self.call(path, "attempt-start", goal["id"], "1", "executor", "work")
            self.call(path, "attempt-complete", attempt["id"], "succeeded")
            evidence = self.call(path, "evidence-record", attempt["id"], "observer", "initial observation")
            self.call(path, "verification-begin", goal["id"], attempt["id"])
            verdict = self.call(path, "verdict-submit", goal["id"], attempt["id"], "verifier", "satisfied", evidence["id"])
            evaluation = self.call(path, "aim-evaluate", aim["id"], "evaluator", "The result covers the Aim.", verdict["id"])

            correction = self.call(path, "evidence-correct", evidence["id"], "observer", "The initial observation was incomplete")
            self.assertEqual(correction["corrected_evidence_id"], evidence["id"])
            reviews = self.call(path, "reviews")
            self.assertEqual(reviews["under_review_verdicts"][0]["id"], verdict["id"])
            self.assertEqual(reviews["under_review_aim_evaluations"][0]["id"], evaluation["id"])
            review = reviews["impact_reviews"][0]
            self.assertEqual(review["trigger_verdict_id"], verdict["id"])

            next_actions = self.call(path, "next")["actions"]
            verdict_resolution = next(action for action in next_actions if action["kind"] == "resolve_verdict_review")
            self.assertEqual(verdict_resolution["subject"]["id"], review["id"])
            self.assertNotIn("resolve_aim_evaluation_review", [action["kind"] for action in next_actions])

            self.call(path, "verdict-review-resolve", review["id"], "reaffirm", "reviewer", "Evidence remains sufficient")
            evaluation_resolution = next(
                action for action in self.call(path, "next")["actions"]
                if action["kind"] == "resolve_aim_evaluation_review"
            )
            self.assertEqual(evaluation_resolution["subject"]["id"], evaluation["id"])
            self.call(path, "aim-evaluation-review-resolve", evaluation["id"], review["id"], "reaffirm", "reviewer", "The valid Verdict still covers the Aim")
            final = self.call(path, "reviews")
            self.assertEqual(final["under_review_verdicts"], [])
            self.assertEqual(final["under_review_aim_evaluations"], [])
            self.assertEqual(final["impact_reviews"][0]["resolutions"][0]["disposition"], "reaffirm")

    def test_invalidated_aim_evaluation_offers_and_records_explicit_aim_reopen(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Reopen only after invalidated closure")
            proposal = self.call(path, "proposal-create", aim["id"], "verify result")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "produce", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "reopen-cli")
            attempt = self.call(path, "attempt-start", goal["id"], "1", "executor", "work")
            self.call(path, "attempt-complete", attempt["id"], "succeeded")
            evidence = self.call(path, "evidence-record", attempt["id"], "observer", "initial observation")
            self.call(path, "verification-begin", goal["id"], attempt["id"])
            verdict = self.call(path, "verdict-submit", goal["id"], attempt["id"], "verifier", "satisfied", evidence["id"])
            evaluation = self.call(path, "aim-evaluate", aim["id"], "evaluator", "The result covers the Aim.", verdict["id"])
            self.call(path, "evidence-correct", evidence["id"], "observer", "The initial observation was wrong")
            review = self.call(path, "reviews")["impact_reviews"][0]
            self.call(path, "verdict-review-resolve", review["id"], "invalidate", "reviewer", "Evidence is unreliable")
            resolution = self.call(path, "aim-evaluation-review-resolve", evaluation["id"], review["id"], "invalidate", "reviewer", "Closure is unsupported")

            reopen = next(
                action for action in self.call(path, "next")["actions"]
                if action["kind"] == "reopen_aim_after_closure_invalidation"
            )
            self.assertEqual(reopen["subject"]["id"], evaluation["id"])
            self.assertIn(resolution["id"], reopen["command_argv"])
            invalidation = self.call(
                path, "aim-closure-invalidate", aim["id"], evaluation["id"], resolution["id"],
                "reviewer", "Resume pursuit after unsupported closure",
            )
            self.assertEqual(invalidation["aim_id"], aim["id"])
            self.assertEqual(invalidation["triggering_record_id"], resolution["id"])
            self.assertNotIn(
                "reopen_aim_after_closure_invalidation",
                [action["kind"] for action in self.call(path, "next")["actions"]],
            )

    def test_inconclusive_verdict_offers_evidence_or_one_use_reverification_paths(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Resolve uncertainty")
            proposal = self.call(path, "proposal-create", aim["id"], "measure")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "investigate", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "inconclusive-cli")
            attempt = self.call(path, "attempt-start", goal["id"], "1", "executor", "measure")
            self.call(path, "attempt-complete", attempt["id"], "succeeded")
            initial = self.call(path, "evidence-record", attempt["id"], "observer", "uncertain")
            self.call(path, "verification-begin", goal["id"], attempt["id"])
            inconclusive = self.call(path, "verdict-submit", goal["id"], attempt["id"], "verifier", "inconclusive", initial["id"])

            actions = self.call(path, "next")["actions"]
            self.assertIn("record_additional_evidence", [action["kind"] for action in actions])
            self.assertIn("decide_reverification", [action["kind"] for action in actions])
            additional = self.call(path, "evidence-record", attempt["id"], "observer", "second measurement")
            superseding = next(action for action in self.call(path, "next")["actions"] if action["kind"] == "submit_superseding_verdict")
            self.assertIn(inconclusive["id"], superseding["command_argv"])
            satisfied = self.call(path, "verdict-submit", goal["id"], attempt["id"], "verifier", "satisfied", initial["id"], additional["id"], "--supersedes-verdict", inconclusive["id"])
            self.assertEqual(satisfied["supersedes_verdict_id"], inconclusive["id"])

            second_aim = self.call(path, "aim-create", "Need a new run")
            second_proposal = self.call(path, "proposal-create", second_aim["id"], "measure again")
            second_goal = self.call(path, "goal-accept", second_proposal["id"], "--target", "target", "--responsibility", "investigate", "--scope", "scope", "--criterion", "criterion", "--verification", "verify", "--key", "reverify-cli")
            second_attempt = self.call(path, "attempt-start", second_goal["id"], "1", "executor", "measure")
            self.call(path, "attempt-complete", second_attempt["id"], "succeeded")
            second_evidence = self.call(path, "evidence-record", second_attempt["id"], "observer", "uncertain")
            self.call(path, "verification-begin", second_goal["id"], second_attempt["id"])
            self.call(path, "verdict-submit", second_goal["id"], second_attempt["id"], "verifier", "inconclusive", second_evidence["id"])
            decision = self.call(path, "reverification-decide", second_goal["id"], "user", "new measurement is needed")
            recovery = next(action for action in self.call(path, "next")["actions"] if action["subject"]["id"] == decision["id"])
            self.assertEqual(recovery["kind"], "start_reverification_attempt")
            retried = self.call(path, "attempt-start", second_goal["id"], "1", "executor", "measure again", "--reverification-decision", decision["id"])
            self.assertEqual(retried["outcome"], "running")

    def test_contract_revise_creates_a_new_immutable_version_from_cli(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal.sqlite3"
            aim = self.call(path, "aim-create", "Revise contract")
            proposal = self.call(path, "proposal-create", aim["id"], "work")
            goal = self.call(path, "goal-accept", proposal["id"], "--target", "target", "--responsibility", "produce", "--scope", "v1 scope", "--criterion", "v1 criterion", "--verification", "v1 verification", "--key", "revision-cli")
            v1 = self.call(path, "attempt-start", goal["id"], "1", "executor", "old work")
            revision = self.call(path, "contract-revise", goal["id"], "--scope", "v2 scope", "--criterion", "v2 criterion", "--verification", "v2 verification", "--actor", "owner", "--reason", "requirements changed")
            self.assertEqual(revision["version"], 2)
            self.assertEqual(revision["success_criterion"], "v2 criterion")
            self.call(path, "attempt-complete", v1["id"], "succeeded")
            current = self.call(path, "next")["actions"]
            start_v2 = next(action for action in current if action["kind"] == "start_attempt")
            self.assertIn("2", start_v2["command_argv"])
