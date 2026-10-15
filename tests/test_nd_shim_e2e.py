#!/usr/bin/env python3
"""E2E test for nd-subsonic-shim against a fake Navidrome. No live services.

Loads the real shim source with its three deployment constants (secret file,
upstream, listen port) rewritten for the sandbox, then exercises the real
handler code over real HTTP.
"""
import hashlib
import json
import os
import pathlib
import re
import socket
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
SHIM_SRC = HERE.parent / "connectors" / "nd-subsonic-shim" / "nd-subsonic-shim.py"
ADMIN_PW = "test-admin-pw"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeNavidrome(BaseHTTPRequestHandler):
    """Native-API fake: /auth/login, /api/user, plus /rest/ping for proxying."""
    calls = []
    users = [{"id": "u1", "userName": "existing"}]

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        FakeNavidrome.calls.append(("POST", self.path, body.decode()))
        if self.path == "/auth/login":
            creds = json.loads(body)
            if creds.get("password") == ADMIN_PW:
                return self._json(200, {"token": "fake-tok"})
            return self._json(401, {})
        if self.path == "/api/user":
            if self.headers.get("x-nd-authorization") != "Bearer fake-tok":
                return self._json(401, {})
            u = json.loads(body)
            FakeNavidrome.users.append({"id": "u-new", "userName": u["userName"]})
            return self._json(200, {"id": "u-new"})
        return self._json(404, {})

    def do_GET(self):
        FakeNavidrome.calls.append(("GET", self.path, ""))
        if self.path.startswith("/api/user"):
            if self.headers.get("x-nd-authorization") != "Bearer fake-tok":
                return self._json(401, {})
            return self._json(200, FakeNavidrome.users)
        if self.path.startswith("/rest/ping"):
            return self._json(200, {"subsonic-response": {"status": "ok",
                                                          "type": "fake-navidrome"}})
        return self._json(404, {})

    def do_DELETE(self):
        FakeNavidrome.calls.append(("DELETE", self.path, ""))
        if self.headers.get("x-nd-authorization") != "Bearer fake-tok":
            return self._json(401, {})
        FakeNavidrome.users = [u for u in FakeNavidrome.users
                               if "/api/user/" + u["id"] != self.path]
        return self._json(200, {})


def load_shim(secret_path, upstream, listen_port):
    """Load the real shim module with sandbox constants (via importlib)."""
    src = SHIM_SRC.read_text(encoding="utf-8")
    src = re.sub(r'^SECRET_FILE = .*$', f'SECRET_FILE = {str(secret_path)!r}',
                 src, flags=re.M)
    src = re.sub(r'^UPSTREAM = .*$', f'UPSTREAM = {upstream!r}', src, flags=re.M)
    src = re.sub(r'^LISTEN_PORT = .*$', f'LISTEN_PORT = {listen_port}',
                 src, flags=re.M)
    assert f'SECRET_FILE = {str(secret_path)!r}' in src
    import importlib.util
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(src)
        mod_path = fh.name
    spec = importlib.util.spec_from_file_location("nd_shim_under_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    finally:
        os.unlink(mod_path)
    return {"Handler": mod.Handler}


def sub_token(password):
    salt = "abc123"
    return {"s": salt, "t": hashlib.md5((password + salt).encode()).hexdigest()}


class ShimE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".secret")
        cls.tmp.write(ADMIN_PW)
        cls.tmp.close()
        up_port, cls.shim_port = free_port(), free_port()
        cls.fake = ThreadingHTTPServer(("127.0.0.1", up_port), FakeNavidrome)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        ns = load_shim(cls.tmp.name, f"http://127.0.0.1:{up_port}", cls.shim_port)
        cls.shim = ThreadingHTTPServer(("127.0.0.1", cls.shim_port), ns["Handler"])
        threading.Thread(target=cls.shim.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.shim_port}"

    @classmethod
    def tearDownClass(cls):
        cls.shim.shutdown()
        cls.fake.shutdown()
        os.unlink(cls.tmp.name)

    def call(self, endpoint, **params):
        q = urllib.parse.urlencode(params)
        with urllib.request.urlopen(f"{self.base}/rest/{endpoint}?{q}",
                                    timeout=10) as r:
            return json.loads(r.read())["subsonic-response"]

    def setUp(self):
        FakeNavidrome.calls = []
        FakeNavidrome.users = [{"id": "u1", "userName": "existing"}]

    def test_create_user_ok(self):
        tok = sub_token(ADMIN_PW)
        resp = self.call("createUser", u="admin", username="newguest",
                         password="guest-pw", email="g@example.invalid", **tok)
        self.assertEqual(resp["status"], "ok")
        native = [c for c in FakeNavidrome.calls if c[1] == "/api/user"]
        self.assertEqual(len(native), 1)
        self.assertIn("newguest", native[0][2])
        self.assertIn({"id": "u-new", "userName": "newguest"},
                      FakeNavidrome.users)

    def test_wrong_auth_rejected_nothing_upstream(self):
        tok = sub_token("wrong-password")
        resp = self.call("createUser", u="admin", username="evil",
                         password="x", **tok)
        self.assertEqual(resp["status"], "failed")
        self.assertEqual(resp["error"]["code"], 40)
        self.assertEqual(FakeNavidrome.calls, [])  # nothing proxied upstream

    def test_delete_user(self):
        tok = sub_token(ADMIN_PW)
        resp = self.call("deleteUser", u="admin", username="existing", **tok)
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(FakeNavidrome.users, [])

    def test_delete_missing_user(self):
        tok = sub_token(ADMIN_PW)
        resp = self.call("deleteUser", u="admin", username="ghost", **tok)
        self.assertEqual(resp["status"], "failed")
        self.assertEqual(resp["error"]["code"], 70)

    def test_update_user_is_acknowledged_noop(self):
        tok = sub_token(ADMIN_PW)
        resp = self.call("updateUser", u="admin", username="existing", **tok)
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(FakeNavidrome.calls, [])  # documented no-op

    def test_other_rest_is_transparent_proxy(self):
        resp = self.call("ping", u="anyone", t="x", s="y")
        self.assertEqual(resp["type"], "fake-navidrome")


import urllib.parse  # noqa: E402  (used in call())

if __name__ == "__main__":
    unittest.main(verbosity=2)
