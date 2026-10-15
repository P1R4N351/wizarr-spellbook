import datetime
import os
import stat
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pscode-collab"))
import provision  # noqa: E402
import state  # noqa: E402


class FakeGitea:
    def __init__(self, fail_on=None):
        self.fail_on, self.log = fail_on, []

    def _maybe(self, what):
        self.log.append(what)
        if self.fail_on == what:
            raise RuntimeError("injected " + what)

    def create_user(self, *a):
        self._maybe("create_user")

    def delete_user(self, *a):
        self._maybe("delete_user")

    def fork_repo(self, *a):
        self._maybe("fork_repo")
        return "http://gitea/ada/widget.git"

    def mint_token(self, *a):
        self._maybe("mint_token")
        return 7, "tok-secret"

    def add_collaborator(self, *a):
        self._maybe("add_collaborator")

    def remove_collaborator(self, *a):
        self._maybe("remove_collaborator")

    def delete_repo(self, *a):
        self._maybe("delete_repo")

    def deactivate_user(self, *a):
        self._maybe("deactivate_user")

    def activate_user(self, *a):
        self._maybe("activate_user")


class FakeContainer:
    NETWORK = "collab-net"

    def __init__(self, fail=False):
        self.fail, self.log = fail, []

    def launch(self, *a, **k):
        self.log.append("launch")
        if self.fail:
            raise RuntimeError("injected launch")

    def start(self, *a, **k):
        self.log.append("start")

    def stop(self, *a, **k):
        self.log.append("stop")

    def destroy(self, *a, **k):
        self.log.append("destroy")

    def exists(self, *a, **k):
        return False

    def ip_of(self, *a, **k):
        self.log.append("ip_of")
        return "10.89.7.2"


class FakeRoute:
    def __init__(self, fail=False):
        self.fail, self.log = fail, []

    def add(self, collab, target, **k):
        self.log.append("add")
        self.last_target = target
        if self.fail:
            raise RuntimeError("injected route")
        return "/c/" + collab

    def remove(self, collab, **k):
        self.log.append("remove")


def _deps(tmp_path, gitea=None, cont=None, rt=None, avail=99999, now="2026-08-21T00:00:00Z"):
    return types.SimpleNamespace(
        store=state.Store(str(tmp_path / "s.json")),
        gitea=gitea or FakeGitea(),
        container=cont or FakeContainer(),
        route=rt or FakeRoute(),
        available_mb=lambda: avail,
        now=lambda: now,
        parse_time=lambda s: datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ"),
        secret=lambda n: "tok-secret",
        token_dir=str(tmp_path / "run-collab"),
    )


def test_create_records_and_returns_path(tmp_path):
    d = _deps(tmp_path)
    rec = provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    assert rec["path"] == "/c/ada"
    assert d.store.get("ada")["gitea_token_id"] == 7


def test_token_file_is_readable_by_a_different_uid_than_its_owner(tmp_path):
    """The container reads this via a single-FILE bind mount as uid 10001,
    a different uid than whatever host user runs provisioning. 0o600 fails
    that read (verified live 2026-08-21: 0o600 -> "Unable to read the
    connection token file", real server rejects); 0o644 must succeed. This
    checks the actual mode bits, not just that the file exists.
    """
    d = _deps(tmp_path)
    provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    token_path = os.path.join(d.token_dir, "ada.tok")
    mode = stat.S_IMODE(os.stat(token_path).st_mode)
    assert mode == 0o644, "token file mode %o must be 0o644, matching the live fix" % mode
    dir_mode = stat.S_IMODE(os.stat(d.token_dir).st_mode)
    # 0o710 root:pscode-collab since the broker config-read fix: the broker
    # group traverses the dir, other host uids still cannot (provision.py).
    assert dir_mode == 0o710, "token_dir mode %o must be 0o710" % dir_mode


