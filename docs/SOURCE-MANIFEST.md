# Source manifest

Where every file in this repository came from. Collected 2026-10-14
(America/Toronto), with a final Manyfold snapshot the same evening.

## Provenance of staged files

| Repo path | Origin | Collected how |
|---|---|---|
| `connectors/nd-subsonic-shim/{nd-subsonic-shim.py,.service}` | private source repository; **verified byte-identical** to the live deployment on the music host (`sha256 cc58c3c4…`, `bf5ac8ba…`, service active) | file copy + sanitizer |
| `connectors/pscode-collab/**` (code, Containerfiles, nft, units, assets) | private source repository; deployed copy on the collab host verified **identical except** `broker.py` (deployed is an older pre-`wa.finish_registration` WebAuthn variant, 7-line diff — superseded here) and `collab-front-forwarder.py` (runs on the funnel front host) | file copy + sanitizer |
| `connectors/pscode-collab/tests/**` | private source repository | file copy + sanitizer |
| `connectors/pscode-collab/machine-settings.example.json` | generated generic template (live file's shape; real endpoints removed, bearer empty in the live file too) | generated |
| `connectors/wizarr-overrides/{jellyfin.py,romm.py}` | live media host `/opt/wizarr/overrides/` (read-only capture; overrides retired 2026-10-14) | live capture + sanitizer |
| `connectors/manyfold/gate.mjs` | live `/opt/manyfold-gate/gate.mjs` on the Manyfold host, snapshotted 2026-10-14 evening (America/Toronto) while the integration was still in progress; code only — secret files (client secret, RSA key, admin credential) were **not read** | live capture + sanitizer |
| `README.md`, `LICENSE`, `docs/**`, connector `README.md`s, `tests/**` (harnesses) | newly written for this repository | authored |

Unsanitized originals are kept in a private repository and are not part of
this one.

## Locations searched

- Private source repository: the Navidrome shim, the pscode-collab stack and
  its tests, RomM integration notes (docs-only; the RomM integration used the
  upstream Wizarr client with no custom code beyond the override above), and
  Manyfold integration reports.
- Commit-message and filename searches over the full private history
  (wizarr/manyfold/subsonic/navidrome).
- Live systems (read-only): Wizarr container host (container definition,
  overrides, `media_server` table safe columns only — 5 servers: Jellyfin,
  Audiobookshelf, Navidrome-via-shim, RomM, pscode-collab-via-shim; 1
  Overseerr connection), music host (shim checksums), collab host (deployed
  pscode-collab tree checksums), Manyfold host (`/opt/manyfold-gate`), and the
  private Gitea (repo inventory: a stale private `pscode-collab` mirror from
  2026-08-25 exists; nothing else wizarr-related).

## Known gaps

- Historic git **blobs** (as opposed to commit messages and filenames) were
  not exhaustively searched.
- One decommissioned host's workspace was not reachable; its repository
  remote is superseded by the live Gitea.
- The Manyfold Wizarr provisioning adapter did not exist at the collection
  snapshot; it will need a follow-up collection once built.

## Custom vs upstream-builtin determination

Custom: nd-subsonic-shim; pscode-collab (entire stack); the historical
`romm.py`/`jellyfin.py` overrides; the manyfold gate. Upstream builtins used
as-is (therefore NOT collected): Wizarr's Jellyfin, Audiobookshelf, current
RomM clients and the Overseerr/Ombi connection.
