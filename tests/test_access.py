import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from rotaplanner import access as acc
from rotaplanner import firewall
from rotaplanner.access import Access, AccessError, parse_allowed
from rotaplanner.app import App
from rotaplanner.server import TOKEN_HEADER, create_server, host_allowed
from rotaplanner.store import Store

from . import helpers as h

TOKEN = "permanent-token"
PASSWORD = "correct horse battery"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class AddressesTest(unittest.TestCase):
    def test_parse_addresses_and_ranges(self):
        nets = parse_allowed(["192.168.1.57", " 192.168.0.0/23 ", "", "192.168.1.57"])
        self.assertEqual([str(n) for n in nets], ["192.168.1.57/32", "192.168.0.0/23"])
        with self.assertRaises(AccessError):
            parse_allowed(["192.168.1"])
        with self.assertRaises(AccessError):
            parse_allowed(["0.0.0.0/0"])
        with self.assertRaises(AccessError):
            parse_allowed(["rm -rf /"])
        self.assertEqual(acc.public_ranges(parse_allowed(["192.168.1.2", "8.8.8.8"])), ["8.8.8.8/32"])

    def test_who_may_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Access(Path(tmp))
            self.assertTrue(a.allows("127.0.0.1"))
            self.assertFalse(a.allows("192.168.1.57"))
            self.assertEqual(a.bind_host(), "127.0.0.1")
            with self.assertRaises(AccessError):  # never open without a password
                a.save_network("network", ["192.168.1.57"])
            a.set_password("", PASSWORD)
            with self.assertRaises(AccessError):  # and never open to nobody in particular
                a.save_network("network", [])
            self.assertTrue(a.save_network("network", ["192.168.1.57"]))
            self.assertEqual(a.bind_host(), "0.0.0.0")
            self.assertTrue(a.allows("192.168.1.57"))
            self.assertTrue(a.allows("::ffff:192.168.1.57"))
            self.assertFalse(a.allows("192.168.1.58"))
            self.assertFalse(a.allows("not an address"))
            again = Access(Path(tmp))  # kept in the file
            self.assertTrue(again.allows("192.168.1.57"))
            self.assertTrue(again.save_network("local", ["192.168.1.57"]))
            self.assertFalse(again.allows("192.168.1.57"))

    def test_file_holds_no_password_in_clear_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Access(Path(tmp))
            a.set_password("", PASSWORD)
            text = (Path(tmp) / acc.FILE_NAME).read_text()
            self.assertNotIn(PASSWORD, text)
            self.assertEqual((Path(tmp) / acc.FILE_NAME).stat().st_mode & 0o077, 0)

    def test_damaged_file_stays_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / acc.FILE_NAME).write_text("{not json")
            a = Access(Path(tmp))
            self.assertEqual(a.bind_host(), "127.0.0.1")
            self.assertTrue(a.has_password)  # cannot be checked: nobody gets in by accident
            self.assertIsNone(a.login("127.0.0.1", "anything"))
            a.reset()
            self.assertFalse(a.has_password)

    def test_network_without_password_in_file_falls_back_to_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / acc.FILE_NAME).write_text(json.dumps({"mode": "network", "allowed": ["192.168.1.5"], "password": None}))
            self.assertFalse(Access(Path(tmp)).allows("192.168.1.5"))


class PasswordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.access = Access(Path(self.tmp.name), clock=self.clock)
        self.access.set_password("", PASSWORD)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hashes(self):
        stored = acc.hash_password("abc12345")
        self.assertTrue(acc.check_password("abc12345", stored))
        self.assertFalse(acc.check_password("abc12346", stored))
        self.assertFalse(acc.check_password("abc12345", {"scheme": "nope", "salt": "00", "hash": "00"}))
        self.assertFalse(acc.check_password("abc12345", {"scheme": "scrypt"}))

    def test_rules_for_changing(self):
        with self.assertRaises(AccessError):
            self.access.set_password("wrong one", "another password")
        with self.assertRaises(AccessError):
            self.access.set_password(PASSWORD, "short")
        self.access.set_password(PASSWORD, "another password")
        self.assertIsNone(self.access.login("127.0.0.1", PASSWORD))
        self.assertIsNotNone(self.access.login("127.0.0.1", "another password"))
        self.access.save_network("network", ["10.0.0.2"])
        with self.assertRaises(AccessError):  # other computers always need one
            self.access.remove_password("another password")

    def test_sessions_belong_to_their_address_and_expire(self):
        session = self.access.login("10.0.0.2", PASSWORD)
        self.assertTrue(self.access.check_session(session, "10.0.0.2"))
        self.assertFalse(self.access.check_session(session, "10.0.0.3"))
        self.assertFalse(self.access.check_session("made-up", "10.0.0.2"))
        self.clock.now += acc.SESSION_IDLE_SECONDS + 1
        self.assertFalse(self.access.check_session(session, "10.0.0.2"))
        session = self.access.login("10.0.0.2", PASSWORD)
        self.access.logout(session)
        self.assertFalse(self.access.check_session(session, "10.0.0.2"))

    def test_back_to_local_logs_other_computers_out(self):
        self.access.save_network("network", ["10.0.0.2"])
        mine, theirs = self.access.login("127.0.0.1", PASSWORD), self.access.login("10.0.0.2", PASSWORD)
        self.access.save_network("local", [])
        self.assertTrue(self.access.check_session(mine, "127.0.0.1"))
        self.assertFalse(self.access.check_session(theirs, "10.0.0.2"))

    def test_attempts_at_the_same_moment_count_too(self):
        import threading as th
        results = []
        workers = [th.Thread(target=lambda: results.append(self.access.login("10.0.0.9", "wrong"))) for _ in range(40)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        self.assertEqual(self.access._failures["10.0.0.9"][0], acc.FREE_ATTEMPTS)  # the rest were refused unchecked
        self.assertIsNone(self.access.login("10.0.0.9", PASSWORD))

    def test_wrong_passwords_make_one_wait(self):
        for _ in range(acc.FREE_ATTEMPTS - 1):
            self.assertIsNone(self.access.login("10.0.0.2", "wrong"))
            self.assertEqual(self.access.wait_left("10.0.0.2"), 0)
        self.assertIsNone(self.access.login("10.0.0.2", "wrong"))
        self.assertEqual(self.access.wait_left("10.0.0.2"), acc.FIRST_WAIT_SECONDS)
        self.assertIsNone(self.access.login("10.0.0.2", PASSWORD))  # even the right one, while waiting
        self.assertEqual(self.access.wait_left("10.0.0.3"), 0)  # another computer is not affected
        self.clock.now += acc.FIRST_WAIT_SECONDS
        self.assertIsNone(self.access.login("10.0.0.2", "wrong"))
        self.assertEqual(self.access.wait_left("10.0.0.2"), acc.FIRST_WAIT_SECONDS * 2)
        self.clock.now += acc.FIRST_WAIT_SECONDS * 2
        self.assertIsNotNone(self.access.login("10.0.0.2", PASSWORD))
        self.assertEqual(self.access.wait_left("10.0.0.2"), 0)


class HostTest(unittest.TestCase):
    def test_host_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Access(Path(tmp))
            own = ["office-pc", "office-pc.local"]
            self.assertTrue(host_allowed("127.0.0.1:8765", 8765, a, own))
            self.assertTrue(host_allowed("localhost:8765", 8765, a, own))
            self.assertFalse(host_allowed("127.0.0.1:8766", 8765, a, own))
            self.assertFalse(host_allowed("192.168.1.5:8765", 8765, a, own))
            self.assertFalse(host_allowed("office-pc:8765", 8765, a, own))
            a.set_password("", PASSWORD)
            a.save_network("network", ["192.168.1.0/24"])
            self.assertTrue(host_allowed("192.168.1.5:8765", 8765, a, own))
            self.assertTrue(host_allowed("OFFICE-PC:8765", 8765, a, own))
            self.assertFalse(host_allowed("evil.example:8765", 8765, a, own))  # DNS rebinding
            self.assertFalse(host_allowed("192.168.1.5", 8765, a, own))


class FirewallTest(unittest.TestCase):
    nets = parse_allowed(["192.168.1.57", "192.168.2.0/24"])

    def runner(self, answers):
        calls = []

        def run(args):
            calls.append(list(args))
            for key, value in answers.items():
                if key in " ".join(args):
                    return value
            return None
        run.calls = calls
        return run

    def test_linux_ufw(self):
        run = self.runner({"is-active ufw": "active\n"})
        result = firewall.status(8765, self.nets, system="Linux", runner=run, read=lambda p: "ENABLED=yes\n")
        self.assertEqual((result["tool"], result["state"]), ("ufw", "on"))
        self.assertEqual(result["commands"]["open"], [
            "sudo ufw allow from 192.168.1.57 to any port 8765 proto tcp",
            "sudo ufw allow from 192.168.2.0/24 to any port 8765 proto tcp"])
        self.assertTrue(all("delete" in c for c in result["commands"]["close"]))
        result = firewall.status(8765, self.nets, system="Linux", runner=run, read=lambda p: None)
        self.assertEqual(result["state"], "unknown")  # said, not guessed
        result = firewall.status(8765, self.nets, system="Linux", runner=self.runner({}), read=lambda p: "ENABLED=no")
        self.assertEqual((result["tool"], result["state"]), ("ufw", "off"))
        result = firewall.status(8765, self.nets, system="Linux", runner=self.runner({}), read=lambda p: None)
        self.assertEqual(result["state"], "off")

    def test_linux_firewalld(self):
        run = self.runner({"is-active firewalld": "active\n"})
        result = firewall.status(8765, self.nets, system="Linux", runner=run, read=lambda p: None)
        self.assertEqual((result["tool"], result["state"]), ("firewalld", "on"))
        self.assertIn('source address="192.168.1.57" port port="8765" protocol="tcp" accept', result["commands"]["open"][0])

    def test_windows(self):
        run = self.runner({"Get-NetFirewallProfile": "Domain=True\nPrivate=True\nPublic=False\n",
                           "Get-NetFirewallRule": "0\n"})
        result = firewall.status(8765, self.nets, system="Windows", runner=run)
        self.assertEqual(result["state"], "on")
        self.assertIs(result["rule"], False)
        self.assertIn("-LocalPort 8765 -RemoteAddress 192.168.1.57,192.168.2.0/24 -Profile Private",
                      result["commands"]["open"][0])
        result = firewall.status(8765, self.nets, system="Windows", runner=self.runner({}))
        self.assertEqual(result["state"], "unknown")

    def test_macos(self):
        run = self.runner({"--getglobalstate": "Firewall is enabled. (State = 1)\n"})
        self.assertEqual(firewall.status(8765, self.nets, system="Darwin", runner=run)["state"], "on")
        run = self.runner({"--getglobalstate": "Firewall is disabled. (State = 0)\n"})
        self.assertEqual(firewall.status(8765, self.nets, system="Darwin", runner=run)["state"], "off")

    def test_tools_are_only_asked_with_fixed_words(self):
        run = self.runner({})
        firewall.status(8765, self.nets, system="Windows", runner=run)
        firewall.status(8765, self.nets, system="Linux", runner=run, read=lambda p: None)
        for args in run.calls:
            self.assertNotIn("192.168", " ".join(args))  # addresses never reach a command that runs


class ServerAccessTest(unittest.TestCase):
    """The server as seen from this computer and from another one (pretended)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(self.tmp.name))
        store.save_catalog(h.catalog())
        store.save_people(h.people())
        store.save_month(h.month())
        self.access = Access(Path(self.tmp.name))
        self.server = create_server(App(store), self.access, TOKEN, 0)
        base = self.server.RequestHandlerClass

        class Pretend(base):
            @property
            def client(self):  # the address a test says it comes from
                return self.headers.get("X-Test-Client") or super().client

        self.server.RequestHandlerClass = Pretend
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, token=TOKEN, client=None, host=None, origin=None, content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers[TOKEN_HEADER] = token
        if client:
            headers["X-Test-Client"] = client
        if origin:
            headers["Origin"] = origin
        payload = json.dumps(body).encode() if body is not None else None
        if payload is not None:
            headers["Content-Type"] = content_type
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        try:
            return response.status, json.loads(data)
        except ValueError:
            return response.status, data

    def test_without_password_as_before(self):
        status, boot = self.call("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(boot["auth"], {"password": False, "local": True})
        self.assertEqual(self.call("GET", "/api/bootstrap", token="wrong")[0], 403)
        self.assertEqual(self.call("GET", "/api/bootstrap", client="192.168.1.57")[0], 403)  # not allowed at all

    def test_other_site_cannot_post(self):
        status, _ = self.call("POST", "/api/settings/save", {"settings": {}}, origin="http://evil.example")
        self.assertEqual(status, 403)
        status, _ = self.call("POST", "/api/settings/save", {"settings": {}}, origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 200)

    def test_password_login_and_other_computer(self):
        status, answer = self.call("POST", "/api/access/password", {"new": PASSWORD})
        self.assertEqual(status, 200)
        mine = answer["session"]
        self.assertTrue(answer["has_password"])
        self.assertNotIn("hash", json.dumps(answer))
        # The permanent token no longer opens anything.
        status, answer = self.call("GET", "/api/bootstrap")
        self.assertEqual((status, answer.get("login")), (401, True))
        self.assertEqual(self.call("GET", f"/print/2026-12/month?t={TOKEN}", token=None)[0], 403)
        self.assertEqual(self.call("GET", "/api/bootstrap", token=mine)[0], 200)
        self.assertEqual(self.call("GET", f"/print/2026-12/month?t={mine}", token=None)[0], 200)

        # Open to one other computer.
        status, answer = self.call("POST", "/api/access/network", {"mode": "network", "allowed": ["192.168.1.57"]}, token=mine)
        self.assertEqual((status, answer["moved"]), (200, True))
        other = "192.168.1.57"
        self.assertEqual(self.call("GET", "/api/bootstrap", token=None, client="192.168.1.58")[0], 403)
        self.assertEqual(self.call("GET", "/api/bootstrap", token=mine, client=other)[0], 401)  # a session is tied to its address
        status, answer = self.call("POST", "/api/login", {"password": "wrong"}, token=None, client=other)
        self.assertEqual(status, 401)
        status, answer = self.call("POST", "/api/login", {"password": PASSWORD}, token=None, client=other,
                                   content_type="text/plain")
        self.assertEqual(status, 400)  # a plain form from another site cannot log in
        status, answer = self.call("POST", "/api/login", {"password": PASSWORD}, token=None, client=other,
                                   host=f"192.168.1.10:{self.port}")
        self.assertEqual(status, 200)
        theirs = answer["session"]
        status, boot = self.call("GET", "/api/bootstrap", token=theirs, client=other, host=f"192.168.1.10:{self.port}")
        self.assertEqual((status, boot["auth"]["local"]), (200, False))
        # Only this computer changes who may connect, the password, or looks at the firewall.
        for method, path, body in (("POST", "/api/access/network", {"mode": "local"}),
                                   ("POST", "/api/access/password", {"current": PASSWORD, "new": "something else"}),
                                   ("GET", "/api/access/firewall", None)):
            self.assertEqual(self.call(method, path, body, token=theirs, client=other)[0], 403, path)
        self.assertEqual(self.call("GET", "/api/access", token=theirs, client=other)[0], 200)
        for path in ("/api/sync/setup", "/api/sync/code", "/api/sync/stop", "/api/months/2026-12/exports/open"):
            self.assertEqual(self.call("POST", path, {}, token=theirs, client=other)[0], 403, path)
        # Log out.
        self.assertEqual(self.call("POST", "/api/logout", {}, token=theirs, client=other)[0], 200)
        self.assertEqual(self.call("GET", "/api/bootstrap", token=theirs, client=other)[0], 401)

    def test_too_many_wrong_passwords(self):
        self.access.set_password("", PASSWORD)
        for _ in range(acc.FREE_ATTEMPTS - 1):
            self.assertEqual(self.call("POST", "/api/login", {"password": "nope"}, token=None)[0], 401)
        status, answer = self.call("POST", "/api/login", {"password": "nope"}, token=None)
        self.assertEqual(status, 429)
        self.assertGreater(answer["wait"], 0)
        self.assertEqual(self.call("POST", "/api/login", {"password": PASSWORD}, token=None)[0], 429)

    def test_removing_the_password_gives_back_the_token(self):
        status, answer = self.call("POST", "/api/access/password", {"new": PASSWORD})
        mine = answer["session"]
        self.assertEqual(self.call("POST", "/api/access/password", {"current": "wrong", "new": ""}, token=mine)[0], 400)
        status, answer = self.call("POST", "/api/access/password", {"current": PASSWORD, "new": ""}, token=mine)
        self.assertEqual((status, answer["session"]), (200, TOKEN))
        self.assertEqual(self.call("GET", "/api/bootstrap")[0], 200)


if __name__ == "__main__":
    unittest.main()
