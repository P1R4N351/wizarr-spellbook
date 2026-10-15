"""Subsonic-shaped provisioning surface for Wizarr.

Wizarr's media_server row registers this as a Navidrome server, so Wizarr's
NavidromeClient speaks Subsonic to it. Unlike nd-subsonic-shim there is no
upstream to proxy to (plan correction C4): every non-provisioning verb is
synthesized.

_STUB_VERBS below is MEASURED from real Wizarr traffic (C16), not seeded:
a logging shim captured what Wizarr actually calls during a live invite
redemption. The original seeded guess was wrong in both directions -- four
of its five verbs were never called, and the most frequent real one
(getNowPlaying) was absent.
"""
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import provision
import subsonic

# MEASURED, not seeded (C16). Task 11 pointed a logging shim at real Wizarr
# traffic and the seeded guess was wrong in both directions: of the five verbs
# originally listed (ping, getLicense, getMusicFolders, getUsers, getUser) only
# `ping` was ever called, while `getNowPlaying` -- the most frequent by far,
# polled roughly every 30s for Wizarr's dashboard -- was missing entirely.
# A plausible-looking stub list is not an observed one.
#
# Anything outside this set still answers ok via the catch-all below, so an
# unlisted verb is never a failure; listing the observed ones is what makes a
# log line reading known=UNKNOWN mean "Wizarr started doing something new",
# which is the only reason this tuple earns its place.
_STUB_VERBS = ("ping", "getNowPlaying")
_AUTHED_VERBS = ("createUser", "deleteUser", "updateUser")

# Append-only record of which Subsonic verbs Wizarr actually calls. This exists
# because _STUB_VERBS above was SEEDED from the standard Subsonic surface, not
# observed -- and a stub set that merely looks plausible is exactly the kind of
# thing that passes review and fails in production. The verb and status are
# recorded; the query string is NOT, because it carries the salted-md5 auth.
_VERB_LOG = "/var/lib/pscode-collab/verbs.log"
_MAX_LOG_BYTES = 1 << 20      # rule 2: bounded; stops an unbounded disk creep


def _record_verb(verb, status, known):
    """Append one verb observation. Never raises: an audit trail must not be
    able to take the provisioning path down with it."""
    assert verb, "verb required"
    assert known in ("stub", "UNKNOWN", "authed", "authed/denied"), \
        "unexpected known-class: %r" % (known,)
    try:
        if os.path.exists(_VERB_LOG) and os.path.getsize(_VERB_LOG) > _MAX_LOG_BYTES:
            return
        with open(_VERB_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s %s status=%s known=%s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                verb, status, known))
    except OSError:
        pass    # deliberately swallowed: logging must never break provisioning


def _verb(path):
    leaf = urllib.parse.urlparse(path).path.rstrip("/").split("/")[-1]
    return leaf.split(".")[0]


def dispatch(path, admin_pw, prov, deps):
    """Return (http_status, body_bytes) for one Subsonic request."""
    assert path.startswith("/"), "path must be an absolute request path"
    assert admin_pw, "admin_pw must be non-empty"
    parsed = urllib.parse.urlparse(path)
    verb = _verb(path)
    params = urllib.parse.parse_qs(parsed.query)
    if verb in _STUB_VERBS:
        _record_verb(verb, 200, "stub")
        return 200, subsonic.body(True)
    if verb not in _AUTHED_VERBS:
        # An UNKNOWN verb still answers ok so Wizarr proceeds, but it is
        # recorded distinctly -- this is the line that tells us _STUB_VERBS
        # was incomplete.
        _record_verb(verb, 200, "UNKNOWN")
        return 200, subsonic.body(True)
    if not subsonic.auth_ok(params, config.ADMIN_USER, admin_pw):
        _record_verb(verb, 40, "authed/denied")
        return 200, subsonic.body(False, 40, "Wrong username or password")
    status, payload = _provision(verb, params, prov, deps)
    _record_verb(verb, status, "authed")
    return status, payload


def _provision(verb, params, prov, deps):
    assert verb in _AUTHED_VERBS, "caller must pre-filter to authed verbs"
    assert isinstance(params, dict), "params must be a parsed query dict"
    name = params.get("username", [""])[0]
    if not name:
        return 200, subsonic.body(False, 10, "username required")
    try:
        if verb == "createUser":
            prov.create(name, params.get("password", [""])[0],
                        params.get("email", [""])[0],
                        config.REPO_OWNER, config.REPO_NAME, deps)
        elif verb == "deleteUser":
            prov.remove(name, deps)
        # updateUser: acknowledged no-op; roles are container-side, not Wizarr's
    except provision.Refused as exc:
        return 200, subsonic.body(False, 50, str(exc))
    except (RuntimeError, OSError) as exc:
        return 500, subsonic.body(False, 0, "provisioning failed: %s" % exc)
    return 200, subsonic.body(True)


class Handler(BaseHTTPRequestHandler):
    server_version = "pscode-collab-shim/1.0"
    admin_pw = ""
    prov = provision
    deps = None

    def log_message(self, fmt, *args):
        pass    # systemd captures stderr; access logs would carry salts

    def do_GET(self):
        status, payload = dispatch(self.path, self.admin_pw, self.prov, self.deps)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_POST = do_GET


def main():
    with open(config.SECRET_FILE, encoding="utf-8") as fh:
        Handler.admin_pw = fh.read().strip()
    assert Handler.admin_pw, "empty admin secret"
    # Handler.deps defaults to None; without this the provisioning path dies on
    # the first real Wizarr call (C16). Built here, not lazily, so a broken
    # secret file fails at startup rather than mid-invite.
    Handler.deps = provision.build_deps()
    assert Handler.deps.store is not None, "deps.store missing"
    ThreadingHTTPServer(("0.0.0.0", config.SHIM_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
