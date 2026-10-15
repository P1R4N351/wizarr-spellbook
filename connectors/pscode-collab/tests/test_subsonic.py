import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pscode-collab"))
import subsonic  # noqa: E402


def test_ok_body_shape():
    got = json.loads(subsonic.body(True))["subsonic-response"]
    assert got["status"] == "ok"
    assert "error" not in got


def test_error_body_carries_code_and_message():
    got = json.loads(subsonic.body(False, 40, "nope"))["subsonic-response"]
    assert got["status"] == "failed"
    assert got["error"]["code"] == 40
    assert got["error"]["message"] == "nope"


def _params(user, pw, salt):
    token = hashlib.md5((pw + salt).encode()).hexdigest()
    return {"u": [user], "t": [token], "s": [salt]}


def test_auth_accepts_correct_salted_md5():
    assert subsonic.auth_ok(_params("admin", "pw", "abc"), "admin", "pw")


def test_auth_rejects_wrong_password():
    assert not subsonic.auth_ok(_params("admin", "bad", "abc"), "admin", "pw")


def test_auth_rejects_wrong_user():
    assert not subsonic.auth_ok(_params("other", "pw", "abc"), "admin", "pw")


def test_auth_rejects_missing_salt():
    p = _params("admin", "pw", "abc")
    p["s"] = [""]
    assert not subsonic.auth_ok(p, "admin", "pw")
