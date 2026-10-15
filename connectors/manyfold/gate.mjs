// manyfold-gate: transparent reverse proxy for Manyfold (guest logins pass
// through untouched) + a minimal OIDC identity provider whose authorize step
// is the household Alpha/Omega OpenPGP card-signed challenge.
//
// Design (parent review, auth-design-review.md, 2026-10-15):
//  - Every normal Manyfold route (including Devise local sign-in for
//    Wizarr-provisioned guests) is proxied 1:1 to the upstream. No hardware
//    key is ever required for guests.
//  - /oidc/* is served HERE and implements authorization-code OIDC for
//    exactly one client (Manyfold). The authorize page issues the same
//    challenge format as /opt/bp-spooler (bp-spool-auth v1, class
//    manyfold-oidc) and verifies the armored signature with gpg in an
//    ephemeral home against the PINNED household keyring + card-lineage
//    tiers. omega -> claims for the pre-created administrator account,
//    alpha -> per-card member identity. No second password is asked.
//  - Nothing here trusts headers; the only bypass-shaped thing is the code
//    and token store, both single-use, short-lived and in-memory (restart
//    invalidates everything).
//
// Mechanism credit: card challenge/verify is a faithful port of
// /opt/bp-spooler/spooler.mjs cardAuth (Piranesi-Main DECIDE: C lineage).
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';

const env = (k, d) => (process.env[k] != null && process.env[k] !== '' ? process.env[k] : d);
const iEnv = (k, d) => { const v = parseInt(process.env[k] || '', 10); return Number.isFinite(v) ? v : d; };

const CFG = {
  listenHost: env('MFG_LISTEN_HOST', '0.0.0.0'),
  listenPort: iEnv('MFG_LISTEN_PORT', 3214),
  upstream:   env('MFG_UPSTREAM', 'http://127.0.0.1:3215'),
  issuer:     env('MFG_ISSUER', 'http://192.0.2.10:3214/oidc'),
  clientId:   env('MFG_CLIENT_ID', 'manyfold'),
  clientSecretFile: env('MFG_CLIENT_SECRET_FILE', '/opt/manyfold-gate/client-secret'),
  rsaKeyFile: env('MFG_RSA_KEY_FILE', '/opt/manyfold-gate/oidc-rsa.pem'),
  keyring:    env('MFG_CARD_KEYRING', '/opt/bp-spooler/card-keyring.asc'),
  lineage:    env('MFG_CARD_LINEAGE', '/opt/bp-spooler/card-lineage.json'),
  gpgBin:     env('MFG_GPG_BIN', 'gpg'),
  tmpDir:     env('MFG_CARD_TMPDIR', '/opt/manyfold-gate/.card-auth-tmp'),
  challengeHost: env('MFG_CHALLENGE_HOST', os.hostname()),
  challengeMaxAgeS: iEnv('MFG_CHALLENGE_MAX_AGE_S', 300),
  codeTtlS:   iEnv('MFG_CODE_TTL_S', 120),
  tokenTtlS:  iEnv('MFG_TOKEN_TTL_S', 3600),
  emailDomain: env('MFG_EMAIL_DOMAIN', 'cards.piranesi.internal'),
  // Browser-facing authorize URL; may differ from the issuer because the
  // Manyfold container reaches us via its compose-bridge gateway while
  // browsers use the tailnet/LAN address. Everything else (token, userinfo,
  // jwks, discovery) is fetched server-side by the container via the issuer.
  authorizeUrl: env('MFG_AUTHORIZE_URL', ''),
};

const CLIENT_SECRET = fs.readFileSync(CFG.clientSecretFile, 'utf8').trim();
const RSA_PEM = fs.readFileSync(CFG.rsaKeyFile, 'utf8');
const RSA_PRIV = crypto.createPrivateKey(RSA_PEM);
const RSA_PUB = crypto.createPublicKey(RSA_PRIV);
const KID = crypto.createHash('sha256').update(RSA_PUB.export({ type: 'spki', format: 'der' })).digest('base64url').slice(0, 16);

const log = (...a) => console.log('[manyfold-gate]', ...a);

