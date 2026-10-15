# nd-subsonic-shim — Wizarr ↔ Navidrome user-provisioning bridge

**Why it exists:** Navidrome returns `501 Not Implemented` for the Subsonic
user-management endpoints, but Wizarr's navidrome client speaks ONLY Subsonic
(`/rest/createUser`, hardcoded `u=admin`). As shipped, Wizarr↔Navidrome invite
provisioning is broken upstream for everyone.

The shim listens beside Navidrome (default `:4534`) and:

- `/rest/createUser`, `/rest/deleteUser` → fulfilled via Navidrome's native
  API (`/auth/login` as `admin` → `/api/user`), response re-encoded as
  Subsonic JSON.
- `/rest/updateUser` → **acknowledged no-op**. This is a known limitation:
  role/quota changes made in Wizarr do not propagate; manage roles in
  Navidrome itself. (Kept deliberately: the native API's update semantics
  differ enough that a partial mapping would be worse than an honest no-op.)
- every other `/rest/*` → transparent proxy to the local Navidrome
  (default `127.0.0.1:4533`).

Inbound auth: the caller must present a valid Subsonic `t/s` token pair for
the same `admin` password the shim holds (`/etc/nd-shim.secret`, mode 0400,
owned by the navidrome service user). Wrong auth → Subsonic error 40, and
nothing is forwarded to the native API.

Point Wizarr's `media_server.url` at the shim port for API calls only; guests
stream from Navidrome directly via `external_url`. No media traffic crosses
the shim.

## Deploy

1. `nd-subsonic-shim.py` → `/usr/local/sbin/`
2. `nd-subsonic-shim.service` → `/etc/systemd/system/`
3. write the Navidrome admin password to `/etc/nd-shim.secret` (0400)
4. `systemctl enable --now nd-subsonic-shim`

Proven end to end **in the original household deployment** (2026-07-31
deployment record, not reproducible from this repo): create → new-user
Subsonic login → delete, plus a full Wizarr invite E2E (302, account
appeared, cleaned up). What this repository itself proves is the sanitized
suite in `tests/test_nd_shim_e2e.py` (fake Navidrome, real shim code).

**Retire condition:** when Navidrome implements Subsonic `createUser` or
Wizarr's client switches to the native API — repoint Wizarr's `url` back to
`:4533` and disable the unit.

P10 notes: stdlib only, bounded reads (`MAX_BODY`), checked returns, no
dynamic code. The `ThreadingHTTPServer` allocates per-connection — an
unavoidable relaxation of the static-allocation rule for any stdlib HTTP
server; bounded by systemd resource limits rather than a rewrite.
