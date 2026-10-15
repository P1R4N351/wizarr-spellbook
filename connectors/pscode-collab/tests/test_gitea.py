import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pscode-collab"))
import gitea  # noqa: E402

SEEN = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _record(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        SEEN.append({"method": self.command, "path": self.path,
                     "auth": self.headers.get("Authorization", ""),
                     "body": json.loads(raw or b"{}")})
        return SEEN[-1]

    def do_GET(self):
        rec = self._record()
        if rec["path"].endswith("/api/v1/user"):
            ok = rec["auth"] == "Basic YWRhOnJpZ2h0"  # base64("ada:right")
            out = json.dumps({"login": "ada"}).encode() if ok else b"{}"
            self.send_response(200 if ok else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        rec = self._record()
        if rec["path"].endswith("/tokens"):
            payload = {"id": 7, "sha1": "tok-secret", "name": "collab"}
        elif "/forks" in rec["path"]:
            payload = {"clone_url": "http://gitea/ada/widget.git"}
        else:
            payload = {"id": 1}
        out = json.dumps(payload).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_PUT(self):
        self._record()
        self.send_response(204)
        self.end_headers()

    def do_PATCH(self):
        self._record()
        out = b"{}"
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_DELETE(self):
        self._record()
        self.send_response(204)
        self.end_headers()


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def setup_function():
    SEEN.clear()


def test_mint_token_uses_the_collaborators_own_basic_auth():
    """C14: gitea refuses POST /users/{u}/tokens over admin token-auth (401),
    so this must authenticate AS the collaborator."""
    srv, base = _serve()
    tid, secret = gitea.mint_token(base, "ada", "pw", "collab")
    srv.shutdown()
    assert (tid, secret) == (7, "tok-secret")
    assert SEEN[0]["path"] == "/api/v1/users/ada/tokens"
    assert SEEN[0]["auth"].startswith("Basic "), \
        "must be basic auth as the collaborator, not `token <admin>`"
    assert SEEN[0]["body"]["scopes"] == ["write:repository"]


def test_mint_token_never_requests_admin_scope():
    srv, base = _serve()
    gitea.mint_token(base, "ada", "pw", "collab")
    srv.shutdown()
    assert not any("admin" in s for s in SEEN[0]["body"]["scopes"])


def test_fork_is_performed_as_the_collaborator_without_an_organization():
    """C14: `organization` means fork INTO an org, so passing a username 404s;
    and the fork must be authenticated as the collaborator so it lands in
    their namespace."""
    srv, base = _serve()
    url = gitea.fork_repo(base, "ada", "pw", "piranesi-org", "pscode")
    srv.shutdown()
    assert url == "http://gitea/ada/widget.git"
    assert SEEN[0]["path"] == "/api/v1/repos/piranesi-org/pscode/forks"
    assert SEEN[0]["auth"].startswith("Basic ")
    assert "organization" not in SEEN[0]["body"], \
        "an `organization` field makes gitea look for an ORG of that name"


def test_add_collaborator_grants_read_as_admin():
    srv, base = _serve()
    gitea.add_collaborator(base, "admin-tok", "piranesi-org", "pscode", "ada")
    srv.shutdown()
    assert SEEN[0]["method"] == "PUT"
    assert SEEN[0]["path"] == "/api/v1/repos/piranesi-org/pscode/collaborators/ada"
    assert SEEN[0]["body"]["permission"] == "read"
    assert SEEN[0]["auth"].startswith("token ")


def test_add_collaborator_rejects_a_bogus_permission():
    try:
        gitea.add_collaborator("http://x", "admin-tok", "o", "r", "ada", "root")
    except AssertionError:
        return
    raise AssertionError("accepted a permission outside read/write/admin")


def test_deactivate_user_patches_active_false():
    """The revoke primitive: an admin token CANNOT delete a user's token (401),
    but deactivating kills both token and password access (verified 403)."""
    srv, base = _serve()
    gitea.deactivate_user(base, "admin-tok", "ada")
    srv.shutdown()
    assert SEEN[0]["method"] == "PATCH"
    assert SEEN[0]["path"] == "/api/v1/admin/users/ada"
    assert SEEN[0]["body"]["active"] is False


def test_activate_user_patches_active_true():
    srv, base = _serve()
    gitea.activate_user(base, "admin-tok", "ada")
    srv.shutdown()
    assert SEEN[0]["body"]["active"] is True


def test_create_user_posts_expected_fields():
    srv, base = _serve()
    gitea.create_user(base, "admin-tok", "ada", "pw123", "ada@example.invalid")
    srv.shutdown()
    assert SEEN[0]["path"] == "/api/v1/admin/users"
    assert SEEN[0]["body"]["username"] == "ada"
    assert SEEN[0]["body"]["must_change_password"] is False


def test_delete_user_issues_delete_on_admin_path():
    srv, base = _serve()
    gitea.delete_user(base, "admin-tok", "ada")
    srv.shutdown()
    assert SEEN[0]["method"] == "DELETE"
    assert SEEN[0]["path"] == "/api/v1/admin/users/ada"


def test_verify_credentials_true_on_correct_password(monkeypatch):
    srv, base = _serve()
    monkeypatch.setattr(gitea.config, "GITEA_BASE", base)
    assert gitea.verify_credentials("ada", "right") is True
    srv.shutdown()


def test_verify_credentials_false_on_wrong_password(monkeypatch):
    srv, base = _serve()
    monkeypatch.setattr(gitea.config, "GITEA_BASE", base)
    assert gitea.verify_credentials("ada", "wrong") is False
    srv.shutdown()
