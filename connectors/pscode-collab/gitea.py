"""Gitea 1.26.1 REST calls for collaborator provisioning.

WHICH IDENTITY PERFORMS WHICH CALL — all four rules below were established by
probing the live server before deployment (plan correction C14), not from the
API docs, and three of them contradict the original design:

  * ADMIN TOKEN: create_user, delete_user, add/remove_collaborator,
    delete_repo, deactivate/activate_user. Minted by gitea's own admin CLI
    (`gitea admin user generate-access-token`), so no password is ever handled.

  * THE COLLABORATOR'S OWN BASIC AUTH: fork_repo and mint_token. Gitea's
    escalation guard REFUSES both over token-auth — `POST /users/{u}/tokens`
    with an admin token returns 401. The shim has the collaborator's password
    because Wizarr supplies it in createUser, so it authenticates as them.

  * A COLLABORATOR CANNOT FORK A PRIVATE REPO THEY CANNOT SEE. Gitea answers
    404 for invisible repos, so add_collaborator(read) must come FIRST. That
    404 is confinement working, not a bug.

  * REVOKE IS DEACTIVATION, NOT TOKEN DELETION. An admin token cannot delete
    a user's token (401, same guard). Deactivating the user kills token AND
    password access (both 403, verified) while preserving their fork for the
    retention window — which token-deletion alone would not have done.
"""
import base64
import json
import urllib.error
import urllib.request

import config

_SCOPES = ["write:repository"]


def _req(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    return req


def _send(req):
    with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
        raw = resp.read(config.MAX_BODY)
    return json.loads(raw) if raw else {}


def _as_admin(method, url, admin_token, payload=None):
    """One call authenticated with the shim's admin token."""
    assert admin_token, "admin token must be non-empty"
    req = _req(method, url, payload)
    req.add_header("Authorization", "token " + admin_token)
    return _send(req)


def _as_user(method, url, user, password, payload=None):
    """One call authenticated as the collaborator (gitea requires this for
    fork_repo and mint_token — see the module docstring)."""
    assert user and password, "user and password required"
    req = _req(method, url, payload)
    raw = ("%s:%s" % (user, password)).encode()
    req.add_header("Authorization", "Basic " + base64.b64encode(raw).decode())
    return _send(req)


def create_user(base, admin_token, name, password, email):
    _as_admin("POST", base + "/api/v1/admin/users", admin_token,
              {"username": name, "password": password, "email": email,
               "must_change_password": False})


def delete_user(base, admin_token, user):
    _as_admin("DELETE", base + "/api/v1/admin/users/" + user, admin_token)


def deactivate_user(base, admin_token, user):
    """Revoke access while KEEPING the account and its fork.

    This is the revoke primitive: verified to make both the collaborator's
    token and their password return 403, without destroying the work the
    retention window exists to preserve.
    """
    assert user, "user required"
    _as_admin("PATCH", base + "/api/v1/admin/users/" + user, admin_token,
              {"login_name": user, "source_id": 0, "active": False})


def activate_user(base, admin_token, user):
    """Re-admit a collaborator whose container is still inside retention."""
    assert user, "user required"
    _as_admin("PATCH", base + "/api/v1/admin/users/" + user, admin_token,
              {"login_name": user, "source_id": 0, "active": True})


def add_collaborator(base, admin_token, owner, repo, user, permission="read"):
    """Grant the collaborator access to the invited repo.

    Must precede fork_repo: gitea returns 404 (not 403) for a private repo the
    user cannot see, so a fork attempted first fails as if the repo did not
    exist.
    """
    assert permission in ("read", "write", "admin"), "bad permission"
    _as_admin("PUT", "%s/api/v1/repos/%s/%s/collaborators/%s" % (
        base, owner, repo, user), admin_token, {"permission": permission})


def remove_collaborator(base, admin_token, owner, repo, user):
    _as_admin("DELETE", "%s/api/v1/repos/%s/%s/collaborators/%s" % (
        base, owner, repo, user), admin_token)


def fork_repo(base, user, password, owner, repo):
    """Fork owner/repo into the collaborator's own namespace; return its URL.

    Authenticated AS the collaborator, with no `organization` field — that
    field means "fork into this ORG", so passing a username there 404s.
    """
    out = _as_user("POST", "%s/api/v1/repos/%s/%s/forks" % (base, owner, repo),
                   user, password, {})
    url = out.get("clone_url", "")
    assert url, "fork returned no clone_url"
    return url


def delete_repo(base, admin_token, owner, repo):
    """Remove a fork. Rollback path only."""
    _as_admin("DELETE", "%s/api/v1/repos/%s/%s" % (base, owner, repo), admin_token)


def mint_token(base, user, password, token_name):
    """Mint a push-scoped token for the collaborator; return (id, secret).

    Authenticated as the collaborator: an admin token gets 401 here.
    """
    out = _as_user("POST", "%s/api/v1/users/%s/tokens" % (base, user),
                   user, password, {"name": token_name, "scopes": list(_SCOPES)})
    tid, secret = out.get("id"), out.get("sha1", "")
    assert tid is not None and secret, "token mint returned no id/secret"
    return tid, secret


def verify_credentials(name, password):
    """True when name/password authenticate against gitea. No token minted.

    Takes only (name, password) — not (base, name, password) — because
    broker.login calls `verify(name, password)` directly (Task 7); GITEA_BASE
    comes from config here rather than from a caller who would otherwise have
    to thread it through the broker's pure, I/O-free login() function.

    A deactivated collaborator fails here (403), so revocation closes the
    broker's door too, not just gitea's.
    """
    assert name, "name must be non-empty"
    assert password, "password must be non-empty"
    req = urllib.request.Request(config.GITEA_BASE + "/api/v1/user")
    raw = ("%s:%s" % (name, password)).encode()
    req.add_header("Authorization", "Basic " + base64.b64encode(raw).decode())
    try:
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False    # 401/403 are the expected denial path, not an error
