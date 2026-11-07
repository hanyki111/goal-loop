from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.persistent_cli import main
from goal_loop_tool.project import init_project, project_database
from goal_loop_tool.repository import SqliteEventRepository


def run(database, *args):
    output = StringIO()
    with redirect_stdout(output):
        main(["--database", str(database), *args])
    return json.loads(output.getvalue().splitlines()[-1])


def fails(database, *args) -> str:
    errors = StringIO()
    with redirect_stderr(errors), redirect_stdout(StringIO()):
        try:
            main(["--database", str(database), *args])
        except SystemExit as raised:
            if raised.code not in (0, None):
                return errors.getvalue()
    raise AssertionError(f"command succeeded: {args}")


def file_state(database: Path) -> dict:
    """Content hashes of the journal and its WAL, and which side files exist (-shm content is SQLite's lock index)."""
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    wal, shm = (database.with_name(database.name + suffix) for suffix in ("-wal", "-shm"))
    return {"journal": digest(database), "wal": digest(wal), "wal_exists": wal.exists(), "shm_exists": shm.exists()}


class AimLinkTests(unittest.TestCase):
    """An umbrella journal links its Aims to Aims of other project journals and reads them read-only (goal-139)."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        root = Path(self.directory.name)
        self.umbrella_root, self.game_root = root / "notes", root / "game"
        self.umbrella_root.mkdir(); self.game_root.mkdir()
        self.umbrella = init_project(self.umbrella_root)[0]
        self.game = init_project(self.game_root)[0]
        self.side = run(self.umbrella, "aim-create", "사이드 프로젝트")
        self.game_aim = run(self.game, "aim-create", "게임을 만든다")
        self.combat = run(self.game, "aim-create", "전투", "--parent", self.game_aim["id"])

    def tearDown(self):
        self.directory.cleanup()

    def link(self, *target):
        return run(self.umbrella, "aim-link-add", self.side["id"], *target, "--external-aim", self.game_aim["id"], "--actor", "claude")

    def links(self, *args):
        return run(self.umbrella, "aim-links", *args)["links"]

    def test_link_is_a_reference_on_the_umbrella_aim_and_shows_the_linked_state(self):
        reference = self.link("--project", str(self.game_root))
        self.assertEqual(reference["location"], f"goal-loop-aim:{self.game_root.resolve()}#{self.game_aim['id']}")
        self.assertEqual(reference["label"], f"Goal Loop Aim game/{self.game_aim['id']}")
        self.assertEqual(reference["subject_id"], self.side["id"])
        proposal = run(self.game, "proposal-create", self.combat["id"], "전투 프로토타입")
        goal = run(self.game, "goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce",
                   "--scope", "s", "--criterion", "c", "--verification", "v", "--key", "k")
        [record] = self.links()
        self.assertEqual(record["state"], "open")
        self.assertEqual(record["external_aim_description"], "게임을 만든다")
        self.assertEqual(record["live_goal_ids"], [goal["id"]])  # a Goal under a sub-Aim counts
        self.assertEqual(record["database"], str(project_database(self.game_root.resolve())))
        self.assertIsNone(record["reason"])
        run(self.game, "aim-pause", self.game_aim["id"], "owner", "rest")
        [record] = self.links("--aim", self.side["id"])
        self.assertEqual((record["state"], record["paused_by"]), ("paused", self.game_aim["id"]))

    def test_a_link_reference_resolves_to_the_linked_project_folder(self):
        reference = self.link("--project", str(self.game_root))
        def resolved():
            aim = next(item for item in run(self.umbrella, "ui-projection")["aims"] if item["id"] == self.side["id"])
            return next((item["resolved"], item["exists"]) for item in aim["references"] if item["id"] == reference["id"])
        self.assertEqual(resolved(), (str(self.game_root.resolve()), True))  # not joined onto this journal's folder
        shutil.rmtree(self.game_root)
        self.assertEqual(resolved(), (str(self.game_root.resolve()), False))

    def test_ui_projection_carries_the_same_links(self):
        self.assertEqual(run(self.umbrella, "ui-projection")["aim_links"], [])
        self.link("--project", str(self.game_root))
        projection = run(self.umbrella, "ui-projection")
        self.assertEqual(projection["schema_version"], 18)
        self.assertEqual(projection["aim_links"], self.links())

    def test_achieved_and_abandoned_states_come_from_the_linked_journal(self):
        other = run(self.game, "aim-create", "버릴 방향")
        run(self.umbrella, "aim-link-add", self.side["id"], "--project", str(self.game_root), "--external-aim", other["id"], "--actor", "claude")
        self.link("--project", str(self.game_root))
        proposal = run(self.game, "proposal-create", self.game_aim["id"], "출시")
        goal = run(self.game, "goal-accept", proposal["id"], "--target", "t", "--responsibility", "produce",
                   "--scope", "s", "--criterion", "c", "--verification", "v", "--key", "k")
        attempt = run(self.game, "attempt-start", goal["id"], "1", "claude", "op")
        run(self.game, "attempt-complete", attempt["id"], "succeeded")
        evidence = run(self.game, "evidence-record", attempt["id"], "claude", "observed")
        run(self.game, "verification-begin", goal["id"], attempt["id"])
        verdict = run(self.game, "verdict-submit", goal["id"], attempt["id"], "codex", "satisfied", evidence["id"])
        evaluation = run(self.game, "aim-evaluate", self.game_aim["id"], "codex", "출시함", verdict["id"])
        run(self.game, "aim-abandon", other["id"], "--actor", "owner", "--reason", "그만둠")
        states = {record["external_aim_id"]: record for record in self.links()}
        self.assertEqual(states[self.game_aim["id"]]["state"], "achieved")
        self.assertEqual(states[self.game_aim["id"]]["valid_evaluation_ids"], [evaluation["id"]])
        self.assertEqual(states[other["id"]]["state"], "abandoned")

    def test_linking_is_refused_when_the_target_cannot_be_followed(self):
        self.assertIn("aim-99", fails(self.umbrella, "aim-link-add", self.side["id"], "--project", str(self.game_root),
                                      "--external-aim", "aim-99", "--actor", "claude"))
        self.assertIn("폴더가 없습니다", fails(self.umbrella, "aim-link-add", self.side["id"], "--project", str(self.game_root / "missing"),
                                          "--external-aim", "aim-1", "--actor", "claude"))
        (self.game_root / "plain").mkdir()
        self.assertIn("저널이 없습니다", fails(self.umbrella, "aim-link-add", self.side["id"], "--project", str(self.game_root / "plain"),
                                          "--external-aim", "aim-1", "--actor", "claude"))
        self.assertIn("같은 저널", fails(self.umbrella, "aim-link-add", self.side["id"], "--project", str(self.umbrella_root),
                                       "--external-aim", self.side["id"], "--actor", "claude"))
        self.link("--project", str(self.game_root))
        self.assertIn("이미", fails(self.umbrella, "aim-link-add", self.side["id"], "--project", str(self.game_root),
                                   "--external-aim", self.game_aim["id"], "--actor", "claude"))
        self.assertIn("aim-42", fails(self.umbrella, "aim-link-add", "aim-42", "--project", str(self.game_root),
                                      "--external-aim", self.game_aim["id"], "--actor", "claude"))
        self.assertEqual(len(self.links()), 1)  # refused links record nothing

    def counts(self):
        """Events, receipts and checkpoint of the linked journal, read before or after the file comparison."""
        import sqlite3
        connection = sqlite3.connect(self.game)
        try:
            return tuple(connection.execute(f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM {table}").fetchone()
                         for table in ("event_journal", "command_receipts", "projection_checkpoint")),                 connection.execute("SELECT last_sequence FROM projection_checkpoint").fetchall()
        finally:
            connection.close()

    def test_reading_without_a_wal_file_creates_no_files(self):
        self.link("--project", str(self.game_root))
        counts = self.counts()  # closing the last connection checkpoints and removes -wal/-shm
        before = file_state(self.game)
        self.assertFalse(before["wal_exists"] or before["shm_exists"])
        self.links()
        run(self.umbrella, "doctor")
        self.assertEqual(file_state(self.game), before)
        self.assertEqual(self.counts(), counts)

    def test_wal_records_are_read_while_a_writer_holds_the_journal_open(self):
        self.link("--project", str(self.game_root))
        import sqlite3
        writer = sqlite3.connect(self.game)  # an open connection keeps -wal and -shm in place
        try:
            writer.execute("PRAGMA wal_autocheckpoint = 0")
            writer.execute("SELECT COUNT(*) FROM event_journal").fetchone()
            run(self.game, "aim-pause", self.game_aim["id"], "owner", "rest")
            self.assertTrue(file_state(self.game)["wal_exists"])
            events = len(SqliteEventRepository.read_only_events(self.game))
            counts = self.counts()
            before = file_state(self.game)
            [record] = self.links()
            self.assertEqual(record["state"], "paused")  # the pause lives only in the WAL so far
            run(self.umbrella, "doctor")
            self.assertEqual(file_state(self.game), before)  # journal and WAL bytes; -wal/-shm presence
            self.assertEqual(len(SqliteEventRepository.read_only_events(self.game)), events)
            self.assertEqual(self.counts(), counts)
        finally:
            writer.close()

    def test_a_wal_left_without_shm_is_read_from_a_copy_and_no_shm_appears(self):
        import sqlite3
        crashed_root = Path(self.directory.name) / "crashed"
        (crashed_root / ".goal-loop").mkdir(parents=True)
        writer = sqlite3.connect(self.game)  # keeps the pause below in the WAL only
        try:
            writer.execute("PRAGMA wal_autocheckpoint = 0")
            writer.execute("SELECT COUNT(*) FROM event_journal").fetchone()
            run(self.game, "aim-pause", self.game_aim["id"], "owner", "rest")
            crashed = project_database(crashed_root)
            shutil.copyfile(self.game, crashed)  # as a crash leaves it: journal and WAL, no -shm
            shutil.copyfile(self.game.with_name(self.game.name + "-wal"), crashed.with_name(crashed.name + "-wal"))
        finally:
            writer.close()
        run(self.umbrella, "aim-link-add", self.side["id"], "--project", str(crashed_root), "--external-aim", self.game_aim["id"], "--actor", "claude")
        before = file_state(crashed)
        self.assertEqual((before["wal_exists"], before["shm_exists"]), (True, False))
        [record] = self.links()
        self.assertEqual(record["state"], "paused")  # the WAL-only record was read
        run(self.umbrella, "doctor")
        self.assertEqual(file_state(crashed), before)

    def test_broken_links_are_reported_and_removed_links_disappear(self):
        reference = self.link("--project", str(self.game_root))
        other_root = Path(self.directory.name) / "other"
        other_root.mkdir(); init_project(other_root)
        other_aim = run(project_database(other_root), "aim-create", "다른 일")
        run(self.umbrella, "aim-link-add", self.side["id"], "--project", str(other_root), "--external-aim", other_aim["id"], "--actor", "claude")
        shutil.rmtree(self.game_root)
        records = {record["reference_id"]: record for record in self.links()}
        self.assertEqual(records[reference["id"]]["state"], "broken")
        self.assertIn("폴더가 없습니다", records[reference["id"]]["reason"])
        self.assertEqual(len(records), 2)
        self.assertNotEqual(next(r for r in records.values() if r["reference_id"] != reference["id"])["state"], "broken")
        doctor = run(self.umbrella, "doctor")
        self.assertEqual([item["reference_id"] for item in doctor["broken_aim_links"]], [reference["id"]])
        run(self.umbrella, "reference-remove", reference["id"], "--actor", "owner", "--reason", "저장소 삭제")
        self.assertEqual(run(self.umbrella, "doctor")["broken_aim_links"], [])
        self.assertEqual(len(self.links()), 1)

    def test_a_missing_aim_or_unreadable_journal_breaks_only_that_link(self):
        self.link("--project", str(self.game_root))
        bad_root = Path(self.directory.name) / "bad"
        bad_root.mkdir(); init_project(bad_root)
        bad_aim = run(project_database(bad_root), "aim-create", "x")
        run(self.umbrella, "aim-link-add", self.side["id"], "--project", str(bad_root), "--external-aim", bad_aim["id"], "--actor", "claude")
        for suffix in ("", "-wal", "-shm"):
            path = project_database(bad_root).with_name(project_database(bad_root).name + suffix)
            if path.exists():
                path.unlink()
        project_database(bad_root).write_bytes(b"not a journal")
        records = {record["project"]: record for record in self.links()}
        self.assertEqual(records[str(bad_root.resolve())]["state"], "broken")
        self.assertIn("읽을 수 없습니다", records[str(bad_root.resolve())]["reason"])
        self.assertEqual(records[str(self.game_root.resolve())]["state"], "open")


if __name__ == "__main__":
    unittest.main()
