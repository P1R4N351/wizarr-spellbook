# Source manifest

Where every file in this repository came from, and everywhere we looked.
Collected 2026-10-14 (America/Toronto) by lane gptpcs-wiz4c01, with a final
Manyfold snapshot the same evening. Authored-by: GPTPCS.

## Provenance of staged files

| Repo path | Origin | Collected how |
|---|---|---|
| `connectors/nd-subsonic-shim/{nd-subsonic-shim.py,.service}` | household repo `skills/nd-subsonic-shim/`; **verified byte-identical** to the live deployment on the music host (`sha256 cc58c3c4…`, `bf5ac8ba…`, service active) | file copy + sanitizer |
| `connectors/pscode-collab/**` (code, Containerfiles, nft, units, assets) | household repo `skills/pscode-collab/`; deployed copy on the collab host verified **identical except** `broker.py` (deployed is an older pre-`wa.finish_registration` WebAuthn variant, 7-line diff — preserved privately, superseded here) and `collab-front-forwarder.py` (runs on the funnel front host) | file copy + sanitizer |
| `connectors/pscode-collab/tests/**` | household repo `skills/tests/pscode-collab/` | file copy + sanitizer |
| `connectors/pscode-collab/machine-settings.example.json` | generated generic template (live file's shape; real endpoints removed, bearer empty in the live file too) | generated |
| `connectors/wizarr-overrides/{jellyfin.py,romm.py}` | live media host `/opt/wizarr/overrides/` (read-only cat over ssh; overrides retired from the quadlet 2026-10-14) | live capture + sanitizer |
| `connectors/manyfold/gate.mjs` | live `/opt/manyfold-gate/gate.mjs` on the Manyfold host, snapshotted 2026-10-14 evening (America/Toronto) while its building lane is mid-integration; code only — secret files (client-secret, RSA key, admin credential) were **not read** | live capture + sanitizer |
| `README.md`, `LICENSE`, `docs/**`, connector `README.md`s, `tests/**` (harnesses) | newly written for this repository | authored by GPTPCS |

Private originals (unsanitized, with household topology) are preserved
separately in the household's private Gitea — see the preservation plan in
the staging area's RESULT.md (not part of this repo).

## Locations searched

- Household repo source copies: `skills/nd-subsonic-shim`, `skills/pscode-collab`,
  `skills/tests/pscode-collab`, `skills/ct137-romm` (docs-only; RomM
  integration used upstream Wizarr client — no custom code beyond the
  override above), `docs/reports/manyfold-wizarr-alpha-*`.
- Workstation coordinator ran eidetic recall (lexical
  `recall.sh wizarr manyfold navidrome` + semantic KNN
  `recall-knn.sh 'custom wizarr connectors navidrome manyfold'`) and the
  all-ref commit-message search `git log --all --grep=wizarr` over the full
  local history: found Navidrome shim commit `5c96a826…`, pscode commits
  `0733b8cb…`/`30704c92…`, RomM docs-only commit `60169c44…` (verified
  docs-only via `git show --stat`), plus filename searches
  (wizarr/manyfold/subsonic). SCUMBS fidelity rating: **unavailable** from
  the wrapper output — not invented.
- Live systems (read-only): Wizarr container host (quadlet, overrides,
  `media_server` table safe columns only — 5 servers: Jellyfin,
  Audiobookshelf, Navidrome-via-shim, RomM, pscode-collab-via-shim; 1
  Overseerr connection), music host (shim checksums), collab host
  (deployed pscode-collab tree checksums), Manyfold host
  (`/opt/manyfold-gate`), household Gitea (repo inventory: a stale private
  `pscode-collab` mirror from 2026-08-25 exists; nothing else
  wizarr-related).

## Known gaps (honestly unsearched)

- Exhaustive search of every historic git **blob** (as opposed to all-ref
  commit messages and filenames) on the workstation was not performed.
- The sunset legacy host's workspace (ssh refused; being decommissioned) —
  its Gitea remote is superseded by the live household Gitea.
- The Manyfold Wizarr provisioning adapter was **unavailable at the
  collection snapshot** — the active lane building the Manyfold integration
  had not yet produced it; it will need a follow-up collection when that
  lane finishes.

## Custom vs upstream-builtin determination

Custom: nd-subsonic-shim; pscode-collab (entire stack); the historical
`romm.py`/`jellyfin.py` overrides; the manyfold gate. Upstream builtins used
as-is (therefore NOT collected): Wizarr's Jellyfin, Audiobookshelf, current
RomM clients and the Overseerr/Ombi connection.
