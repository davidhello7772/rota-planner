import http.client
import json
import smtplib
import tempfile
import threading
import time
import unittest
from pathlib import Path

from rotaplanner import emails, mail, transfer
from rotaplanner.access import Access
from rotaplanner.app import ApiError, App
from rotaplanner.exporters import versions
from rotaplanner.model import Assignment
from rotaplanner.server import TOKEN_HEADER, create_server
from rotaplanner.store import Store

from . import helpers as h

CODE = "abcd efgh ijkl mnop"  # shaped like an app password; invented
SENDER = "rota@centre.example"
LIST = "\n".join(["Name\tE-mail", "Ana Berg\tana.berg@example.org", "Ben Cole\tben.cole@example.org",
                  "Cleo Dane\tcleo@example.org", "Someone Else\tsomeone@example.org"])


class PretendServer:
    """Stands for the mail server: records what would be sent. No network."""

    def __init__(self):
        self.sent, self.logins, self.refuse, self.wrong_password, self.hold = [], [], set(), False, None

    def connect(self, settings):
        if self.wrong_password:
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        self.logins.append((settings["address"], settings["password"], settings["host"], settings["port"], settings["security"]))
        return self

    def send_message(self, message):
        if self.hold is not None:
            self.hold.wait(5)
        to = message["To"].addresses[0].addr_spec
        if to in self.refuse:
            raise smtplib.SMTPRecipientsRefused({to: (550, b"No such user")})
        self.sent.append(message)

    def quit(self):
        pass


