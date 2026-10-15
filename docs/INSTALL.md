# Installation and configuration

Every connector here is deployment-shaped: small services + systemd units +
one or two secret files. Placeholders used throughout: `*.example.ts.net`
hostnames, documentation addresses (`100.64.0.0/10`, `192.0.2.0/24`,
`10.0.0.0/24`), `node-*` host names. Replace with your own.

## nd-subsonic-shim (Navidrome)

On the Navidrome host:

```sh
install -m 0755 connectors/nd-subsonic-shim/nd-subsonic-shim.py /usr/local/sbin/
install -m 0644 connectors/nd-subsonic-shim/nd-subsonic-shim.service /etc/systemd/system/
# the shim authenticates inbound callers and acts upstream as this admin:
printf '%s' "$NAVIDROME_ADMIN_PASSWORD" > /etc/nd-shim.secret
chmod 0400 /etc/nd-shim.secret; chown navidrome /etc/nd-shim.secret
systemctl enable --now nd-subsonic-shim
```

In Wizarr, add a **navidrome** media server with `url` pointing at the shim
(`http://<host>:4534`) and `external_url` at Navidrome itself — media
streaming never crosses the shim. Known limitation: `updateUser` is an
acknowledged no-op; manage roles in Navidrome.

## pscode-collab (browser Code-OSS workspaces)

Read `connectors/pscode-collab/README.md` first; this is a stack, not one
service. Outline:

1. Build the workspace image: `build-image.sh` (Containerfile lineage).
2. Create `/etc/pscode-collab/`:
   - `admin.secret` — inbound Subsonic admin password (what Wizarr presents)
   - `gitea.token` — Gitea admin token used to mint collaborator tokens
   - `repo.conf` — `owner=`/`name=` of the invited repository, `base_path=`,
     optional `rp_id=`/`secure_origin=` WebAuthn overrides
   - optional `machine-settings.json` — start from
     `machine-settings.example.json`; set your inference-gateway endpoint and
     deploy-time bearer (never bake a bearer into an image)
3. Install the systemd units (`pscode-collab-*.service`, `collab-router.service`,
   `systemd/pscode-workspace-recovery.*`), the egress allowlist
   (`collab-egress.nft` + `collab-egress.service`), and adapt addresses.
4. Register in Wizarr as a **navidrome** server pointing at the shim port
   (default `:4535`).
5. Verify isolation from inside a real container: `isolation-verify.sh`.

## wizarr-overrides (historical)

Bind-mount a file over the matching path inside the Wizarr container, e.g.:

```
Volume=/opt/wizarr/overrides/romm.py:/usr/src/app/app/services/media/romm.py:ro
```

**Warning (learned the hard way):** overrides shadow upstream modules across
image auto-updates and will eventually crash the app. Pin the image tag for
as long as any override is mounted.

## manyfold gate

```sh
node connectors/manyfold/gate.mjs
```

Configured by `MFG_*` environment variables (listen host/port, upstream
Manyfold URL, OIDC issuer, secret/RSA-key file paths, pinned card keyring +
lineage paths). Point Manyfold's `OIDC_CLIENT_ID/SECRET/ISSUER` at the gate.
The Wizarr provisioning adapter for Manyfold is not yet part of this
repository (integration in progress).
