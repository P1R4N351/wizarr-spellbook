# Sanitization

How this repository was scrubbed before publication. Authored-by: GPTPCS
(gptpcs-wiz4c01). The sanitizer is a deterministic, auditable script run in
the private staging area; every replacement is counted and reviewed.

## What was removed or replaced

- **Tailnet names**: the household tailnet domain and every full service
  hostname → `*.example.ts.net` placeholders.
- **Addresses**: all real addresses → generic private-network **example
  placeholders that require replacement** at deployment (consistent per
  original host so cross-references survive). They are placeholders, not
  reserved documentation ranges.
- **Host/node names**: hypervisor and container names → `node-*` / role
  names ("the collab host", "the media host"…).
- **People**: the operator's name and login — prose → "the operator";
  test-fixture username → `alice` (scoped pass over test files only).
- **Household-specific deploy plumbing**: `deploy.sh`,
  `deploy-waist-lane.sh` and the live `machine-settings.json` are excluded;
  a generic `machine-settings.example.json` replaces the latter.
- **Private READMEs**: not copied wholesale; connector READMEs here are
  rewritten for a public audience.

## Credentials and secrets

- The exported repository contains **no credential or database files**:
  secret material was excluded at collection (credential files handled by
  path only; DB queries selected non-secret columns; `gate.mjs` secrets live
  in separate root-owned files that were not collected).
- The one bearer-shaped field in the live machine settings was empty; the
  shipped `machine-settings.example.json` is a generated generic template.
- Scan scope and results: every staged file and **every blob in the fresh
  git repository** (via `git rev-list --objects` + `git cat-file`) was
  scanned for the tailnet domain, every real IP seen during collection,
  host/container names, the operator's name/login, PEM private-key headers,
  known token shapes (`ghp_`/`gho_`/`github_pat_`/`AKIA`/`xox*`), and
  long-value secret assignments — **0 hits**. Full evidence in the private
  staging RESULT.md reviewed before publication.

## What deliberately remains

- The project name ("piranesi") and service branding/assets — the repo is
  published under that identity.
- Dates, design rationale, upstream version numbers, port numbers, and the
  documented `updateUser` no-op limitation of the Navidrome shim.
- Git history: **none**. This repository starts fresh; the private history
  (with household topology) stays in the household's private Gitea.

## Verification

- Zero-residual grep over the staged tree and the same scan over **every
  object in the fresh git repository** — see "Credentials and secrets"
  above for scope and results.
- Results are recorded in the private staging RESULT.md reviewed before
  publication.
