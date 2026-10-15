#!/usr/bin/env bash
# E2E test for connectors/manyfold/gate.mjs against a fake upstream Manyfold.
# No live services; throwaway secrets generated per run. Verifies:
#   1. transparent proxy pass-through (guest path untouched)
#   2. OIDC discovery + JWKS served
#   3. token endpoint rejects a wrong client secret
#   4. card authorize fails closed when no keyring is pinned
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
GATE="$HERE/../connectors/manyfold/gate.mjs"
WORK=$(mktemp -d)
trap 'kill $UP_PID $GATE_PID 2>/dev/null || true; rm -rf "$WORK"' EXIT

UP_PORT=$((20000 + RANDOM % 10000))
GATE_PORT=$((30000 + RANDOM % 10000))

# throwaway secrets
head -c 24 /dev/urandom | base64 > "$WORK/client-secret"
openssl genrsa -out "$WORK/oidc-rsa.pem" 2048 2>/dev/null

# fake upstream Manyfold: echoes a recognizable body
node -e "
require('node:http').createServer((req, res) => {
  res.writeHead(200, {'x-fake-upstream': 'manyfold'});
  res.end('FAKE-MANYFOLD ' + req.method + ' ' + req.url);
}).listen($UP_PORT, '127.0.0.1', () => console.log('up'));
" & UP_PID=$!
sleep 1

MFG_LISTEN_HOST=127.0.0.1 MFG_LISTEN_PORT=$GATE_PORT \
MFG_UPSTREAM="http://127.0.0.1:$UP_PORT" \
MFG_ISSUER="http://127.0.0.1:$GATE_PORT/oidc" \
MFG_CLIENT_SECRET_FILE="$WORK/client-secret" \
MFG_RSA_KEY_FILE="$WORK/oidc-rsa.pem" \
MFG_CARD_KEYRING="$WORK/nonexistent-keyring.asc" \
MFG_CARD_LINEAGE="$WORK/nonexistent-lineage.json" \
MFG_CARD_TMPDIR="$WORK/card-tmp" \
node "$GATE" & GATE_PID=$!
sleep 1.5

pass=0; fail=0
check() { # name expected actual
  if [[ "$3" == *"$2"* ]]; then echo "ok   $1"; pass=$((pass+1));
  else echo "FAIL $1: wanted [$2] got [$3]"; fail=$((fail+1)); fi
}

# 1. guest pass-through
body=$(curl -s "http://127.0.0.1:$GATE_PORT/models")
check "proxy pass-through body" "FAKE-MANYFOLD GET /models" "$body"
hdr=$(curl -sI "http://127.0.0.1:$GATE_PORT/" | tr -d '\r')
check "proxy pass-through header" "x-fake-upstream: manyfold" "$hdr"

# 2. OIDC discovery + JWKS
disc=$(curl -s "http://127.0.0.1:$GATE_PORT/oidc/.well-known/openid-configuration")
check "discovery issuer" "\"issuer\":\"http://127.0.0.1:$GATE_PORT/oidc\"" "$disc"
jwks=$(curl -s "http://127.0.0.1:$GATE_PORT/oidc/jwks")
check "jwks has RSA key" '"kty":"RSA"' "$jwks"

# 3. token endpoint rejects wrong client secret
tok=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$GATE_PORT/oidc/token" \
  -d "grant_type=authorization_code&code=bogus&client_id=manyfold&client_secret=wrong")
check "wrong client secret -> 401" "401" "$tok"

# 4. card authorize fails closed with no pinned keyring
auth=$(curl -s -X POST "http://127.0.0.1:$GATE_PORT/oidc/verify" \
  --data-urlencode "signature=-----BEGIN PGP SIGNATURE-----
bogus
-----END PGP SIGNATURE-----" \
  --data-urlencode "challenge=bogus" -w '|%{http_code}')
case "$auth" in
  *card-auth-disabled*|*40[013]*) echo "ok   card auth fails closed (no keyring)"; pass=$((pass+1));;
  *) echo "FAIL card auth: $auth"; fail=$((fail+1));;
esac

echo "----"
echo "passed=$pass failed=$fail"
[[ $fail -eq 0 ]]
