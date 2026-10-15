import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pscode-collab"))
import broker  # noqa: E402
import state  # noqa: E402


def _store(tmp_path, revoked=None):
    s = state.Store(str(tmp_path / "s.json"))
    s.put({"name": "ada", "port": 47100, "path": "/c/ada",
           "gitea_token_id": 7, "created_at": "t", "revoked_at": revoked})
    return s


def _verify(name, password):
    return name == "ada" and password == "right"


def test_valid_login_redirects_with_token(tmp_path):
    status, headers, _ = broker.login(
        {"username": ["ada"], "password": ["right"]},
        _store(tmp_path), _verify, token_of=lambda n: "tok-abc")
    assert status == 302
    assert headers["Location"] == "/c/ada/?tkn=tok-abc"


def test_wrong_password_is_401_and_leaks_no_path(tmp_path):
    status, headers, body = broker.login(
        {"username": ["ada"], "password": ["wrong"]},
        _store(tmp_path), _verify, token_of=lambda n: "tok-abc")
    assert status == 401
    assert "Location" not in headers
    assert b"/c/ada" not in body


def test_unknown_user_is_401_not_500(tmp_path):
    status, _, _ = broker.login(
        {"username": ["nobody"], "password": ["right"]},
        _store(tmp_path), _verify, token_of=lambda n: "tok-abc")
    assert status == 401


def test_revoked_collaborator_cannot_log_in(tmp_path):
    status, _, _ = broker.login(
        {"username": ["ada"], "password": ["right"]},
        _store(tmp_path, revoked="2026-08-21T00:00:00Z"), _verify,
        token_of=lambda n: "tok-abc")
    assert status == 401


def test_missing_fields_are_401(tmp_path):
    status, _, _ = broker.login({}, _store(tmp_path), _verify,
                                token_of=lambda n: "tok-abc")
    assert status == 401


def test_missing_password_only_is_401(tmp_path):
    status, _, _ = broker.login({"username": ["ada"]}, _store(tmp_path), _verify,
                                token_of=lambda n: "tok-abc")
    assert status == 401


def test_redirect_carries_the_public_base_path_when_set(monkeypatch, tmp_path):
    """C8 Option A mounts the front under a path prefix (all three
    funnel-eligible ports on the media host are already taken), so the browser-facing
    Location must include it — redirecting to the bare the collab host-internal route
    would land the collaborator outside the mount.
    """
    monkeypatch.setattr(broker.config, "PUBLIC_BASE_PATH", "/collab")
    status, headers, _ = broker.login(
        {"username": ["ada"], "password": ["right"]},
        _store(tmp_path), _verify, token_of=lambda n: "tok-abc")
    assert status == 302
    assert headers["Location"] == "/collab/c/ada/?tkn=tok-abc"


def test_redirect_tolerates_a_trailing_slash_on_the_base_path(monkeypatch, tmp_path):
    monkeypatch.setattr(broker.config, "PUBLIC_BASE_PATH", "/collab/")
    _, headers, _ = broker.login(
        {"username": ["ada"], "password": ["right"]},
        _store(tmp_path), _verify, token_of=lambda n: "tok-abc")
    assert headers["Location"] == "/collab/c/ada/?tkn=tok-abc", \
        "a trailing slash must not produce a doubled // in the redirect"


def test_token_is_url_quoted_in_the_redirect(tmp_path):
    status, headers, _ = broker.login(
        {"username": ["ada"], "password": ["right"]},
        _store(tmp_path), _verify, token_of=lambda n: "a b&c")
    assert status == 302
    assert headers["Location"] == "/c/ada/?tkn=a%20b%26c"
