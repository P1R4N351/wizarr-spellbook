"""Hardware-key sign-in for the collaborator broker.

The verification itself is Yubico's own python-fido2 (Debian python3-fido2).
Nothing here re-implements a signature check, a CBOR parse or a COSE key --
rolling those by hand on an internet-facing door would be the wrong kind of
brave. What this module owns is the glue the library deliberately leaves to
the application:

  * where credentials live and how they are written (atomically),
  * the challenge that must not outlive its ceremony,
  * the signature COUNTER, which Fido2Server does NOT check -- it verifies the
    signature and returns; a cloned authenticator is caught here or nowhere,
  * one-time enrollment tickets, so registering a key needs an authorisation
    that arrives out of band rather than a password on a public form,
  * a failure throttle, because this door answers the public internet through
    the media host's funnel,
  * the SHAPE of a browser's answer, held before the library is shown it (see
    answer_of): the library makes N bytes of a JSON number N.

Every in-memory table is bounded and every entry expires: an unbounded dict
keyed by anything an anonymous caller supplies is a memory-exhaustion lever.
"""
import base64
import json
import os
import secrets
import tempfile
import threading
import time

# ★ python-fido2 1.2 ships the modern WebAuthn JSON mapping switched OFF for
# backwards compatibility. With it off, from_dict() expects RAW BYTES and a
# browser-shaped response -- base64url strings, which is what
# PublicKeyCredential.toJSON() and every WebAuthn front end produces -- dies
# with "string argument without an encoding". The flag is global and must be
# set before fido2.webauthn is imported, so it goes here, above the imports,
# and not in main().
from fido2.features import webauthn_json_mapping

webauthn_json_mapping.enabled = True

from fido2.server import Fido2Server                        # noqa: E402
from fido2.webauthn import (AttestedCredentialData, AuthenticationResponse,  # noqa: E402
                            PublicKeyCredentialRpEntity,
                            PublicKeyCredentialUserEntity,
                            ResidentKeyRequirement, UserVerificationRequirement)

MAX_USERS = 32              # rule 2: every table below is bounded
MAX_CREDS_PER_USER = 8
MAX_TICKETS = 16
MAX_PENDING = 64
MAX_SESSIONS = 128
MAX_THROTTLE = 256

CHALLENGE_TTL = 300         # seconds a ceremony may stay open
SESSION_TTL = 12 * 3600
TICKET_TTL = 30 * 60
THROTTLE_WINDOW = 300
THROTTLE_MAX_FAILS = 8

MAX_ID_TEXT = 1400          # a credential id is at most 1023 bytes (WebAuthn's
                            # own bound): 1364 characters of base64url

# Every member of an answer's `response` that python-fido2 1.2 decodes to
# bytes: the assertion's five and the attestation's two (clientDataJSON is in
# both). test_webauthn.py reads the same list out of the library's own
# dataclasses and fails if the library decodes a member that is not named
# here -- which is how `credentialId` came to be on it: no browser sends that
# member, and the library decodes it all the same.
ANSWER_TEXTS = ("clientDataJSON", "authenticatorData", "signature",
                "userHandle", "credentialId", "attestationObject")
_NOT_A_KEYS = "that answer did not come from a security key"


def b64u(raw):
    """URL-safe base64 without padding -- the encoding WebAuthn uses."""
    assert isinstance(raw, (bytes, bytearray)), "b64u encodes bytes"
    return base64.urlsafe_b64encode(bytes(raw)).decode("ascii").rstrip("=")


def unb64u(text):
    assert isinstance(text, str), "unb64u decodes str"
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jsonable(value):
    """Make Fido2Server's options JSON-transportable.

    The options carry raw bytes (challenge, credential ids) and enum members,
    neither of which json.dumps will touch.

    Iterative, with an explicit stack, because P10 rule 1 forbids recursion --
    and the reason the rule exists applies here: this walks a structure that
    arrives from a library, and a recursive walk of attacker-influenced depth
    is a stack-exhaustion lever rather than a merely inelegant one. Both the
    depth and the total node count are bounded.
    """
    root = {"v": value}
    stack = [(root, "v", value, 0)]
    steps = 0
    while stack:                                  # rule 2: bounded by `steps`
        steps += 1
        assert steps < 4096, "options structure larger than any real option set"
        parent, key, item, depth = stack.pop()
        assert depth < 12, "options nested deeper than any real option set"
        if isinstance(item, (bytes, bytearray)):
            parent[key] = b64u(bytes(item))
        elif isinstance(item, dict) or hasattr(item, "items"):
            out = {}
            parent[key] = out
            for k, v in item.items():
                out[str(k)] = None
                stack.append((out, str(k), v, depth + 1))
        elif isinstance(item, (list, tuple)):
            out = [None] * len(item)
            parent[key] = out
            for index, v in enumerate(item):
                stack.append((out, index, v, depth + 1))
        elif hasattr(item, "value") and not isinstance(item, (str, int, float,
                                                              bool)):
            stack.append((parent, key, item.value, depth + 1))   # enum member
        else:
            parent[key] = item
    return root["v"]


