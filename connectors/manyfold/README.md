# manyfold — Wizarr + hardware-key integration for Manyfold (integration in progress)

Status at collection time (2026-10-14, America/Toronto): the **gate** (`gate.mjs`) exists and is
under live test by its building lane; the Wizarr **provisioning adapter**
(bridge wrapping Manyfold's CLI) is not yet finished. This snapshot is
collected honestly with that label — expect the final pieces to follow.

## `gate.mjs` — transparent proxy + minimal OIDC IdP

A single-file Node service in front of Manyfold:

- **Guests pass through untouched**: regular Manyfold logins (accounts a
  Wizarr invite provisions) work with no hardware key.
- **Hardware-key sign-in is additive**, via Manyfold's supported OIDC envs
  (`OIDC_CLIENT_ID/SECRET/ISSUER`): the gate hosts a minimal OIDC IdP whose
  authorize step is an OpenPGP card-signed challenge, verified by `gpg` in an
  ephemeral home against a **pinned keyring** + card-lineage tier map
  (admin tier → administrator account; member tier → member account). No
  second password after card proof; no spoofable headers; no bypass routes.
- Secrets (client secret, RSA key, admin credential) are read from root-owned
  files outside the repo — none are baked into the code.
- In-memory, single-use, short-lived code and token stores (restart revokes).

Configuration is entirely via `MFG_*` environment variables (see the top of
`gate.mjs`).

## Facts (verified against Manyfold v0.147.2)

- Manyfold has **no user-management HTTP API**. Supported automation is the
  in-container CLI: `bin/manyfold users create/approve/password`; deletion via
  a rails runner. Multi-user mode requires `MULTIUSER=enabled`.
- Wizarr (v2026.10.1) has zero Manyfold support upstream.

## Remaining design (provisioning adapter, not yet built)

1. **Bridge** — a token-authenticated HTTP service wrapping the CLI; the only
   thing Wizarr talks to.
2. **Wizarr client** — either an override module
   (`@register_media_client("manyfold")`) or the Subsonic-dialect trick used
   by `pscode-collab`: answer `/rest/createUser`/`/rest/deleteUser` and
   register as a "navidrome" server in Wizarr.

Authored-by: GPTPCS (gptpcs-wiz4c01) — this document only; `gate.mjs` retains
its original household authorship.

