#!/usr/bin/env bash
# Build the browser-branding overlay for the collective PSCode collaborator.
# Usage: build-web-branding-image.sh [<tag>] [<base-image>]
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly HERE
readonly DEFAULT_TAG="localhost/pscode-collab:1.128.0-kudzu3"
readonly DEFAULT_BASE_IMAGE="localhost/pscode-collab:1.128.0-kudzu2"

fail() {
	printf 'build-web-branding-image: %s\n' "$1" >&2
	exit "${2:-1}"
}

main() {
	local tag="${1:-${DEFAULT_TAG}}"
	local base_image="${2:-${DEFAULT_BASE_IMAGE}}"
	local runtime=""
	local stage=""

	case "$#" in
		0|1|2) ;;
		*) fail 'usage: build-web-branding-image.sh [<tag>] [<base-image>]' 2 ;;
	esac

	runtime="$(command -v podman || command -v docker || true)"
	[[ -n "${runtime}" ]] || fail 'neither podman nor docker is available' 3
	"${runtime}" image exists "${base_image}" || fail "base image is unavailable: ${base_image}" 3

	for source in Containerfile.web-branding patch-web-branding.js assets/piranesi-web-icon-192.png assets/piranesi-web-icon-512.png assets/piranesi-web-favicon.ico; do
		[[ -f "${HERE}/${source}" ]] || fail "required source is absent: ${source}" 3
	done

	stage="$(mktemp -d "${TMPDIR:-/tmp}/pscode-web-branding.XXXXXX")"
	trap 'rm -rf -- "${stage}"' EXIT
	mkdir -p "${stage}/assets"
	cp "${HERE}/Containerfile.web-branding" "${stage}/Containerfile"
	cp "${HERE}/patch-web-branding.js" "${stage}/patch-web-branding.js"
	cp "${HERE}/assets/piranesi-web-icon-192.png" "${stage}/assets/piranesi-web-icon-192.png"
	cp "${HERE}/assets/piranesi-web-icon-512.png" "${stage}/assets/piranesi-web-icon-512.png"
	cp "${HERE}/assets/piranesi-web-favicon.ico" "${stage}/assets/piranesi-web-favicon.ico"

	"${runtime}" build --build-arg "BASE_IMAGE=${base_image}" --tag "${tag}" "${stage}"
	# shellcheck disable=SC2016 # Node receives this program verbatim.
	"${runtime}" run --rm --entrypoint /opt/pscode/node "${tag}" -e '
		const fs = require("node:fs");
		const manifest = JSON.parse(fs.readFileSync("/opt/pscode/resources/server/manifest.json", "utf8"));
		if (manifest.name !== "Piranesi Studio Code" || manifest.short_name !== "PSCode") process.exit(1);
		for (const [file, size] of [["code-192.png", 192], ["code-512.png", 512]]) {
			const contents = fs.readFileSync(`/opt/pscode/resources/server/${file}`);
			if (contents.readUInt32BE(16) !== size || contents.readUInt32BE(20) !== size) process.exit(1);
		}
		console.log("pscode web image branding: verified");
	'
	printf 'build-web-branding-image: tagged %s\n' "${tag}"
}

main "$@"
