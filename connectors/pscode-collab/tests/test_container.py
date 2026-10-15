import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pscode-collab"))
import config  # noqa: E402
import container  # noqa: E402


class FakeRunner:
    def __init__(self, rc=0, out=""):
        self.calls = []
        self._rc, self._out = rc, out

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self._rc, self._out, "")


def test_launch_applies_memory_and_cpu_caps():
    r = FakeRunner()
    container.launch("ada", 47100, "/run/collab/ada.tok", "collab-ada", runner=r)
    argv = r.calls[-1]
    assert "--memory" in argv and argv[argv.index("--memory") + 1] == "%dm" % config.MEM_MB
    assert "--cpus" in argv and argv[argv.index("--cpus") + 1] == str(config.CPUS)


def test_launch_does_not_publish_a_port():
    """C17: podman publishing does not work on an --internal network, and the
    internal network IS the egress guarantee -- so routing targets the
    container IP rather than dropping isolation to make publishing work."""
    r = FakeRunner()
    container.launch("ada", 47100, "/run/collab/ada.tok", "collab-ada", runner=r)
    argv = r.calls[-1]
    assert "--publish" not in argv, "publishing is silently broken on --internal"
    assert not any("47100" in str(a) for a in argv)


def test_ip_of_reads_the_container_address():
    r = FakeRunner(out="10.89.7.2\n")
    assert container.ip_of("ada", runner=r) == "10.89.7.2"
    assert r.calls[-1][:2] == ["podman", "inspect"]


def test_ip_of_raises_when_the_container_reports_no_ip():
    try:
        container.ip_of("ada", runner=FakeRunner(out="  \n"))
    except AssertionError:
        return
    raise AssertionError("accepted an empty container IP")


def test_launch_uses_dedicated_network_not_host():
    r = FakeRunner()
    container.launch("ada", 47100, "/run/collab/ada.tok", "collab-ada", runner=r)
    argv = r.calls[-1]
    assert "--network" in argv
    assert argv[argv.index("--network") + 1] == container.NETWORK
    assert "host" not in (argv[argv.index("--network") + 1],)


def test_launch_mounts_token_read_only():
    r = FakeRunner()
    container.launch("ada", 47100, "/run/collab/ada.tok", "collab-ada", runner=r)
    argv = r.calls[-1]
    assert "/run/collab/ada.tok:/run/collab/token:ro" in argv


def test_launch_never_publishes_swap():
    r = FakeRunner()
    container.launch("ada", 47100, "/run/collab/ada.tok", "collab-ada", runner=r)
    argv = r.calls[-1]
    assert "--memory-swap" in argv
    assert argv[argv.index("--memory-swap") + 1] == argv[argv.index("--memory") + 1]


def test_no_server_base_path_arg_when_prefix_unset():
    r = FakeRunner()
    container.launch("ada", 47100, "/run/collab/ada.tok", "collab-ada", runner=r)
    assert "--server-base-path" not in r.calls[-1], \
        "an empty PUBLIC_BASE_PATH must not pass a bare --server-base-path"


def test_server_base_path_passed_when_prefix_set(monkeypatch):
    """Without this, Code-OSS generates every asset URL relative to / and the
    browser requests them from outside the mount (C13)."""
    monkeypatch.setattr(container.config, "PUBLIC_BASE_PATH", "/collab")
    r = FakeRunner()
    container.launch("ada", 47100, "/run/collab/ada.tok", "collab-ada", runner=r)
    argv = r.calls[-1]
    assert "--server-base-path" in argv
    assert argv[argv.index("--server-base-path") + 1] == "/collab/c/ada"
    # must come AFTER the image name, or podman treats it as a podman flag
    assert argv.index("--server-base-path") > argv.index(config.IMAGE)


def test_stop_does_not_remove():
    r = FakeRunner()
    container.stop("ada", runner=r)
    assert r.calls[-1][:2] == ["podman", "stop"]
    assert "rm" not in r.calls[-1]


def test_start_restarts_without_recreating():
    r = FakeRunner()
    container.start("ada", runner=r)
    # podman start wrapped in a transient systemd scope since [c3ede23]:
    # each conmon gets its own unit instead of landing in the caller's cgroup.
    assert r.calls[-1][-3:] == ["podman", "start", "collab-ada"]
    assert r.calls[-1][0] == "systemd-run"
    assert "rm" not in r.calls[-1] and "run" not in r.calls[-1]


def test_exists_false_on_nonzero_rc():
    assert not container.exists("ada", runner=FakeRunner(rc=1))


def test_exists_true_on_zero_rc():
    assert container.exists("ada", runner=FakeRunner(rc=0))


def test_launch_raises_on_runner_failure():
    try:
        container.launch("ada", 47100, "/t", "v", runner=FakeRunner(rc=125))
    except RuntimeError:
        return
    raise AssertionError("launch swallowed a nonzero podman rc")


def test_stop_raises_on_runner_failure():
    try:
        container.stop("ada", runner=FakeRunner(rc=1))
    except RuntimeError:
        return
    raise AssertionError("stop swallowed a nonzero podman rc")


def test_destroy_raises_on_runner_failure():
    try:
        container.destroy("ada", runner=FakeRunner(rc=1))
    except RuntimeError:
        return
    raise AssertionError("destroy swallowed a nonzero podman rc")


def test_name_for_rejects_path_separator():
    try:
        container.name_for("ada/../etc")
    except AssertionError:
        return
    raise AssertionError("name_for accepted a path-separator name")