def test_route_failure_rolls_back_container_and_token(tmp_path):
    g, c, r = FakeGitea(), FakeContainer(), FakeRoute(fail=True)
    d = _deps(tmp_path, g, c, r)
    try:
        provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    except RuntimeError:
        pass
    else:
        raise AssertionError("create swallowed the route failure")
    assert "destroy" in c.log, "container left running after failed provision"
    assert "delete_repo" in g.log, "fork left behind after failed provision"
    assert "delete_user" in g.log, "gitea user left behind"
    assert d.store.get("ada") is None, "half-provisioned record persisted"


def test_launch_failure_rolls_back_gitea(tmp_path):
    g, c = FakeGitea(), FakeContainer(fail=True)
    d = _deps(tmp_path, g, c)
    try:
        provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    except RuntimeError:
        pass
    assert "remove_collaborator" in g.log and "delete_user" in g.log
    assert d.store.get("ada") is None


def test_create_refused_when_headroom_short(tmp_path):
    d = _deps(tmp_path, avail=1)
    try:
        provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    except provision.Refused as exc:
        assert "headroom" in str(exc)
        return
    raise AssertionError("provisioned despite insufficient headroom")


def test_remove_stops_but_does_not_destroy(tmp_path):
    d = _deps(tmp_path)
    provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    provision.remove("ada", d)
    assert "stop" in d.container.log
    assert "destroy" not in d.container.log[1:], "revoke destroyed retained work"
    assert d.store.get("ada")["revoked_at"] == "2026-08-21T00:00:00Z"


def test_reinvite_within_retention_reuses_container(tmp_path):
    """Spec lifecycle: a re-invite must not lose in-progress work."""
    d = _deps(tmp_path)
    first = provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    provision.remove("ada", d)
    d.container.log.clear()
    d.gitea.log.clear()
    again = provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    assert again["port"] == first["port"], "re-invite moved the container"
    assert "launch" not in d.container.log, "re-invite relaunched from a fresh image"
    assert "start" in d.container.log, "re-invite did not restart the retained container"
    assert "create_user" not in d.gitea.log, "re-invite recreated the gitea user"
    assert d.store.get("ada")["revoked_at"] is None


def test_reinvite_after_retention_provisions_fresh(tmp_path):
    d = _deps(tmp_path)
    provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    provision.remove("ada", d)
    rec = d.store.get("ada")
    rec["revoked_at"] = "2020-01-01T00:00:00Z"      # long past retention
    d.store.put(rec)
    d.container.log.clear()
    provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    assert "launch" in d.container.log, "stale record was reused past retention"


def test_reinvite_at_exact_retention_boundary_provisions_fresh(tmp_path):
    """The boundary belongs to reap.py's sweep, not to a lingering resume."""
    d = _deps(tmp_path)
    provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    provision.remove("ada", d)
    rec = d.store.get("ada")
    rec["revoked_at"] = "2026-08-07T00:00:00Z"       # exactly 14 days before now
    d.store.put(rec)
    d.container.log.clear()
    provision.create("ada", "pw", "a@x.invalid", "piranesi", "widget", d)
    assert "launch" in d.container.log, "exact-boundary record was resumed, not reaped"


def test_grant_precedes_fork(tmp_path):
    """C14: gitea answers 404 for a private repo the collaborator cannot see,
    so a fork attempted before the read grant fails as if the repo did not
    exist. Order is a correctness property, not a style choice."""
    d = _deps(tmp_path)
    provision.create("ada", "pw", "a@x.invalid", "piranesi-org", "widget", d)
    log = d.gitea.log
    assert log.index("add_collaborator") < log.index("fork_repo"), \
        "fork ran before the read grant: %s" % log


def test_remove_deactivates_rather_than_revoking_a_token(tmp_path):
    """An admin token cannot delete a user's token (401). Deactivation kills
    token AND password while preserving the fork retention protects."""
    d = _deps(tmp_path)
    provision.create("ada", "pw", "a@x.invalid", "piranesi-org", "widget", d)
    d.gitea.log.clear()
    provision.remove("ada", d)
    assert "deactivate_user" in d.gitea.log
    assert "delete_user" not in d.gitea.log, "revoke must not destroy the account"
    assert "delete_repo" not in d.gitea.log, "revoke must not destroy their fork"


