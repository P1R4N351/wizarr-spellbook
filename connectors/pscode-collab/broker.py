"""Collaborator landing endpoint: the door onto a PSCode workspace.

Wizarr's media_server.external_url is a single per-server column, so it cannot
carry a per-collaborator session URL (plan correction C3). It points here
instead. A visitor proves who they are -- with a security key, or with the
gitea credentials Wizarr created -- and is redirected into their own container
path carrying its connection token as ?tkn= (Code-OSS then persists it as the
vscode-tkn cookie: src/vs/base/common/network.ts:181-182).

TWO WAYS THROUGH THE DOOR, and the key is the front one. This surface answers
the public internet through the media host's funnel, where a password form is a
credential-phishing target and a replayable secret; a WebAuthn assertion is
neither, because it is bound to the origin that asked for it. Passwords stay
because collaborators are provisioned with gitea credentials and nothing else,
not because they are the better door.

PATHS ARRIVE PREFIXED. collab-router re-adds the public mount prefix
("/collab") to every request before forwarding, including requests that came
in without it, so this server sees "/collab/..." on both the funnel and the
tailnet. Routing therefore strips the prefix first and never matches on a bare
path.
"""
import json
import os
import urllib.parse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import gitea
import login_page
import state
import webauthn_auth as wa

_DENIED = b"Sign-in failed."
_TOKEN_DIR = "/run/collab"
_COOKIE = "pscode_sid"
_MAX_JSON = 16384          # rule 2: an attestation object is a few KB at most


def token_of(name):
    """Read the collaborator's connection token from its 0o644 file.

    0o644, not 0o600 (C12 in the plan): the broker reads this as whatever
    host user runs it, same as provision.py's writer, so this specific read
    was never blocked by the uid mismatch that broke the container's own
    read of the same file — but the file is written 0o644 for the
    container's sake, so this docstring must not claim a mode the file
    doesn't have.
    """
    assert name, "name must be non-empty"
    path = os.path.join(_TOKEN_DIR, os.path.basename(name) + ".tok")
    with open(path, encoding="utf-8") as fh:
        content = fh.read().strip()
    assert content, "token file was empty: " + path
    return content


def workspace_dest(name, store, token_of=token_of):
    """Where `name` should land, or None when nothing is provisioned.

    Returning None rather than raising is the point: an account can be real
    and authenticated while its workspace does not exist yet, and that is a
    page to render, not an error to swallow.
    """
    assert name, "name must be non-empty"
    assert store is not None, "a record store is required"
    rec = store.get(name)
    if rec is None or rec.get("revoked_at") or not rec.get("path"):
        return None
    try:
        token = token_of(name)
    except OSError:
        return None
    # The Location the BROWSER follows, so it must carry the public path
    # prefix the front mounts this under (C13) — rec["path"] is only the
    # the collab host-internal route, and redirecting to it bare would drop the prefix
    # and land the collaborator outside the mount.
    # safe="" matters: urllib.parse.quote leaves "/" unescaped by default, so
    # a connection token containing a slash would be read as another path
    # segment and the redirect would land somewhere else entirely. Today's
    # tokens are hex, so this changes nothing now and cannot bite later.
    return "%s%s/?tkn=%s" % (config.PUBLIC_BASE_PATH.rstrip("/"),
                             rec["path"], urllib.parse.quote(token, safe=""))


def strip_base(path, base):
    """Remove the public mount prefix from an incoming path.

    collab-router re-adds the prefix to EVERY request before forwarding, so
    this runs on the funnel and the tailnet alike. Matching on a bare "/" here
    instead would route nothing.
    """
    assert isinstance(path, str) and path.startswith("/"), "absolute path required"
    assert base is None or isinstance(base, str), "base must be a string"
    base = (base or "").rstrip("/")
    if base and path.startswith(base):
        path = path[len(base):]
    return path or "/"


def login(form, store, verify, token_of=token_of):
    """Authenticate one password sign-in. Returns (status, headers, body)."""
    assert isinstance(form, dict), "form must be a parsed urlencoded dict"
    assert callable(verify), "verify must be a callable(name, password) -> bool"
    name = form.get("username", [""])[0]
    password = form.get("password", [""])[0]
    if not name or not password:
        return 401, {}, _DENIED
    rec = store.get(name)
    if rec is None or rec.get("revoked_at"):
        return 401, {}, _DENIED
    if not verify(name, password):
        return 401, {}, _DENIED
    dest = workspace_dest(name, store, token_of)
    if dest is None:
        return 200, {}, b""          # authenticated, but nowhere to go
    return 302, {"Location": dest}, b""


