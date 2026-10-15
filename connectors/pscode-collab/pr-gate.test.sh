#!/usr/bin/env bash
# pr-gate.test.sh — functional test against the real scanners, real corpus.
# Plants a genuine G2 (os command injection via shell=True) fixture, the
# corpus's own Critical->error mapping — not an invented finding.
set -uo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly HERE
REPO_ROOT="$(cd -- "${HERE}/../.." && pwd -P)"
readonly REPO_ROOT

# Script-scoped, not `local` inside main(): the EXIT trap runs after main()
# returns, when a `local fixtures` would already be out of scope — `set -u`
# then kills the trap itself with "fixtures: unbound variable", masking a
# fully-passed test behind a nonzero exit. Caught by actually running this.
fixtures=""
trap 'rm -rf -- "${fixtures}"' EXIT

main() {
  fixtures=$(mktemp -d)

  printf 'def f(n):\n    assert n >= 0\n    return n + 1\n' >"${fixtures}/clean.py"
  # Real G2 corpus cue: os\.system\(|shell\s*=\s*True|popen\(
  printf 'import subprocess\n\ndef run(cmd):\n    subprocess.run(cmd, shell=True)\n' \
    >"${fixtures}/dirty.py"

  export P10_SCAN="${REPO_ROOT}/skills/p10-scan.py"
  export VULN_SCAN="${REPO_ROOT}/home-config/.piranesi/code/skills/vibe-vuln-scan/vibe-vuln-scan.py"
  local corpus="${REPO_ROOT}/projects/worktrees/kudzu-link-fallback/memory/vibe-coding-vulns.md"

  test -x "${VULN_SCAN}" -o -f "${VULN_SCAN}" ||
    {
      printf 'SKIP: vibe-vuln-scan.py not found at %s\n' "${VULN_SCAN}"
      exit 0
    }
  test -f "${corpus}" ||
    {
      printf 'SKIP: corpus not found at %s\n' "${corpus}"
      exit 0
    }

  local rc_clean rc_dirty
  set +e
  python3 "${VULN_SCAN}" --corpus "${corpus}" --severity error "${fixtures}/clean.py" >/dev/null 2>&1
  rc_clean=$?
  python3 "${VULN_SCAN}" --corpus "${corpus}" --severity error "${fixtures}/dirty.py" >/dev/null 2>&1
  rc_dirty=$?
  set -e

  printf 'vibe-vuln-scan direct: clean=%d dirty=%d (expect 0, 1)\n' "${rc_clean}" "${rc_dirty}"
  test "${rc_clean}" -eq 0 || {
    printf 'FAIL: clean fixture flagged\n' >&2
    exit 1
  }
  test "${rc_dirty}" -ne 0 || {
    printf 'FAIL: planted shell=True NOT caught by the real scanner\n' >&2
    exit 1
  }

  # Now the actual gate, unmodified, exercising both P10 and vuln paths with
  # a real corpus threaded through via VULN_CORPUS:
  local gate_rc_clean gate_rc_dirty
  set +e
  VULN_CORPUS="${corpus}" bash "${HERE}/pr-gate.sh" "${fixtures}/clean.py" >/dev/null 2>&1
  gate_rc_clean=$?
  VULN_CORPUS="${corpus}" bash "${HERE}/pr-gate.sh" "${fixtures}/dirty.py" >/dev/null 2>&1
  gate_rc_dirty=$?
  set -e

  printf 'pr-gate.sh end-to-end: clean=%d dirty=%d (expect 0, 8)\n' "${gate_rc_clean}" "${gate_rc_dirty}"
  test "${gate_rc_clean}" -eq 0 || {
    printf 'FAIL: gate rejected a clean file\n' >&2
    exit 1
  }
  test "${gate_rc_dirty}" -eq 8 ||
    {
      printf 'FAIL: gate did not block the planted shell=True (rc=%d)\n' "${gate_rc_dirty}" >&2
      exit 1
    }

  local gate_rc_missing
  set +e
  VULN_SCAN="${REPO_ROOT}/skills/pscode-collab/pr-gate-nonexistent-marker.py" \
    bash "${HERE}/pr-gate.sh" "${fixtures}/clean.py" >/dev/null 2>&1
  gate_rc_missing=$?
  set -e
  printf 'pr-gate.sh with a genuinely absent VULN_SCAN: rc=%d (expect 8, fail-closed)\n' "${gate_rc_missing}"
  test "${gate_rc_missing}" -eq 8 ||
    {
      printf 'FAIL: gate did not fail closed on a missing scanner\n' >&2
      exit 1
    }

  set +e
  bash "${HERE}/pr-gate.sh" >/dev/null 2>&1
  local gate_rc_usage=$?
  set -e
  printf 'pr-gate.sh with no args: rc=%d (expect 2)\n' "${gate_rc_usage}"
  test "${gate_rc_usage}" -eq 2 || {
    printf 'FAIL: usage exit code wrong\n' >&2
    exit 1
  }

  printf 'pr-gate.test.sh: ALL PASSED\n'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
