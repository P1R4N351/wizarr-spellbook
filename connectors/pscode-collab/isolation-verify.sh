#!/usr/bin/env bash
# isolation-verify.sh — adversarial probe, run INSIDE a collab container.
# Exit 0 only when every forbidden destination is unreachable and every
# required one is. Every check is a NEGATIVE assertion that fails loudly when
# unwanted reachability exists — a probe that only checks the happy path would
# stay silent on the exact failure this exists to catch.
set -uo pipefail
# P10 RELAXATIONS: relaxes rule 10 (set -e omitted) — deny()/allow() below
# intentionally test a command that is EXPECTED to fail (a blocked
# connection); -e would abort the script on the very outcome it is checking
# for, rather than letting FAIL accumulate across every probe.

FAIL=0

deny() { # deny <label> <host> <port>
  local label="${1}" host="${2}" port="${3}"
  if timeout 4 bash -c "echo > /dev/tcp/${host}/${port}" 2>/dev/null; then
    printf 'REACHABLE (must not be): %s %s:%s\n' "${label}" "${host}" "${port}"
    FAIL=1
  else
    printf 'blocked ok: %s %s:%s\n' "${label}" "${host}" "${port}"
  fi
}

allow() { # allow <label> <host> <port>
  local label="${1}" host="${2}" port="${3}"
  if timeout 6 bash -c "echo > /dev/tcp/${host}/${port}" 2>/dev/null; then
    printf 'reachable ok: %s %s:%s\n' "${label}" "${host}" "${port}"
  else
    printf 'UNREACHABLE (must be allowed): %s %s:%s\n' "${label}" "${host}" "${port}"
    FAIL=1
  fi
}

main() {
  allow gitea-http 100.64.0.10 18792
  allow gitea-ssh 100.64.0.10 2222
  deny gitea-host-shell 100.64.0.10 22
  deny collab-host-ssh 100.64.0.11 22
  deny wizarr-ct 10.0.0.133 5690
  deny odysseus 127.0.0.1 18811
  deny tailscale-api 100.100.100.100 80

  if [[ -r /var/lib/tailscale/tailscaled.state ]]; then
    printf 'tailnet state readable\n'
    FAIL=1
  fi
  if [[ -r /root/.ssh/id_ed25519 ]]; then
    printf 'ssh key readable\n'
    FAIL=1
  fi

  exit "${FAIL}"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
