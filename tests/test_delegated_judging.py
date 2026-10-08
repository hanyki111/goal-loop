from contextlib import chdir, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from unittest.mock import patch
import unittest

from goal_loop_tool.delegated_judging import (JudgingError, Workspace, claude_runner, codex_runner, env_from_ps1, judge_aim, judge_goal,
                                              parse_trees, runner_for)
from goal_loop_tool.persistent_cli import main


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


def fake_agent(answer: dict, seen: list):
    """Stand-in for Codex: records what it was given, scribbles in the copy, and writes its answer."""
    def runner(prompt, ws, schema, out, log):
        seen.append({"prompt": prompt, "ws": ws, "schema": json.loads(schema.read_text(encoding="utf-8")), "env": ws.env()})
        (ws.project / "src.txt").write_text("changed by the judge", encoding="utf-8")
        log.write_text("agent log", encoding="utf-8")
        out.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return runner


def verdict_answer(goal_id: str, result: str) -> dict:
    return {"goal_id": goal_id, "result": result, "criteria": [{"item": "(1) 동작한다", "met": "met", "basis": "직접 실행"}],
            "reason": f"(1) 충족: 직접 실행했다\n결과 {result}", "checked": ["테스트 실행"], "not_checked": []}


class DelegatedJudgingTests(unittest.TestCase):
    """The judge runs on a copy of the project, and its answer is recorded unchanged under its own name."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        base = Path(self.directory.name).resolve()
        self.project = base / "repo"
        (self.project / "node_modules").mkdir(parents=True)  # never copied
        (self.project / "src.txt").write_text("original", encoding="utf-8")
        self.work = base / "judging"
        self.work.mkdir()
        kept = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        self.env = patch.dict("os.environ", {**kept, "LOCALAPPDATA": str(base / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(base)}, clear=True)
        self.env.start()
        self.cwd = chdir(self.project)
        self.cwd.__enter__()
        run("project-init")
        self.database = self.project / ".goal-loop" / "journal.sqlite3"
        self.aim = run("aim-create", "판정 시험")
        proposal = run("proposal-create", self.aim["id"], "접근안\n\n달성 기준: (1) 동작한다")
        self.goal = run("goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce", "--scope", "s", "--criterion", "(1) 동작한다", "--verification", "v", "--key", "k")
        self.attempt = run("attempt-start", self.goal["id"], "1", "claude", "run")
        run("attempt-complete", self.attempt["id"], "succeeded")
        (self.project / "log.txt").write_text("ok", encoding="utf-8")
        self.evidence = run("evidence-record", self.attempt["id"], "claude", "실행했다", "--attach", "log.txt")
        run("verification-begin", self.goal["id"], self.attempt["id"])

    def tearDown(self):
        self.cwd.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def test_goal_judgment_runs_on_a_copy_and_is_recorded_unchanged(self):
        seen = []
        result = judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "satisfied"), seen), base=self.work)
        ws = seen[0]["ws"]
        self.assertTrue(ws.project.is_relative_to(self.work))
        self.assertFalse((ws.project / "node_modules").exists())
        self.assertEqual(seen[0]["env"]["GOAL_LOOP_CEILING_DIRECTORIES"], str(ws.project))
        self.assertEqual(seen[0]["env"]["TEMP"], str(ws.tmp))
        self.assertIn("(1) 동작한다", seen[0]["prompt"])
        self.assertIn("실행했다", seen[0]["prompt"])
        # The copied journal opens with its attachments where it looks for them.
        copied = run("--database", str(ws.database), "ui-projection")
        self.assertEqual(copied["evidence"][0]["attachments"][0]["status"], "preserved")
        # The judge's scribble stayed in the copy.
        self.assertEqual((self.project / "src.txt").read_text(encoding="utf-8"), "original")
        self.assertEqual(result["result"], "satisfied")
        projection = run("ui-projection")
        verdict = next(item for item in projection["verdicts"] if item["id"] == result["verdict"])
        self.assertEqual((verdict["verifier_actor_id"], verdict["result"]), ("codex", "satisfied"))
        self.assertEqual(verdict["reason"], "(1) 충족: 직접 실행했다\n결과 satisfied")
        self.assertEqual(verdict["evidence_ids"], [self.evidence["id"], result["evidence"]])
        report = next(item for item in projection["evidence"] if item["id"] == result["evidence"])
        self.assertEqual(report["actor"], "codex")
        self.assertEqual([item["name"] for item in report["attachments"]], [f"answer-{self.goal['id']}.json", f"prompt-{self.goal['id']}.md", f"agent-{self.goal['id']}.log"])
        self.assertEqual(next(item for item in projection["goals"] if item["id"] == self.goal["id"])["phase"], "verified")

    def test_added_evidence_does_not_hide_the_verifying_attempts_evidence(self):
        judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "not_satisfied"), []), base=self.work)
        second = run("attempt-start", self.goal["id"], "1", "claude", "repair")
        run("attempt-complete", second["id"], "succeeded")
        current = run("evidence-record", second["id"], "claude", "고친 뒤 다시 실행했다")
        run("verification-begin", self.goal["id"], second["id"])
        seen = []
        judge_goal(self.database, self.goal["id"], evidence_ids=[self.evidence["id"]], record=False,
                   runner=fake_agent(verdict_answer(self.goal["id"], "satisfied"), seen), base=self.work)
        prompt = seen[0]["prompt"]
        self.assertIn(self.evidence["id"], prompt)  # the earlier Evidence asked for
        self.assertIn(current["id"], prompt)  # the verifying Attempt's own Evidence is still there
        self.assertIn("고친 뒤 다시 실행했다", prompt)

    def test_extra_tree_is_copied_for_the_judge_and_the_original_is_untouched(self):
        other = Path(self.directory.name).resolve() / "other-repo"
        (other / "src").mkdir(parents=True)
        (other / "src" / "route.ts").write_text("original route", encoding="utf-8")
        (other / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        (other / "node_modules" / "pkg").mkdir(parents=True)
        (other / "web" / "node_modules").mkdir(parents=True)
        for output in ("dist", "build", "target", "web/dist"):
            (other / output).mkdir(parents=True, exist_ok=True)
            (other / output / "bundle.js").write_text("built", encoding="utf-8")
        seen = []

        def runner(prompt, ws, schema, out, log):
            seen.append({"prompt": prompt, "ws": ws})
            (ws.trees["ao"] / "src" / "route.ts").write_text("changed by the judge", encoding="utf-8")
            out.write_text(json.dumps(verdict_answer(self.goal["id"], "satisfied"), ensure_ascii=False), encoding="utf-8")

        judge_goal(self.database, self.goal["id"], runner=runner, base=self.work, trees=parse_trees([f"ao={other}"]))
        ws = seen[0]["ws"]
        copy = ws.trees["ao"]
        self.assertTrue(copy.is_relative_to(ws.root))
        self.assertFalse((copy / ".git").exists())
        # build output is never copied
        for output in ("dist", "build", "target", "web/dist"):
            self.assertFalse((copy / output).exists(), output)
        # node_modules are linked to the original, not copied
        self.assertTrue((copy / "node_modules" / "pkg").is_dir())
        self.assertTrue((copy / "web" / "node_modules").is_dir())
        self.assertIn(f"추가 작업 트리 사본: ao → {copy}", seen[0]["prompt"])
        self.assertIn("원본 경로는 읽지도 쓰지도 마라", seen[0]["prompt"])
        self.assertEqual((other / "src" / "route.ts").read_text(encoding="utf-8"), "original route")

    def test_tree_argument_must_be_name_equals_path(self):
        with self.assertRaises(JudgingError):
            parse_trees(["no-equals-sign"])
        with self.assertRaises(JudgingError):
            judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "satisfied"), []), base=self.work,
                       trees={"ao": Path(self.directory.name) / "missing"})

    def test_codex_gets_each_tree_copy_as_a_writable_directory(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            return 0

        ws = Workspace(Path("r"), Path("r/project"), Path("r/db"), Path("r/tmp"), {"ao": Path("r/trees/ao")})
        with patch("goal_loop_tool.delegated_judging.shutil.which", return_value="codex"), \
             patch("goal_loop_tool.delegated_judging._run_judge", side_effect=fake_run):
            codex_runner("p", ws, Path("s"), Path("a"), Path(self.directory.name) / "log.txt")
        command = captured["command"]
        self.assertIn(str(Path("r/trees/ao")), command)
        self.assertEqual(command[command.index(str(Path("r/trees/ao"))) - 1], "--add-dir")

    def test_judge_that_executed_the_attempt_is_refused_before_running(self):
        ran = []
        with self.assertRaises(JudgingError) as caught:
            judge_goal(self.database, self.goal["id"], agent="claude", runner=fake_agent(verdict_answer(self.goal["id"], "satisfied"), ran), base=self.work)
        self.assertIn("실행자와 같습니다", str(caught.exception))
        self.assertEqual(ran, [])

    def test_claude_runner_runs_a_fresh_cli_session_and_keeps_secrets_out_of_the_log(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured.update(command=command, **kwargs)
            kwargs["stdout"].write_text(json.dumps({"is_error": False, "structured_output": {"result": "satisfied"}, "note": "token-abcdefgh-123"}), encoding="utf-8")
            kwargs["stderr"].write_text("using token-abcdefgh-123", encoding="utf-8")
            return 0

        base = Path(self.directory.name)
        ws = Workspace(base / "r", base / "r" / "project", base / "r" / "db", base / "r" / "tmp", {"ao": base / "r" / "trees" / "ao"})
        ws.root.mkdir()
        schema, answer, log = base / "schema.json", base / "answer.json", base / "log.txt"
        schema.write_text('{"type":"object"}', encoding="utf-8")
        secrets = {"ANTHROPIC_BASE_URL": "https://glm.example/api", "ANTHROPIC_AUTH_TOKEN": "token-abcdefgh-123"}
        with patch("goal_loop_tool.delegated_judging.shutil.which", return_value="claude"), \
             patch("goal_loop_tool.delegated_judging._run_judge", side_effect=fake_run):
            claude_runner(secrets, model="opus")("판정하라", ws, schema, answer, log)
        command = captured["command"]
        self.assertEqual(command[:4], ["claude", "-p", "--output-format", "json"])
        self.assertEqual(command[command.index("--json-schema") + 1], '{"type":"object"}')
        self.assertEqual(command[command.index("--model") + 1], "opus")
        tools = command[command.index("--allowedTools") + 1:command.index("--add-dir")]
        self.assertEqual(tools, ["Bash", "Read", "Grep", "Glob", "Edit", "Write"])
        self.assertIn(str(ws.trees["ao"]), command)
        self.assertEqual(captured["cwd"], ws.project)
        self.assertEqual(captured["prompt"], "판정하라")
        self.assertEqual(captured["env"]["ANTHROPIC_AUTH_TOKEN"], "token-abcdefgh-123")  # only the child process gets it
        self.assertEqual(json.loads(answer.read_text(encoding="utf-8")), {"result": "satisfied"})
        self.assertNotIn("token-abcdefgh-123", log.read_text(encoding="utf-8"))
        self.assertIn("(숨김)", log.read_text(encoding="utf-8"))

    def test_glm_connection_is_read_from_the_users_settings_file(self):
        base = Path(self.directory.name)
        ps1 = base / "set-glm.ps1"
        ps1.write_text("$env:ANTHROPIC_BASE_URL = \"https://glm.example/api\"\n$env:ANTHROPIC_AUTH_TOKEN = 'secret-value-1'\n"
                       "$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = 1\n$env:PATH = 'not-copied'\n", encoding="utf-8")
        self.assertEqual(env_from_ps1(ps1), {"ANTHROPIC_BASE_URL": "https://glm.example/api", "ANTHROPIC_AUTH_TOKEN": "secret-value-1",
                                             "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        with self.assertRaises(JudgingError):
            runner_for("glm", self.database)  # no judges.json yet
        (self.project / ".goal-loop" / "judges.json").write_text(json.dumps({"glm": {"env_from_ps1": str(ps1)}}), encoding="utf-8")
        self.assertTrue(callable(runner_for("glm", self.database)))
        self.assertIs(runner_for("codex", self.database), codex_runner)
        with self.assertRaises(JudgingError):
            runner_for("someone", self.database)

    def test_inconclusive_then_new_evidence_is_superseded_and_no_record_records_nothing(self):
        first = judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "inconclusive"), []), base=self.work)
        self.assertIsNone(first["supersedes"])
        dry = judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "satisfied"), []), base=self.work, record=False)
        self.assertFalse(dry["recorded"])
        self.assertEqual(len(run("ui-projection")["verdicts"]), 1)
        run("evidence-record", self.attempt["id"], "claude", "근거 보충")
        second = judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "satisfied"), []), base=self.work)
        self.assertEqual(second["supersedes"], first["verdict"])

    def test_goal_must_be_verifying(self):
        aim = run("aim-create", "다른 목표")
        proposal = run("proposal-create", aim["id"], "p")
        idle = run("goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce", "--scope", "s", "--criterion", "c", "--verification", "v", "--key", "k2")
        with self.assertRaises(JudgingError):
            judge_goal(self.database, idle["id"], runner=fake_agent(verdict_answer(idle["id"], "satisfied"), []), base=self.work)

    def test_corrected_evidence_is_shown_with_its_correction_and_not_cited(self):
        wrong = run("evidence-record", self.attempt["id"], "claude", "파일 189개를 더한다")
        correction = run("evidence-correct", wrong["id"], "claude", "정정: 189개가 아니라 190개다")
        run("evidence-record", self.attempt["id"], "claude", "파일 190개를 더한다")
        seen = []
        result = judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "satisfied"), seen), base=self.work)
        self.assertTrue(result["recorded"])
        verdict = next(item for item in run("ui-projection")["verdicts"] if item["id"] == result["verdict"])
        self.assertNotIn(wrong["id"], verdict["evidence_ids"])
        self.assertIn(result["evidence"], verdict["evidence_ids"])
        block = seen[0]["prompt"].split(f"### {wrong['id']}", 1)[1].split("### ", 1)[0]
        self.assertIn(f"정정됨({correction['id']}, claude): 정정: 189개가 아니라 190개다", block)
        self.assertIn("판정의 근거로 인용되지 않는다", block)

    def test_recorded_judgment_carries_a_memory_timeline_draft_only_with_memory_tool(self):
        plain = judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "inconclusive"), []), base=self.work)
        self.assertNotIn("memory_draft", plain)
        (self.project / ".memory" / "timeline" / "daily").mkdir(parents=True)
        run("evidence-record", self.attempt["id"], "claude", "근거 보충")
        result = judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "satisfied"), []), base=self.work)
        self.assertEqual(result["memory_draft"]["text"], f"{self.goal['id']} satisfied({result['verdict']}, codex): t")
        self.assertTrue(result["memory_draft"]["command"].startswith('python -m memory_tool record "'))
        self.assertEqual(list((self.project / ".memory" / "timeline" / "daily").iterdir()), [])

    def test_aim_prompt_shows_criteria_proposals_as_the_aims_criteria(self):
        criteria = run("proposal-create", self.aim["id"], "기준 접근안\n(1) 동작한다\n(2) 문서가 있다")
        run("proposal-close", criteria["id"], "--kind", "criteria", "--actor", "claude", "--reason", "목표의 달성 기준")
        seen = []
        judge_aim(self.database, self.aim["id"], runner=fake_agent({"aim_id": self.aim["id"], "achieved": False, "cited": [], "criteria": [], "conclusion": "아직", "checked": [], "not_checked": []}, seen), base=self.work, record=False)
        prompt = seen[0]["prompt"]
        criteria_section = prompt.split("### 달성 기준", 1)[1].split("### 접근안", 1)[0]
        self.assertIn(criteria["id"], criteria_section)
        self.assertIn("(2) 문서가 있다", criteria_section)
        self.assertNotIn(criteria["id"], prompt.split("### 접근안", 1)[1].split("### 과제와 검증 판단", 1)[0])

    def test_aim_is_recorded_only_when_achieved_with_acceptable_citations(self):
        verdict = judge_goal(self.database, self.goal["id"], runner=fake_agent(verdict_answer(self.goal["id"], "satisfied"), []), base=self.work)["verdict"]
        base_answer = {"aim_id": self.aim["id"], "criteria": [{"item": "(1)", "met": "met", "basis": "b"}], "conclusion": "(1) 충족: 동작한다",
                       "checked": [], "not_checked": []}
        not_yet = judge_aim(self.database, self.aim["id"], runner=fake_agent({**base_answer, "achieved": False, "cited": []}, []), base=self.work)
        self.assertFalse(not_yet["recorded"])
        result = judge_aim(self.database, self.aim["id"], runner=fake_agent({**base_answer, "achieved": True, "cited": [verdict, "verdict-99"]}, []), base=self.work)
        self.assertEqual(result["cited"], [verdict])
        evaluation = run("ui-projection")["aim_evaluations"][0]
        self.assertEqual((evaluation["evaluator_actor_id"], evaluation["verdict_ids"]), ("codex", [verdict]))


FAKE_CODEX = """import json, subprocess, sys, time
args = sys.argv[1:]
answer = args[args.index("-o") + 1]
sys.stdin.read()
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)", {marker!r}])
open(answer, "w", encoding="utf-8").write(json.dumps({{"result": "satisfied"}}))
time.sleep(300)
"""


def kill_marked(marker: str) -> None:
    """Test cleanup: end every process whose command line names ``marker``."""
    if os.name == "nt":
        script = f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{marker}*' -and $_.Name -ne 'powershell.exe' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
        subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, check=False)
    else:
        subprocess.run(["pkill", "-f", marker], capture_output=True, check=False)


def marked_alive(marker: str) -> bool:
    if os.name == "nt":
        script = f"@(Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{marker}*' -and $_.Name -ne 'powershell.exe' }}).Count"
        done = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, check=False)
        return done.stdout.strip() not in ("", "0")
    return subprocess.run(["pgrep", "-f", marker], capture_output=True, check=False).returncode == 0


_PROCESS_ACCESS: bool | None = None


def process_access() -> bool:
    """Whether this environment can list and end other processes; a judge's sandbox (Codex) cannot (verdict-97, -118)."""
    global _PROCESS_ACCESS
    if _PROCESS_ACCESS is None:
        from goal_loop_tool.delegated_judging import _end_tree, _processes
        marker = f"gl-process-probe-{os.getpid()}-{time.monotonic_ns()}"
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker])
        try:
            listed = any(marker in command for _, command in _processes())
            _end_tree(child.pid)
            try:
                child.wait(timeout=10)
                ended = True
            except subprocess.TimeoutExpired:
                ended = False
            _PROCESS_ACCESS = listed and ended
        finally:
            if child.poll() is None:
                child.kill()
    return _PROCESS_ACCESS


