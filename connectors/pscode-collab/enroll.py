#!/usr/bin/env python3
"""Mint, list and revoke hardware-key enrollments from the the collab host shell.

Enrolling a key needs an authorisation that does NOT arrive over the same
public form the key is meant to replace. So it arrives here instead: an
operator with a shell on the collab host mints a one-time link, hands it to the person,
and the link dies on first use or after half an hour, whichever comes first.

RUNS AS ROOT, WRITES AS THE SERVICE USER. This is the third place in this
codebase where that distinction has mattered (see state.py on the 0600
mkstemp, and on the shim/broker uid split). A root-owned webauthn.json would
authenticate fine and then fail the next time the broker tried to advance a
signature counter, which is a failure that would surface days later as a
mysterious rejected key. Dropping privileges up front means every write in
this process is made by the user that has to own the result.
"""
import os
import pwd
import sys

SERVICE_USER = "pscode-collab"
_MAX_LIST = 64          # rule 2: bounded output


def drop_privileges(user=SERVICE_USER):
    """Become the service user, or stay put when already unprivileged."""
    if os.geteuid() != 0:
        return
    entry = pwd.getpwnam(user)
    os.setgid(entry.pw_gid)
    os.initgroups(user, entry.pw_gid)
    os.setuid(entry.pw_uid)
    assert os.geteuid() == entry.pw_uid, "failed to drop to " + user


def usage():
    sys.stderr.write(
        "usage:\n"
        "  enroll.py new <user> [label]   mint a one-time enrollment link\n"
        "  enroll.py list                 show enrolled keys\n"
        "  enroll.py revoke <user> <id>   remove one key\n")
    return 2


def cmd_new(creds, argv):
    if not argv:
        return usage()
    username = argv[0]
    label = argv[1] if len(argv) > 1 else "security key"
    ticket = creds.mint_ticket(username, label)
    import config
    print("Enrollment link for %s (%s), valid 30 minutes, single use:\n"
          % (username, label))
    print("  %s%s/enroll?t=%s\n"
          % (config.SECURE_ORIGIN, config.PUBLIC_BASE_PATH.rstrip("/"), ticket))
    print("Open it in a browser that can reach that host, with the key "
          "plugged in.")
    return 0


def cmd_list(creds):
    data = creds._read()                       # read-only view for the operator
    users = data.get("users", {})
    if not users:
        print("No keys are enrolled.")
        return 0
    shown = 0
    for name, rec in users.items():
        print("%s:" % name)
        for cred in rec.get("credentials", []):
            if shown >= _MAX_LIST:
                print("  ... more not shown")
                return 0
            shown += 1
            print("  %-28s id=%s  uses=%d  uv=%s"
                  % (cred.get("label", "?"), cred["id"][:16],
                     cred.get("sign_count", 0), cred.get("user_verified")))
    return 0


def cmd_revoke(creds, argv):
    if len(argv) < 2:
        return usage()
    username, prefix = argv[0], argv[1]
    with creds._lock:
        data = creds._read()
        rec = data.get("users", {}).get(username)
        if not rec:
            sys.stderr.write("no such account: %s\n" % username)
            return 1
        keep = [c for c in rec.get("credentials", [])
                if not c["id"].startswith(prefix)]
        removed = len(rec.get("credentials", [])) - len(keep)
        if removed == 0:
            sys.stderr.write("no key on %s matches id %s\n" % (username, prefix))
            return 1
        rec["credentials"] = keep
        creds._write(data)
    print("Removed %d key(s) from %s." % (removed, username))
    return 0


def main(argv):
    if len(argv) < 2:
        return usage()
    drop_privileges()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import config
    import webauthn_auth as wa
    creds = wa.CredStore(config.WEBAUTHN_STORE)
    action = argv[1]
    if action == "new":
        return cmd_new(creds, argv[2:])
    if action == "list":
        return cmd_list(creds)
    if action == "revoke":
        return cmd_revoke(creds, argv[2:])
    return usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