def test_reinvite_reactivates_the_account(tmp_path):
    """Without this the collaborator gets a running container they cannot
    authenticate to — remove() deactivated them."""
    d = _deps(tmp_path)
    provision.create("ada", "pw", "a@x.invalid", "piranesi-org", "widget", d)
    provision.remove("ada", d)
    d.gitea.log.clear()
    provision.create("ada", "pw", "a@x.invalid", "piranesi-org", "widget", d)
    assert "activate_user" in d.gitea.log, "re-invite left the account deactivated"


def test_build_deps_supplies_every_attribute_create_uses(monkeypatch, tmp_path):
    """The test that would have caught C16.

    Every other test in this file INJECTS a fake `deps`, so the whole suite
    passed green while shim.main() left Handler.deps = None and production
    died on the first real Wizarr call. A test that supplies the thing
    production forgets to build cannot observe it missing -- so this one
    builds the PRODUCTION bundle and checks it against the attributes
    create()/remove()/_resume() actually reach for.
    """
    monkeypatch.setattr(provision.config, "STATE_PATH", str(tmp_path / "s.json"))
    tok = tmp_path / "gitea.token"
    tok.write_text("fake-admin-token")
    monkeypatch.setattr(provision.config, "GITEA_TOKEN_FILE", str(tok))

    d = provision.build_deps()
    for attr in ("store", "gitea", "container", "route", "available_mb",
                 "now", "parse_time", "secret", "token_dir"):
        assert hasattr(d, attr), "build_deps() is missing %r" % attr
        assert getattr(d, attr) is not None, "build_deps().%s is None" % attr

    # the callables must actually work, not merely exist
    assert d.secret("gitea") == "fake-admin-token"
    assert d.parse_time(d.now()).year >= 2026
    assert isinstance(d.store.all(), list)

    # and the real modules must expose what provision.create() calls on them
    for fn in ("create_user", "delete_user", "add_collaborator",
               "remove_collaborator", "fork_repo", "delete_repo",
               "mint_token", "deactivate_user", "activate_user"):
        assert hasattr(d.gitea, fn), "gitea module missing %r" % fn
    for fn in ("launch", "start", "stop", "destroy"):
        assert hasattr(d.container, fn), "container module missing %r" % fn
    for fn in ("add", "remove"):
        assert hasattr(d.route, fn), "route module missing %r" % fn


def test_build_deps_rejects_an_empty_secret_file(monkeypatch, tmp_path):
    monkeypatch.setattr(provision.config, "STATE_PATH", str(tmp_path / "s.json"))
    empty = tmp_path / "empty.token"
    empty.write_text("")
    monkeypatch.setattr(provision.config, "GITEA_TOKEN_FILE", str(empty))
    d = provision.build_deps()
    try:
        d.secret("gitea")
    except AssertionError:
        return
    raise AssertionError("an empty secret file was accepted")


def test_record_carries_the_container_ip(tmp_path):
    """collab-router.py routes on this (C18); without it a provisioned
    collaborator resolves to the broker instead of their own container."""
    d = _deps(tmp_path)
    rec = provision.create("ada", "pw", "a@x.invalid", "piranesi-org", "widget", d)
    assert rec["ip"] == "10.89.7.2"
    assert d.store.get("ada")["ip"] == "10.89.7.2"


def test_resume_rereads_the_ip(tmp_path):
    """A restarted container gets a NEW address (C17), so a resume that kept
    the old one would route a live session into nothing."""
    d = _deps(tmp_path)
    provision.create("ada", "pw", "a@x.invalid", "piranesi-org", "widget", d)
    provision.remove("ada", d)
    d.container.log.clear()
    provision.create("ada", "pw", "a@x.invalid", "piranesi-org", "widget", d)
    assert "ip_of" in d.container.log, "resume did not re-read the container IP"
