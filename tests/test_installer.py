"""The one-file installer and updater (``install-rota-planner.sh``), tried for
real on a pretend GitHub: a local repository in a temporary folder, a pretend
home folder, no network. Mac and Linux only (it is a bash script)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from rotaplanner.__main__ import RUNNING_FILE
from rotaplanner.store import Store
from tests import helpers

PROJECT = Path(__file__).resolve().parent.parent
SCRIPT = "install-rota-planner.sh"
USABLE = (os.name == "posix" and shutil.which("bash") and shutil.which("git")
          and (PROJECT / ".git").exists())
WINDOW_PYTHON = USABLE and subprocess.run(
    ["python3", "-c", "import sys, tkinter; sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)"],
    capture_output=True).returncode == 0


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


class RunningMarkTest(unittest.TestCase):
    def test_the_program_notes_it_is_running_and_removes_the_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / RUNNING_FILE
            code = ("import sys; from pathlib import Path; from rotaplanner.__main__ import mark_running;"
                    "mark_running(Path(sys.argv[1])); print((Path(sys.argv[1]) / '.running').read_text().strip())")
            run = subprocess.run([sys.executable, "-c", code, tmp], cwd=PROJECT, capture_output=True,
                                 text=True, check=True)
            self.assertTrue(run.stdout.strip().isdigit())
            self.assertFalse(marker.exists())

    def test_a_note_left_by_a_crash_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / RUNNING_FILE
            marker.write_text("12345\n")
            code = ("import sys; from pathlib import Path; from rotaplanner.__main__ import mark_running;"
                    "mark_running(Path(sys.argv[1])); print((Path(sys.argv[1]) / '.running').read_text())")
            run = subprocess.run([sys.executable, "-c", code, tmp], cwd=PROJECT, capture_output=True,
                                 text=True, check=True)
            self.assertEqual(run.stdout.split(), [str(run.stdout.split()[0])])
            self.assertNotEqual(run.stdout.split()[0], "12345")
            self.assertFalse(marker.exists())


@unittest.skipUnless(USABLE, "needs bash, git and a git copy of the program")
class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        # The pretend GitHub: the program as it is now (committed or not), without data.
        self.work = self.tmp / "work"
        files = subprocess.run(["git", "-C", str(PROJECT), "ls-files", "-z", "-co", "--exclude-standard"],
                               check=True, capture_output=True).stdout.decode().split("\0")
        for name in filter(None, files):
            source = PROJECT / name
            if not source.is_file():
                continue  # deleted but not yet committed
            target = self.work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        git(self.tmp, "init", "-q", "-b", "master", str(self.work))
        self._commit("The program")
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.work), str(self.origin)], check=True)
        git(self.work, "remote", "add", "origin", str(self.origin))
        self.folder = self.home / "Rota Planner"

    def _commit(self, message):
        git(self.work, "add", "-A")
        git(self.work, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-q", "-m", message)

    def _publish(self, message, name="NEWS.txt", text="new\n"):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self._commit(message)
        git(self.work, "push", "-q", "origin", "master")

    def run_script(self, *args, script=None, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "ROTA_"))}
        env.update(HOME=str(self.home), XDG_DATA_HOME=str(self.home / ".local" / "share"),
                   DISPLAY="", WAYLAND_DISPLAY="", **extra)
        script = script or (self.work / SCRIPT)
        return subprocess.run(["bash", str(script), *args], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)

    def install(self, *args, **extra):
        run = self.run_script("--repo", str(self.origin), "--folder", str(self.folder), *args, **extra)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return run

    def test_install_then_launchers_then_nothing_new(self):
        run = self.install(ROTA_INSTALL_WINDOW="no")
        self.assertIn("Rota Planner is installed", run.stdout)
        self.assertTrue((self.folder / "run.py").exists())
        self.assertEqual(git(self.folder, "config", "pull.ff"), "only")
        self.assertNotIn("Update Rota Planner", run.stdout)
        if sys.platform == "darwin":
            self.assertFalse((self.home / "Applications" / "Update Rota Planner.app").exists())
        else:
            apps = self.home / ".local" / "share" / "applications"
            self.assertFalse((apps / "rota-planner-update.desktop").exists())  # new versions: in Rota Planner itself
            start = (apps / "rota-planner.desktop").read_text()
            self.assertIn(f'Exec="{self.folder}/rota-planner.sh"', start)
            self.assertIn("Terminal=true", start)
            icon = self.home / ".local" / "share" / "icons" / "rota-planner.png"
            self.assertTrue(icon.read_bytes().startswith(b"\x89PNG"))
            self.assertIn(f"Icon={icon}", start)
            if shutil.which("desktop-file-validate"):
                for name in ("rota-planner.desktop",):
                    check = subprocess.run(["desktop-file-validate", str(apps / name)], capture_output=True, text=True)
                    self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        again = self.run_script("--update", script=self.folder / SCRIPT)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("up to date", again.stdout)

    def test_update_keeps_the_data_and_saves_a_copy_first(self):
        self.install()
        store = Store(self.folder / "data")
        store.save_catalog(helpers.catalog())
        store.save_people(helpers.people())
        before = (self.folder / "data" / "people.json").read_bytes()
        self._publish("Printouts: bigger names")
        run = self.run_script("--update", script=self.folder / SCRIPT)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Printouts: bigger names", run.stdout)
        self.assertTrue((self.folder / "NEWS.txt").exists())
        self.assertEqual((self.folder / "data" / "people.json").read_bytes(), before)
        copies = list((self.folder / "data" / "backups").glob("before-update-*.zip"))
        self.assertEqual(len(copies), 1)
        with zipfile.ZipFile(copies[0]) as copy:
            self.assertTrue(any(name.endswith("people.json") for name in copy.namelist()))

    def test_no_update_while_the_program_is_open(self):
        self.install()
        self._publish("Something new")
        program = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "run.py"])
        self.addCleanup(program.wait)
        self.addCleanup(program.kill)
        data = self.folder / "data"
        data.mkdir(exist_ok=True)
        (data / RUNNING_FILE).write_text(f"999999999\n{program.pid}\n")  # a line left by a crash, then the open one
        run = self.run_script("--update", script=self.folder / SCRIPT)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Rota Planner is open", run.stdout)
        self.assertFalse((self.folder / "NEWS.txt").exists())
        program.kill()
        program.wait()
        run = self.run_script("--update", script=self.folder / SCRIPT)  # the note left behind is harmless
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_a_version_that_does_not_load_is_taken_back(self):
        self.install()
        before = git(self.folder, "rev-parse", "HEAD")
        self._publish("Broken", name="rotaplanner/broken.py", text="def (\n")
        run = self.run_script("--update", script=self.folder / SCRIPT)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("previous\nversion was put back", run.stdout)
        self.assertIn("broken.py", run.stdout)  # what went wrong is shown
        self.assertEqual(git(self.folder, "rev-parse", "HEAD"), before)

    @unittest.skipUnless(sys.platform.startswith("linux") and WINDOW_PYTHON, "needs a Python with Tk 8.6 on Linux")
    def test_the_icon_opens_the_window_when_python_can(self):
        run = self.install()
        self.assertNotIn("python3-tk", run.stdout)
        start = (self.home / ".local" / "share" / "applications" / "rota-planner.desktop").read_text()
        self.assertIn(f'run.py" --window', start)
        self.assertIn(f'"{self.folder}/run.py"', start)
        self.assertIn("Terminal=false", start)
        self.assertIn("StartupWMClass=Rota-planner\n", start)  # the window's class: the taskbar shows this icon

    def test_mac_icons(self):
        import plistlib
        self.install(ROTA_INSTALL_LAUNCHERS="mac", ROTA_INSTALL_WINDOW="no")
        apps = self.home / "Applications"
        start = apps / "Rota Planner.app"
        self.assertFalse((apps / "Update Rota Planner.app").exists())
        for app, role in ((start, "start"),):
            with open(app / "Contents" / "Info.plist", "rb") as plist:
                info = plistlib.load(plist)
            self.assertEqual(info["CFBundleExecutable"], "launch")
            self.assertEqual(info["CFBundleIdentifier"], f"local.rotaplanner.{role}")
            launch = app / "Contents" / "MacOS" / "launch"
            self.assertTrue(os.access(launch, os.X_OK))
            check = subprocess.run(["sh", "-n", str(launch)], capture_output=True)
            self.assertEqual(check.returncode, 0)
        self.assertIn(f'exec /usr/bin/open "{self.folder}/Rota Planner.command"',
                      (start / "Contents" / "MacOS" / "launch").read_text())
        self.assertTrue(os.access(self.folder / "Rota Planner.command", os.X_OK))
        # An app of the same name that this installer did not make is left alone, and said so.
        shutil.rmtree(start)
        (start / "Contents").mkdir(parents=True)
        (start / "Contents" / "Info.plist").write_text("someone else's")
        run = self.run_script("--update", script=self.folder / SCRIPT, ROTA_INSTALL_LAUNCHERS="mac")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual((start / "Contents" / "Info.plist").read_text(), "someone else's")

    def _old_update_launcher(self, folder, mac):
        """The "Update Rota Planner" icon an older version made for ``folder``."""
        if mac:
            app = self.home / "Applications" / "Update Rota Planner.app"
            (app / "Contents" / "Resources").mkdir(parents=True)
            (app / "Contents" / "Info.plist").write_text("<string>local.rotaplanner.update</string>")
            (app / "Contents" / "Resources" / "Update Rota Planner.command").write_text(
                f'#!/bin/sh\nexec /bin/bash "{folder}/install-rota-planner.sh" --update --wait --folder "{folder}"\n')
            return app
        entry = self.home / ".local" / "share" / "applications" / "rota-planner-update.desktop"
        entry.write_text(f"[Desktop Entry]\nName=Update Rota Planner\nPath={folder}\n")
        return entry

    def test_the_old_update_icon_goes_when_updating(self):
        for mac in (False, True):
            with self.subTest(mac=mac):
                launchers = "mac" if mac else "linux"
                if not self.folder.exists():
                    self.install(ROTA_INSTALL_LAUNCHERS=launchers, ROTA_INSTALL_WINDOW="no")
                old = self._old_update_launcher(self.folder, mac)
                run = self.run_script("--update", script=self.folder / SCRIPT, ROTA_INSTALL_LAUNCHERS=launchers,
                                      ROTA_INSTALL_WINDOW="no")
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertFalse(old.exists())
                # One made for another copy is left alone.
                other = self._old_update_launcher(self.home / "Other copy", mac)
                run = self.run_script("--update", script=self.folder / SCRIPT, ROTA_INSTALL_LAUNCHERS=launchers,
                                      ROTA_INSTALL_WINDOW="no")
                self.assertTrue(other.exists())
                shutil.rmtree(other) if other.is_dir() else other.unlink()

    def test_changed_program_files_are_never_overwritten(self):
        self.install()
        self._publish("Something new")
        (self.folder / "run.py").write_text("# changed here\n")
        run = self.run_script("--update", script=self.folder / SCRIPT)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("were changed on this computer", run.stdout)
        self.assertEqual((self.folder / "run.py").read_text(), "# changed here\n")

    def test_data_brought_from_an_older_copy(self):
        old = self.tmp / "rota-planner-master"
        Store(old / "data").save_catalog(helpers.catalog())
        self.install("--from", f"'{old}' ")  # as dragged into a Terminal
        self.assertTrue((self.folder / "data" / "catalog.json").exists())
        self.assertTrue((old / "data" / "catalog.json").exists())  # the old copy is left as it was

    def test_a_version_with_files_in_the_data_folder_is_never_applied(self):
        self.install()
        store = Store(self.folder / "data")
        store.save_catalog(helpers.catalog())
        before = (self.folder / "data" / "catalog.json").read_bytes()
        path = self.work / "data" / "catalog.json"
        path.parent.mkdir()
        path.write_text("{}")
        git(self.work, "add", "-f", "data/catalog.json")
        self._commit("Committed data by mistake")
        git(self.work, "push", "-q", "origin", "master")
        run = self.run_script("--update", script=self.folder / SCRIPT)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("contains files in the data folder", run.stdout)
        self.assertEqual((self.folder / "data" / "catalog.json").read_bytes(), before)
        self.assertFalse(list((self.folder / "data").glob("backups/before-update-*")))

    def test_brought_data_is_private(self):
        old = self.tmp / "unzipped"
        Store(old / "data").save_catalog(helpers.catalog())
        os.chmod(old / "data", 0o755)
        os.chmod(old / "data" / "catalog.json", 0o644)
        self.install("--from", str(old))
        data = self.folder / "data"
        self.assertEqual(data.stat().st_mode & 0o777, 0o700)
        self.assertEqual((data / "catalog.json").stat().st_mode & 0o077, 0)

    @unittest.skipUnless(sys.platform.startswith("linux") and any(
        shutil.which(t) for t in ("apt-get", "dnf", "pacman", "zypper")), "needs a Linux package manager")
    def test_missing_tk_is_offered_and_installed_with_sudo(self):
        tools = self.tmp / "tools"
        tools.mkdir()
        log = self.tmp / "sudo.log"
        (tools / "sudo").write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')  # a pretend sudo: installs nothing
        (tools / "sudo").chmod(0o755)
        run = self.install("--yes", ROTA_INSTALL_PRETEND_NO_TK="1", PATH=f"{tools}:{os.environ['PATH']}")
        asked = log.read_text()
        self.assertIn("install", asked)
        self.assertRegex(asked, r"python3-tk|python3-tkinter|\btk\b")
        self.assertIn("needs Tk for Python", run.stdout)
        self.assertIn("python3-tk", run.stdout)  # still no Tk (pretend): the hint says how
        start = (self.home / ".local" / "share" / "applications" / "rota-planner.desktop").read_text()
        self.assertIn("Terminal=true", start)
        self.assertNotIn("StartupWMClass", start)  # its window is the terminal's

    def _with_data_and_key(self):
        self.install(ROTA_INSTALL_WINDOW="no")
        store = Store(self.folder / "data")
        store.save_catalog(helpers.catalog())
        (self.folder / "exports").mkdir()
        (self.folder / "exports" / "rota.pdf").write_text("pdf")
        ssh = self.home / ".ssh"
        ssh.mkdir(exist_ok=True)
        for name in ("rota-planner-deploy", "rota-planner-deploy.pub", "rota-planner-known_hosts", "id_other"):
            (ssh / name).write_text("key")
        apps = self.home / ".local" / "share" / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        (apps / "other.desktop").write_text("[Desktop Entry]\nName=Other\n")

    def uninstall(self, *args):
        return self.run_script("--uninstall", *args, script=self.folder / SCRIPT, ROTA_INSTALL_WINDOW="no")

    def test_uninstall_keeps_the_data_aside(self):
        self._with_data_and_key()
        run = self.uninstall("--yes")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse(self.folder.exists())
        kept = [p for p in self.home.iterdir() if p.name.startswith("Rota Planner data (kept ")]
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / "data" / "catalog.json").exists())
        self.assertTrue((kept[0] / "exports" / "rota.pdf").exists())
        self.assertEqual(kept[0].stat().st_mode & 0o777, 0o700)
        ssh = self.home / ".ssh"
        self.assertEqual(sorted(p.name for p in ssh.iterdir()), ["id_other"])  # only its own keys go
        share = self.home / ".local" / "share"
        left = sorted(p.name for p in (share / "applications").iterdir() if p.suffix == ".desktop")
        self.assertEqual(left, ["other.desktop"])
        self.assertFalse((share / "icons" / "rota-planner.png").exists())
        self.assertIn("Your data is in", run.stdout)
        # Installed again, the data comes back with --from.
        again = self.install("--from", str(kept[0]), ROTA_INSTALL_WINDOW="no")
        self.assertIn("The data was copied", again.stdout)
        self.assertTrue((self.folder / "data" / "catalog.json").exists())

    def test_uninstall_with_delete_data_removes_everything_of_its_own(self):
        self._with_data_and_key()
        from rotaplanner.sync import Sync
        shared = self.tmp / "shared"
        shared.mkdir()
        paired = Sync(Store(self.folder / "data"))
        paired.start("This one", str(shared))
        mine = shared / f"rota-{paired.state['computer_id']}.rpsync"
        other = shared / "rota-c-ba9876543210.rpsync"
        other.write_text("y")
        self.assertTrue(mine.exists())
        run = self.uninstall("--yes", "--delete-data")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse(self.folder.exists())
        self.assertFalse([p for p in self.home.iterdir() if p.name.startswith("Rota Planner data")])
        self.assertFalse(mine.exists())
        self.assertTrue(other.exists())  # the other computer's file is never touched

    def _paired(self):
        """This copy and another computer, set up to share through a folder."""
        from rotaplanner.sync import Sync
        shared = self.tmp / "shared"
        shared.mkdir()
        here = Sync(Store(self.folder / "data"))
        code = here.start("This one", str(shared))
        other_store = Store(self.tmp / "other")
        other = Sync(other_store)
        other.join("Office laptop", str(shared), code)
        other.apply(other.preview()["plan"], {})  # it takes everything this copy has
        other.exchange()
        here.exchange()
        return shared, here, other, other_store

    def test_uninstall_leaves_the_meeting_place_once_the_others_have_everything(self):
        self._with_data_and_key()
        shared, here, other, _ = self._paired()
        mine = shared / f"rota-{here.state['computer_id']}.rpsync"
        self.assertTrue(mine.exists())
        result = self.tmp / "result.txt"
        run = self.uninstall("--yes", "--result", str(result))  # keeping the data
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse(mine.exists())
        self.assertTrue((shared / f"rota-{other.state['computer_id']}.rpsync").exists())
        self.assertIn("every other computer has its changes", run.stdout)
        lines = dict(line.split("=", 1) for line in result.read_text().splitlines())
        self.assertEqual(lines["status"], "done")
        self.assertEqual(lines["sync"], "left")
        self.assertTrue(lines["kept"].startswith(str(self.home / "Rota Planner data (kept ")))
        self.assertTrue((Path(lines["kept"]) / "data" / "sync.json").exists())  # kept whole, to come back

    def test_uninstall_keeps_its_file_for_a_computer_that_is_behind(self):
        self._with_data_and_key()
        shared, here, other, _ = self._paired()
        Store(self.folder / "data").save_people(helpers.people())  # a change the other has not taken
        here.exchange()
        mine = shared / f"rota-{here.state['computer_id']}.rpsync"
        result = self.tmp / "result.txt"
        run = self.uninstall("--yes", "--delete-data", "--result", str(result))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(mine.exists())  # the other computer still needs it
        self.assertIn("Office laptop", run.stdout)
        self.assertIn("sync=kept", result.read_text())
        self.assertFalse(self.folder.exists())

    def test_uninstall_asks_first_and_waits_while_open(self):
        self._with_data_and_key()
        run = self.uninstall()  # no Terminal, no --yes
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Nothing was removed", run.stdout)
        self.assertTrue((self.folder / "data" / "catalog.json").exists())
        program = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "run.py"])
        self.addCleanup(program.wait)
        self.addCleanup(program.kill)
        (self.folder / "data" / RUNNING_FILE).write_text(f"{program.pid}\n")
        run = self.uninstall("--yes")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Rota Planner is open", run.stdout)
        self.assertTrue((self.folder / "run.py").exists())

    def test_uninstall_keeps_what_another_copy_uses(self):
        self._with_data_and_key()
        apps = self.home / ".local" / "share" / "applications"
        other = (apps / "rota-planner.desktop").read_text().replace(str(self.folder), str(self.home / "Other copy"))
        (apps / "rota-planner.desktop").write_text(other)  # the icons now open another copy
        run = self.uninstall("--yes")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Another copy of Rota Planner is installed", run.stdout)
        self.assertTrue((self.home / ".ssh" / "rota-planner-deploy").exists())
        self.assertTrue((apps / "rota-planner.desktop").exists())
        self.assertTrue((self.home / ".local" / "share" / "icons" / "rota-planner.png").exists())

    def test_nothing_to_keep_when_only_the_program_wrote_there(self):
        self.install(ROTA_INSTALL_WINDOW="no")
        data = self.folder / "data"
        data.mkdir()
        (data / ".session-token").write_text("t")
        (data / "start.html").write_text("x")
        run = self.uninstall("--yes")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse([p for p in self.home.iterdir() if p.name.startswith("Rota Planner data")])

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("script") and any(
        shutil.which(t) for t in ("apt-get", "dnf", "pacman", "zypper")), "needs Linux, script and a package manager")
    def test_a_refused_window_offer_is_not_repeated(self):
        self.install(ROTA_INSTALL_WINDOW="no")

        def by_hand():  # as typed in a Terminal, answering "n" to whatever is asked
            env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "ROTA_"))}
            env.update(HOME=str(self.home), XDG_DATA_HOME=str(self.home / ".local" / "share"),
                       DISPLAY="", WAYLAND_DISPLAY="", ROTA_INSTALL_PRETEND_NO_TK="1")
            return subprocess.run(["script", "-qec", f"bash '{self.folder / SCRIPT}'", "/dev/null"],
                                  input="n\nn\nn\n", env=env, capture_output=True, text=True, timeout=120)

        first = by_hand()
        self.assertIn("needs Tk for Python", first.stdout)  # offered once, refused
        self.assertTrue((self.folder / ".git" / "rota-planner-no-window").exists())
        second = by_hand()
        self.assertNotIn("needs Tk for Python", second.stdout)  # not asked again
        self.assertIn("python3-tk", second.stdout)  # the hint still says how

    def test_uninstall_through_a_link_removes_the_real_folder(self):
        self._with_data_and_key()
        link = self.home / "RP"
        link.symlink_to(self.folder)
        run = self.run_script("--uninstall", "--yes", "--delete-data", "--folder", str(link),
                              script=self.folder / SCRIPT, ROTA_INSTALL_WINDOW="no")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse(self.folder.exists())  # the data really went, not just the link

    def test_a_linked_data_folder_is_never_half_removed(self):
        self._with_data_and_key()
        elsewhere = self.home / "elsewhere"
        (self.folder / "data").rename(elsewhere)
        (self.folder / "data").symlink_to(elsewhere)
        run = self.uninstall("--yes", "--delete-data")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("is a link", run.stdout)
        self.assertTrue((elsewhere / "catalog.json").exists())
        self.assertTrue((self.folder / "run.py").exists())

    def test_icons_of_a_copy_with_a_longer_name_are_not_this_ones(self):
        self._with_data_and_key()
        apps = self.home / ".local" / "share" / "applications"
        longer = str(self.folder) + " 2"
        entry = (apps / "rota-planner.desktop").read_text().replace(str(self.folder), longer)
        (apps / "rota-planner.desktop").write_text(entry)
        run = self.uninstall("--yes")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue((apps / "rota-planner.desktop").exists())
        self.assertTrue((self.home / ".ssh" / "rota-planner-deploy").exists())

    def test_a_copy_with_work_of_its_own_is_not_removed(self):
        self._with_data_and_key()
        (self.folder / "rotaplanner" / "new_idea.py").write_text("# work in progress\n")
        run = self.uninstall("--yes")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("program changes of its own", run.stdout)
        self.assertTrue((self.folder / "rotaplanner" / "new_idea.py").exists())

    def test_uninstall_where_nothing_is_installed(self):
        run = self.run_script("--uninstall", "--yes", "--folder", str(self.home / "Nothing"))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("not installed", run.stdout)

    def test_a_folder_that_is_not_an_installation_is_left_alone(self):
        self.folder.mkdir()
        (self.folder / "notes.txt").write_text("mine")
        run = self.run_script("--repo", str(self.origin), "--folder", str(self.folder))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("already exists", run.stdout)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["notes.txt"])


if __name__ == "__main__":
    unittest.main()
