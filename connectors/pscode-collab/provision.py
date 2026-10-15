"""Create and revoke one collaborator, with ordered rollback.

# P10 RELAXATIONS: relaxes rule 7 (broad except) in _unwind() only — it runs
# inside an active `except Exception: _unwind(undo); raise`, and narrowing it
# risks one rollback step's unanticipated failure shadowing the real
# provisioning error about to be re-raised. Justified at the call site too.

The spec's error-handling requirement is that a failed createUser leaves no
partial container. That is enforced here by unwinding completed steps in
reverse on any exception, then re-raising so the shim can return non-2xx and
Wizarr shows its own invite-failure UX.

A re-invite inside RETENTION_DAYS resumes the retained container instead of
provisioning fresh, so an accidental revoke does not destroy in-progress work.
"""
import datetime
import grp
import os
import secrets
import types

import config
import container as container_mod
import gitea as gitea_mod
import route as route_mod
import state

_TOKEN_DIR = "/run/collab"
_BROKER_GROUP = "pscode-collab"


class Refused(Exception):
    """Admission denied. Distinct from a provisioning failure."""


def build_deps():
    """The PRODUCTION dependency bundle create()/remove() expect.

    This exists because it did not, and nothing caught that: every unit test
    injects its own fake `deps`, so the whole suite passed green while
    shim.main() left Handler.deps = None and the real provisioning path died
    on the first Wizarr call with
    `AttributeError: 'NoneType' object has no attribute 'store'`.
    Tests that supply the very thing production forgets to build cannot
    observe it missing — only an end-to-end could, and did (C16).
    """
    assert config.STATE_PATH, "config.STATE_PATH must be set"
    assert config.GITEA_TOKEN_FILE, "config.GITEA_TOKEN_FILE must be set"

    def _secret(which):
        path = {"gitea": config.GITEA_TOKEN_FILE}[which]
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        assert value, "secret file is empty: " + path
        return value

    return types.SimpleNamespace(
        store=state.Store(config.STATE_PATH),
        gitea=gitea_mod,
        container=container_mod,
        route=route_mod,
        available_mb=state.read_available_mb,
        now=lambda: datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        parse_time=lambda s: datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ"),
        secret=_secret,
        token_dir=_TOKEN_DIR,
    )


def _admit(deps):
    active = len([r for r in deps.store.all() if not r.get("revoked_at")])
    ok, why = state.admission_ok(deps.available_mb(), active)
    if not ok:
        raise Refused(why)


_CONNECTION_TOKEN_BYTES = 20   # 40 hex chars, the shape the server already had


def new_connection_token():
    """A fresh workbench connection token, independent of any other credential.

    WHY THIS EXISTS (2026-09-01). This used to be the sha1 that gitea's
    mint_token returned -- create() wrote the gitea API token straight in as
    the connection token. That made one secret serve two jobs, so ANY path
    that revealed the connection token also handed out a push-scoped
    (write:repository) gitea credential. That is not hypothetical: the
    collab-alice container's own startup banner prints
    "Web UI available at ...?tkn=<token>", and on 2026-09-01 that line put a
    live gitea token into an agent transcript.

    A connection token only has to be unguessable, so it is generated here and
    is now the ONLY thing that leaks when a URL leaks. secrets.token_hex is
    the CSPRNG; 20 bytes gives the same 40-hex shape the server was already
    reading, so nothing downstream has to change.
    """
    return secrets.token_hex(_CONNECTION_TOKEN_BYTES)


def _prepare_token_dir(token_dir):
    """Keep host token storage private while allowing the broker to traverse it.

    The container receives only one read-only file bind mount.  The broker,
    which issues a redirect after a successful WebAuthn assertion, also needs
    traversal permission to read that file.  A reboot removes /run/collab, so
    this must be repaired whenever a token is written rather than assumed from
    the original provisioning transaction.
    """
    assert token_dir and os.path.isabs(token_dir), "absolute token directory required"
    try:
        gid = grp.getgrnam(_BROKER_GROUP).gr_gid
    except KeyError as exc:
        raise RuntimeError("missing broker group: " + _BROKER_GROUP) from exc
    os.makedirs(token_dir, exist_ok=True, mode=0o710)
    os.chown(token_dir, -1, gid)
    os.chmod(token_dir, 0o710)


