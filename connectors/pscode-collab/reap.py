"""Retention sweep: hard-delete containers revoked longer than RETENTION_DAYS.

This is the ONLY code path in pscode-collab that destroys anything. It refuses
to touch a record that is not revoked, and it is bounded per run.
"""
import config

_MAX_PER_RUN = 8    # rule 2: bounded; a runaway sweep is worse than a slow one


def due(records, now, parse_time):
    """Records whose retention window has fully elapsed."""
    out = []
    for rec in records[:64]:                    # rule 2: bounded
        revoked = rec.get("revoked_at")
        if not revoked:
            continue                             # never reap a live collaborator
        if (parse_time(now) - parse_time(revoked)).days >= config.RETENTION_DAYS:
            out.append(rec)
    return out[:_MAX_PER_RUN]


def sweep(deps):
    """Destroy due containers and drop their records. Returns names reaped."""
    reaped = []
    for rec in due(deps.store.all(), deps.now(), deps.parse_time):
        assert rec.get("revoked_at"), "refusing to reap a live collaborator"
        deps.container.destroy(rec["name"])
        deps.store.drop(rec["name"])
        reaped.append(rec["name"])
    return reaped
