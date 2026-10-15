# pscode-collab — Wizarr-fronted browser Code-OSS collaborator stack

Gives an invited external collaborator a browser-only Code-OSS session,
provisioned by a Wizarr invite, running entirely inside an isolated podman
container — no repository content, credential, or build artifact ever lands
on their device. Modelled on `nd-subsonic-shim`, the proven Wizarr
provisioning bridge for Navidrome.

How it plugs into Wizarr: `shim.py`/`subsonic.py` expose a Subsonic-shaped
surface (`/rest/createUser`, `/rest/deleteUser`, token auth), so the stack is
registered in Wizarr as a "navidrome" media server. A redeemed invite
therefore provisions: a Gitea account + scoped token + fork (`gitea.py`,
`provision.py`), an isolated workspace container (`container.py`,
`config.py`), a route on the front (`route.py`, `collab-router.py`), and a
landing/login page (`broker.py`, `login_page.py`). Revocation reverses all of
it (`reap.py`); `recover.py`/`relaunch.py` + `systemd/` handle restarts.

Security pieces:
- `collab-egress.nft` — per-port egress allowlist for the workspace container
  (Gitea HTTP/SSH and the inference gateway only; everything else blocked).
- `isolation-verify.sh` — probes the allowlist from inside a real container.
- `pr-gate.sh` — fail-closed scanner gate on pull requests
  (`pr-gate.test.sh` proves it fails closed).
- `webauthn_auth.py`/`enroll.py` — optional hardware-key sign-in for the
  broker door. RP ID pinning rationale is documented in `config.py`.
- The browser carries no provider key: in-editor chat routes through a
  household inference gateway ("waist"); see `Containerfile.kudzu4`,
  `patch-workbench-modpack.js`, `machine-settings.json` (bearer deliberately
  empty — injected at deploy, never baked into an image layer).

Container images are built by the `Containerfile*` lineage (bare Code-OSS
server bundle + shim extension + branding overlays, `build-image.sh`).
Household deploy scripts are deliberately not included; the deployment shape
is documented in the repository `docs/INSTALL.md`. `machine-settings.example.json`
is a generic template — set your own gateway endpoint and (optionally) a
bearer at deploy time; never bake a bearer into an image layer.

Tests: `tests/` (unit suite, fake upstreams) plus module-local
`test_*.py`. All runnable without podman, Gitea, or Wizarr — see the
repository `tests/README.md`.

All hostnames/addresses in this tree are sanitized placeholders; set your own
in `config.py` and the deploy-time conf files it documents
(`/etc/pscode-collab/repo.conf` et al.).