class Handler(BaseHTTPRequestHandler):
    server_version = "pscode-collab-broker/2.0"
    store = None
    creds = None
    server_fido = None
    pending = None
    sessions = None
    throttle = None
    verify = staticmethod(lambda name, password: False)

    def log_message(self, fmt, *args):
        pass    # never log credentials or tokens

    # ---- plumbing -------------------------------------------------------

    def _base(self):
        return config.PUBLIC_BASE_PATH.rstrip("/")

    def _route(self):
        """The path with the mount prefix and query string removed."""
        return strip_base(urllib.parse.urlsplit(self.path).path, self._base())

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

    def _source(self):
        """Best available client identity for throttling.

        X-Forwarded-For is trusted only for its FIRST hop and only as a
        throttle key -- it is attacker-settable on a direct tailnet
        connection, so it never grants anything, it only slows something down.
        """
        fwd = self.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
        return self.client_address[0]

    def _send(self, status, headers, body, ctype="text/html; charset=utf-8"):
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self._send(status, headers or {}, body, "application/json")

    def _read_json(self):
        size = min(int(self.headers.get("Content-Length") or 0), _MAX_JSON)
        if size <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(size).decode("utf-8", "replace"))
        except ValueError:
            return {}

    def _session_user(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw)
        except cookies.CookieError:
            return None
        morsel = jar.get(_COOKIE)
        return self.sessions.who(morsel.value) if morsel else None

    def _session_header(self, username):
        # Secure unconditionally, rather than from X-Forwarded-Proto. Both
        # real doors are https (the funnel and the tailnet :8450 front), the
        # forwarded header is attacker-settable on a direct tailnet
        # connection, and the failure mode of being wrong in the safe
        # direction is mild: over plain http the browser simply declines to
        # keep the cookie, so the plain door stops offering sessions instead
        # of offering them in cleartext. Password sign-in still works there,
        # because that redirect carries its own ?tkn= and never needed the
        # cookie.
        parts = ["%s=%s" % (_COOKIE, self.sessions.open(username)),
                 "Path=" + (self._base() or "/"), "HttpOnly", "Secure",
                 "SameSite=Lax", "Max-Age=%d" % wa.SESSION_TTL]
        return {"Set-Cookie": "; ".join(parts)}

    def _door(self, notice=None):
        return login_page.render_login(config.WEBAUTHN_RP_ID,
                                       config.SECURE_ORIGIN, self._base(),
                                       notice, config.WEBAUTHN_ORIGINS)

    def _landing(self, username):
        return login_page.render_landing(
            username, "No workspace yet",
            "You are signed in, but no PSCode workspace has been provisioned "
            "for this account. Ask the House to provision one, then open this "
            "door again.")

    # ---- routes ---------------------------------------------------------

    def do_GET(self):
        assert self.store is not None, "handler was never wired to a store"
        assert self.creds is not None, "handler was never wired to credentials"
        route = self._route()
        if route == "/enroll":
            return self._enroll_page()
        if route not in ("/", ""):
            return self._send(404, {}, b"Not found.")
        who = self._session_user()
        if who:
            dest = workspace_dest(who, self.store)
            if dest:
                return self._send(302, {"Location": dest}, b"")
            return self._send(200, {}, self._landing(who))
        self._send(200, {}, self._door())

    def do_POST(self):
        assert self.server_fido is not None, "handler has no WebAuthn server"
        assert self.pending is not None, "handler has no ceremony table"
        route = self._route()
        if self.throttle.blocked(self._source()):
            return self._json(429, {"error": "Too many failed attempts. "
                                             "Wait a few minutes."})
        if route in ("/", ""):
            return self._password_login()
        if route == "/webauthn/login/begin":
            return self._login_begin()
        if route == "/webauthn/login/finish":
            return self._login_finish()
        if route == "/webauthn/register/begin":
            return self._register_begin()
        if route == "/webauthn/register/finish":
            return self._register_finish()
        self._send(404, {}, b"Not found.")

    def _password_login(self):
        size = min(int(self.headers.get("Content-Length") or 0), 4096)
        raw = self.rfile.read(size).decode("utf-8", "replace")
        form = urllib.parse.parse_qs(raw)
        status, headers, body = login(form, self.store, self.verify)
        if status == 401:
            self.throttle.fail(self._source())
            return self._send(401, {}, self._door(
                "That username or password didn't work."))
        name = form.get("username", [""])[0]
        self.throttle.clear(self._source())
        headers = dict(headers)
        headers.update(self._session_header(name))
        if status == 302:
            return self._send(302, headers, b"")
        self._send(200, headers, self._landing(name))

    def _login_begin(self):
        body = self._read_json()
        username = (body.get("username") or "").strip() or None
        try:
            options, ceremony = wa.authentication_options(
                self.server_fido, self.creds, username)
        except ValueError as exc:
            self.throttle.fail(self._source())
            return self._json(400, {"error": str(exc)})
        payload = wa.jsonable(dict(options))
        payload["handle"] = self.pending.put(ceremony)
        self._json(200, payload)

    def _login_finish(self):
        body = self._read_json()
        ceremony = self.pending.take(body.pop("handle", None))
        if ceremony is None:
            self.throttle.fail(self._source())
            return self._json(400, {"error": "That sign-in took too long. "
                                             "Touch your key again."})
        try:
            who = wa.finish_authentication(self.server_fido, self.creds,
                                           ceremony, body)
        except (ValueError, KeyError) as exc:
            self.throttle.fail(self._source())
            return self._json(401, {"error": str(exc) or "That key was not "
                                                         "accepted."})
        self.throttle.clear(self._source())
        dest = workspace_dest(who, self.store) or ((self._base() or "") + "/")
        self._json(200, {"ok": True, "dest": dest}, self._session_header(who))

    def _enroll_page(self):
        ticket = (self._query().get("t") or [""])[0]
        found = self.creds.peek_ticket(ticket)
        if not found:
            return self._send(
                404, {},
                login_page.render_landing(
                    "nobody", "That link is spent",
                    "Enrollment links work once and expire after half an "
                    "hour. Ask the House for a fresh one."))
        username, label = found
        self._send(200, {}, login_page.render_enroll(
            username, label, ticket, self._base(), config.SECURE_ORIGIN))

    def _register_begin(self):
        body = self._read_json()
        found = self.creds.peek_ticket(body.get("ticket"))
        if not found:
            self.throttle.fail(self._source())
            return self._json(403, {"error": "This enrollment link is no "
                                             "longer valid."})
        username, _ = found
        options, ceremony = wa.registration_options(
            self.server_fido, self.creds, username, username)
        payload = wa.jsonable(dict(options))
        payload["handle"] = self.pending.put((ceremony, username))
        self._json(200, payload)

    def _register_finish(self):
        body = self._read_json()
        # Check the CEREMONY FIRST. Spending the ticket before this test burned
        # the link whenever the ceremony merely expired, which contradicted the
        # very reason spending was deferred to /finish and cost the visitor a
        # fresh link per slow touch. Abandon and timeout now cost the same:
        # nothing.
        ceremony = self.pending.take(body.pop("handle", None))
        if ceremony is None:
            return self._json(400, {"error": "That enrollment took too long. "
                                             "Ask for a fresh link."})
        # The ticket is SPENT here, not at /begin: spending it earlier would
        # burn the link on a ceremony the visitor could still abandon, and
        # leave them holding a dead link and no key.
        found = self.creds.spend_ticket(body.get("ticket"))
        if not found:
            self.throttle.fail(self._source())
            return self._json(403, {"error": "This enrollment link is no "
                                             "longer valid."})
        username, label = found
        body.pop("ticket", None)   # ours, not part of the WebAuthn response
        try:
            # Through wa, never register_complete directly: the library parses
            # the answer itself, and makes N bytes of a number N (answer_of).
            auth_data = wa.finish_registration(self.server_fido, ceremony[0],
                                               body)
            self.creds.add_credential(username, auth_data.credential_data,
                                      label, auth_data.is_user_verified())
        except (ValueError, KeyError) as exc:
            self.throttle.fail(self._source())
            return self._json(400, {"error": str(exc) or "That key was not "
                                                         "accepted."})
        self._json(200, {"ok": True, "user": username})


def main():
    Handler.store = state.Store(config.STATE_PATH)
    Handler.creds = wa.CredStore(config.WEBAUTHN_STORE)
    Handler.server_fido = wa.build_server(config.WEBAUTHN_RP_ID,
                                          config.WEBAUTHN_RP_NAME,
                                          config.WEBAUTHN_ORIGINS)
    Handler.pending = wa.Pending()
    Handler.sessions = wa.Sessions()
    Handler.throttle = wa.Throttle()
    Handler.verify = staticmethod(gitea.verify_credentials)
    ThreadingHTTPServer(("0.0.0.0", config.BROKER_PORT),
                        Handler).serve_forever()


if __name__ == "__main__":
    main()
