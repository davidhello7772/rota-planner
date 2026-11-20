"""Looking for a newer version and installing it from inside the program,
against a pretend repository on this computer (no network)."""

import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from rotaplanner import __main__ as program
from rotaplanner.server import PEN_FREE, local_only
from rotaplanner.store import Store
from rotaplanner.updates import UpdateError, Updates

from . import helpers

PROJECT = Path(__file__).resolve().parent.parent
USABLE = bool(shutil.which("git")) and (PROJECT / ".git").exists()


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(USABLE, "needs git and a git copy of the program")
class UpdatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"  # where new versions are made
        files = subprocess.run(["git", "-C", str(PROJECT), "ls-files", "-z", "-co", "--exclude-standard"],
                               check=True, capture_output=True).stdout.decode().split("\0")
        for name in filter(None, files):
            if (PROJECT / name).is_file():
                (self.work / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PROJECT / name, self.work / name)
        git(self.tmp, "init", "-q", "-b", "master", str(self.work))
        self._commit("The program")
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.work), str(self.origin)], check=True)
        git(self.work, "remote", "add", "origin", str(self.origin))
        self.program = self.tmp / "installed"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.program)], check=True)
        self.store = Store(self.tmp / "data")
        self.store.save_catalog(helpers.catalog())
        self.store.save_people(helpers.people())
        self.updates = Updates(self.program, self.store)

    def _commit(self, message):
        git(self.work, "add", "-A")
        git(self.work, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-q", "-m", message)

    def publish(self, message, name="NEWS.txt", text="new\n", force=False):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if force:
            git(self.work, "add", "-f", name)
        self._commit(message)
        git(self.work, "push", "-q", "origin", "master")

    def test_nothing_new_then_something(self):
        info = self.updates.check()
        self.assertTrue(info["supported"])
        self.assertEqual((info["available"], info["items"], info["problem"]), (0, [], ""))
        self.assertTrue(info["version"] and info["checked_text"])
        self.publish("Printouts: bigger names")
        self.publish("Check: a clearer balance", name="MORE.txt")
        self.assertEqual(self.updates.info()["available"], 0)  # not known before looking: nothing is fetched by info()
        info = self.updates.check()
        self.assertEqual(info["available"], 2)
        self.assertEqual(info["items"], ["Check: a clearer balance", "Printouts: bigger names"])
        again = Updates(self.program, self.store)  # remembered for the next start
        self.assertEqual(again.info()["available"], 2)

    def test_install_keeps_the_data_and_asks_to_start_again(self):
        self.publish("Something new")
        before = (self.store.root / "people.json").read_bytes()
        done = self.updates.install()
        self.assertTrue(done["installed"] and done["restarting"])
        self.assertTrue(self.updates.restart.is_set())
        self.assertTrue((self.program / "NEWS.txt").exists())
        self.assertEqual((self.store.root / "people.json").read_bytes(), before)
        copies = list((self.store.root / "backups").glob("before-update-*.zip"))
        self.assertEqual([done["safety"]], [c.name for c in copies])
        with zipfile.ZipFile(copies[0]) as copy:
            self.assertTrue(any(name.endswith("people.json") for name in copy.namelist()))
        self.assertEqual(self.updates.info()["available"], 0)
        with self.assertRaisesRegex(UpdateError, "already up to date"):
            self.updates.install()

    def test_a_version_that_does_not_load_is_taken_back(self):
        before = git(self.program, "rev-parse", "HEAD")
        self.publish("Broken", name="rotaplanner/broken.py", text="def (\n")
        with self.assertRaisesRegex(UpdateError, "previous one was put back"):
            self.updates.install()
        self.assertEqual(git(self.program, "rev-parse", "HEAD"), before)
        self.assertFalse(self.updates.restart.is_set())

    def test_nothing_of_the_version_taken_back_stays(self):
        self.publish("Broken", name="rotaplanner/broken.py", text="def (\n")
        with self.assertRaises(UpdateError):
            self.updates.install(works=lambda: bool(__import__("compileall").compile_dir(
                str(self.program / "rotaplanner"), quiet=2)) and False)
        self.assertEqual(list((self.program / "rotaplanner").rglob("__pycache__")), [])
        self.assertEqual(self.updates.info()["available"], 1)  # still offered, should it be repaired

    def test_trying_the_new_version_writes_no_compiled_file(self):
        self.assertTrue(self.updates._works())
        self.assertEqual(list((self.program / "rotaplanner").rglob("__pycache__")), [])

    def test_only_what_was_listed_is_installed(self):
        self.publish("Something new")
        listed = self.updates.check()["target"]
        self.assertEqual(listed, git(self.work, "rev-parse", "HEAD"))
        self.publish("One more, since the list was shown", name="MORE.txt")
        with self.assertRaisesRegex(UpdateError, "Look at the list again"):
            self.updates.install(expected=listed)
        self.assertFalse((self.program / "NEWS.txt").exists())
        info = self.updates.info()
        self.assertEqual(info["available"], 2)  # the list is fresh now
        self.assertTrue(self.updates.install(expected=info["target"])["installed"])

    def test_the_data_is_locked_only_while_it_is_copied(self):
        self.publish("Something new")
        lock, seen = threading.Lock(), []
        self.updates.install(works=lambda: seen.append(lock.locked()) or True, data_lock=lock)
        self.assertEqual(seen, [False])
        self.assertFalse(lock.locked())

    def test_installed_meanwhile_with_the_installer(self):
        self.publish("Something new")
        self.assertEqual(self.updates.check()["available"], 1)
        git(self.program, "merge", "-q", "--ff-only", "@{u}")
        self.assertEqual(Updates(self.program, self.store).info()["available"], 0)

    def test_each_run_of_the_program_has_its_own_name(self):
        self.assertRegex(self.updates.info()["boot"], "^[0-9a-f]{16}$")

    def test_changed_program_files_are_never_overwritten(self):
        self.publish("Something new")
        (self.program / "run.py").write_text("# changed here\n")
        with self.assertRaisesRegex(UpdateError, "changed on this computer"):
            self.updates.install()
        self.assertEqual((self.program / "run.py").read_text(), "# changed here\n")

    def test_a_version_with_files_in_the_data_folder_is_never_applied(self):
        self.publish("Committed data by mistake", name="data/catalog.json", text="{}", force=True)
        with self.assertRaisesRegex(UpdateError, "files in the data folder"):
            self.updates.install()
        self.assertFalse((self.program / "data").exists())
        self.assertFalse(list(self.store.backups_dir.glob("before-update-*")) if self.store.backups_dir.exists() else [])

    def test_nor_one_with_the_folder_spelt_in_capitals(self):
        # On a Mac's or Windows' disk, Data/ is data/.
        self.publish("Committed data by mistake", name="Data/catalog.json", text="{}", force=True)
        with self.assertRaisesRegex(UpdateError, "files in the data folder"):
            self.updates.install()
        self.assertFalse((self.program / "Data").exists())

    def test_no_repository_no_fuss(self):
        shutil.rmtree(self.origin)
        info = self.updates.check()
        self.assertIn("could not be reached", info["problem"])
        with self.assertRaisesRegex(UpdateError, "Nothing was changed"):
            self.updates.install()

    def test_a_copy_without_git_says_how_to_update(self):
        plain = Updates(self.tmp / "unzipped", self.store)
        info = plain.check()
        self.assertFalse(info["supported"])
        self.assertIn("by hand", info["reason"])
        with self.assertRaises(UpdateError):
            plain.install()

    def test_looking_by_itself_once_a_day_can_be_turned_off(self):
        now = [1_000_000.0]
        updates = Updates(self.program, self.store, clock=lambda: now[0])
        self.assertTrue(updates.due())
        updates.check()
        self.assertFalse(updates.due())
        now[0] += 21 * 3600
        self.assertTrue(updates.due())
        updates.set_auto(False)
        self.assertFalse(updates.due())
        self.assertFalse(Updates(self.program, self.store, clock=lambda: now[0]).due())  # remembered

    def test_without_connection_it_does_not_ask_again_at_once(self):
        now = [1_000_000.0]
        updates = Updates(self.program, self.store, clock=lambda: now[0])
        shutil.rmtree(self.origin)
        self.assertTrue(updates.check()["problem"])
        self.assertFalse(updates.due())
        now[0] += 2 * 3600
        self.assertTrue(updates.due())
        self.assertEqual((self.store.root / "updates.json").stat().st_mode & 0o077, 0)


class StartingAgainTest(unittest.TestCase):
    def command(self, *typed, port=8765):
        args = program.parse_args(list(typed))
        demo = args.demo or args.reset_demo
        root = args.data or program.PROJECT_ROOT / ("data-demo" if demo else "data")
        args.port = port  # the one in use
        return program.restart_command(args, root, demo)[2:]

    def test_the_same_data_and_the_port_in_use(self):
        self.assertEqual(self.command(), ["--port", "8765", "--no-browser", "--after-update"])
        self.assertEqual(self.command("--window", "--port", "9000", port=9003)[:2], ["--port", "9003"])
        self.assertIn("--window", self.command("--window"))

    def test_nothing_is_reset_a_second_time(self):
        for typed in (["--reset-demo"], ["--reset-dem"], ["--demo", "--reset-access"], ["--demo", "--reset-acc"]):
            command = self.command(*typed)
            self.assertIn("--demo", command, typed)  # still the demonstration data, never the real one
            self.assertFalse([word for word in command if word.startswith("--reset")], typed)

    def test_a_data_folder_is_named_in_full(self):
        command = self.command("--data", "some/folder")
        named = Path(command[command.index("--data") + 1])
        self.assertTrue(named.is_absolute())
        self.assertEqual(named, (Path.cwd() / "some/folder").resolve())


class UpdateRoutesTest(unittest.TestCase):
    def test_installing_is_for_this_computer_and_needs_no_pen(self):
        for path in ("/api/update/check", "/api/update/install", "/api/update/options"):
            self.assertTrue(local_only(path), path)
            self.assertIsNotNone(PEN_FREE.fullmatch(path), path)
        self.assertFalse(local_only("/api/update"))  # anyone using the program may read which version it is


if __name__ == "__main__":
    unittest.main()