def _write_token(collab, secret, token_dir, name=None):
    """Write one collaborator's connection token.

    `name` overrides the file's basename so the same proven mode/ownership
    handling can also write the collaborator's gitea token beside it, rather
    than the two credentials being conflated into one file again.

    0o644 on the file, 0o710 root:pscode-collab on token_dir. The container reads this token
    via a single-FILE bind mount (container.launch's --volume), so the
    reading process's uid is the image's fixed uid (10001, "collab") — a
    different uid than the host-side pscode-collab service user that writes
    this file, and bind-mounting a file (not a directory) means only the
    file's own mode bits gate access; the container never sees token_dir at
    all. The broker's group needs directory traversal, never directory listing,
    so it can mint a redirect after authentication. Verified live 2026-08-21: 0o600 here made the real server fail with
    "Unable to read the connection token file" — allow() showed 200 only
    after relaxing to 0o644. token_dir's own 0o700 is what keeps other host
    processes from reading a token whose own bits are otherwise permissive;
    another collaborator's container has no path into this directory at all,
    since it only ever receives its own single token file, never the dir.
    """
    assert collab and "/" not in collab, "bad collaborator name"
    assert secret, "secret must be non-empty"
    assert name is None or "/" not in name, "bad token file name"
    _prepare_token_dir(token_dir)
    path = os.path.join(token_dir, name or (collab + ".tok"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secret)
    return path


def _write_gitea_token(collab, secret, token_dir):
    """Give the collaborator their gitea token as a credential of its own.

    Piranesi-Main DECIDE:C (2026-09-01): generate the connection token
    independently, keep minting the gitea token, and SURFACE that token to the
    collaborator for push, rather than leaving it orphaned. Decoupling alone
    would have minted a live write:repository token that nobody holds, which
    trades one wart for another.

    It is deliberately NOT put in the state record: /var/lib/pscode-collab/
    state.json is 0644 root-owned and world-readable on the host, and a
    credential has no business there. A single-file bind mount is the same
    channel the connection token already uses and is proven against this
    image's fixed uid.
    """
    return _write_token(collab, secret, token_dir, name=collab + ".gitea")


def _unwind(steps):
    """Run rollback callables in reverse. Bounded; never raises.

    # P10 RELAXATIONS: relaxes rule 7 (broad except) deliberately — this runs
    # inside an active `except Exception: _unwind(undo); raise` in create().
    # Narrowing to specific types risks an unanticipated exception from one
    # rollback step escaping and shadowing the real provisioning failure that
    # is about to be re-raised; every rollback step must be tried regardless
    # of how an earlier one failed.
    """
    for undo in reversed(steps[:16]):          # rule 2: bounded
        try:
            undo()
        except Exception:                       # noqa: BLE001
            pass    # rollback is best-effort; the raised original error wins


def _retained(rec, deps):
    """True when a revoked record is still inside the recovery window."""
    if rec is None or not rec.get("revoked_at"):
        return rec is not None
    revoked = deps.parse_time(rec["revoked_at"])
    return (deps.parse_time(deps.now()) - revoked).days < config.RETENTION_DAYS


def _resume(rec, deps):
    """Re-invite inside the retention window: restart, do not re-provision.

    Reactivating the gitea account is not optional — remove() revokes by
    DEACTIVATING it (C14), so without this the collaborator gets a running
    container they cannot authenticate to, at the broker or at gitea.
    """
    assert rec.get("name"), "record missing name"
    assert rec.get("port"), "record missing port"
    deps.gitea.activate_user(config.GITEA_BASE, deps.secret("gitea"), rec["name"])
    deps.container.start(rec["name"])
    # Re-read the IP: a restarted container gets a new one (C17).
    rec["ip"] = deps.container.ip_of(rec["name"])
    rec["path"] = deps.route.add(rec["name"], "%s:3000" % rec["ip"])
    rec["revoked_at"] = None
    deps.store.put(rec)
    return rec


def _provision_gitea(name, password, email, repo_owner, repo_name, deps,
                     admin, undo):
    """The gitea half of provisioning. Appends its own rollback steps to undo.

    Split out of create() to keep that function inside the P10 length limit,
    but the ordering here is load-bearing in its own right (C14), which is
    reason enough for it to be one named thing:
      - the read grant MUST precede the fork, because gitea answers 404 for a
        private repo the collaborator cannot yet see;
      - fork and token mint run AS the collaborator, because gitea's
        escalation guard refuses both over admin token-auth (401);
      - there is deliberately NO token-revoke undo -- an admin token cannot
        delete a user's token either, and the delete_user undo destroys it.
    """
    assert name and password, "name and password required"
    assert isinstance(undo, list), "undo must be the caller's rollback list"
    deps.gitea.create_user(config.GITEA_BASE, admin, name, password, email)
    undo.append(lambda: deps.gitea.delete_user(config.GITEA_BASE, admin, name))
    deps.gitea.add_collaborator(
        config.GITEA_BASE, admin, repo_owner, repo_name, name, "read")
    undo.append(lambda: deps.gitea.remove_collaborator(
        config.GITEA_BASE, admin, repo_owner, repo_name, name))
    clone_url = deps.gitea.fork_repo(
        config.GITEA_BASE, name, password, repo_owner, repo_name)
    undo.append(lambda: deps.gitea.delete_repo(
        config.GITEA_BASE, admin, name, repo_name))
    tid, secret = deps.gitea.mint_token(
        config.GITEA_BASE, name, password, "collab")
    return clone_url, tid, secret


def create(name, password, email, repo_owner, repo_name, deps):
    """Provision one collaborator. Raises Refused or RuntimeError.

    A re-invite inside RETENTION_DAYS resumes the retained container instead of
    provisioning fresh, so an accidental revoke does not destroy in-progress
    work (spec lifecycle, "Re-invite of the same collaborator").
    """
    assert name and password, "name and password required"
    assert repo_owner and repo_name, "repo_owner and repo_name required"
    existing = deps.store.get(name)
    if _retained(existing, deps):
        return _resume(existing, deps)
    _admit(deps)
    admin = deps.secret("gitea")
    undo = []
    try:
        clone_url, tid, gitea_secret = _provision_gitea(
            name, password, email, repo_owner, repo_name, deps, admin, undo)
        port = deps.store.next_port()
        token_dir = getattr(deps, "token_dir", _TOKEN_DIR)
        # The connection token is generated here and is NOT the gitea token.
        # They were the same value until 2026-09-01; see new_connection_token.
        token_path = _write_token(name, new_connection_token(), token_dir)
        gitea_path = _write_gitea_token(name, gitea_secret, token_dir)
        deps.container.launch(name, port, token_path, "collab-" + name,
                              gitea_token_path=gitea_path)
        undo.append(lambda: deps.container.destroy(name))
        # Read the IP AFTER launch and never cache it: it is assigned per run
        # (C17), so a stale value would route a live session into nothing.
        ip = deps.container.ip_of(name)
        path = deps.route.add(name, "%s:3000" % ip)
        undo.append(lambda: deps.route.remove(name))
    except Exception:
        _unwind(undo)
        deps.store.drop(name)
        raise
    # `ip` is what collab-router.py routes on (C18). It is re-read on every
    # resume because a restarted container gets a new one (C17).
    rec = {"name": name, "port": port, "path": path, "clone_url": clone_url,
           "ip": ip, "gitea_token_id": tid,
           "created_at": deps.now(), "revoked_at": None}
    deps.store.put(rec)
    return rec


def remove(name, deps):
    """Revoke access. Stops the container; retains it for RETENTION_DAYS."""
    assert name, "name required"
    rec = deps.store.get(name)
    if rec is None:
        return
    assert rec.get("gitea_token_id") is not None, "record missing gitea_token_id"
    admin = deps.secret("gitea")
    deps.route.remove(name)
    deps.container.stop(name)
    # Deactivation, not token deletion: an admin token cannot delete a user's
    # token (401), and deactivating kills BOTH the token and the password
    # (verified 403 each) while preserving the fork the retention window
    # exists to protect (C14).
    deps.gitea.deactivate_user(config.GITEA_BASE, admin, name)
    rec["revoked_at"] = deps.now()
    deps.store.put(rec)