// ---- card challenge (bp-spooler shape, class manyfold-oidc) -----------------
const PENDING_MAX = 256;
const pending = new Map(); // nonce -> issued ms
function isoNoMs(ms) { return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z'); }
function newChallenge() {
  const nonce = crypto.randomBytes(8).toString('hex');
  if (pending.size >= PENDING_MAX) pending.delete(pending.keys().next().value);
  pending.set(nonce, Date.now());
  return `bp-spool-auth v1 class=manyfold-oidc host=${CFG.challengeHost} nonce=${nonce} @${isoNoMs(Date.now())}`;
}
function parseChallenge(text) {
  const m = /^bp-spool-auth v1 class=([a-z-]+) host=(\S+) nonce=([0-9a-f]{16}) @(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)$/
    .exec(String(text || '').trim());
  if (!m) return null;
  const at = Date.parse(m[4]);
  if (!Number.isFinite(at)) return null;
  return { class: m[1], host: m[2], nonce: m[3], at };
}

function gpgPrimaryFprOf(sigArmored, text) {
  return new Promise((resolve) => {
    let dir;
    try { fs.mkdirSync(CFG.tmpDir, { recursive: true, mode: 0o700 }); fs.chmodSync(CFG.tmpDir, 0o700); dir = fs.mkdtempSync(path.join(CFG.tmpDir, 'mfg-card-')); }
    catch { try { dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mfg-card-')); } catch { return resolve(null); } }
    const done = (v) => { try { fs.rmSync(dir, { recursive: true, force: true }); } catch {} resolve(v); };
    try {
      fs.writeFileSync(path.join(dir, 'sig.asc'), sigArmored);
      fs.writeFileSync(path.join(dir, 'msg.txt'), text);
      fs.chmodSync(dir, 0o700);
    } catch { return done(null); }
    const base = ['--homedir', dir, '--batch', '--no-tty', '--no-autostart', '--no-options'];
    execFile(CFG.gpgBin, [...base, '--import', CFG.keyring], { timeout: 10000, maxBuffer: 1 << 20 }, (impErr) => {
      if (impErr) { log('gpg import failed', impErr.code || 'unknown'); return done(null); }
      execFile(CFG.gpgBin, [...base, '--status-fd', '1', '--verify', path.join(dir, 'sig.asc'), path.join(dir, 'msg.txt')],
        { timeout: 10000, maxBuffer: 1 << 20 }, (err, stdout) => {
          if (err) return done(null);
          const valid = String(stdout || '').split('\n').find(l => l.startsWith('[GNUPG:] VALIDSIG '));
          if (!valid) return done(null);
          const f = valid.split(' ');
          const primary = (f[11] || f[2] || '').toUpperCase();
          done(/^[0-9A-F]{40}$/.test(primary) ? primary : null);
        });
    });
  });
}

async function verifyCardSignature(sig, challenge) {
  if (!fs.existsSync(CFG.keyring) || !fs.existsSync(CFG.lineage)) return { ok: false, reason: 'card-auth-disabled' };
  const p = parseChallenge(challenge);
  if (!p || p.class !== 'manyfold-oidc') return { ok: false, reason: 'bad-challenge' };
  if (p.host !== CFG.challengeHost) return { ok: false, reason: 'host-mismatch' };
  const now = Date.now();
  if (now - p.at > CFG.challengeMaxAgeS * 1000 || p.at - now > 60000) return { ok: false, reason: 'stale' };
  if (!pending.has(p.nonce)) return { ok: false, reason: 'replayed' };
  const sigStr = String(sig || '');
  if (sigStr.length > 16384 || !sigStr.includes('BEGIN PGP SIGNATURE')) return { ok: false, reason: 'bad-signature' };
  const primary = await gpgPrimaryFprOf(sigStr, String(challenge).trim());
  if (!primary) return { ok: false, reason: 'bad-signature' };
  let lineage; try { lineage = JSON.parse(fs.readFileSync(CFG.lineage, 'utf8')); } catch { return { ok: false, reason: 'card-auth-disabled' }; }
  const card = lineage && lineage.cards && typeof lineage.cards === 'object' ? lineage.cards[primary] : null;
  if (!card) return { ok: false, reason: 'unregistered' };
  if (card.tier !== 'alpha' && card.tier !== 'omega') return { ok: false, reason: 'tier' };
  pending.delete(p.nonce); // spent on success only; a paste typo may be retried
  log(`card verified tier=${card.tier} card=${card.label || primary.slice(-16)}`);
  return { ok: true, tier: card.tier, fpr: primary, label: card.label || null };
}

function claimsFor(v) {
  // omega -> the pre-created administrator account (linked by verified email
  // on first login); alpha -> stable per-card member identity, auto-created
  // by Manyfold's from_omniauth with the default member role.
  if (v.tier === 'omega') {
    return { sub: `card:${v.fpr}`, email: `omega@${CFG.emailDomain}`, email_verified: true, preferred_username: 'omega' };
  }
  const short = v.fpr.slice(-8).toLowerCase();
  return { sub: `card:${v.fpr}`, email: `alpha-${short}@${CFG.emailDomain}`, email_verified: true, preferred_username: `alpha-${short}` };
}

// ---- OIDC plumbing -----------------------------------------------------------
const codes = new Map();   // code -> { claims, nonce, redirect_uri, exp }
const tokens = new Map();  // access_token -> { claims, exp }
function sweep() {
  const now = Date.now();
  for (const [k, v] of codes) if (v.exp < now) codes.delete(k);
  for (const [k, v] of tokens) if (v.exp < now) tokens.delete(k);
}
setInterval(sweep, 30000).unref();

function b64u(buf) { return Buffer.from(buf).toString('base64url'); }
function signJwt(payload) {
  const header = b64u(JSON.stringify({ alg: 'RS256', typ: 'JWT', kid: KID }));
  const body = b64u(JSON.stringify(payload));
  const sig = crypto.sign('sha256', Buffer.from(`${header}.${body}`), RSA_PRIV);
  return `${header}.${body}.${b64u(sig)}`;
}
function jwks() {
  const j = RSA_PUB.export({ format: 'jwk' });
  return { keys: [{ kty: 'RSA', use: 'sig', alg: 'RS256', kid: KID, n: j.n, e: j.e }] };
}
function discovery() {
  return {
    issuer: CFG.issuer,
    authorization_endpoint: CFG.authorizeUrl || `${CFG.issuer}/authorize`,
    token_endpoint: `${CFG.issuer}/token`,
    userinfo_endpoint: `${CFG.issuer}/userinfo`,
    jwks_uri: `${CFG.issuer}/jwks`,
    response_types_supported: ['code'],
    subject_types_supported: ['public'],
    grant_types_supported: ['authorization_code'],
    id_token_signing_alg_values_supported: ['RS256'],
    scopes_supported: ['openid', 'email', 'profile'],
    token_endpoint_auth_methods_supported: ['client_secret_basic', 'client_secret_post'],
    claims_supported: ['sub', 'email', 'email_verified', 'preferred_username'],
  };
}

function tse(a, b) { // timing-safe string equal
  const x = Buffer.from(String(a)), y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}
function clientAuthorized(req, form) {
  const h = req.headers.authorization || '';
  if (h.startsWith('Basic ')) {
    const [id, ...rest] = Buffer.from(h.slice(6), 'base64').toString('utf8').split(':');
    return tse(decodeURIComponent(id), CFG.clientId) && tse(decodeURIComponent(rest.join(':')), CLIENT_SECRET);
  }
  return tse(form.get('client_id') || '', CFG.clientId) && tse(form.get('client_secret') || '', CLIENT_SECRET);
}
function redirectUriAllowed(uri) {
  // exactly Manyfold's devise callback, any host:port it may self-describe as,
  // but never a foreign path or scheme.
  try {
    const u = new URL(uri);
    return (u.protocol === 'http:' || u.protocol === 'https:') && u.pathname === '/users/auth/openid_connect/callback';
  } catch { return false; }
}

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function authorizePage(q, challenge, errMsg) {
  const keep = ['client_id', 'redirect_uri', 'state', 'nonce', 'scope', 'response_type']
    .map(k => `<input type="hidden" name="${k}" value="${esc(q.get(k) || '')}">`).join('\n');
  return `<!doctype html><html><head><meta charset="utf-8"><title>Alpha/Omega sign-in — Manyfold</title>
<style>body{font-family:monospace;background:#111;color:#ddd;max-width:46em;margin:3em auto;padding:0 1em}
pre{background:#000;padding:.7em;border:1px solid #333;white-space:pre-wrap;word-break:break-all}
textarea{width:100%;height:11em;background:#000;color:#0f0;border:1px solid #333}
button{background:#222;color:#ddd;border:1px solid #555;padding:.5em 1.4em;cursor:pointer}
.err{color:#f66}.cap{color:#888}</style></head><body>
<h2>Manyfold — hardware-key sign-in</h2>
<div class="cap">Sign this challenge with a household card (Alpha = member, Omega = administrator), e.g.<br>
<code>echo -n '&lt;challenge&gt;' | gpg --armor --detach-sign</code> — then paste the armored signature.</div>
${errMsg ? `<p class="err">${esc(errMsg)}</p>` : ''}
<pre id="ch">${esc(challenge)}</pre>
<form method="POST" action="/oidc/verify">
<input type="hidden" name="challenge" value="${esc(challenge)}">
${keep}
<textarea name="signature" placeholder="-----BEGIN PGP SIGNATURE-----"></textarea><br>
<button type="submit">verify</button>
</form>
<p class="cap">Guests: this page is only for household hardware keys. Use the normal
<a href="/users/sign_in" style="color:#8cf">Manyfold sign-in</a> with your invited account.</p>
</body></html>`;
}

function readBody(req, cb) {
  let b = ''; let n = 0;
  req.on('data', (c) => { n += c.length; if (n > 1 << 20) { req.destroy(); } else b += c; });
  req.on('end', () => cb(b));
}
function sendJson(res, code, obj) {
  const s = JSON.stringify(obj);
  res.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  res.end(s);
}
function sendHtml(res, code, html) {
  res.writeHead(code, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
  res.end(html);
}

async function handleOidc(req, res, u) {
  const p = u.pathname;
  if (p === '/oidc/.well-known/openid-configuration' && req.method === 'GET') return sendJson(res, 200, discovery());
  if (p === '/oidc/jwks' && req.method === 'GET') return sendJson(res, 200, jwks());

  if (p === '/oidc/authorize' && req.method === 'GET') {
    const q = u.searchParams;
    if (!tse(q.get('client_id') || '', CFG.clientId)) return sendHtml(res, 400, 'unknown client');
    if (!redirectUriAllowed(q.get('redirect_uri') || '')) return sendHtml(res, 400, 'bad redirect_uri');
    if ((q.get('response_type') || '') !== 'code') return sendHtml(res, 400, 'only response_type=code');
    return sendHtml(res, 200, authorizePage(q, newChallenge()));
  }

  if (p === '/oidc/verify' && req.method === 'POST') {
    return readBody(req, async (body) => {
      const f = new URLSearchParams(body);
      if (!tse(f.get('client_id') || '', CFG.clientId) || !redirectUriAllowed(f.get('redirect_uri') || '')) {
        return sendHtml(res, 400, 'bad client/redirect');
      }
      const v = await verifyCardSignature(f.get('signature'), f.get('challenge'));
      if (!v.ok) {
        log(`verify denied reason=${v.reason}`);
        return sendHtml(res, 403, authorizePage(f, newChallenge(), `denied: ${v.reason} — a fresh challenge was issued`));
      }
      const code = crypto.randomBytes(24).toString('base64url');
      codes.set(code, { claims: claimsFor(v), nonce: f.get('nonce') || null, redirect_uri: f.get('redirect_uri'), exp: Date.now() + CFG.codeTtlS * 1000 });
      const loc = new URL(f.get('redirect_uri'));
      loc.searchParams.set('code', code);
      if (f.get('state')) loc.searchParams.set('state', f.get('state'));
      res.writeHead(302, { location: loc.toString(), 'cache-control': 'no-store' });
      return res.end();
    });
  }

  if (p === '/oidc/token' && req.method === 'POST') {
    return readBody(req, (body) => {
      const f = new URLSearchParams(body);
      if (!clientAuthorized(req, f)) return sendJson(res, 401, { error: 'invalid_client' });
      if ((f.get('grant_type') || '') !== 'authorization_code') return sendJson(res, 400, { error: 'unsupported_grant_type' });
      const c = codes.get(f.get('code') || '');
      codes.delete(f.get('code') || ''); // single use, spent even on failure below
      if (!c || c.exp < Date.now()) return sendJson(res, 400, { error: 'invalid_grant' });
      if (!tse(f.get('redirect_uri') || '', c.redirect_uri)) return sendJson(res, 400, { error: 'invalid_grant' });
      const now = Math.floor(Date.now() / 1000);
      const idt = { iss: CFG.issuer, aud: CFG.clientId, iat: now, exp: now + CFG.tokenTtlS, ...c.claims };
      if (c.nonce) idt.nonce = c.nonce;
      const access = crypto.randomBytes(24).toString('base64url');
      tokens.set(access, { claims: c.claims, exp: Date.now() + CFG.tokenTtlS * 1000 });
      return sendJson(res, 200, { access_token: access, token_type: 'Bearer', expires_in: CFG.tokenTtlS, id_token: signJwt(idt) });
    });
  }

  if (p === '/oidc/userinfo' && (req.method === 'GET' || req.method === 'POST')) {
    const h = req.headers.authorization || '';
    const t = h.startsWith('Bearer ') ? tokens.get(h.slice(7)) : null;
    if (!t || t.exp < Date.now()) return sendJson(res, 401, { error: 'invalid_token' });
    return sendJson(res, 200, t.claims);
  }

  return sendJson(res, 404, { error: 'not_found' });
}

// ---- transparent proxy --------------------------------------------------------
const UP = new URL(CFG.upstream);
function proxy(req, res) {
  const opt = {
    host: UP.hostname, port: UP.port, method: req.method, path: req.url,
    headers: { ...req.headers },
  };
  const pr = http.request(opt, (ur) => {
    res.writeHead(ur.statusCode, ur.headers);
    ur.pipe(res);
  });
  pr.on('error', (e) => {
    log('proxy error', e.code || e.message);
    if (!res.headersSent) res.writeHead(502, { 'content-type': 'text/plain' });
    res.end('upstream unavailable');
  });
  req.pipe(pr);
}

const server = http.createServer((req, res) => {
  let u;
  try { u = new URL(req.url, `http://${CFG.challengeHost}`); } catch { res.writeHead(400); return res.end(); }
  if (u.pathname === '/oidc/health') return sendJson(res, 200, { ok: true, issuer: CFG.issuer, keyring: fs.existsSync(CFG.keyring) });
  if (u.pathname.startsWith('/oidc/')) { handleOidc(req, res, u).catch((e) => { log('oidc error', e.message); if (!res.headersSent) sendJson(res, 500, { error: 'internal' }); }); return; }
  proxy(req, res);
});
// websockets (action cable etc.) pass straight through
server.on('upgrade', (req, socket, head) => {
  const pr = http.request({ host: UP.hostname, port: UP.port, method: req.method, path: req.url, headers: req.headers });
  pr.on('upgrade', (ur, upSocket, upHead) => {
    const lines = [`HTTP/1.1 101 Switching Protocols`];
    for (const [k, v] of Object.entries(ur.headers)) lines.push(`${k}: ${v}`);
    socket.write(lines.join('\r\n') + '\r\n\r\n');
    if (upHead && upHead.length) socket.write(upHead);
    if (head && head.length) upSocket.write(head);
    upSocket.pipe(socket); socket.pipe(upSocket);
  });
  pr.on('error', () => socket.destroy());
  pr.end(head);
});

server.listen(CFG.listenPort, CFG.listenHost, () => {
  log(`listening on ${CFG.listenHost}:${CFG.listenPort} -> ${CFG.upstream}; issuer ${CFG.issuer}; keyring ${CFG.keyring}`);
});
