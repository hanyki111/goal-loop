from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.persistent_cli import main
from goal_loop_tool.project import init_project


def run(database, *args):
    output = StringIO()
    with redirect_stdout(output):
        main(["--database", str(database), *args])
    return json.loads(output.getvalue().splitlines()[-1])


def digest(database: Path) -> dict:
    return {suffix: (hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None)
            for suffix, path in (("", database), ("-wal", database.with_name(database.name + "-wal")),
                                 ("-shm-exists", database.with_name(database.name + "-shm")))}


class UmbrellaNextTests(unittest.TestCase):
    """An umbrella journal's next gathers its linked journals' next actions with their source (goal-142)."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        root = Path(self.directory.name).resolve()
        self.roots = {name: root / name for name in ("notes", "game", "music")}
        for folder in self.roots.values():
            folder.mkdir()
        self.db = {name: init_project(folder)[0] for name, folder in self.roots.items()}
        self.side = run(self.db["notes"], "aim-create", "사이드 프로젝트")
        self.hobby = run(self.db["notes"], "aim-create", "취미")
        self.game_aim = run(self.db["game"], "aim-create", "게임을 만든다")
        self.combat = run(self.db["game"], "aim-create", "전투", "--parent", self.game_aim["id"])
        self.other = run(self.db["game"], "aim-create", "연결되지 않은 목표")
        self.music_aim = run(self.db["music"], "aim-create", "피아노")
        self.game_link = self.link(self.side, "game", self.game_aim)
        self.music_link = self.link(self.hobby, "music", self.music_aim)

    def tearDown(self):
        self.directory.cleanup()

    def link(self, aim, name, external):
        return run(self.db["notes"], "aim-link-add", aim["id"], "--project", str(self.roots[name]),
                   "--external-aim", external["id"], "--actor", "claude")

    def goal(self, name, aim, key):
        proposal = run(self.db[name], "proposal-create", aim["id"], key)
        return run(self.db[name], "goal-accept", proposal["id"], "--target", key, "--responsibility", "produce",
                   "--scope", "s", "--criterion", "c", "--verification", "v", "--key", key)

    def next(self, *args):
        return run(self.db["notes"], "next", *args)

    def linked(self, record, reference):
        return next(item for item in record["linked"] if item["reference_id"] == reference["id"])

    def test_linked_actions_carry_their_source_and_point_at_the_linked_journal(self):
        combat_goal = self.goal("game", self.combat, "combat")
        self.goal("game", self.other, "unlinked")
        record = self.next()
        game = self.linked(record, self.game_link)
        self.assertEqual((game["state"], game["reason"]), ("open", None))
        self.assertEqual([(a["kind"], a["subject"]["id"]) for a in game["actions"]], [("start_attempt", combat_goal["id"])])  # subtree only
        action = game["actions"][0]
        self.assertEqual(action["source"], {"reference_id": self.game_link["id"], "aim_id": self.side["id"], "project": str(self.roots["game"]),
                                            "database": str(self.db["game"]), "external_aim_id": self.game_aim["id"]})
        self.assertEqual(action["command_argv"][:3], ["goal-loop", "--database", str(self.db["game"])])
        music = self.linked(record, self.music_link)
        self.assertEqual([a["kind"] for a in music["actions"]], ["create_proposal"])
        self.assertEqual(record["skipped"], [])
        self.assertEqual(record["database"], str(self.db["notes"]))

    def test_paused_abandoned_or_achieved_umbrella_aims_are_skipped_with_a_reason(self):
        self.goal("game", self.combat, "combat")
        run(self.db["notes"], "aim-pause", self.side["id"], "owner", "쉰다")
        record = self.next()
        self.assertEqual([item["reference_id"] for item in record["linked"]], [self.music_link["id"]])
        [skipped] = record["skipped"]
        self.assertEqual(skipped["reference_id"], self.game_link["id"])
        self.assertIn("멈춤", skipped["reason"])
        run(self.db["notes"], "aim-resume", self.side["id"], "owner", "다시")
        run(self.db["notes"], "aim-abandon", self.hobby["id"], "--actor", "owner", "--reason", "그만둠")
        record = self.next()
        self.assertEqual([item["reference_id"] for item in record["skipped"]], [self.music_link["id"]])
        self.assertIn("폐기", record["skipped"][0]["reason"])

    def test_parent_pause_skips_links_of_sub_aims(self):
        child = run(self.db["notes"], "aim-create", "게임 하위", "--parent", self.side["id"])
        link = self.link(child, "game", self.combat)
        run(self.db["notes"], "aim-pause", self.side["id"], "owner", "쉰다")
        self.assertIn(link["id"], [item["reference_id"] for item in self.next()["skipped"]])

    def test_a_broken_link_is_reported_and_the_rest_still_shows(self):
        own = run(self.db["notes"], "proposal-create", self.side["id"], "직접 하는 일")
        shutil.rmtree(self.roots["music"])
        record = self.next()
        music = self.linked(record, self.music_link)
        self.assertEqual((music["state"], music["actions"]), ("broken", []))
        self.assertIn("폴더가 없습니다", music["reason"])
        self.assertEqual(self.linked(record, self.game_link)["state"], "open")
        self.assertIn(("accept_proposal", own["id"]), [(a["kind"], a["subject"]["id"]) for a in record["actions"]])

    def test_an_achieved_linked_aim_suggests_importing_until_it_is_imported(self):
        goal = self.goal("game", self.game_aim, "release")
        attempt = run(self.db["game"], "attempt-start", goal["id"], "1", "claude", "op")
        run(self.db["game"], "attempt-complete", attempt["id"], "succeeded")
        evidence = run(self.db["game"], "evidence-record", attempt["id"], "claude", "observed")
        run(self.db["game"], "verification-begin", goal["id"], attempt["id"])
        verdict = run(self.db["game"], "verdict-submit", goal["id"], attempt["id"], "codex", "satisfied", evidence["id"])
        run(self.db["game"], "aim-evaluate", self.game_aim["id"], "codex", "출시", verdict["id"])
        [suggestion] = [a for a in self.next()["actions"] if a["kind"] == "import_linked_evaluation"]
        self.assertEqual(suggestion["subject"], {"kind": "reference", "id": self.game_link["id"]})
        self.assertEqual(suggestion["aim_id"], self.side["id"])
        self.assertEqual(suggestion["command_argv"][3:], ["aim-link-import", self.game_link["id"], "--actor", "<actor>"])
        run(self.db["notes"], "aim-link-import", self.game_link["id"], "--actor", "claude")
        actions = self.next()["actions"]
        self.assertFalse(any(a["kind"] == "import_linked_evaluation" for a in actions))
        self.assertIn(("evaluate_aim", self.side["id"]), [(a["kind"], a["subject"]["id"]) for a in actions])

    def test_a_linked_aim_is_not_asked_for_its_own_proposal(self):
        unlinked = run(self.db["notes"], "aim-create", "아직 연결 없음")
        kinds = [(a["kind"], a["subject"]["id"]) for a in self.next()["actions"]]
        self.assertNotIn(("create_proposal", self.side["id"]), kinds)
        self.assertIn(("create_proposal", unlinked["id"]), kinds)
        run(self.db["notes"], "reference-remove", self.game_link["id"], "--actor", "owner", "--reason", "해제")
        self.assertIn(("create_proposal", self.side["id"]), [(a["kind"], a["subject"]["id"]) for a in self.next()["actions"]])

    def test_aim_scope_limits_the_links(self):
        record = self.next("--aim", self.hobby["id"])
        self.assertEqual([item["reference_id"] for item in record["linked"]], [self.music_link["id"]])
        self.assertEqual(record["skipped"], [])

    def test_without_links_the_output_has_no_linked_keys_and_linked_journals_are_not_written(self):
        alone = Path(self.directory.name) / "alone"
        alone.mkdir()
        database = init_project(alone)[0]
        run(database, "aim-create", "혼자")
        self.assertEqual(set(run(database, "next")), {"database", "actions"})
        self.goal("game", self.combat, "combat")
        before = {name: digest(self.db[name]) for name in ("game", "music")}
        self.next(); self.next("--aim", self.side["id"])
        self.assertEqual({name: digest(self.db[name]) for name in ("game", "music")}, before)


if __name__ == "__main__":
    unittest.main()
