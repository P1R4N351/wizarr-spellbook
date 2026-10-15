# Tests

Everything here runs locally against **fake upstreams** — no live service is
contacted and no real user is ever created.

## Prerequisites

- Python 3.12+, Node.js (for the gate test), `openssl`.
- An isolated venv with pinned deps — the WebAuthn modules **require**
  `fido2` 1.x (2.x removed `fido2.features.webauthn_json_mapping` and is not
  compatible; without fido2 installed, pytest collection of the pscode-collab
  suite **errors**, it is not skipped):

  ```sh
  python3 -m venv .venv
  .venv/bin/pip install pytest 'fido2<2'
  ```

- The pscode-collab provisioning tests check real token-dir group semantics
  and need the broker group to exist on the test host:

  ```sh
  sudo groupadd pscode-collab   # once; or run them on a host that has it
  ```

  Without it, `provision.create` raises `RuntimeError: missing broker group`
  and those tests fail. (Verified behavior, not a skip.)

## Running

- `test_nd_shim_e2e.py` — boots a fake Navidrome (stdlib HTTP server
  implementing `/auth/login`, `/api/user`, and a `/rest/ping` control
  endpoint), loads the real shim code via importlib with its three deployment
  constants (secret path, upstream, port) rewritten for the sandbox, and
  exercises: authenticated `createUser` → fake upstream receives the
  native-API call; wrong Subsonic token → error 40 and **nothing** reaches
  the upstream; `deleteUser` → lookup + delete; `updateUser` → acknowledged
  no-op; any other `/rest/*` → transparent proxy.

  ```sh
  python3 tests/test_nd_shim_e2e.py        # stdlib only, no venv needed
  ```

- `test_gate_e2e.sh` — generates throwaway OIDC client secret + RSA key,
  starts `connectors/manyfold/gate.mjs` against a fake upstream Manyfold,
  and verifies: transparent proxy pass-through for guests, OIDC discovery +
  JWKS served, token endpoint rejects a wrong client secret, and the
  card-verify endpoint **fails closed** when no keyring is pinned.

  ```sh
  bash tests/test_gate_e2e.sh              # needs node + openssl
  ```

- pscode-collab unit suite — `connectors/pscode-collab/tests/` **and** the
  module-local `test_*.py`, all built on injected fakes (fake podman runner,
  fake Gitea, fake store). The two directories contain duplicate test module
  basenames (`test_broker.py` exists in both), so run with
  `--import-mode=importlib` in one invocation:

  ```sh
  cd connectors/pscode-collab
  ../../.venv/bin/python -m pytest --import-mode=importlib \
      tests/ test_broker.py test_container_scope.py \
      test_provision_auth.py test_recover.py test_webauthn.py -q
  ```

  Expected: all pass (179 tests + 58 subtests with Python 3.12.3,
  pytest 9.1.1, fido2 1.2.0).

## Historical evidence vs this suite

The connector READMEs cite end-to-end proofs (real Wizarr invite → account →
revoke) from the **original household deployments**; those are deployment
records, not reproducible from this repository. The suite above is what
this sanitized repository itself proves.