class JudgeProcessTests(unittest.TestCase):
    """A judge that answers but leaves a process running must not hold the judgment (goal-78)."""

    def setUp(self):
        if not process_access():
            self.skipTest("이 환경은 다른 프로세스를 나열하거나 끝낼 수 없다(판정 샌드박스 등) — 프로세스 정리 시험을 건너뛴다")
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.marker = f"gl-judge-marker-{os.getpid()}-{time.monotonic_ns()}"
        self.addCleanup(kill_marked, self.marker)

    def test_judge_that_keeps_running_after_its_answer_is_ended_and_its_answer_kept(self):
        base = Path(self.directory.name) / self.marker  # the marker in every fake path lets cleanup find each process
        base.mkdir()
        script = base / "fake_codex.py"
        script.write_text(FAKE_CODEX.format(marker=self.marker), encoding="utf-8")
        if os.name == "nt":
            fake = base / "codex.cmd"
            fake.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            fake = base / "codex"
            fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            fake.chmod(0o755)
        ws = Workspace(base / "r", base / "r" / "project", base / "r" / "db", base / "r" / "tmp", {})
        ws.project.mkdir(parents=True)
        answer, log = base / "answer.json", base / "log.txt"
        with patch("goal_loop_tool.delegated_judging.shutil.which", return_value=str(fake)), \
             patch("goal_loop_tool.delegated_judging.ANSWER_GRACE_SECONDS", 1.0, create=True), \
             patch("goal_loop_tool.delegated_judging.POLL_SECONDS", 0.2, create=True):
            worker = threading.Thread(target=codex_runner, args=("판정하라", ws, base / "schema.json", answer, log), daemon=True)
            worker.start()
            worker.join(timeout=45)
        self.assertFalse(worker.is_alive(), "codex_runner가 답을 쓴 뒤에도 남은 판정 프로세스를 기다리며 끝나지 않았다")
        self.assertEqual(json.loads(answer.read_text(encoding="utf-8")), {"result": "satisfied"})
        self.assertFalse(marked_alive(self.marker), "판정자가 띄운 하위 프로세스가 남았다")
        self.assertIn("판정 프로세스 트리를 끝냈습니다", log.read_text(encoding="utf-8"))

    def test_judge_returns_even_when_ending_its_process_tree_is_refused(self):
        base = Path(self.directory.name) / self.marker  # the marker in every fake path lets cleanup find each process
        base.mkdir()
        script = base / "fake_codex.py"
        script.write_text(FAKE_CODEX.format(marker=self.marker), encoding="utf-8")
        if os.name == "nt":
            fake = base / "codex.cmd"
            fake.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            fake = base / "codex"
            fake.write_text(f'#!/bin/sh\n"{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            fake.chmod(0o755)
        ws = Workspace(base / "r", base / "r" / "project", base / "r" / "db", base / "r" / "tmp", {})
        ws.project.mkdir(parents=True)
        answer, log = base / "answer.json", base / "log.txt"
        with patch("goal_loop_tool.delegated_judging.shutil.which", return_value=str(fake)), \
             patch("goal_loop_tool.delegated_judging._end_tree", lambda pid: None, create=True), \
             patch("goal_loop_tool.delegated_judging.ANSWER_GRACE_SECONDS", 1.0, create=True), \
             patch("goal_loop_tool.delegated_judging.POLL_SECONDS", 0.2, create=True):
            worker = threading.Thread(target=codex_runner, args=("판정하라", ws, base / "schema.json", answer, log), daemon=True)
            worker.start()
            worker.join(timeout=45)
        self.assertFalse(worker.is_alive(), "트리 종료가 거부되자 codex_runner가 끝나지 않았다")
        self.assertEqual(json.loads(answer.read_text(encoding="utf-8")), {"result": "satisfied"})

    def test_leftover_processes_in_the_judge_copy_are_ended_and_others_are_not(self):
        from goal_loop_tool.delegated_judging import _end_leftovers
        root = Path(self.directory.name) / f"goal-loop-judge-{self.marker}"
        sleep = [sys.executable, "-c", "import time; time.sleep(300)"]
        inside = subprocess.Popen([*sleep, str(root / "trees" / "ao" / "server")])
        outside = subprocess.Popen([*sleep, f"{self.marker}-elsewhere"])
        self.addCleanup(outside.kill)
        self.addCleanup(inside.kill)
        time.sleep(1)
        ended = _end_leftovers(root)
        self.assertIsNotNone(inside.wait(timeout=15))
        self.assertIsNone(outside.poll())
        # A venv python.exe on Windows relaunches the base interpreter with the same command line, so both may be listed.
        self.assertIn(str(inside.pid), [line.split(" ", 1)[0] for line in ended])
        self.assertTrue(all(root.name in line for line in ended))


if __name__ == "__main__":
    unittest.main()
