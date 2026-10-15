#!/usr/bin/env bash
# pr-gate.sh — block a collaborator PR on P10 or vulnerability findings.
# Usage: pr-gate.sh <changed-file>...
# Exit 0 = merge-eligible, 6 = P10 red, 8 = vuln red, 2 = usage.
#
# P10 RELAXATIONS: relaxes rule 10 (set -e omitted) — every call is explicitly
# rc-checked, and -e would abort the deliberate `test -f "${VULN}"` fallthrough
# to the fail-closed else branch below.
#
# This gate scans diffs from external, untrusted collaborators (the whole
# point of this design), so its own temp-file handling uses mktemp rather
# than a predictable $$-suffixed path — a symlink race in shared /tmp is
# exactly the class of thing a hostile PR would try.
set -uo pipefail

readonly EX_USAGE=2
readonly EX_P10=6
readonly EX_VULN=8

main() {
  test "$#" -ge 1 || {
    printf 'usage: pr-gate.sh <file>...\n' >&2
    exit "${EX_USAGE}"
  }

  local p10="${P10_SCAN:-skills/p10-scan.py}"
  local vuln="${VULN_SCAN:-skills/vibe-vuln-scan/vibe-vuln-scan.py}"
  # vibe-vuln-scan.py's own default corpus resolution is
  # <script-dir>/../../memory/vibe-coding-vulns.md — this repo has no such
  # file at its root today (verified 2026-08-21), so a deployment that
  # colocates the scanner without also colocating the corpus would fail
  # silently on every scan unless the corpus path is explicit here.
  local -a vuln_corpus_args=()
  if [[ -n "${VULN_CORPUS:-}" ]]; then
    vuln_corpus_args=(--corpus "${VULN_CORPUS}")
  fi
  local rc=0
  local scratch
  scratch=$(mktemp -d)
  trap 'rm -rf -- "${scratch}"' EXIT

  local f
  for f in "$@"; do
    test -f "${f}" || continue
    if ! python3 "${p10}" --file "${f}" --json >"${scratch}/p10.out" 2>&1; then
      printf 'P10 findings in %s:\n' "${f}"
      cat -- "${scratch}/p10.out"
      rc="${EX_P10}"
    fi
  done

  if test -f "${vuln}"; then
    for f in "$@"; do
      test -f "${f}" || continue
      # vibe-vuln-scan takes exactly one positional path; loop per file so a
      # multi-file PR gets every file scanned, not just the first.
      if ! python3 "${vuln}" "${vuln_corpus_args[@]}" --severity error "${f}" \
        >"${scratch}/vuln.out" 2>&1; then
        printf 'vulnerability findings in %s:\n' "${f}"
        cat -- "${scratch}/vuln.out"
        rc="${EX_VULN}"
      fi
    done
  else
    printf 'vibe-vuln-scan not present at %s — refusing to pass a PR unscanned\n' "${vuln}" >&2
    rc="${EX_VULN}"
  fi

  exit "${rc}"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