class CredStore:
    """Credentials and enrollment tickets, one atomically-replaced JSON file.

    Written by the broker service user, so it lives beside state.json under
    the unit's single ReadWritePaths. It holds PUBLIC keys and credential ids
    only -- there is no secret in this file, which is why 0640 is enough.
    """

    def __init__(self, path):
        assert path, "store path required"
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def _read(self):
        if not os.path.exists(self._path):
            return {"users": {}, "tickets": {}}
        with open(self._path, encoding="utf-8") as fh:
            data = json.load(fh)
        assert isinstance(data, dict), "credential store must be an object"
        data.setdefault("users", {})
        data.setdefault("tickets", {})
        assert len(data["users"]) <= MAX_USERS, "credential store over user cap"
        return data

    def _write(self, data):
        assert len(data.get("users", {})) <= MAX_USERS, "refusing to exceed user cap"
        assert len(data.get("tickets", {})) <= MAX_TICKETS, "refusing to exceed ticket cap"
        directory = os.path.dirname(self._path)
        fd, tmp = tempfile.mkstemp(dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(tmp, 0o640)
        os.replace(tmp, self._path)

    def user_ids(self):
        """Every (username, user_id_bytes) pair, for usernameless sign-in."""
        with self._lock:
            data = self._read()
        return [(name, unb64u(rec["user_id"]))
                for name, rec in data["users"].items()]

    def credentials_for(self, username):
        """AttestedCredentialData for one user, or [] when unknown."""
        with self._lock:
            data = self._read()
        rec = data["users"].get(username)
        if not rec:
            return []
        return [AttestedCredentialData(unb64u(c["cred"]))
                for c in rec.get("credentials", [])]

    def all_credentials(self):
        """(username, AttestedCredentialData) for every enrolled key."""
        with self._lock:
            data = self._read()
        out = []
        for name, rec in data["users"].items():
            for cred in rec.get("credentials", [])[:MAX_CREDS_PER_USER]:
                out.append((name, AttestedCredentialData(unb64u(cred["cred"]))))
        return out

    def ensure_user(self, username):
        """Return the user's stable WebAuthn handle, creating it once.

        The handle is random rather than derived from the name: it is echoed
        back by the authenticator as userHandle, and a derivable handle would
        leak the account name to anyone who can read the credential.
        """
        assert username, "username required"
        with self._lock:
            data = self._read()
            rec = data["users"].get(username)
            if rec is None:
                assert len(data["users"]) < MAX_USERS, "user cap reached"
                rec = {"user_id": b64u(secrets.token_bytes(16)),
                       "credentials": []}
                data["users"][username] = rec
                self._write(data)
            return unb64u(rec["user_id"])

    def add_credential(self, username, cred_data, label, user_verified):
        """Persist one freshly registered key."""
        assert username and label, "username and label required"
        raw = bytes(cred_data)
        with self._lock:
            data = self._read()
            rec = data["users"].setdefault(
                username, {"user_id": b64u(secrets.token_bytes(16)),
                           "credentials": []})
            creds = rec.setdefault("credentials", [])
            if len(creds) >= MAX_CREDS_PER_USER:
                raise ValueError("this account already holds the maximum "
                                 "number of keys")
            cred_id = b64u(cred_data.credential_id)
            if any(c["id"] == cred_id for c in creds):
                raise ValueError("that key is already enrolled here")
            creds.append({"id": cred_id, "cred": b64u(raw), "label": label,
                          "added_at": int(time.time()), "sign_count": 0,
                          "last_used": None, "user_verified": bool(user_verified)})
            self._write(data)

    def owner_of(self, credential_id):
        """Which account holds this credential id, if any."""
        assert credential_id, "credential id required"
        wanted = b64u(credential_id)
        with self._lock:
            data = self._read()
        for name, rec in data["users"].items():
            for cred in rec.get("credentials", []):
                if cred["id"] == wanted:
                    return name
        return None

    def note_use(self, credential_id, new_count):
        """Advance the signature counter, refusing a regression.

        A FIDO authenticator that reports counters increments this on every
        assertion. A value at or below the stored one means the assertion came
        from a copy of the key -- the one thing a signature check cannot see.
        Authenticators that do not implement the counter report 0 forever, and
        that case is explicitly allowed rather than silently treated as a
        clone.
        """
        assert credential_id, "credential id required"
        assert new_count >= 0, "counter cannot be negative"
        wanted = b64u(credential_id)
        with self._lock:
            data = self._read()
            for rec in data["users"].values():
                for cred in rec.get("credentials", []):
                    if cred["id"] != wanted:
                        continue
                    stored = int(cred.get("sign_count", 0))
                    if not (stored == 0 and new_count == 0) and new_count <= stored:
                        raise ValueError(
                            "signature counter went backwards (%d after %d): "
                            "this key may have been cloned" % (new_count, stored))
                    cred["sign_count"] = new_count
                    cred["last_used"] = int(time.time())
                    self._write(data)
                    return
        raise ValueError("unknown credential")

    def mint_ticket(self, username, label, now=None):
        """One-time authorisation to enrol a key for `username`."""
        assert username and label, "username and label required"
        now = int(now if now is not None else time.time())
        token = secrets.token_urlsafe(24)
        with self._lock:
            data = self._read()
            live = {t: v for t, v in data["tickets"].items()
                    if v.get("expires", 0) > now}
            if len(live) >= MAX_TICKETS:
                raise ValueError("too many enrollment tickets are open")
            live[token] = {"user": username, "label": label,
                           "expires": now + TICKET_TTL}
            data["tickets"] = live
            self._write(data)
        return token

    def peek_ticket(self, token, now=None):
        """The ticket's (user, label) if it is live, else None. Does not spend."""
        if not token:
            return None
        now = int(now if now is not None else time.time())
        with self._lock:
            data = self._read()
        rec = data["tickets"].get(token)
        if not rec or rec.get("expires", 0) <= now:
            return None
        return rec["user"], rec["label"]

    def spend_ticket(self, token, now=None):
        """Redeem a ticket exactly once. Returns (user, label) or None."""
        if not token:
            return None
        now = int(now if now is not None else time.time())
        with self._lock:
            data = self._read()
            rec = data["tickets"].get(token)
            if not rec or rec.get("expires", 0) <= now:
                return None
            data["tickets"] = {t: v for t, v in data["tickets"].items()
                               if t != token and v.get("expires", 0) > now}
            self._write(data)
            return rec["user"], rec["label"]


class Pending:
    """Open ceremonies, keyed by a handle the browser echoes back.

    Bounded and expiring: the handle is minted here, but /begin is reachable
    without any credential, so an attacker controls how often entries are
    created. Oldest-first eviction keeps that from becoming a memory lever.
    """

    def __init__(self, ttl=CHALLENGE_TTL, cap=MAX_PENDING):
        self._items = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._cap = cap

    def put(self, value, now=None):
        now = now if now is not None else time.time()
        handle = secrets.token_urlsafe(18)
        with self._lock:
            self._prune(now)
            if len(self._items) >= self._cap:
                oldest = min(self._items, key=lambda k: self._items[k][1])
                del self._items[oldest]
            self._items[handle] = (value, now + self._ttl)
        return handle

    def take(self, handle, now=None):
        """Fetch and consume. A ceremony handle is never valid twice."""
        now = now if now is not None else time.time()
        if not handle:
            return None
        with self._lock:
            self._prune(now)
            item = self._items.pop(handle, None)
        return item[0] if item else None

    def _prune(self, now):
        for key in [k for k, v in self._items.items() if v[1] <= now]:
            del self._items[key]


class Sessions:
    """Signed-in browsers. Same bounding rules as Pending."""

    def __init__(self, ttl=SESSION_TTL, cap=MAX_SESSIONS):
        self._items = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._cap = cap

    def open(self, username, now=None):
        assert username, "a session must name its user"
        now = now if now is not None else time.time()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune(now)
            if len(self._items) >= self._cap:
                oldest = min(self._items, key=lambda k: self._items[k][1])
                del self._items[oldest]
            self._items[token] = (username, now + self._ttl)
        return token

    def who(self, token, now=None):
        now = now if now is not None else time.time()
        if not token:
            return None
        with self._lock:
            self._prune(now)
            item = self._items.get(token)
        return item[0] if item else None

    def _prune(self, now):
        for key in [k for k, v in self._items.items() if v[1] <= now]:
            del self._items[key]


class Throttle:
    """Per-source failure counter with a fixed window.

    Not a rate limiter for the whole door -- a deliberate, narrow one: it
    counts FAILURES only, so a working key or password is never slowed down.
    """

    def __init__(self, window=THROTTLE_WINDOW, limit=THROTTLE_MAX_FAILS,
                 cap=MAX_THROTTLE):
        self._items = {}
        self._lock = threading.Lock()
        self._window = window
        self._limit = limit
        self._cap = cap

    def blocked(self, source, now=None):
        now = now if now is not None else time.time()
        with self._lock:
            self._prune(now)
            count, _ = self._items.get(source, (0, now))
            return count >= self._limit

    def fail(self, source, now=None):
        now = now if now is not None else time.time()
        with self._lock:
            self._prune(now)
            if source not in self._items and len(self._items) >= self._cap:
                oldest = min(self._items, key=lambda k: self._items[k][1])
                del self._items[oldest]
            count, _ = self._items.get(source, (0, now))
            self._items[source] = (count + 1, now + self._window)

    def clear(self, source):
        with self._lock:
            self._items.pop(source, None)

    def _prune(self, now):
        for key in [k for k, v in self._items.items() if v[1] <= now]:
            del self._items[key]


def build_server(rp_id, rp_name, origins):
    """A Fido2Server pinned to one RP ID and an explicit origin allowlist.

    The allowlist is exact-match on purpose. python-fido2's default origin
    check accepts any origin whose host matches the RP ID, which would admit
    a different PORT on the same host -- and on this tailnet one host serves
    several unrelated services on different ports.
    """
    assert rp_id, "an RP ID is required"
    assert origins, "at least one allowed origin is required"
    allowed = frozenset(origins)
    return Fido2Server(PublicKeyCredentialRpEntity(id=rp_id, name=rp_name),
                       verify_origin=lambda origin: origin in allowed)


def registration_options(server, store, username, display_name):
    """Begin enrollment. Resident key so later sign-ins need no username."""
    assert username, "username required"
    user = PublicKeyCredentialUserEntity(
        name=username, id=store.ensure_user(username),
        display_name=display_name or username)
    return server.register_begin(
        user, credentials=store.credentials_for(username),
        resident_key_requirement=ResidentKeyRequirement.REQUIRED,
        user_verification=UserVerificationRequirement.PREFERRED)


def authentication_options(server, store, username=None):
    """Begin sign-in.

    With no username the ceremony is usernameless: allowCredentials is left
    empty and the key itself supplies the identity from its resident
    credential. That is why registration insists on a resident key.
    """
    creds = store.credentials_for(username) if username else None
    if username and not creds:
        raise ValueError("no key is enrolled for that account")
    return server.authenticate_begin(
        credentials=creds,
        user_verification=UserVerificationRequirement.PREFERRED)


def answer_of(body):
    """A browser's answer, HELD TO ITS SHAPE before python-fido2 is shown it.

    ★ python-fido2 1.2 turns a value that is NOT text, where base64url text
    belongs, into bytes with the type's own constructor: a JSON number N in
    `id` or `signature` becomes N zero bytes, a list of numbers that many. It
    does so while the answer is only being PARSED -- before a challenge, an
    origin or a signature has been looked at -- and a sign-in answer needs
    nothing but a handle from the open /webauthn/login/begin. A stranger with
    no key and no account chose how much this process allocated, and
    broker._MAX_JSON never saw it: that bounds the body, not the number
    written in it.

    So, before the library is shown anything: the credential's name is
    bounded text, `response` is an object, every member the library decodes
    is text or absent, and the answer names ONE credential (`id` and `rawId`
    are the same bytes; the library reads the first and ignores the second).

    A refusal is a ValueError on purpose: broker.py already answers one with
    a 4xx and a counted failure.
    """
    if not isinstance(body, dict):
        raise ValueError(_NOT_A_KEYS)
    cred_id, said = body.get("id"), body.get("response")
    if not (isinstance(cred_id, str) and 0 < len(cred_id) <= MAX_ID_TEXT
            and isinstance(said, dict)):
        raise ValueError(_NOT_A_KEYS)
    if body.get("rawId") is not None and body.get("rawId") != cred_id:
        raise ValueError("that answer names two different keys")
    for part in ANSWER_TEXTS:                     # rule 2: a fixed tuple
        if said.get(part) is not None and not isinstance(said.get(part), str):
            raise ValueError(_NOT_A_KEYS)
    return body


def finish_registration(server, state, response):
    """Verify a registration and return what the key attested.

    The enrolment twin of finish_authentication, and here for one reason:
    Fido2Server.register_complete parses the browser's answer itself, so the
    answer is held to its shape first. broker.py calls THIS and never
    register_complete directly.
    """
    assert server is not None, "a WebAuthn server is required"
    assert state is not None, "no ceremony was open for this handle"
    return server.register_complete(state, answer_of(response))


def finish_authentication(server, store, state, response):
    """Verify an assertion and return the account it proves.

    Order matters: the answer is held to its shape before the library parses
    it (answer_of), the signature is checked next (by the library), and only
    an assertion that already verified is allowed to move the counter.
    """
    assert state is not None, "no ceremony was open for this handle"
    response = answer_of(response)
    parsed = AuthenticationResponse.from_dict(response)
    credential_id = parsed.id
    owner = store.owner_of(credential_id)
    if owner is None:
        raise ValueError("that key is not enrolled here")
    matched = server.authenticate_complete(
        state, store.credentials_for(owner), response)
    store.note_use(matched.credential_id,
                   parsed.response.authenticator_data.counter)
    return owner