def wait_done(app, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        status = app.mail_status()
        if status["done"]:
            return status
        time.sleep(0.01)
    raise AssertionError("the sending did not finish")


class MailTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        store = Store(self.root)
        store.save_catalog(h.catalog())
        store.save_people(h.people())
        month = h.month()
        month.assignments = [Assignment("lunch", "2026-12-01", 0, person_id="ana"),
                             Assignment("supper", "2026-12-02", 0, person_id="ana"),
                             Assignment("night", "2026-12-01", 0, person_id="ben")]
        store.save_month(month)
        self.app = App(store)
        self.server = PretendServer()
        self.app.mail.connect = self.server.connect
        self.app.mail.pause = 0

    def tearDown(self):
        self.tmp.cleanup()

    def set_up_account(self):
        self.app.mail_save({"account": {"address": SENDER, "name": "Rota – Demo", "password": CODE}})
        self.app.mail_test({})
        self.server.sent.clear()

    def rows(self, preview, **changes):
        return [{"row": r["row"], "person_id": changes.get(r["name"], r["person_id"]), "remember": r["remember"]}
                for r in preview["rows"] if r["send"] or r["name"] in changes]

    # -- off by default ---------------------------------------------------------

    def test_off_until_set_up_and_tested(self):
        self.assertEqual(self.app.mail_info()["configured"], False)
        for call in (lambda: self.app.mail_preview("2026-12", {"text": LIST}),
                     lambda: self.app.mail_send("2026-12", {"text": LIST, "rows": [{"row": 1, "person_id": "ana"}]})):
            with self.assertRaisesRegex(ApiError, "not set up"):
                call()
        self.app.mail_save({"account": {"address": SENDER, "password": CODE}})
        self.assertEqual(self.app.mail_info()["configured"], False)  # saved, not yet proved to work
        with self.assertRaisesRegex(ApiError, "not set up"):
            self.app.mail_preview("2026-12", {"text": LIST})
        self.assertEqual(self.server.logins, [])  # and nothing ever connected
        self.assertFalse(self.server.sent)

    def test_account_file_is_private_local_and_never_shown(self):
        self.set_up_account()
        info = self.app.mail_info()
        self.assertEqual((info["configured"], info["address"], info["has_password"]), (True, SENDER, True))
        self.assertNotIn("abcd", json.dumps(info))
        self.assertEqual(self.server.logins[0], (SENDER, "abcdefghijklmnop", "smtp.gmail.com", 587, "starttls"))
        path = self.root / mail.FILE_NAME
        self.assertEqual(path.stat().st_mode & 0o077, 0)
        self.assertNotIn(b"abcdefgh", transfer.export_bytes(self.app.store))  # not in "Move to another computer"
        self.assertNotIn(mail.FILE_NAME, transfer.TOP_LEVEL)                  # so not in the sync file either
        # Changing the account needs a new test; keeping it does not.
        self.app.mail_save({"account": {"address": SENDER, "name": "Other name", "password": ""}})
        self.assertTrue(self.app.mail_info()["configured"])
        with self.assertRaisesRegex(ApiError, "again"):  # the code kept is never sent to another account or server
            self.app.mail_save({"account": {"address": "other@centre.example", "password": ""}})
        with self.assertRaisesRegex(ApiError, "again"):
            self.app.mail_save({"account": {"address": SENDER, "password": "", "host": "mail.elsewhere.example"}})
        self.app.mail_save({"account": {"address": "other@centre.example", "password": CODE}})
        self.assertFalse(self.app.mail_info()["configured"])
        self.app.mail_remove({})
        self.assertFalse(path.exists())
        self.assertEqual(self.app.mail_info()["has_password"], False)

    def test_wrong_app_password_is_explained(self):
        self.app.mail_save({"account": {"address": SENDER, "password": CODE}})
        self.server.wrong_password = True
        with self.assertRaisesRegex(ApiError, "refused the app password"):
            self.app.mail_test({})
        self.assertFalse(self.app.mail_info()["configured"])
        for bad in ({"address": "not an address", "password": CODE}, {"address": SENDER, "password": ""},
                    {"address": SENDER, "password": CODE, "host": "bad host!"}, {"address": SENDER, "password": CODE, "port": "x"}):
            fresh = mail.Mail(Path(self.tmp.name) / "elsewhere")
            with self.assertRaises(mail.MailError):
                fresh.save(bad)

    # -- the list -----------------------------------------------------------------

    def test_reading_lists(self):
        entries = mail.read_list("Ana Berg <Ana.Berg@Example.org>\nben.cole@example.org\nnot a line with an address")
        self.assertEqual([(e.name, e.address) for e in entries], [("Ana Berg", "ana.berg@example.org"), ("", "ben.cole@example.org")])
        form = "Timestamp\tEmail address\tFirst name\tLast name\tMorning\n1/11\tana.berg@example.org\tAna\tBerg\tTue 1, Wed 2"
        self.assertEqual([(e.name, e.address) for e in mail.read_list(form)], [("Ana Berg", "ana.berg@example.org")])
        self.assertEqual(mail.mask("ana.berg@example.org"), "an•••@example.org")

    def test_preview_recognises_by_fingerprint_then_name_and_shows_no_address(self):
        self.set_up_account()
        salt = self.app.store.email_salt()
        emails.remember(self.app.people, "ana", emails.fingerprint("ana.berg@example.org", salt), salt)
        preview = self.app.mail_preview("2026-12", {"text": LIST + "\nAna again\tana.berg@example.org"})
        found = {r["name"]: (r["person_id"], r["match"], r["send"], r["remember"]) for r in preview["rows"]}
        self.assertEqual(found["Ana Berg"], ("ana", "recognised", True, False))
        self.assertEqual(found["Ben Cole"], ("ben", "name", True, True))        # a first address: remembered by default
        self.assertEqual(found["Someone Else"], (None, "unknown", False, False))
        self.assertEqual(found["Ana again"][1:3], ("duplicate", False))
        self.assertNotIn("ana.berg", json.dumps(preview))
        self.assertEqual(preview["rows"][0]["address"], "an•••@example.org")
        self.assertEqual([p["id"] for p in preview["people"]], ["ana", "ben"])  # those with shifts
        self.assertEqual(preview["phase"]["state"], "draft")

    def test_a_known_address_wins_over_a_name(self):
        self.set_up_account()
        salt = self.app.store.email_salt()
        emails.remember(self.app.people, "ana", emails.fingerprint("ana.berg@example.org", salt), salt)
        rows = self.app.mail_preview("2026-12", {"text": "Ana Berg\tana.old@example.org\nAnita\tana.berg@example.org"})["rows"]
        self.assertEqual([(r["match"], r["send"]) for r in rows], [("duplicate", False), ("recognised", True)])

    def test_the_plan_is_worked_out_here(self):
        chosen = {"rows": [{"row": 1, "person_id": "ana"}, {"row": 3, "person_id": "cleo"}, {"row": 9, "person_id": "ana"}]}
        plan = self.app.mail_plan("2026-12", {**chosen, "who": "all"})
        self.assertEqual((plan["count"], plan["send_to"], plan["missing"], plan["no_shift"]), (1, ["ana"], ["Ben C."], 1))
        self.assertEqual(self.app.mail_plan("2026-12", {**chosen, "who": "changed"})["count"], 0)  # nothing published yet
        self.assertEqual(self.app.mail_plan("2026-12", {"rows": "nonsense", "who": "x"})["count"], 0)

    def test_nothing_is_remembered_if_the_sending_does_not_start(self):
        self.set_up_account()
        preview = self.app.mail_preview("2026-12", {"text": LIST})
        with self.assertRaisesRegex(ApiError, "Nobody to send to"):
            self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview), "who": "changed"})
        self.assertFalse(any(p.email_fp for p in self.app.people))

    # -- sending ---------------------------------------------------------------------

    def test_one_message_per_person_with_their_calendar(self):
        self.set_up_account()
        preview = self.app.mail_preview("2026-12", {"text": LIST})
        started = self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview), "who": "all"})
        self.assertEqual(started["total"], 2)  # Cleo is in the list but has no shift
        status = wait_done(self.app)
        self.assertEqual((status["sent"], status["failed"], status["problem"]), (2, [], ""))
        by_to = {m["To"].addresses[0].addr_spec: m for m in self.server.sent}
        self.assertEqual(sorted(by_to), ["ana.berg@example.org", "ben.cole@example.org"])
        message = by_to["ana.berg@example.org"]
        self.assertEqual(len(message["To"].addresses), 1)
        self.assertEqual(message["Subject"], "Your shifts for December 2026")
        self.assertEqual(str(message["From"]), f"Rota – Demo <{SENDER}>")
        body = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("Hello Ana,", body)
        self.assertIn("Lunch cook", body)
        self.assertNotIn("Night guard", body)  # Ben's shift is not in Ana's message
        attachment = next(message.iter_attachments())
        self.assertEqual((attachment.get_content_type(), attachment.get_filename()), ("text/calendar", "Ana B - rota December 2026.ics"))
        calendar = attachment.get_content()
        calendar = calendar.decode("utf-8") if isinstance(calendar, bytes) else calendar
        self.assertIn("METHOD:PUBLISH", calendar)
        self.assertEqual(calendar.count("BEGIN:VEVENT"), 2)
        # Who was e-mailed is remembered (for version 0, a draft); Ben's address as a fingerprint, since it was his first.
        self.assertEqual(self.app.month("2026-12").mailed, {"ana": 0, "ben": 0})
        self.assertTrue(next(p for p in self.app.people if p.id == "ben").email_fp)

    def test_no_address_is_kept_anywhere(self):
        self.set_up_account()
        preview = self.app.mail_preview("2026-12", {"text": LIST})
        answers = [preview, self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview)}), wait_done(self.app),
                   self.app.mail_info(), self.app.month_view("2026-12")]
        for answer in answers:  # at most masked ("an•••@example.org"), in the check of the list just given
            for whole in ("ana.berg", "ben.cole", "cleo@", "someone@"):
                self.assertNotIn(whole, json.dumps(answer))
        for answer in answers[1:]:
            self.assertNotIn("example.org", json.dumps(answer))
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"example.org", path.read_bytes(), path.name)

    def test_only_changed_and_only_not_yet_emailed(self):
        self.set_up_account()
        month = self.app.month("2026-12")
        versions.publish(month, self.app.people, self.app.settings)
        self.app.store.save_month(month)
        preview = self.app.mail_preview("2026-12", {"text": LIST})
        self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview), "who": "all"})
        wait_done(self.app)
        self.assertEqual(self.app.month("2026-12").mailed, {"ana": 1, "ben": 1})
        self.server.sent.clear()
        with self.assertRaisesRegex(ApiError, "Nobody to send to"):  # everyone has version 1 already
            self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview), "who": "new"})
        self.app.assign("2026-12", {"task_id": "lunch", "date": "2026-12-03", "position": 0, "person_id": "ana"})  # after printing
        preview = self.app.mail_preview("2026-12", {"text": LIST})
        self.assertEqual({p["id"]: p["changed"] for p in preview["people"]}, {"ana": True, "ben": False})
        self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview), "who": "changed"})
        wait_done(self.app)
        self.assertEqual([m["To"].addresses[0].addr_spec for m in self.server.sent], ["ana.berg@example.org"])
        self.assertIn("(changed)", self.server.sent[0].get_body(preferencelist=("plain",)).get_content())
        self.app.undo("2026-12", {})  # undoing the shift does not forget who was e-mailed
        self.assertEqual(self.app.month("2026-12").mailed, {"ana": 1, "ben": 1})

    def test_a_refused_address_does_not_stop_the_others(self):
        self.set_up_account()
        self.server.refuse = {"ana.berg@example.org"}
        preview = self.app.mail_preview("2026-12", {"text": LIST})
        self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview)})
        status = wait_done(self.app)
        self.assertEqual(status["sent"], 1)
        self.assertEqual(status["failed"], [{"name": "Ana Berg", "reason": "The address was refused by the mail server."}])
        self.assertEqual(self.app.month("2026-12").mailed, {"ben": 0})

    def test_stop_and_one_sending_at_a_time(self):
        self.set_up_account()
        self.server.hold = threading.Event()
        preview = self.app.mail_preview("2026-12", {"text": LIST})
        self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview)})
        with self.assertRaisesRegex(ApiError, "already being sent"):
            self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview)})
        with self.assertRaisesRegex(ApiError, "being sent"):
            self.app.mail_remove({})
        self.app.mail_stop({})
        self.server.hold.set()
        status = wait_done(self.app)
        self.assertEqual((status["stopped"], status["sent"]), (True, 1))  # the one under way went; the next did not
        self.assertEqual(len(self.app.month("2026-12").mailed), 1)         # and it is recorded at once, not at the end

    def test_a_lost_password_stops_everything(self):
        self.set_up_account()
        self.server.wrong_password = True
        preview = self.app.mail_preview("2026-12", {"text": LIST})
        self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview)})
        status = wait_done(self.app)
        self.assertEqual(status["sent"], 0)
        self.assertIn("refused the app password", status["problem"])

    def test_no_connection_stops_at_once(self):
        self.set_up_account()

        def unreachable(_settings):
            raise OSError("network is unreachable")

        self.app.mail.connect = unreachable
        preview = self.app.mail_preview("2026-12", {"text": LIST})
        self.app.mail_send("2026-12", {"text": LIST, "rows": self.rows(preview)})
        status = wait_done(self.app)
        self.assertEqual((status["sent"], status["failed"]), (0, []))
        self.assertIn("could not be reached", status["problem"])
        self.assertEqual(self.app.month("2026-12").mailed, {})

    def test_headers_cannot_be_injected(self):
        settings = {"address": SENDER, "name": "Rota"}
        message = mail.Mail.build(settings, "ana@example.org", "Ana\nBcc: x@example.org", "Hello\r\nBcc: y@example.org", "text")
        self.assertNotIn("Bcc", message.keys())
        self.assertEqual(len(message["To"].addresses), 1)
        self.assertNotIn("\n", message["Subject"])


