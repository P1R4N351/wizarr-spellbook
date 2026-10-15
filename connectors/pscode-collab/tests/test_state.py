import os
import pathlib
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pscode-collab"))
import config  # noqa: E402
import state  # noqa: E402


def test_put_get_drop_roundtrip(tmp_path):
    store = state.Store(str(tmp_path / "s.json"))
    store.put({"name": "ada", "port": 47100})
    assert store.get("ada")["port"] == 47100
    store.drop("ada")
    assert store.get("ada") is None


def test_next_port_skips_taken(tmp_path):
    store = state.Store(str(tmp_path / "s.json"))
    store.put({"name": "ada", "port": config.PORT_BASE})
    assert store.next_port() == config.PORT_BASE + 1


def test_admission_refuses_when_headroom_short():
    ok, why = state.admission_ok(config.RESERVE_MB + config.MEM_MB - 1, active=0)
    assert not ok and "headroom" in why


def test_admission_allows_with_exact_headroom():
    ok, _ = state.admission_ok(config.RESERVE_MB + config.MEM_MB, active=0)
    assert ok


def test_admission_refuses_at_hard_cap():
    plenty = config.RESERVE_MB + config.MEM_MB * 100
    ok, why = state.admission_ok(plenty, active=config.MAX_COLLABORATORS)
    assert not ok and "cap" in why


def test_read_available_mb_parses_meminfo(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal:  12582912 kB\nMemAvailable:  10039296 kB\n")
    assert state.read_available_mb(str(p)) == 9804


def test_repo_conf_overlay_reads_owner_and_name(tmp_path):
    """The invited repo is deploy-time (C9). deploy.sh re-pushes config.py, so
    the value must live outside it or a hand edit is silently reverted."""
    conf = tmp_path / "repo.conf"
    conf.write_text("owner=piranesi-org\nname=claude-skills\n")
    owner, name, base = config._load_repo_conf(str(conf))
    assert (owner, name) == ("piranesi-org", "claude-skills")


def test_repo_conf_absent_leaves_the_failloud_default(tmp_path):
    """An unset deployment must 404 at gitea, not silently fork something."""
    owner, name, base = config._load_repo_conf(str(tmp_path / "nope.conf"))
    assert name == "REPLACE_ME", "absent conf must not invent a repo name"


def test_repo_conf_ignores_junk_lines(tmp_path):
    conf = tmp_path / "repo.conf"
    conf.write_text("# a comment\n\nnonsense\nname=widget\n")
    owner, name, base = config._load_repo_conf(str(conf))
    assert name == "widget"


def test_state_file_is_readable_by_the_unprivileged_broker(tmp_path):
    """C16 consequence: the shim writes this as root, the broker reads it as
    pscode-collab. mkstemp's 0600 default made every sign-in fail with
    PermissionError. The record carries no token values or passwords."""
    store = state.Store(str(tmp_path / "s.json"))
    store.put({"name": "ada", "port": 47100, "gitea_token_id": 7})
    mode = stat.S_IMODE(os.stat(str(tmp_path / "s.json")).st_mode)
    assert mode == 0o644, "state file mode %o locks out the broker" % mode


def test_state_file_never_holds_a_token_value(tmp_path):
    """Widening to 0644 is only safe while this stays true."""
    store = state.Store(str(tmp_path / "s.json"))
    store.put({"name": "ada", "port": 47100, "path": "/c/ada",
               "clone_url": "http://gitea/ada/r.git", "gitea_token_id": 7,
               "created_at": "t", "revoked_at": None})
    raw = pathlib.Path(str(tmp_path / "s.json")).read_text()
    for forbidden in ("password", "secret", "sha1", "token_value"):
        assert forbidden not in raw, "state file leaks %r" % forbidden


def test_repo_conf_carries_the_public_base_path(tmp_path):
    """base_path is per-deployment too (C13): it depends on which public mount
    fronts this install, and deploy.sh re-pushes config.py."""
    conf = tmp_path / "repo.conf"
    conf.write_text("name=widget\nbase_path=/collab\n")
    owner, name, base = config._load_repo_conf(str(conf))
    assert base == "/collab"
