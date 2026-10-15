#!/usr/bin/env python3
"""Recover active PSCode workspaces after volatile /run state is lost."""
import json
import os
import sys

import container
import provision

MAX_RECORDS = 128


def _active(records):
    """Return bounded, valid active workspace records."""
    out = []
    for rec in records[:MAX_RECORDS]:
        if not isinstance(rec, dict) or rec.get("revoked_at"):
            continue
        if rec.get("name") and rec.get("path"):
            out.append(rec)
    return out


def recover_record(rec, deps):
    """Restore one existing container without provisioning an account or repo."""
    name = rec["name"]
    token_path = os.path.join(deps.token_dir, name + ".tok")
    if not deps.container.exists(name):
        return {"name": name, "status": "missing-container", "made_token": False}
    made_token = False
    if not os.path.isfile(token_path):
        provision._write_token(name, provision.new_connection_token(), deps.token_dir)
        made_token = True
    started = False
    if not deps.container.is_running(name):
        deps.container.start(name)
        started = True
    rec["ip"] = deps.container.ip_of(name)
    deps.store.put(rec)
    return {"name": name, "status": "recovered" if (made_token or started) else "healthy",
            "made_token": made_token, "started": started}


def recover(deps):
    """Recover every active workspace and return machine-readable receipts."""
    result = []
    for rec in _active(deps.store.all()):
        result.append(recover_record(rec, deps))
    return result


def main():
    deps = provision.build_deps()
    result = recover(deps)
    print(json.dumps(result, separators=(",", ":")))
    return 1 if any(row["status"] == "missing-container" for row in result) else 0


if __name__ == "__main__":
    sys.exit(main())
