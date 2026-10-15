"""Routing decisions for collab-router.py (C18).

Loaded via importlib because the filename carries a dash -- deliberate, it is
a deployed script rather than an importable module.
"""
import importlib.util
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "pscode-collab" / "collab-router.py"
_spec = importlib.util.spec_from_file_location("collab_router", _SRC)
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def _state(tmp_path, records):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(records))
    cr.STATE = str(p)
    return p


def test_root_goes_to_the_broker(tmp_path):
    _state(tmp_path, [])
    assert cr._target_for("/") == cr.BROKER


def test_collaborator_path_goes_to_their_container(tmp_path):
    _state(tmp_path, [{"name": "ada", "ip": "10.89.7.9"}])
    assert cr._target_for("/c/ada/") == ("10.89.7.9", 3000)


def test_public_prefix_is_accepted_too(tmp_path):
    """the media host may or may not strip its /collab mount prefix before forwarding
    (C13 left that open), so the router accepts BOTH forms rather than
    depending on an answer that was never established."""
    _state(tmp_path, [{"name": "ada", "ip": "10.89.7.9"}])
    assert cr._target_for("/collab/c/ada/") == ("10.89.7.9", 3000)
    assert cr._target_for("/collab/c/ada/static/main.js") == ("10.89.7.9", 3000)


def test_unknown_collaborator_falls_back_to_the_broker(tmp_path):
    _state(tmp_path, [{"name": "ada", "ip": "10.89.7.9"}])
    assert cr._target_for("/c/nobody/") == cr.BROKER


def test_path_traversal_does_not_resolve(tmp_path):
    _state(tmp_path, [{"name": "ada", "ip": "10.89.7.9"}])
    assert cr._target_for("/c/../etc/") == cr.BROKER
    assert cr._target_for("/c/ada/../../root") == ("10.89.7.9", 3000)


def test_record_without_an_ip_is_not_routed(tmp_path):
    """A record written before the container came up has no address; routing
    to a half-provisioned entry would be worse than the broker."""
    _state(tmp_path, [{"name": "ada"}])
    assert cr._target_for("/c/ada/") == cr.BROKER


def test_unreadable_state_still_serves_the_broker(tmp_path):
    cr.STATE = str(tmp_path / "does-not-exist.json")
    assert cr._target_for("/") == cr.BROKER
    assert cr._target_for("/c/ada/") == cr.BROKER


def _conf(tmp_path, body):
    p = tmp_path / "repo.conf"
    p.write_text(body)
    cr.REPO_CONF = str(p)
    return p


def test_router_readds_the_prefix_the_funnel_strips(tmp_path):
    """C19, measured: tailscale funnel --set-path STRIPS its prefix before
    forwarding, so the request arrives as /c/<name>/ while the browser's URLs
    all carry /collab/c/<name>/. The container is configured with the PUBLIC
    path, so the prefix has to be restored on the way in or it 404s a path it
    was never told to answer."""
    _conf(tmp_path, "base_path=/collab\n")
    assert cr._rewrite("/c/ada/") == "/collab/c/ada/"
    assert cr._rewrite("/c/ada/static/main.js") == "/collab/c/ada/static/main.js"


def test_rewrite_is_idempotent_if_the_prefix_survived(tmp_path):
    """Whether the funnel strips is a property of the mount, not a constant --
    so the rewrite must not double the prefix when it is already present."""
    _conf(tmp_path, "base_path=/collab\n")
    assert cr._rewrite("/collab/c/ada/") == "/collab/c/ada/"
    assert cr._rewrite("/collab") == "/collab"


def test_no_prefix_configured_is_a_passthrough(tmp_path):
    cr.REPO_CONF = str(tmp_path / "absent.conf")
    assert cr._rewrite("/c/ada/") == "/c/ada/"


def test_public_prefix_is_read_at_call_time(tmp_path):
    """Not as a default argument: a default binds at definition, which would
    make a config change invisible until restart -- and made this silently
    untestable when first written."""
    _conf(tmp_path, "base_path=/one\n")
    assert cr._public_prefix() == "/one"
    _conf(tmp_path, "base_path=/two\n")
    assert cr._public_prefix() == "/two", "prefix was bound at definition time"