class MailRoutesTest(unittest.TestCase):
    """Everything about e-mail is for the computer where the program runs."""

    def test_refused_from_another_computer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_catalog(h.catalog())
            store.save_people(h.people())
            store.save_month(h.month())
            access = Access(Path(tmp))
            access.set_password("", "a long password")
            access.save_network("network", ["192.168.1.57"])
            server = create_server(App(store), access, "token", 0)
            base = server.RequestHandlerClass

            class Pretend(base):
                @property
                def client(self):
                    return self.headers.get("X-Test-Client") or super().client

            server.RequestHandlerClass = Pretend
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                def call(method, path, client, body=None):
                    session = access.login(client, "a long password")
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    headers = {"Host": f"127.0.0.1:{port}", TOKEN_HEADER: session, "X-Test-Client": client}
                    payload = json.dumps(body).encode() if body is not None else None
                    if payload is not None:
                        headers["Content-Type"] = "application/json"
                    conn.request(method, path, body=payload, headers=headers)
                    response = conn.getresponse()
                    data = json.loads(response.read())
                    conn.close()
                    return response.status, data

                other = "192.168.1.57"
                self.assertEqual(call("GET", "/api/mail", other), (200, {"configured": False, "elsewhere": True}))
                for method, path in (("POST", "/api/mail/save"), ("POST", "/api/mail/test"), ("POST", "/api/mail/remove"),
                                     ("GET", "/api/mail/status"), ("POST", "/api/mail/stop"),
                                     ("POST", "/api/months/2026-12/mail/preview"), ("POST", "/api/months/2026-12/mail/send")):
                    self.assertEqual(call(method, path, other, {} if method == "POST" else None)[0], 403, path)
                status, info = call("GET", "/api/mail", "127.0.0.1")
                self.assertEqual((status, info["configured"], info["has_password"]), (200, False, False))
                self.assertEqual(call("GET", "/api/mail/status", "127.0.0.1")[0], 200)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
