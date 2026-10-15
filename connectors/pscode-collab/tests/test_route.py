import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pscode-collab"))
import config  # noqa: E402
import route  # noqa: E402


class FakeRunner:
    def __init__(self, rc=0):
        self.calls = []
        self._rc = rc

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self._rc, "", "")


def test_path_for_is_scoped_under_c():
    assert route.path_for("ada") == "/c/ada"


def test_path_for_rejects_path_separator():
    try:
        route.path_for("ada/../etc")
    except AssertionError:
        return
    raise AssertionError("path_for accepted a path-separator name")


def test_add_targets_configured_serve_port():
    r = FakeRunner()
    route.add("ada", "10.89.7.2:3000", runner=r)
    argv = r.calls[-1]
    assert "--https" in argv
    assert argv[argv.index("--https") + 1] == str(config.SERVE_PORT)


def test_add_points_at_the_container_ip_not_a_published_port():
    """C17: an --internal network cannot publish, so routing targets the
    container's own address on collab-net."""
    r = FakeRunner()
    route.add("ada", "10.89.7.2:3000", runner=r)
    assert "http://10.89.7.2:3000" in r.calls[-1]
    assert not any("127.0.0.1" in a for a in r.calls[-1])


def test_add_rejects_a_target_without_a_port():
    try:
        route.add("ada", "10.89.7.2", runner=FakeRunner())
    except AssertionError:
        return
    raise AssertionError("accepted a target with no port")


def test_add_returns_the_path():
    assert route.add("ada", "10.89.7.2:3000", runner=FakeRunner()) == "/c/ada"


def test_add_raises_on_runner_failure():
    try:
        route.add("ada", "10.89.7.2:3000", runner=FakeRunner(rc=1))
    except RuntimeError:
        return
    raise AssertionError("add swallowed a nonzero tailscale rc")


def test_remove_turns_the_path_off():
    r = FakeRunner()
    route.remove("ada", runner=r)
    assert r.calls[-1][-2:] == ["/c/ada", "off"]


def test_remove_raises_on_runner_failure():
    try:
        route.remove("ada", runner=FakeRunner(rc=1))
    except RuntimeError:
        return
    raise AssertionError("remove swallowed a nonzero tailscale rc")
