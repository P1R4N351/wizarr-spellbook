#!/usr/bin/env bash
# build-image.sh — tag the pscode-collab image from a built reh-web bundle.
#
# Usage: build-image.sh <bundle-dir> [<tag>]
#   <bundle-dir>  the vscode-reh-web-linux-x64 tree produced by
#                 `gulp vscode-reh-web-linux-x64-min`
#   <tag>         image tag (default localhost/pscode-collab:1.128.0)
#
# WHY THIS EXISTS AS A SCRIPT AND NOT A COMMAND IN THE PLAN
# --------------------------------------------------------
# The Containerfile hard-codes an ENTRYPOINT path into the bundle. If the bundle
# lays that binary down under a different name (upstream has moved it between
# `bin/code-server` and `bin/code-server-oss` across releases), the image builds
# clean and then dies at run time with "no such file or directory" — a failure
# that looks like a podman problem and is not. So the path is CONFIRMED against
# the actual bundle here, before the tag is applied.
#
# Exit: 0 ok, 2 usage, 3 bundle missing/malformed, 4 entrypoint mismatch,
#       5 image build failed.
# P10 RELAXATIONS: relaxes rule 10 (set -e omitted) — every call is explicitly
# rc-checked with `|| die`, and -e would abort the deliberate fallbacks
# (`command -v podman || command -v docker`) and the diagnostic `find || true`.
set -uo pipefail

readonly DEFAULT_TAG="localhost/pscode-collab:1.128.0"
# Verified 2026-08-21 against a real vscode-reh-web-linux-x64 build: this
# fork's OSS-branded product.json produces bin/code-server-oss, not
# bin/code-server. build-image.sh caught the mismatch and refused to tag
# (exit 4) exactly as designed, before this was updated to match.
readonly ENTRY_REL="bin/code-server-oss"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly HERE

die() {
  printf 'build-image: %s\n' "${1}" >&2
  exit "${2}"
}

# Script-scoped, not `local` inside main(): the EXIT trap runs after main()
# returns, when a `local staged` would already be out of scope — set -u then
# kills the trap itself, and rm -rf never runs. Found live on 2026-08-21: it
# leaked three 808 MB staged bundle copies into /tmp across three real runs
# before this was fixed. Same defect, same fix, as pr-gate.test.sh's
# `fixtures` variable earlier in this same session — not caught here the
# first time because the lesson wasn't carried back across files.
staged=""
trap 'rm -rf -- "${staged}"' EXIT

main() {
  local bundle tag runtime
  bundle="${1:-}"
  tag="${2:-${DEFAULT_TAG}}"
  [[ -n "${bundle}" ]] || die "usage: build-image.sh <bundle-dir> [<tag>]" 2

  [[ -d "${bundle}" ]] || die "bundle dir not found: ${bundle}" 3

  # Rule 7: assert the bundle is what we think before spending a build on it.
  if [[ ! -x "${bundle}/${ENTRY_REL}" ]]; then
    printf 'build-image: %s is not an executable in the bundle.\n' "${ENTRY_REL}" >&2
    printf 'build-image: bundle bin/ actually contains:\n' >&2
    find "${bundle}/bin" -maxdepth 1 -mindepth 1 -printf '  %f\n' 2>/dev/null >&2 || true
    die "entrypoint mismatch — fix ENTRYPOINT in the Containerfile to match" 4
  fi

  # The server ships its own node; a bundle without it will not run in a
  # distro-node-free image.
  [[ -x "${bundle}/node" ]] || printf 'build-image: WARNING no ./node in the bundle\n' >&2

  # podman build needs the bundle inside the context, beside the Containerfile.
  staged="$(mktemp -d)"
  cp -- "${HERE}/Containerfile" "${staged}/Containerfile" ||
    die "could not stage the Containerfile" 3
  cp -a -- "${bundle}" "${staged}/vscode-reh-web-linux-x64" ||
    die "could not stage the bundle" 3

  runtime="$(command -v podman || command -v docker)" ||
    die "neither podman nor docker on PATH" 3

  "${runtime}" build -t "${tag}" -f "${staged}/Containerfile" "${staged}" ||
    die "image build failed" 5

  printf 'build-image: tagged %s\n' "${tag}"
  "${runtime}" image inspect -f '  size={{.Size}} user={{.Config.User}}' "${tag}" 2>/dev/null || true
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
