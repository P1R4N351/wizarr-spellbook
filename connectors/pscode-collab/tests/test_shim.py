import hashlib
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pscode-collab"))
import shim  # noqa: E402


class Recorder:
    def __init__(self, refuse=False):
        self.created, self.removed, self.refuse = [], [], refuse

    def create(self, name, pw, email, owner, repo, deps):
        if self.refuse:
            raise shim.provision.Refused("headroom short: 0 MB spendable")
        self.created.append(name)
        return {"name": name, "path": "/c/" + name}

    def remove(self, name, deps):
        self.removed.append(name)


def _q(verb, pw="pw", user="admin", **extra):
    salt = "s1"
    tok = hashlib.md5((pw + salt).encode()).hexdigest()
    params = {"u": user, "t": tok, "s": salt}
    params.update(extra)
    return "/rest/%s.view?%s" % (verb, urllib.parse.urlencode(params))


def test_create_user_provisions():
    rec = Recorder()
    status, payload = shim.dispatch(_q("createUser", username="ada",
                                       password="p", email="a@x.invalid"),
                                    "pw", rec, deps=None)
    assert status == 200
    assert json.loads(payload)["subsonic-response"]["status"] == "ok"
    assert rec.created == ["ada"]


def test_delete_user_revokes():
    rec = Recorder()
    shim.dispatch(_q("deleteUser", username="ada"), "pw", rec, deps=None)
    assert rec.removed == ["ada"]


def test_bad_auth_is_error_40_and_provisions_nothing():
    rec = Recorder()
    status, payload = shim.dispatch(_q("createUser", pw="wrong", username="ada"),
                                    "pw", rec, deps=None)
    assert status == 200
    assert json.loads(payload)["subsonic-response"]["error"]["code"] == 40
    assert rec.created == []


def test_ping_is_stubbed_ok_without_auth():
    status, payload = shim.dispatch("/rest/ping.view", "pw", Recorder(), deps=None)
    assert json.loads(payload)["subsonic-response"]["status"] == "ok"


def test_refusal_surfaces_as_subsonic_error_not_silent_ok():
    rec = Recorder(refuse=True)
    status, payload = shim.dispatch(_q("createUser", username="ada", password="p"),
                                    "pw", rec, deps=None)
    got = json.loads(payload)["subsonic-response"]
    assert got["status"] == "failed"
    assert "headroom" in got["error"]["message"]


def test_unknown_verb_is_stubbed_not_proxied():
    status, payload = shim.dispatch("/rest/getLicense.view", "pw", Recorder(), deps=None)
    assert json.loads(payload)["subsonic-response"]["status"] == "ok"


def test_create_user_without_username_is_error_10():
    rec = Recorder()
    status, payload = shim.dispatch(_q("createUser", password="p"), "pw", rec, deps=None)
    got = json.loads(payload)["subsonic-response"]
    assert got["error"]["code"] == 10
    assert rec.created == []


def test_update_user_is_acknowledged_noop():
    rec = Recorder()
    status, payload = shim.dispatch(_q("updateUser", username="ada"), "pw", rec, deps=None)
    assert json.loads(payload)["subsonic-response"]["status"] == "ok"
    assert rec.created == [] and rec.removed == []


def test_dispatch_extracts_verb_from_dotted_view_suffix():
    # Wizarr calls /rest/ping.view, not /rest/ping — confirm the .view is stripped
    assert shim._verb("/rest/ping.view") == "ping"
    assert shim._verb("/rest/createUser.view?u=admin") == "createUser"


def test_getnowplaying_is_a_known_stub_not_unknown():
    """C16: Wizarr polls getNowPlaying ~every 30s. It was absent from the
    seeded list, so real traffic logged as known=UNKNOWN continuously -- which
    would have drowned the signal that UNKNOWN is supposed to carry."""
    assert "getNowPlaying" in shim._STUB_VERBS
    status, payload = shim.dispatch("/rest/getNowPlaying.view", "pw",
                                    Recorder(), deps=None)
    assert status == 200
    assert json.loads(payload)["subsonic-response"]["status"] == "ok"


def test_seeded_verbs_that_wizarr_never_called_were_dropped():
    """Four of the five originally-seeded verbs were never observed. Keeping
    them would make known=UNKNOWN mean less, not more."""
    for never_seen in ("getLicense", "getMusicFolders", "getUsers", "getUser"):
        assert never_seen not in shim._STUB_VERBS, \
            "%s was never observed in real traffic; it should not be listed" % never_seen
        # still answered ok by the catch-all -- an unlisted verb is not a failure
        status, payload = shim.dispatch("/rest/%s.view" % never_seen, "pw",
                                        Recorder(), deps=None)
        assert status == 200
        assert json.loads(payload)["subsonic-response"]["status"] == "ok"
