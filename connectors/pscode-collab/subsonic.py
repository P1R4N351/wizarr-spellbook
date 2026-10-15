"""Subsonic wire format and inbound auth. Pure functions; no I/O.

Modelled on skills/nd-subsonic-shim/nd-subsonic-shim.py, which proved this is
the exact shape Wizarr's NavidromeClient accepts.
"""
import hashlib
import hmac
import json

_VERSION = "1.16.1"


def body(ok, code=0, message=""):
    """Encode a Subsonic response envelope as JSON bytes."""
    assert isinstance(ok, bool), "ok must be bool"
    resp = {"status": "ok" if ok else "failed", "version": _VERSION,
            "type": "pscode-collab-shim", "serverVersion": "1.0"}
    if not ok:
        resp["error"] = {"code": code, "message": message}
    return json.dumps({"subsonic-response": resp}).encode()


def auth_ok(params, admin_user, admin_pw):
    """True when params carry a valid salted-md5 token for admin_pw.

    Wizarr sends u/t/s (never p=). Constant-time compare so a wrong token
    leaks no timing signal.
    """
    assert admin_pw, "admin password must be non-empty"
    assert isinstance(params, dict), "params must be a parsed query dict"
    user = params.get("u", [""])[0]
    token = params.get("t", [""])[0]
    salt = params.get("s", [""])[0]
    if user != admin_user or not token or not salt:
        return False
    want = hashlib.md5((admin_pw + salt).encode()).hexdigest()
    return hmac.compare_digest(token, want)
