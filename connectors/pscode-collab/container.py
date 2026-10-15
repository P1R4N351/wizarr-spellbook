"""podman lifecycle for one collaborator container.

Isolation properties asserted here and verified live in Task 8:
  - joined to a dedicated network, never --network host;
  - published on 127.0.0.1 only, so the only public path is the tailscale
    serve route the broker hands out;
  - memory and cpu capped (config), because the collab host's swap is exhausted;
  - token mounted read-only; never passed on argv.
"""
import os
import subprocess

import config

NETWORK = "collab-net"
_PREFIX = "collab-"


def name_for(collab):
    assert collab and "/" not in collab, "bad collaborator name"
    return _PREFIX + collab


def _run(runner, argv):
    proc = runner(argv, capture_output=True, text=True, timeout=120)
    return proc


# podman leaves a conmon supervisor behind for every container it starts, and
# conmon stays in the CALLER's cgroup. Called from the 10-minute recovery
# oneshot, that made pscode-workspace-recovery.service own collab-alice's conmon
# (a KillMode or stop of that unit would take the live workspace down) and
# logged a left-over-process warning on every run. Wrapping the podman
# invocations that create a conmon in a transient scope gives each conmon its
# own systemd unit instead. [c3ede23]
_SCOPE = ["systemd-run", "--scope", "--quiet", "--collect"]


def _scoped(argv):
    return _SCOPE + list(argv)


def exists(collab, runner=subprocess.run):
    proc = _run(runner, ["podman", "container", "exists", name_for(collab)])
    return proc.returncode == 0


def is_running(collab, runner=subprocess.run):
    """Return the retained container's running state, failing closed on bad output."""
    proc = _run(runner, [
        "podman", "inspect", name_for(collab), "--format", "{{.State.Running}}"])
    if proc.returncode != 0:
        raise RuntimeError("podman inspect failed rc=%d" % proc.returncode)
    state = (proc.stdout or "").strip().lower()
    if state not in ("true", "false"):
        raise RuntimeError("podman inspect returned invalid running state")
    return state == "true"


def launch(collab, port, token_path, volume, runner=subprocess.run,
           gitea_token_path=None, machine_settings_path=None):
    """Start (or restart) the collaborator's container. Raises on failure.

    `gitea_token_path`, when given, mounts the collaborator's gitea token at
    /run/collab/gitea-token as a SECOND read-only single-file mount. It is a
    distinct credential from the connection token: until 2026-09-01 one value
    served both jobs, so anything that revealed the workbench URL also handed
    out a push-scoped gitea token. Keyword-only-by-default and optional, so
    existing callers (and _resume) are unaffected.

    `machine_settings_path` (default: config.MACHINE_SETTINGS when that file
    exists) mounts the workstationhine-scope workbench settings read-only, so the
    waist endpoint and its bearer never enter an image layer.
    """
    if machine_settings_path is None and os.path.exists(config.MACHINE_SETTINGS):
        machine_settings_path = config.MACHINE_SETTINGS
    assert isinstance(port, int) and 1 <= port <= 65535, "bad port: %r" % (port,)
    argv = [
        "podman", "run", "-d", "--name", name_for(collab),
        "--network", NETWORK,
        # NO --publish (C17): podman port publishing does not work on an
        # --internal network, and the internal network is the egress
        # guarantee -- dropping it to make publishing work would trade a
        # structural property for a convenience. tailscale serve reaches the
        # container on its network IP instead; see ip_of().
        "--memory", "%dm" % config.MEM_MB,
        "--cpus", str(config.CPUS),
        "--memory-swap", "%dm" % config.MEM_MB,   # no swap: host swap is full
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--volume", "%s:/run/collab/token:ro" % token_path,
        "--volume", "%s:/home/collab/work" % volume,
        "--restart", "no",
    ]
    if gitea_token_path:
        argv += ["--volume", "%s:/run/collab/gitea-token:ro" % gitea_token_path]
    if machine_settings_path:
        argv += ["--volume", "%s:%s:ro" % (machine_settings_path, config.MACHINE_SETTINGS_MOUNT)]
    argv.append(config.IMAGE)
    # Args after the image append to the Containerfile's exec-form ENTRYPOINT.
    # Code-OSS generates every asset URL relative to its base path, so when the
    # public front mounts this session under a prefix (C13), the server has to
    # be told or the browser requests assets from a path that does not exist.
    if config.PUBLIC_BASE_PATH:
        argv += ["--server-base-path",
                 "%s/c/%s" % (config.PUBLIC_BASE_PATH.rstrip("/"), collab)]
    proc = _run(runner, _scoped(argv))
    if proc.returncode != 0:
        raise RuntimeError("podman run failed rc=%d: %s" % (
            proc.returncode, (proc.stderr or "")[:200]))


def ip_of(collab, runner=subprocess.run):
    """The container's address on collab-net.

    Routing targets this rather than a published host port, because an
    --internal network cannot publish (C17). The IP is assigned per run, so
    callers must re-read it after every launch/start -- never cache it across
    a restart.
    """
    proc = _run(runner, [
        "podman", "inspect", name_for(collab), "--format",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"])
    if proc.returncode != 0:
        raise RuntimeError("podman inspect failed rc=%d" % proc.returncode)
    ip = (proc.stdout or "").strip()
    assert ip, "container reported no IP on " + NETWORK
    return ip


def start(collab, runner=subprocess.run):
    """Restart a retained container for a re-invite. Never re-creates."""
    proc = _run(runner, _scoped(["podman", "start", name_for(collab)]))
    if proc.returncode != 0:
        raise RuntimeError("podman start failed rc=%d" % proc.returncode)


def stop(collab, runner=subprocess.run):
    """Stop without removing — session end must not destroy work in progress."""
    proc = _run(runner, ["podman", "stop", name_for(collab)])
    if proc.returncode != 0:
        raise RuntimeError("podman stop failed rc=%d" % proc.returncode)


def destroy(collab, runner=subprocess.run):
    """Remove container and its volume. Retention sweep only, never on stop."""
    proc = _run(runner, ["podman", "rm", "-f", "-v", name_for(collab)])
    if proc.returncode != 0:
        raise RuntimeError("podman rm failed rc=%d" % proc.returncode)
