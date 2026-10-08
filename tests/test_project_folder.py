from contextlib import chdir, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.persistent_cli import main
from goal_loop_tool.project import (
    ProjectLocationError, artifacts_dir_for, attachment_status, normalize_reference, project_root_for,
    resolve_reference, store_attachment,
)


def run(*args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        main(list(args))
    return json.loads(output.getvalue().splitlines()[-1])


class ProjectFolderTests(unittest.TestCase):
    """A project keeps its journal in .goal-loop/ and any command below the folder finds it."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        # The ceiling keeps discovery inside this temporary root even when TEMP sits inside another project.
        self.env = patch.dict("os.environ", {"LOCALAPPDATA": str(self.root / "appdata"), "GOAL_LOOP_CEILING_DIRECTORIES": str(self.root)}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def test_project_init_creates_self_ignoring_folder_and_is_idempotent(self):
        project = self.root / "repo"
        project.mkdir()
        first = run("project-init", str(project))
        self.assertTrue(first["created"])
        self.assertEqual(Path(first["database"]), project / ".goal-loop" / "journal.sqlite3")
        self.assertEqual((project / ".goal-loop" / ".gitignore").read_text(encoding="utf-8"), "*\n")
        self.assertFalse(run("project-init", str(project))["created"])
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            main(["project-init", str(self.root / "missing")])

    def test_commands_below_the_project_discover_its_journal(self):
        project = self.root / "repo"
        (project / "src" / "deep").mkdir(parents=True)
        run("project-init", str(project))
        with chdir(project / "src" / "deep"):
            where = run("where")
            self.assertEqual(where["source"], "project")
            self.assertEqual(Path(where["project_root"]), project)
            self.assertEqual(Path(where["artifacts"]), project / ".goal-loop" / "artifacts")
            aim = run("aim-create", "found from a subfolder")
        self.assertEqual(run("--project", str(project), "status")["aims"], [aim["id"]])
        # An explicit database still wins, and a journal chosen by path still knows its project.
        self.assertEqual(Path(run("--database", str(project / ".goal-loop" / "journal.sqlite3"), "where")["project_root"]), project)

    def test_outside_any_project_the_user_default_is_used_and_where_creates_nothing(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        with chdir(outside):
            where = run("where")
        self.assertEqual(where["source"], "default")
        self.assertIsNone(where["project_root"])
        self.assertFalse(where["exists"])
        self.assertFalse(Path(where["database"]).exists())

    def test_ceiling_stops_discovery_from_reaching_an_enclosing_project(self):
        enclosing = self.root / "enclosing"
        inner = enclosing / "sandbox" / "work"
        inner.mkdir(parents=True)
        with chdir(enclosing):
            run("project-init")
        with chdir(inner):
            self.assertEqual(run("where")["source"], "project")
        with patch.dict("os.environ", {"GOAL_LOOP_CEILING_DIRECTORIES": str(enclosing / "sandbox")}):
            with chdir(inner):
                self.assertEqual(run("where")["source"], "default")

    def test_location_options_are_mutually_exclusive_and_project_must_exist(self):
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            main(["--database", "a.sqlite3", "--project", ".", "where"])
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            main(["--project", str(self.root), "status"])


class ReferenceAndAttachmentLocationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "design.md").write_text("design", encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def test_reference_inside_project_is_stored_relative_and_keeps_anchor(self):
        stored = normalize_reference("design.md#판단 기준", self.root, self.root / "docs")
        self.assertEqual(stored, "docs/design.md#판단 기준")
        self.assertEqual(resolve_reference(stored, self.root), (str(self.root / "docs" / "design.md"), True))

    def test_reference_url_outside_and_missing_cases(self):
        self.assertEqual(normalize_reference("https://example.com/a", self.root, self.root), "https://example.com/a")
        outside = TemporaryDirectory()
        try:
            other = Path(outside.name).resolve() / "note.txt"
            other.write_text("x", encoding="utf-8")
            self.assertEqual(normalize_reference(str(other), self.root, self.root), other.as_posix())
            self.assertEqual(normalize_reference("docs/design.md", None, self.root), (self.root / "docs" / "design.md").as_posix())
        finally:
            outside.cleanup()
        with self.assertRaises(ProjectLocationError):
            normalize_reference("docs/typo.md", self.root, self.root)
        self.assertEqual(resolve_reference("docs/design.md", None), ("docs/design.md", False))

    def test_attachment_copy_is_content_addressed_and_detects_change(self):
        log = self.root / "pytest.log"
        log.write_text("115 passed", encoding="utf-8")
        artifacts = self.root / "artifacts"
        stored = store_attachment(log, artifacts)
        self.assertEqual(stored.size, len("115 passed"))
        log.write_text("edited later", encoding="utf-8")
        self.assertEqual(attachment_status(artifacts, stored.name, stored.sha256), "preserved")
        (artifacts / stored.sha256 / stored.name).write_text("tampered", encoding="utf-8")
        self.assertEqual(attachment_status(artifacts, stored.name, stored.sha256), "changed")
        self.assertEqual(attachment_status(artifacts, "gone.log", stored.sha256), "missing")

    def test_attachment_size_limit_and_folderless_artifacts_dir(self):
        big = self.root / "big.bin"
        with big.open("wb") as handle:
            handle.truncate(10 * 1024 * 1024 + 1)
        with self.assertRaises(ProjectLocationError):
            store_attachment(big, self.root / "artifacts")
        database = self.root / "workspace-1.sqlite3"
        self.assertIsNone(project_root_for(database))
        self.assertEqual(artifacts_dir_for(database), self.root / "workspace-1.artifacts")


if __name__ == "__main__":
    unittest.main()
