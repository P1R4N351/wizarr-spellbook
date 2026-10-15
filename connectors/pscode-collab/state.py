"""Collaborator records and the measured admission gate.

The admission gate reads real headroom at provisioning time instead of
trusting a constant: the collab host's swap is fully consumed, so an over-admission
does not degrade, it invokes the OOM killer against unrelated services.
"""
import json
import os
import tempfile

import config

_MAX_RECORDS = 64  # rule 2: bounded


class Store:
    """Atomic JSON record store keyed by collaborator name."""

    def __init__(self, path):
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def all(self):
        if not os.path.exists(self._path):
            return []
        with open(self._path, encoding="utf-8") as fh:
            recs = json.load(fh)
        assert len(recs) <= _MAX_RECORDS, "state file over cap"
        return recs

    def get(self, name):
        return next((r for r in self.all() if r.get("name") == name), None)

    def put(self, rec):
        assert rec.get("name"), "record needs a name"
        recs = [r for r in self.all() if r.get("name") != rec["name"]]
        recs.append(rec)
        self._write(recs)

    def drop(self, name):
        self._write([r for r in self.all() if r.get("name") != name])

    def next_port(self):
        taken = {r.get("port") for r in self.all()}
        for offset in range(config.PORT_SPAN):     # rule 2: bounded
            port = config.PORT_BASE + offset
            if port not in taken:
                return port
        raise RuntimeError("no free port in span")

    def _write(self, recs):
        assert len(recs) <= _MAX_RECORDS, "refusing to exceed record cap"
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self._path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(recs, fh, indent=2)
        # 0644: the SHIM writes this as root (C16 -- rootless podman is
        # impossible in this unprivileged LXC) while the BROKER reads it as
        # the unprivileged service user. mkstemp defaults to 0600, which made
        # every broker sign-in die with PermissionError. Safe to widen: the
        # record holds names, ports, paths, clone URLs and gitea token IDs --
        # no token VALUES and no passwords. Those live in /run/collab, whose
        # directory is 0700 precisely so this file does not have to be.
        os.chmod(tmp, 0o644)
        os.replace(tmp, self._path)


def read_available_mb(meminfo_path="/proc/meminfo"):
    """MemAvailable in MB. Raises if the field is absent — never guesses."""
    with open(meminfo_path, encoding="utf-8") as fh:
        for _ in range(64):                        # rule 2: bounded
            line = fh.readline()
            if not line:
                break
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable absent from " + meminfo_path)


def admission_ok(available_mb, active):
    """Gate one new container against measured headroom and the hard cap."""
    if active >= config.MAX_COLLABORATORS:
        return False, "cap reached: %d active" % active
    spendable = available_mb - config.RESERVE_MB
    if spendable < config.MEM_MB:
        return False, "headroom short: %d MB spendable, %d MB needed" % (
            spendable, config.MEM_MB)
    return True, "ok"
