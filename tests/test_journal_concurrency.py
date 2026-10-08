from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import JournalConflictError, SqliteEventRepository


class JournalConcurrencyTests(unittest.TestCase):
    """Two sessions sharing one journal must not append events derived from a stale replay."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "goal.sqlite3"

    def tearDown(self):
        self.directory.cleanup()

    def session(self):
        return PersistentGoalLoop(SqliteEventRepository(self.path))

    def test_command_overtaken_by_another_session_is_rederived(self):
        first, second = self.session(), self.session()
        original = first._make_trial
        interleaved = []

        def make_trial_then_let_other_session_commit():
            trial = original()
            if not interleaved:
                interleaved.append(second.create_aim("written by the other session"))
            return trial

        first._make_trial = make_trial_then_let_other_session_commit
        mine = first.create_aim("written by this session")
        self.assertEqual(interleaved[0].id, "aim-1")
        self.assertEqual(mine.id, "aim-2")
        restarted = self.session()
        self.assertEqual(restarted.aims["aim-1"].description, "written by the other session")
        self.assertEqual(restarted.aims["aim-2"].description, "written by this session")

    def test_stale_expected_sequence_is_refused_by_the_repository(self):
        loop = self.session()
        loop.create_aim("base")
        repository = SqliteEventRepository(self.path)
        event = repository.events()[0]
        with self.assertRaises(JournalConflictError):
            repository.commit(command_id="stale", command_type="create_aim", request_fingerprint="{}",
                              events=(event.__class__("event_stale", event.event_type, event.actor_id, event.subject_kind,
                                                      event.subject_id, event.payload, event.schema_version),),
                              result={}, expected_last_sequence=0)
        self.assertEqual(len(repository.events()), 1)


if __name__ == "__main__":
    unittest.main()
