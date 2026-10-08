from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.persistent_cli import guided_main, main
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.model import GoalIdentity
from goal_loop_tool.service import ContractDraft


class GuidedPersistentCliTests(unittest.TestCase):
    def scripted_input(self, answers: list[str]):
        iterator = iter(answers)
        return lambda _prompt: next(iterator)

    def test_guided_flow_creates_aim_goal_and_first_attempt(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            transcript: list[str] = []
            guided_main(
                database,
                input_fn=self.scripted_input([
                    "1", "검증된 사용 흐름을 만든다", "CLI 사용 시나리오를 만든다",
                    "사용자가 실행할 안내", "1", "CLI와 안내 문서", "처음 사용자도 끝까지 실행한다",
                    "사용자가 결과와 로그를 확인한다", "y", "human", "시나리오를 실행한다", "0",
                ]),
                output=transcript.append,
            )
            loop = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertEqual(len(loop.aims), 1)
            self.assertEqual(len(loop.goals), 1)
            self.assertEqual(len(loop.attempts), 1)
            self.assertEqual(loop.goals["goal-1"].identity.responsibility_kind, "produce")
            self.assertIn("Goal goal-1을 Contract v1로 확정했습니다.", transcript)
            self.assertTrue(any("Attempt attempt-1을 시작했습니다." in line for line in transcript))
            self.assertTrue(any("결과 목표 (target)" in line for line in transcript))
            self.assertTrue(any("검증 방법 (verification)" in line for line in transcript))

    def test_guided_status_and_help_do_not_create_facts(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            transcript: list[str] = []
            guided_main(database, input_fn=self.scripted_input(["2", "3", "aim-create", "0"]), output=transcript.append)
            self.assertTrue(any("아직 Aim이 없습니다" in line for line in transcript))
            self.assertTrue(any("aim-create:" in line for line in transcript))
            loop = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertFalse(loop.aims)

    def test_guided_exposes_a_full_beginner_guide_and_all_command_help(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            transcript: list[str] = []
            guided_main(
                database,
                input_fn=self.scripted_input(["7", "3", "all", "0"]),
                output=transcript.append,
            )
            text = "\n".join(transcript)
            self.assertIn("대화형 Goal Loop 안내", text)
            self.assertIn("Attempt 성공은 Goal 성공 판정이 아닙니다", text)
            self.assertIn("Verdict inconclusive", text)
            self.assertIn("상세 명령 도움말", text)
            self.assertIn("aim-pause:", text)
            loop = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertFalse(loop.aims)

    def test_existing_goal_menu_starts_its_first_attempt(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            transcript: list[str] = []
            guided_main(
                database,
                input_fn=self.scripted_input([
                    "1", "사용 흐름을 검증한다", "안내를 따라 실행한다",
                    "사용자가 첫 실행을 완료한다", "1", "첫 실행만", "활동 로그에 기록이 보인다",
                    "reviewer가 로그를 확인한다", "n",
                    "2", "6", "", "1", "human", "기존 Goal의 첫 실행을 수행한다", "0",
                ]),
                output=transcript.append,
            )
            loop = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertEqual(len(loop.attempts), 1)
            self.assertFalse(any("다음으로 할 일을 Proposal" in line for line in transcript))
            self.assertTrue(any("Attempt attempt-1을 시작했습니다." in line for line in transcript))

    def test_goal_manager_completes_attempt_records_evidence_and_submits_verdict(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            transcript: list[str] = []
            guided_main(
                database,
                input_fn=self.scripted_input([
                    "1", "사용 흐름을 검증한다", "안내를 따라 실행한다",
                    "사용자가 첫 실행을 완료한다", "1", "첫 실행만", "활동 로그에 기록이 보인다",
                    "reviewer가 로그를 확인한다", "y", "human", "첫 실행을 수행한다",
                    "6", "", "1",  # running Attempt -> succeeded
                    "6", "", "1", "human", "activity에 실행 기록이 있다",  # Evidence
                    "6", "", "2",  # verification begins
                    "6", "", "2", "reviewer", "1", "", "기준 충족: 활동 로그에 기록이 보인다",  # satisfied Verdict with reason
                    "0",
                ]),
                output=transcript.append,
            )
            loop = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertEqual(loop.attempts["attempt-1"].outcome.value, "succeeded")
            self.assertEqual(len(loop.evidence), 1)
            self.assertEqual(loop.verdicts["verdict-1"].result.value, "satisfied")
            self.assertEqual(loop.verdicts["verdict-1"].reason, "기준 충족: 활동 로그에 기록이 보인다")
            self.assertTrue(any("검증을 시작했습니다" in line for line in transcript))
            self.assertTrue(any("Verdict verdict-1을 satisfied" in line for line in transcript))

    def test_guided_inconclusive_recovery_adds_evidence_and_supersedes_the_verdict(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            transcript: list[str] = []
            guided_main(
                database,
                input_fn=self.scripted_input([
                    "1", "불확실성을 해소한다", "측정한다", "측정 결과", "2", "측정", "측정값", "reviewer가 확인", "y", "executor", "첫 측정",
                    "6", "", "1", "6", "", "1", "observer", "초기 측정은 불확실", "6", "", "2",
                    "6", "", "2", "reviewer", "3", "", "",  # inconclusive, no reason
                    "6", "", "1", "observer", "두 번째 측정",  # additional Evidence
                    "6", "", "2", "reviewer", "1", "", "",  # superseding satisfied Verdict
                    "0",
                ]),
                output=transcript.append,
            )
            loop = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertEqual(loop.verdicts["verdict-1"].result.value, "inconclusive")
            self.assertIsNone(loop.verdicts["verdict-1"].reason)
            self.assertEqual(loop.verdicts["verdict-2"].supersedes_verdict_id, "verdict-1")
            self.assertEqual(loop.verdicts["verdict-2"].result.value, "satisfied")
            self.assertTrue(any("자동 재시도하지 않습니다" in line for line in transcript))

    def test_guided_exposes_advanced_lifecycle_menu(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            transcript: list[str] = []
            guided_main(database, input_fn=self.scripted_input(["8", "0", "0"]), output=transcript.append)
            self.assertTrue(any("고급 lifecycle 관리" in line for line in transcript))

    def test_guided_keeps_admission_rejection_in_the_terminal_without_a_traceback(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            loop = PersistentGoalLoop(SqliteEventRepository(database))
            aim = loop.create_aim("Paused Aim")
            proposal = loop.create_proposal(aim.id, "work")
            goal = loop.accept_proposal(proposal.id, GoalIdentity(aim.id, "target", "produce"), ContractDraft("scope", "criterion", "verify"), idempotency_key="paused-guided")
            loop.pause_aim(aim.id, "owner", "waiting")
            transcript: list[str] = []
            guided_main(
                database,
                input_fn=self.scripted_input(["6", "", "1", "executor", "work", "0"]),
                output=transcript.append,
            )
            restarted = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertFalse(restarted.attempts)
            self.assertTrue(any("Goal·Attempt 관리 작업을 기록하지 않았습니다" in line for line in transcript))
            self.assertTrue(any("admission/reviews" in line for line in transcript))

    def test_new_aim_requires_a_nonempty_proposal_before_it_is_saved(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            transcript: list[str] = []
            guided_main(
                database,
                input_fn=self.scripted_input([
                    "1", "CLI 검증", "", "사용자 흐름을 관찰하고 개선한다",
                    "", "1", "CLI 흐름", "activity에 기록이 보인다", "reviewer가 로그를 확인한다", "0",
                ]),
                output=transcript.append,
            )
            loop = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertEqual(loop.proposals["proposal-1"].aim_id, "aim-1")
            self.assertFalse(loop.goals)
            self.assertTrue(any("Proposal proposal-1을 저장했습니다" in line for line in transcript))
            self.assertTrue(any("Proposal은 비워 둘 수 없습니다" in line for line in transcript))

    def test_aim_manager_can_recover_a_legacy_aim_without_a_proposal(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "guided.sqlite3"
            PersistentGoalLoop(SqliteEventRepository(database)).create_aim("기존 Aim")
            transcript: list[str] = []
            guided_main(
                database,
                input_fn=self.scripted_input(["5", "", "1", "나중에 추가한 Proposal", "0"]),
                output=transcript.append,
            )
            loop = PersistentGoalLoop(SqliteEventRepository(database))
            self.assertEqual(loop.proposals["proposal-1"].direction, "나중에 추가한 Proposal")

    def test_noninteractive_help_explains_command_without_json(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "example.sqlite3"
            output = StringIO()
            with redirect_stdout(output):
                main(["--database", str(database), "help", "verdict-submit"])
            text = output.getvalue()
            self.assertIn("Evidence를 인용", text)
            self.assertIn(f"persistent --database {database} verdict-submit", text)
