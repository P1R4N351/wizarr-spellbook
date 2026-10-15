#!/usr/bin/env python3
"""Subsonic-to-native translation shim for Navidrome user provisioning.

Navidrome returns 501 for the Subsonic user-management endpoints
(createUser/deleteUser), but Wizarr's navidrome client only speaks
Subsonic. This shim sits beside navidrome (the music host) and:

  /rest/createUser  -> native POST /api/user   (login as admin first)
  /rest/deleteUser  -> native DELETE /api/user/<id>
  everything else   -> transparent proxy to the local navidrome

Only Wizarr talks to the shim (media_server.url). Guests stream from
navidrome directly via external_url — no media bytes pass through here.

Inbound calls must authenticate as the SAME admin the shim acts as:
t == md5(admin_password + s). Anything else gets Subsonic error 40.

P10: stdlib only, bounded reads, checked returns, no dynamic code.
"""
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_PORT = 4534
UPSTREAM = "http://127.0.0.1:4533"
SECRET_FILE = "/etc/nd-shim.secret"
ADMIN_USER = "admin"
MAX_BODY = 1 << 20  # proxied control responses only; streams never pass here
TIMEOUT = 15

with open(SECRET_FILE, encoding="utf-8") as fh:
    ADMIN_PW = fh.read().strip()
if not ADMIN_PW:
    sys.stderr.write("nd-shim: empty secret file\n")
    sys.exit(1)


def subsonic_body(ok, code=0, message=""):
    resp = {"status": "ok" if ok else "failed", "version": "1.16.1",
            "type": "nd-shim", "serverVersion": "1.0"}
    if not ok:
        resp["error"] = {"code": code, "message": message}
    return json.dumps({"subsonic-response": resp}).encode()


def auth_ok(params):
    user = params.get("u", [""])[0]
    token = params.get("t", [""])[0]
    salt = params.get("s", [""])[0]
    if user != ADMIN_USER or not token or not salt:
        return False
    want = hashlib.md5((ADMIN_PW + salt).encode()).hexdigest()
    return token == want


def native_login():
    body = json.dumps({"username": ADMIN_USER, "password": ADMIN_PW}).encode()
    req = urllib.request.Request(
        UPSTREAM + "/auth/login", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        tok = json.loads(r.read(MAX_BODY)).get("token", "")
    if not tok:
        raise RuntimeError("native login returned no token")
    return tok


def native_create_user(params):
    tok = native_login()
    payload = {
        "userName": params.get("username", [""])[0],
        "name": params.get("username", [""])[0],
        "password": params.get("password", [""])[0],
        "email": params.get("email", [""])[0],
        "isAdmin": params.get("adminRole", ["false"])[0] == "true",
    }
    if not payload["userName"] or not payload["password"]:
        return subsonic_body(False, 10, "username and password required")
    req = urllib.request.Request(
        UPSTREAM + "/api/user", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "x-nd-authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        r.read(MAX_BODY)
    return subsonic_body(True)


def native_delete_user(params):
    tok = native_login()
    uname = params.get("username", [""])[0]
    if not uname:
        return subsonic_body(False, 10, "username required")
    req = urllib.request.Request(
        UPSTREAM + "/api/user?_end=1000&_start=0",
        headers={"x-nd-authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        users = json.loads(r.read(MAX_BODY))
    uid = next((u["id"] for u in users if u.get("userName") == uname), "")
    if not uid:
        return subsonic_body(False, 70, "user not found: " + uname)
    req = urllib.request.Request(
        UPSTREAM + "/api/user/" + urllib.parse.quote(uid), method="DELETE",
        headers={"x-nd-authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        r.read(MAX_BODY)
    return subsonic_body(True)


class Handler(BaseHTTPRequestHandler):
    server_version = "nd-shim/1.0"

    def log_message(self, fmt, *args):  # quiet; systemd captures errors
        pass

    def _send(self, status, body, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self):
        url = UPSTREAM + self.path
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                body = r.read(MAX_BODY)
                ctype = r.headers.get("Content-Type", "application/json")
                self._send(r.status, body, ctype)
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read(MAX_BODY) or b"",
                       e.headers.get("Content-Type", "text/plain"))
        except (urllib.error.URLError, OSError) as e:
            self._send(502, subsonic_body(False, 0, "upstream: " + str(e)))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        endpoint = parsed.path.rstrip("/").split("/")[-1].split(".")[0]
        if endpoint not in ("createUser", "deleteUser", "updateUser"):
            self._proxy()
            return
        params = urllib.parse.parse_qs(parsed.query)
        if not auth_ok(params):
            self._send(200, subsonic_body(False, 40, "Wrong username or password"))
            return
        try:
            if endpoint == "createUser":
                self._send(200, native_create_user(params))
            elif endpoint == "deleteUser":
                self._send(200, native_delete_user(params))
            else:  # updateUser: acknowledged no-op; roles are navidrome-side
                self._send(200, subsonic_body(True))
        except (urllib.error.URLError, RuntimeError, OSError) as e:
            self._send(200, subsonic_body(False, 0, "shim: " + str(e)))

    do_POST = do_GET


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
