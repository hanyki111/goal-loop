from contextlib import chdir, redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.persistent_cli import main
from tests.test_ao_runtime import FakeAo


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


class AoLinkTests(unittest.TestCase):
    """A Goal Loop Goal and the AO Goals it ran are linked by references and can be found from either side."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        base = Path(self.directory.name).resolve()
        self.project = base / "repo"
        self.project.mkdir()
        self.ao_workspace = base / "ao" / "workspace"
        ao_project = self.ao_workspace / "projects" / "demo"
        (ao_project / "goals").mkdir(parents=True)
        (ao_project / "goals" / "goals.json").write_text(json.dumps({"goals": [
            {"id": "fix-search", "title": "검색", "status": "completed"}, {"id": "other", "title": "다른 일", "status": "completed"}]}), encoding="utf-8")
        session = ao_project / "sessions" / "run-7"
        session.mkdir(parents=True)
        (session / "session.json").write_text(json.dumps({"id": "run-7", "goalId": "fix-search", "status": "completed"}), encoding="utf-8")
        kept = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        self.env = patch.dict("os.environ", {**kept, "LOCALAPPDATA": str(base / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(base)}, clear=True)
        self.env.start()
        self.cwd = chdir(self.project)
        self.cwd.__enter__()
        run("project-init")
        self.aim = run("aim-create", "AO 연결")
        self.servers: list[FakeAo] = []

    def tearDown(self):
        for server in self.servers:
            server.close()
        self.cwd.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def goal(self, key: str) -> dict:
        proposal = run("proposal-create", self.aim["id"], "p")
        return run("goal-accept", proposal["id"], "--target", "검색을 고친다", "--responsibility", "produce", "--scope", "s",
                   "--criterion", "c", "--verification", "v", "--key", key)

    def completed_attempt(self, goal: dict) -> dict:
        attempt = run("attempt-start", goal["id"], "1", "claude", "AO에서 실행")
        run("attempt-complete", attempt["id"], "succeeded")
        return attempt

    def test_projection_summarizes_each_linked_ao_run_from_the_journal(self):
        events = self.ao_workspace / "projects" / "demo" / "sessions" / "run-7" / "events.jsonl"
        lines = [{"type": "verifier:result", "timestamp": f"2026-10-07T09:0{index}:00Z",
                  "payload": {"verifierType": "command", "passed": passed, "reason": reason}}
                 for index, (passed, reason) in enumerate([(True, "ok"), (False, "vitest 실패 1"), (False, "vitest 실패 2"),
                                                           (False, "vitest 실패 3"), (False, "vitest 실패 4"), (True, "ok")])]
        events.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
        goal = self.goal("k-runs")
        attempt = self.completed_attempt(goal)
        evidence = run("ao-import", attempt["id"], "claude", str(self.ao_workspace / "projects" / "demo"), "fix-search")
        projection = run("ui-projection")
        self.assertEqual(projection["schema_version"], 15)
        [summary] = [item for item in projection["ao_runs"] if item["goal_id"] == goal["id"]]
        self.assertEqual((summary["ao_project_id"], summary["ao_goal_id"], summary["title"], summary["status"]), ("demo", "fix-search", "검색", "completed"))
        self.assertEqual((summary["evidence_ids"], summary["attempt_ids"]), ([evidence["id"]], [attempt["id"]]))
        [session] = summary["sessions"]
        self.assertEqual((session["id"], session["status"], session["verifiers_passed"], session["verifiers_failed"]), ("run-7", "completed", 2, 4))
        self.assertEqual([item["reason"] for item in session["recent_failures"]], ["vitest 실패 2", "vitest 실패 3", "vitest 실패 4"])
        self.assertEqual(summary["commits"], [])
        other = self.goal("k-none")
        self.assertFalse(any(item["goal_id"] == other["id"] for item in run("ui-projection")["ao_runs"]))

    def test_without_a_web_address_links_use_ao_locations_and_respect_older_links(self):
        goal = self.goal("k-ao")
        attempt = self.completed_attempt(goal)
        # A link stored before the resident AO web server was retired.
        legacy = run("reference-add", goal["id"], "http://localhost:5173/projects/demo/goals/fix-search", "--actor", "claude",
                     "--label", "AO Goal demo/fix-search")
        imported = run("ao-import", attempt["id"], "claude", str(self.ao_workspace / "projects" / "demo"), "fix-search")
        self.assertIsNone(imported["ao_link"])  # the AO Goal is already linked, whatever address it was stored with
        other = run("ao-import", attempt["id"], "claude", str(self.ao_workspace / "projects" / "demo"), "other")
        references = next(g for g in run("ui-projection")["goals"] if g["id"] == goal["id"])["references"]
        new = next(item for item in references if item["id"] == other["ao_link"])
        self.assertEqual((new["location"], new["resolved"], new["exists"]), ("ao:/projects/demo/goals/other",) * 2 + (True,))
        self.assertEqual({row["ao_goal_id"] for row in run("ao-links", "--goal", goal["id"])["links"]}, {"fix-search", "other"})
        self.assertIn(legacy["id"], {item["id"] for item in references})

    def test_ao_import_links_once_and_links_are_found_from_both_sides(self):
        goal = self.goal("k1")
        attempt = self.completed_attempt(goal)
        first = run("ao-import", attempt["id"], "claude", str(self.ao_workspace / "projects" / "demo"), "fix-search")
        self.assertIsNotNone(first["ao_link"])
        again = run("ao-import", attempt["id"], "claude", str(self.ao_workspace / "projects" / "demo"), "fix-search")
        self.assertIsNone(again["ao_link"])  # a live link with the same address already exists
        elsewhere = run("ao-import", attempt["id"], "claude", str(self.ao_workspace / "projects" / "demo"), "fix-search", "--ao-web-url", "http://ao.example:8080/")
        self.assertIsNotNone(elsewhere["ao_link"])  # same AO Goal, different address: its own link
        locations = sorted(item["location"] for item in next(g for g in run("ui-projection")["goals"] if g["id"] == goal["id"])["references"])
        self.assertEqual(locations, ["ao:/projects/demo/goals/fix-search", "http://ao.example:8080/projects/demo/goals/fix-search"])
        run("reference-remove", elsewhere["ao_link"], "--actor", "claude", "--reason", "주소 비교 시험")
        reference = next(item for item in run("ui-projection")["goals"] if item["id"] == goal["id"])["references"][0]
        self.assertEqual((reference["label"], reference["location"]), ("AO Goal demo/fix-search", "ao:/projects/demo/goals/fix-search"))
        by_goal = run("ao-links", "--goal", goal["id"])["links"]
        by_ao = run("ao-links", "--ao-goal", "demo/fix-search")["links"]
        self.assertEqual(by_goal, by_ao)
        self.assertEqual((by_goal[0]["goal_id"], by_goal[0]["ao_session_ids"], by_goal[0]["evidence_ids"]), (goal["id"], ["run-7"], [first["id"], again["id"], elsewhere["id"]]))
        self.assertEqual(run("ao-links", "--ao-goal", "demo/other")["links"], [])
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            run("ao-links", "--ao-goal", "fix-search")

    def test_dispatch_links_even_without_an_ao_workspace(self):
        goal = self.goal("k2")
        fake = FakeAo(["completed"], ["completed"])
        self.servers.append(fake)
        started = run("ao-attempt-start", goal["id"], "1", "claude", "AO", "--ao-project-id", "demo", "--ao-goal-id", "fix-search",
                      "--ao-url", fake.url, "--ao-web-url", "http://ao.example:8080", "--poll-seconds", "0.01")
        result = run("runtime-dispatch", started["runtime_request"]["request_id"], "worker-a")
        self.assertIsNotNone(result["ao_link"])
        self.assertNotIn("ao_import", result)
        link = run("ao-links", "--goal", goal["id"])["links"][0]
        self.assertEqual(link["location"], "http://ao.example:8080/projects/demo/goals/fix-search")
        self.assertEqual(link["ao_session_ids"], [])  # nothing imported, so no Session is known yet

    def test_backfill_adds_links_missing_for_requests_and_imported_evidence(self):
        requested = self.goal("k3")
        run("ao-attempt-start", requested["id"], "1", "claude", "AO", "--ao-project-id", "demo", "--ao-goal-id", "other")
        imported = self.goal("k4")
        attempt = self.completed_attempt(imported)
        evidence = run("ao-import", attempt["id"], "claude", str(self.ao_workspace / "projects" / "demo"), "fix-search")
        run("reference-remove", evidence["ao_link"], "--actor", "claude", "--reason", "backfill 시험")
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            run("ao-links", "--backfill")
        result = run("ao-links", "--backfill", "--actor", "claude")
        self.assertEqual(sorted((item["goal_id"], item["ao_goal_id"]) for item in result["added"]),
                         sorted([(requested["id"], "other"), (imported["id"], "fix-search")]))
        self.assertEqual(run("ao-links", "--backfill", "--actor", "claude")["added"], [])
        self.assertEqual(len(result["links"]), 2)

    def test_a_snapshot_attached_by_hand_does_not_create_a_link(self):
        sample = self.goal("k5")
        source = self.completed_attempt(sample)
        snapshot = next(item for item in run("ao-import", source["id"], "claude", str(self.ao_workspace / "projects" / "demo"), "fix-search")["attachments"]
                        if item["name"].startswith("ao-goal-"))
        other = self.goal("k6")
        attempt = self.completed_attempt(other)
        stored = self.project / ".goal-loop" / "artifacts" / snapshot["sha256"] / snapshot["name"]
        run("evidence-record", attempt["id"], "claude", "import 형식의 예시로 스냅샷을 첨부했다", "--attach", str(stored))
        added = run("ao-links", "--backfill", "--actor", "claude")["added"]
        self.assertNotIn(other["id"], [item["goal_id"] for item in added])
        self.assertEqual(run("ao-links", "--goal", other["id"])["links"], [])


if __name__ == "__main__":
    unittest.main()
