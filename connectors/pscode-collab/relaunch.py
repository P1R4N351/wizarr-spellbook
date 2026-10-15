#!/usr/bin/env python3
"""Re-create one ACTIVE collaborator container on the current config.IMAGE.

Used when the image changes (e.g. -kudzu3 -> -kudzu4) and the collaborator
must keep their record: name, port, path, gitea token, workspace volume. The
named volume `collab-<name>` is never removed (plain `podman rm`, no -v), the
connection and gitea tokens are reused from the token dir, and the tailscale
serve path is re-pointed if the container's IP changed (C17: the IP is
assigned per run and is never cached).

Usage: relaunch.py <collab> [--dry-run]
Exit: 0 relaunched; 2 usage; 3 no such active record / missing token; 4 podman.
"""
import os
import subprocess
import sys

import config
import container
import route
import state

TOKEN_DIR = "/run/collab"
MAX_RECORDS = 128


def find_active(name, store):
    for rec in store.all()[:MAX_RECORDS]:             # bounded
        if rec.get("name") == name and not rec.get("revoked_at"):
            return rec
    return None


def relaunch(name, dry):
    store = state.Store(config.STATE_PATH)
    rec = find_active(name, store)
    if rec is None:
        sys.stderr.write("relaunch: no active record for %s\n" % name)
        return 3
    token_path = os.path.join(TOKEN_DIR, name + ".tok")
    gitea_path = os.path.join(TOKEN_DIR, name + ".gitea")
    if not os.path.isfile(token_path):
        sys.stderr.write("relaunch: connection token missing: %s (run recover.py first)\n" % token_path)
        return 3
    gitea_arg = gitea_path if os.path.isfile(gitea_path) else None
    print("relaunch: %s port=%s image=%s machine_settings=%s" % (
        name, rec["port"], config.IMAGE,
        config.MACHINE_SETTINGS if os.path.exists(config.MACHINE_SETTINGS) else "(none)"))
    if dry:
        return 0
    if container.exists(name):
        try:
            container.stop(name)
        except RuntimeError as exc:
            print("relaunch: stop: %s (continuing)" % exc)
        proc = subprocess.run(["podman", "rm", "-f", container.name_for(name)],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            sys.stderr.write("relaunch: podman rm failed rc=%d: %s\n" % (proc.returncode, proc.stderr[:200]))
            return 4
    container.launch(name, int(rec["port"]), token_path, "collab-" + name,
                     gitea_token_path=gitea_arg)
    ip = container.ip_of(name)
    if ip != rec.get("ip"):
        route.add(name, "%s:3000" % ip)
        rec["ip"] = ip
        store.put(rec)
        print("relaunch: ip %s -> route %s re-pointed" % (ip, rec["path"]))
    else:
        print("relaunch: ip unchanged (%s)" % ip)
    return 0


def main(argv):
    if len(argv) < 2 or len(argv) > 3 or (len(argv) == 3 and argv[2] != "--dry-run"):
        sys.stderr.write(__doc__)
        return 2
    return relaunch(argv[1], len(argv) == 3)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
