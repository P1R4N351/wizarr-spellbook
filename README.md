# wizarr-spellbook

Custom connectors and provisioning bridges built around [Wizarr](https://github.com/wizarrrr/wizarr),
the invitation manager for self-hosted media services. Everything here was built because
Wizarr's built-in clients could not reach a service we wanted to invite people to —
each connector translates Wizarr's expectations into whatever the target service
actually speaks.

All code is sanitized for publication: hostnames, tailnet names, addresses and
deployment identifiers are placeholders (`*.example.ts.net`, `100.64.0.0/10` doc
addresses, `node-*` host names, `alice` fixture user). Adapt them to your deployment.

## Connectors

| Connector | Target service | Mechanism | Status |
|---|---|---|---|
| [`connectors/nd-subsonic-shim/`](connectors/nd-subsonic-shim/) | Navidrome | Subsonic→native-API user-provisioning shim | live; E2E-proven in original deployment |
| [`connectors/pscode-collab/`](connectors/pscode-collab/) | Code-OSS workspaces + Gitea | Full Wizarr-fronted collaborator stack (speaks Subsonic to Wizarr) | live; E2E-proven in original deployment |
| [`connectors/wizarr-overrides/`](connectors/wizarr-overrides/) | Jellyfin, RomM | In-image overrides of Wizarr's media clients | retired/historical |
| [`connectors/manyfold/`](connectors/manyfold/) | Manyfold | Transparent reverse-proxy gate + minimal OIDC IdP (hardware-key sign-in); Wizarr provisioning adapter in progress | integration in progress |

## The trick most of these share

Wizarr only provisions users on services it has a client for. Its Navidrome client
speaks the Subsonic API — so anything that answers Subsonic's
`/rest/createUser`, `/rest/deleteUser` and token auth can be registered in Wizarr
as a "navidrome" media server and get fully working invite → account → revoke
flows for free. `nd-subsonic-shim` uses this to fix real Navidrome (whose own
Subsonic user management returns 501); `pscode-collab` uses it to provision
something that isn't a media server at all.

## Docs

- [`docs/INSTALL.md`](docs/INSTALL.md) — installation and configuration examples
- [`docs/SOURCE-MANIFEST.md`](docs/SOURCE-MANIFEST.md) — where every file came from
- [`docs/SANITIZATION.md`](docs/SANITIZATION.md) — what was sanitized and how

## Tests

`tests/` exercises connector behavior against fake upstreams — no live service,
no real user creation. "E2E-proven" statuses above refer to the original
household deployments (deployment records); the sanitized suite here is what
this repository itself proves. See [`tests/README.md`](tests/README.md).

## License

MIT (see [LICENSE](LICENSE)). The files in `connectors/wizarr-overrides/` are
derivative of Wizarr (MIT, © Wizarr contributors) — they subclass and replace
modules of the upstream application and retain its license.

---
*Repository docs authored by GPTPCS (gptpcs-wiz4c01). Source code retains its
original (household) authorship; no authorship metadata was added to copied source.*
