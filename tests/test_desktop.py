"""Rota Planner on this computer (``desktop.py``): what the window says before
uninstalling, reading the installer's answer, and the old "Update" icon."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rotaplanner import desktop


def status(**more):
    base = {"configured": True, "pending": False, "connection": {"online": True}, "pen": {},
            "others": [{"name": "Office laptop", "state": "ok", "text": "Up to date"}], "units": []}
    base.update(more)
    return base


class AdviceTest(unittest.TestCase):
    def tones(self, advice):
        return [tone for tone, _ in advice["lines"]]

    def test_alone(self):
        self.assertEqual(desktop.advice({"configured": False}, delete=False),
                         {"lines": [], "allowed": True, "shared": False})
        alone = desktop.advice(None, delete=True)
        self.assertTrue(alone["allowed"])
        self.assertIn("no other copy", alone["lines"][0][1])

    def test_everyone_up_to_date(self):
        advice = desktop.advice(status(), delete=True)
        self.assertTrue(advice["allowed"])
        self.assertEqual(self.tones(advice), ["ok", "ok", ""])
        self.assertIn("Office laptop has everything", advice["lines"][1][1])
        self.assertIn("Nothing changes on the other computers", advice["lines"][-1][1])

    def test_unsent_changes(self):
        online = desktop.advice(status(pending=True), delete=True)
        self.assertTrue(online["allowed"])  # they are sent first
        self.assertEqual(online["lines"][0][0], "warn")
        offline = status(pending=True, connection={"online": False})
        self.assertTrue(desktop.advice(offline, delete=False)["allowed"])  # kept with the data
        refused = desktop.advice(offline, delete=True)
        self.assertFalse(refused["allowed"])
        self.assertEqual(refused["lines"][0][0], "bad")

    def test_a_computer_behind_keeps_the_file(self):
        behind = status(others=[{"name": "Office laptop", "state": "behind"}, {"name": "Home", "state": "ok"}])
        lines = [words for _, words in desktop.advice(behind, delete=False)["lines"]]
        self.assertTrue(any("Office laptop has not taken everything" in line for line in lines))
        self.assertTrue(any("Home has everything" in line for line in lines))
        differ = status(units=[{"state": "both"}])
        self.assertIn("has not taken everything", desktop.advice(differ, delete=False)["lines"][1][1])

    def test_a_computer_gone_and_behind(self):
        gone = status(others=[{"name": "Old", "state": "gone", "text": "Not seen for 3 weeks", "behind": 2}])
        words = desktop.advice(gone, delete=False)["lines"][1][1]
        self.assertIn("Not seen for 3 weeks", words)
        self.assertIn("file stays at the meeting place", words)

    def test_pen_gone_and_unreadable(self):
        advice = desktop.advice(status(pen={"mine": True}, others=[
            {"name": "Old", "state": "gone", "text": "Not seen for 3 weeks"},
            {"name": "Junk", "state": "problem", "text": "Made with another code"}]), delete=True)
        words = " ".join(w for _, w in advice["lines"])
        self.assertIn("hands over", words)
        self.assertIn("Old: Not seen for 3 weeks.", words)
        self.assertNotIn("Junk", words)
        only_junk = desktop.advice(status(others=[{"name": "Junk", "state": "problem"}]), delete=True)
        self.assertIn("No other computer has this data", only_junk["lines"][-2][1])


class InstallerWordsTest(unittest.TestCase):
    def test_result_values(self):
        self.assertEqual(desktop.result_values("sync=kept\nstatus=refused\nstatus=done\nkept=/h/x y\n"),
                         {"sync": "kept", "status": "done", "kept": "/h/x y"})

    def test_closing_words(self):
        said = ("Uninstalling Rota Planner removes:\n  - the program\n\nRota Planner is uninstalled.\n"
                "Your data is in: /h/kept\nNot every other computer has taken this computer's last changes yet\n"
                "(Office laptop), so its file stays.\n\ngit, Python and Tk stay installed: other programs may use them.\n")
        self.assertEqual(desktop.closing_words(said, done=True),
                         "Not every other computer has taken this computer's last changes yet (Office laptop), "
                         "so its file stays.\n\ngit, Python and Tk stay installed: other programs may use them.")
        refused = "Uninstalling…\n\nRota Planner is open. Close its window first,\nthen uninstall again.\n"
        self.assertEqual(desktop.closing_words(refused, done=False),
                         "Rota Planner is open. Close its window first, then uninstall again.")
        self.assertEqual(desktop.closing_words("", done=False), "")


@unittest.skipUnless(os.name == "posix", "the icons are made on a Mac or Linux")
class OldUpdateIconTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.program = self.home / "Rota Planner"
        self.program.mkdir(parents=True)
        (self.program / desktop.SCRIPT).write_text("#!/bin/bash\n")
        patch = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / "share")})
        patch.start()
        self.addCleanup(patch.stop)

    def entry(self, folder):
        apps = self.home / "share" / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        path = apps / "rota-planner-update.desktop"
        path.write_text(f"[Desktop Entry]\nName=Update Rota Planner\nPath={folder}\n")
        return path

    @unittest.skipIf(desktop.sys.platform == "darwin", "the Linux icon")
    def test_linux(self):
        other = self.entry(f"{self.program} 2")  # another copy's
        self.assertFalse(desktop.tidy_old_update_icon(self.program))
        self.assertTrue(other.exists())
        mine = self.entry(self.program.resolve())
        self.assertTrue(desktop.tidy_old_update_icon(self.program))
        self.assertFalse(mine.exists())
        self.assertFalse(desktop.tidy_old_update_icon(self.program))  # nothing more to do
        link = self.home / "through a link"
        link.symlink_to(self.program)
        linked = self.entry(link)  # installed through a link: the same copy
        self.assertTrue(desktop.tidy_old_update_icon(self.program.resolve()))
        self.assertFalse(linked.exists())

    def test_mac(self):
        with mock.patch.object(desktop.sys, "platform", "darwin"):
            app = self.home / "Applications" / "Update Rota Planner.app"
            (app / "Contents" / "Resources").mkdir(parents=True)
            (app / "Contents" / "Info.plist").write_text("<string>someone.else</string>")
            command = app / "Contents" / "Resources" / "Update Rota Planner.command"
            command.write_text(f'exec /bin/bash "{self.program.resolve()}/install-rota-planner.sh" --update\n')
            self.assertFalse(desktop.tidy_old_update_icon(self.program))  # not the installer's
            (app / "Contents" / "Info.plist").write_text("<string>local.rotaplanner.update</string>")
            self.assertTrue(desktop.tidy_old_update_icon(self.program))
            self.assertFalse(app.exists())

    def test_supported(self):
        (self.program / ".git").mkdir()
        self.assertTrue(desktop.supported(self.program, True, ""))
        self.assertFalse(desktop.supported(self.program, False, ""))  # the demo, or --data
        self.assertFalse(desktop.supported(self.program, True, "not installed with the installer"))
        (self.program / desktop.SCRIPT).unlink()
        self.assertFalse(desktop.supported(self.program, True, ""))


if __name__ == "__main__":
    unittest.main()
