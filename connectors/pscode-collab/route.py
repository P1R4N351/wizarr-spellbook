"""Per-collaborator tailscale-serve path on the collab host's tailnet identity.

Tailscale terminates TLS and proxies WebSockets, which a stdlib python proxy
would have to reimplement. The broker therefore authenticates and redirects
here rather than proxying the session itself.
"""
import subprocess

import config

_PREFIX = "/c/"


def path_for(collab):
    assert collab and "/" not in collab, "bad collaborator name"
    return _PREFIX + collab


def add(collab, target, runner=subprocess.run):
    """Register the collaborator's path; return it.

    `target` is host:port on collab-net (e.g. "10.89.7.2:3000"), not a
    published loopback port -- an --internal network cannot publish (C17).
    """
    assert target and ":" in target, "target must be host:port, got %r" % (target,)
    path = path_for(collab)
    argv = ["tailscale", "serve", "--bg", "--https", str(config.SERVE_PORT),
            "--set-path", path, "http://" + target]
    proc = runner(argv, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError("tailscale serve failed rc=%d: %s" % (
            proc.returncode, (proc.stderr or "")[:200]))
    return path


def remove(collab, runner=subprocess.run):
    argv = ["tailscale", "serve", "--https", str(config.SERVE_PORT),
            "--set-path", path_for(collab), "off"]
    proc = runner(argv, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError("tailscale serve off failed rc=%d" % proc.returncode)
