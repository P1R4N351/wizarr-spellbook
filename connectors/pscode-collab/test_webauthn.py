"""Tests for the hardware-key door.

THE AUTHENTICATOR IN HERE IS NOT THE CODE UNDER TEST. It is built straight
from the WebAuthn structures -- authenticatorData laid out byte by byte, a
COSE key encoded with the library's own CBOR encoder, signatures produced by
`cryptography` -- so a credential that verifies here verified against an
independently constructed one, not against our own idea of what we emit. The
alternative (asking our code to produce the thing our code then checks) is the
test that encodes the bug it was written to catch.

What is genuinely OURS, and therefore what these tests are really for:
  * the signature counter check, which python-fido2 does not perform,
  * the exact-origin allowlist, which is stricter than the library default,
  * single-use, expiring enrollment tickets,
  * bounded, expiring in-memory tables,
  * the SHAPE of a browser's answer, held before the library is shown it:
    python-fido2 makes N bytes of a JSON number N where base64url belongs,
  * the empty-workspace path, where authentication succeeds and there is
    still nowhere to send the visitor.
"""
import dataclasses
import json
import os
import struct
import sys
import tempfile
import time
import typing
import unittest
from hashlib import sha256
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2 import cbor
from fido2 import webauthn as fido_webauthn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import broker
import webauthn_auth as wa

RP_ID = "door.example.test"
ORIGIN = "https://door.example.test:8450"
SIBLING = "https://door.example.test:8443"      # same host, different service
AAGUID = b"\x00" * 16

# Stands in for the 268435456 of a real attempt. The check under test is on
# the TYPE, so the size proves nothing extra -- and if the check is ever
# broken, a red run of these tests should cost megabytes, not the machine.
BIG = 1 << 20
NOT_TEXT = (BIG, [1], True, {"a": 1})


def library_spy(case):
    """Record every answer python-fido2 is shown, for the life of one test.

    Wraps the two doors through which a browser's answer reaches the library's
    parser -- AuthenticationResponse.from_dict and RegistrationResponse
    .from_dict; Fido2Server's *_complete calls go through the same two -- and
    DELEGATES, so an answer that is text still verifies and each test can show
    that the spy sees.
    """
    assert hasattr(case, "addCleanup"), "a test case to hang the un-patching on"
    seen = []
    for cls in (fido_webauthn.AuthenticationResponse,
                fido_webauthn.RegistrationResponse):
        assert callable(cls.from_dict), "the library's parser moved"

        def shown(data, _real=cls.from_dict, _name=cls.__name__):
            seen.append(_name)
            return _real(data)
        patcher = mock.patch.object(cls, "from_dict", side_effect=shown)
        patcher.start()
        case.addCleanup(patcher.stop)
    return seen


def bytes_members(cls):
    """JSON names of the members python-fido2 declares as bytes on `cls`.

    Those are the members it will build with bytes(value) when value is not
    text. Read from the library's own dataclasses, so the answer is about the
    library that is installed and not about our memory of it.
    """
    assert dataclasses.is_dataclass(cls), "not one of the library's answers"
    hints = typing.get_type_hints(cls)
    assert hints, "the library's answer declares no members to read"
    found = set()
    for field in dataclasses.fields(cls):
        kinds = [k for k in typing.get_args(hints[field.name])
                 if k is not type(None)] or [hints[field.name]]
        if any(isinstance(k, type) and issubclass(k, bytes) for k in kinds):
            found.add(cls._get_field_key(field))
    return found


def b64u(raw):
    import base64
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class SoftKey:
    """A minimal FIDO2 authenticator, assembled from the spec."""

    def __init__(self, rp_id=RP_ID, cred_id=b"credential-0001"):
        self.rp_id = rp_id
        self.cred_id = cred_id
        self.priv = ec.generate_private_key(ec.SECP256R1())
        self.counter = 0

    def _cose(self):
        nums = self.priv.public_key().public_numbers()
        return cbor.encode({1: 2, 3: -7, -1: 1,
                            -2: nums.x.to_bytes(32, "big"),
                            -3: nums.y.to_bytes(32, "big")})

    def _auth_data(self, flags, attested=False):
        data = sha256(self.rp_id.encode()).digest()
        data += bytes([flags]) + struct.pack(">I", self.counter)
        if attested:
            data += AAGUID + struct.pack(">H", len(self.cred_id))
            data += self.cred_id + self._cose()
        return data

    @staticmethod
    def _client_data(kind, challenge, origin):
        return json.dumps({"type": kind, "challenge": challenge,
                           "origin": origin, "crossOrigin": False},
                          separators=(",", ":")).encode()

    def register(self, options, origin=ORIGIN):
        challenge = wa.jsonable(dict(options))["publicKey"]["challenge"]
        client_data = self._client_data("webauthn.create", challenge, origin)
        auth_data = self._auth_data(0x45, attested=True)   # UP | UV | AT
        attestation = cbor.encode({"fmt": "none", "attStmt": {},
                                   "authData": auth_data})
        return {"id": b64u(self.cred_id), "rawId": b64u(self.cred_id),
                "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64u(client_data),
                             "attestationObject": b64u(attestation)}}

    def assert_(self, options, origin=ORIGIN, bump=True, counter=None):
        if bump:
            self.counter += 1
        if counter is not None:
            self.counter = counter
        challenge = wa.jsonable(dict(options))["publicKey"]["challenge"]
        client_data = self._client_data("webauthn.get", challenge, origin)
        auth_data = self._auth_data(0x05)                  # UP | UV
        signature = self.priv.sign(auth_data + sha256(client_data).digest(),
                                   ec.ECDSA(hashes.SHA256()))
        return {"id": b64u(self.cred_id), "rawId": b64u(self.cred_id),
                "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64u(client_data),
                             "authenticatorData": b64u(auth_data),
                             "signature": b64u(signature), "userHandle": None}}


class DoorTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.creds = wa.CredStore(os.path.join(self.dir, "webauthn.json"))
        self.server = wa.build_server(RP_ID, "Test House", (ORIGIN,))
        self.key = SoftKey()

    def _enrol(self, user="alice", key=None):
        key = key or self.key
        options, ceremony = wa.registration_options(
            self.server, self.creds, user, user)
        result = wa.finish_registration(self.server, ceremony,
                                        key.register(options))
        self.creds.add_credential(user, result.credential_data, "test key",
                                  result.is_user_verified())
        return result

    # ---- the full round trip, end to end -------------------------------

    def test_register_then_authenticate(self):
        self._enrol()
        options, ceremony = wa.authentication_options(self.server, self.creds)
        who = wa.finish_authentication(self.server, self.creds, ceremony,
                                       self.key.assert_(options))
        self.assertEqual(who, "alice")

    def test_usernameless_begin_sends_no_allow_list(self):
        """A resident credential must not need the name typed first."""
        self._enrol()
        options, _ = wa.authentication_options(self.server, self.creds)
        pk = wa.jsonable(dict(options))["publicKey"]
        self.assertFalse(pk.get("allowCredentials"))

    # ---- the counter: ours, and the only clone detection there is ------

    def test_counter_advances_and_is_recorded(self):
        self._enrol()
        for _ in range(3):
            options, ceremony = wa.authentication_options(self.server, self.creds)
            wa.finish_authentication(self.server, self.creds, ceremony,
                                     self.key.assert_(options))
        with open(self.creds._path, encoding="utf-8") as fh:
            stored = json.load(fh)["users"]["alice"]["credentials"][0]
        self.assertEqual(stored["sign_count"], 3)

    def test_counter_regression_is_refused(self):
        """A replayed-forward key is exactly what a clone looks like."""
        self._enrol()
        options, ceremony = wa.authentication_options(self.server, self.creds)
        wa.finish_authentication(self.server, self.creds, ceremony,
                                 self.key.assert_(options, counter=9))
        options, ceremony = wa.authentication_options(self.server, self.creds)
        clone = self.key.assert_(options, bump=False, counter=4)
        with self.assertRaises(ValueError) as caught:
            wa.finish_authentication(self.server, self.creds, ceremony, clone)
        self.assertIn("cloned", str(caught.exception))

    def test_counter_of_zero_is_allowed_forever(self):
        """Authenticators without a counter report 0 and are not clones."""
        self._enrol()
        for _ in range(2):
            options, ceremony = wa.authentication_options(self.server, self.creds)
            wa.finish_authentication(self.server, self.creds, ceremony,
                                     self.key.assert_(options, bump=False,
                                                      counter=0))

    # ---- origin and RP scoping -----------------------------------------

    def test_sibling_service_on_same_host_is_refused(self):
        """The RP ID cannot separate ports; the origin allowlist must."""
        self._enrol()
        options, ceremony = wa.authentication_options(self.server, self.creds)
        with self.assertRaises(ValueError):
            wa.finish_authentication(self.server, self.creds, ceremony,
                                     self.key.assert_(options, origin=SIBLING))

    def test_key_bound_to_another_rp_is_refused(self):
        other = SoftKey(rp_id="elsewhere.example.test", cred_id=b"other-0001")
        options, ceremony = wa.registration_options(
            self.server, self.creds, "alice", "alice")
        with self.assertRaises(ValueError):
            self.server.register_complete(ceremony, other.register(options))

    def test_unenrolled_key_is_refused(self):
        self._enrol()
        stranger = SoftKey(cred_id=b"stranger-0001")
        options, ceremony = wa.authentication_options(self.server, self.creds)
        with self.assertRaises(ValueError):
            wa.finish_authentication(self.server, self.creds, ceremony,
                                     stranger.assert_(options))

    def test_tampered_signature_is_refused(self):
        self._enrol()
        options, ceremony = wa.authentication_options(self.server, self.creds)
        response = self.key.assert_(options)
        good = response["response"]["signature"]
        response["response"]["signature"] = ("A" if good[0] != "A" else "B") + good[1:]
        with self.assertRaises(ValueError):
            wa.finish_authentication(self.server, self.creds, ceremony, response)

    # ---- the shape of an answer: held BEFORE the library sees it --------

    def test_what_is_not_text_never_reaches_the_library(self):
        """A stranger's number must not become that many bytes.

        Every answer here is refused, and refused by US: the spy on the
        library's parser stays empty, and so does the one on the store lookup
        that used to base64 whatever the parser had allocated.
        """
        self._enrol()
        seen, looked_up = library_spy(self), []
        real = self.creds.owner_of
        self.creds.owner_of = lambda cid: looked_up.append(len(cid)) or real(cid)
        options, _ = wa.authentication_options(self.server, self.creds)
        good = self.key.assert_(options)
        # (`rawId` left out of the rows about `id`: an answer whose two names
        # differ is refused for THAT, and would hide what `id` alone does)
        bare = {k: v for k, v in good.items() if k != "rawId"}
        deep = []
        for _ in range(3000):
            deep = [deep]
        shapes = [dict(bare, id=BIG), dict(bare, id=5), dict(bare, id=[1, 2]),
                  dict(bare, id=True), dict(bare, id=None), dict(bare, id=""),
                  dict(bare, id={"a": 1}),
                  dict(bare, id="A" * (wa.MAX_ID_TEXT + 1)),
                  dict(good, id=deep, rawId=deep), dict(good, rawId=deep),
                  dict(good, rawId=BIG), dict(good, response=None),
                  dict(good, response="x"), dict(good, response=[1]),
                  dict(good, response=BIG), {}, {"id": 7}, [], "x", BIG, None]
        shapes += [dict(good, response=dict(good["response"], **{part: value}))
                   for part in ("clientDataJSON", "authenticatorData",
                                "signature", "userHandle", "credentialId")
                   for value in NOT_TEXT]
        for index, body in enumerate(shapes):
            _, ceremony = wa.authentication_options(self.server, self.creds)
            with self.subTest(index=index), self.assertRaises(ValueError):
                wa.finish_authentication(self.server, self.creds, ceremony,
                                         body)
        # 41 answers; the library was shown none and the store was asked none
        self.assertEqual((len(shapes), seen, looked_up), (41, [], []))
        # (the spy can see: an answer that IS text is shown to the library,
        # twice -- once to be read here, once by the library to be verified)
        options, ceremony = wa.authentication_options(self.server, self.creds)
        who = wa.finish_authentication(self.server, self.creds, ceremony,
                                       self.key.assert_(options))
        self.assertEqual((who, seen, looked_up),
                         ("alice", ["AuthenticationResponse"] * 2,
                          [len(self.key.cred_id)]))

    def test_registration_that_is_not_text_never_reaches_the_library(self):
        """The same lever on the enrolment answer (it needs a live ticket)."""
        seen = library_spy(self)
        options, _ = wa.registration_options(self.server, self.creds, "alice",
                                             "alice")
        good = self.key.register(options)
        bare = {k: v for k, v in good.items() if k != "rawId"}
        shapes = [dict(bare, id=BIG), dict(bare, id=[1, 2]),
                  dict(bare, id=True), dict(bare, id=None),
                  dict(good, rawId=BIG), dict(good, response=None),
                  dict(good, response=[1]), {}, []]
        shapes += [dict(good, response=dict(good["response"], **{part: value}))
                   for part in ("clientDataJSON", "attestationObject")
                   for value in NOT_TEXT]
        for index, body in enumerate(shapes):
            _, ceremony = wa.registration_options(self.server, self.creds,
                                                  "alice", "alice")
            with self.subTest(index=index), self.assertRaises(ValueError):
                wa.finish_registration(self.server, ceremony, body)
        self.assertEqual((len(shapes), seen), (17, []))
        # (the spy can see: a registration that IS text reaches the library)
        options, ceremony = wa.registration_options(self.server, self.creds,
                                                    "alice", "alice")
        made = wa.finish_registration(self.server, ceremony,
                                      self.key.register(options))
        self.assertEqual((made.credential_data.credential_id, seen),
                         (self.key.cred_id, ["RegistrationResponse"]))

    def test_an_answer_names_one_credential(self):
        """`id` and `rawId` are the same bytes; the library reads only `id`."""
        self._enrol()
        seen = library_spy(self)
        options, ceremony = wa.authentication_options(self.server, self.creds)
        two = dict(self.key.assert_(options), rawId=b64u(b"another-credential"))
        with self.assertRaises(ValueError):
            wa.finish_authentication(self.server, self.creds, ceremony, two)
        self.assertEqual(seen, [])
        # ...refused for the two names alone: the same answer with the one
        # name, and with rawId left out altogether, is accepted
        for drop in ((), ("rawId",)):
            options, ceremony = wa.authentication_options(self.server,
                                                          self.creds)
            one = {k: v for k, v in self.key.assert_(options).items()
                   if k not in drop}
            self.assertEqual(wa.finish_authentication(
                self.server, self.creds, ceremony, one), "alice")

    def test_every_member_the_library_makes_bytes_of_is_held_to_text(self):
        """ANSWER_TEXTS is a list ABOUT python-fido2, so it is checked against it.

        The list was first written from the WebAuthn field names and missed
        `credentialId`: no browser sends it, and python-fido2 1.2 decodes it
        all the same. If a later library decodes another member, this fails
        and names it -- before an answer can carry a number there.
        """
        nested = (bytes_members(fido_webauthn.AuthenticatorAssertionResponse)
                  | bytes_members(fido_webauthn.AuthenticatorAttestationResponse))
        self.assertGreaterEqual(len(nested), 6, "the reader found too little")
        self.assertEqual(nested - set(wa.ANSWER_TEXTS), set())
        # ...and at the top level it decodes `id` alone, which answer_of
        # bounds by name (a `rawId` the library began to decode would show here)
        for cls in (fido_webauthn.AuthenticationResponse,
                    fido_webauthn.RegistrationResponse):
            self.assertEqual(bytes_members(cls), {"id"}, cls.__name__)

    # ---- enrollment tickets --------------------------------------------

    def test_ticket_is_single_use(self):
        token = self.creds.mint_ticket("alice", "yubikey")
        self.assertEqual(self.creds.spend_ticket(token), ("alice", "yubikey"))
        self.assertIsNone(self.creds.spend_ticket(token))

    def test_ticket_expires(self):
        now = time.time()
        token = self.creds.mint_ticket("alice", "yubikey", now=now)
        self.assertIsNone(self.creds.peek_ticket(
            token, now=now + wa.TICKET_TTL + 1))

    def test_peek_does_not_spend(self):
        token = self.creds.mint_ticket("alice", "yubikey")
        self.creds.peek_ticket(token)
        self.assertIsNotNone(self.creds.spend_ticket(token))

    def test_duplicate_key_is_refused(self):
        self._enrol()
        options, ceremony = wa.registration_options(
            self.server, self.creds, "alice", "alice")
        result = self.server.register_complete(ceremony,
                                               self.key.register(options))
        with self.assertRaises(ValueError):
            self.creds.add_credential("alice", result.credential_data, "again",
                                      True)

    def test_key_cap_per_user(self):
        for index in range(wa.MAX_CREDS_PER_USER):
            self._enrol(key=SoftKey(cred_id=b"cred-%04d" % index))
        with self.assertRaises(ValueError):
            self._enrol(key=SoftKey(cred_id=b"cred-over"))

    # ---- bounded, expiring tables --------------------------------------

    def test_pending_handle_is_consumed_once(self):
        pending = wa.Pending()
        handle = pending.put("ceremony")
        self.assertEqual(pending.take(handle), "ceremony")
        self.assertIsNone(pending.take(handle))

    def test_pending_expires(self):
        pending = wa.Pending(ttl=10)
        now = time.time()
        handle = pending.put("ceremony", now=now)
        self.assertIsNone(pending.take(handle, now=now + 11))

    def test_pending_is_capped(self):
        pending = wa.Pending(cap=4)
        handles = [pending.put(index) for index in range(10)]
        live = sum(1 for h in handles if pending.take(h) is not None)
        self.assertLessEqual(live, 4)

    def test_sessions_expire_and_are_capped(self):
        sessions = wa.Sessions(ttl=10, cap=3)
        now = time.time()
        token = sessions.open("alice", now=now)
        self.assertEqual(sessions.who(token, now=now), "alice")
        self.assertIsNone(sessions.who(token, now=now + 11))
        tokens = [sessions.open("u%d" % i) for i in range(6)]
        self.assertLessEqual(sum(1 for t in tokens if sessions.who(t)), 3)

    def test_throttle_blocks_only_after_repeated_failures(self):
        throttle = wa.Throttle(window=60, limit=3)
        self.assertFalse(throttle.blocked("1.2.3.4"))
        for _ in range(3):
            throttle.fail("1.2.3.4")
        self.assertTrue(throttle.blocked("1.2.3.4"))
        self.assertFalse(throttle.blocked("5.6.7.8"))
        throttle.clear("1.2.3.4")
        self.assertFalse(throttle.blocked("1.2.3.4"))

    def test_throttle_window_lapses(self):
        throttle = wa.Throttle(window=60, limit=2)
        now = time.time()
        throttle.fail("1.2.3.4", now=now)
        throttle.fail("1.2.3.4", now=now)
        self.assertTrue(throttle.blocked("1.2.3.4", now=now))
        self.assertFalse(throttle.blocked("1.2.3.4", now=now + 61))

    # ---- store hygiene --------------------------------------------------

    def test_store_file_is_not_world_readable(self):
        self._enrol()
        mode = os.stat(self.creds._path).st_mode & 0o777
        self.assertEqual(mode, 0o640)

    def test_user_handle_is_not_derived_from_the_name(self):
        first = self.creds.ensure_user("alice")
        self.assertEqual(self.creds.ensure_user("alice"), first)
        self.assertNotIn(b"alice", first)

    def test_jsonable_encodes_bytes_and_enums(self):
        from fido2.webauthn import UserVerificationRequirement as UV
        out = wa.jsonable({"c": b"\x01\x02", "uv": UV.PREFERRED, "l": [b"\x03"]})
        self.assertEqual(out["c"], b64u(b"\x01\x02"))
        self.assertEqual(out["uv"], "preferred")
        self.assertEqual(out["l"], [b64u(b"\x03")])


class Routed(broker.Handler):
    """broker.Handler with the socket taken out: a JSON body in, an answer out.

    BaseHTTPRequestHandler's own __init__ reads a request off a socket, so it
    is not run. What a /finish route asks of the handler around it is supplied
    here instead, which leaves the route's own code -- the part under test --
    exactly as it is served.
    """

    def __init__(self, body):
        self._body, self.sent = body, None

    def _read_json(self):
        return self._body

    def _source(self):
        return "192.0.2.7"

    def _json(self, status, payload, headers=None):
        self.sent = (status, payload)


class NoWorkspaces:
    """A record store in which nobody has been given a workspace yet."""

    def get(self, name):
        return None


class RouteTest(unittest.TestCase):
    """broker.py's two /finish routes go through the shape check.

    A check the routes do not call protects nothing, and the registration
    route used to hand the browser's answer to the library itself.
    """

    def setUp(self):
        directory = tempfile.mkdtemp()
        self.door = type("Door", (Routed,), {
            "store": NoWorkspaces(),
            "creds": wa.CredStore(os.path.join(directory, "webauthn.json")),
            "server_fido": wa.build_server(RP_ID, "Test House", (ORIGIN,)),
            "pending": wa.Pending(), "sessions": wa.Sessions(),
            "throttle": wa.Throttle(limit=1)})
        self.key = SoftKey()
        self.seen = library_spy(self)

    def _post(self, route, body):
        handler = self.door(body)
        getattr(handler, route)()
        return handler.sent

    def _register(self, change):
        """One enrolment through both routes; `change` may spoil the answer."""
        ticket = self.door.creds.mint_ticket("alice", "test key")
        _, asked = self._post("_register_begin", {"ticket": ticket})
        answer = self.key.register(asked)
        change(answer)
        return self._post("_register_finish", dict(
            answer, ticket=ticket, handle=asked["handle"]))

    def _sign_in(self, change):
        _, asked = self._post("_login_begin", {})
        answer = self.key.assert_(asked)
        change(answer)
        return self._post("_login_finish", dict(answer,
                                                handle=asked["handle"]))

    def test_register_finish_holds_the_answer_to_its_shape(self):
        status, _ = self._register(
            lambda answer: answer["response"].update(attestationObject=BIG))
        self.assertEqual((status, self.seen), (400, []))
        self.assertEqual(self.door.creds.credentials_for("alice"), [])
        self.assertTrue(self.door.throttle.blocked("192.0.2.7"),
                        "a refused shape is a counted failure")
        # (the route still enrols a key: the same route, an answer of text)
        status, payload = self._register(lambda answer: None)
        self.assertEqual((status, payload, self.seen),
                         (200, {"ok": True, "user": "alice"},
                          ["RegistrationResponse"]))

    def test_login_finish_holds_the_answer_to_its_shape(self):
        self._register(lambda answer: None)
        del self.seen[:]
        # (one name only, so the refusal is about `id` and not about `rawId`)
        status, _ = self._sign_in(
            lambda answer: (answer.pop("rawId"), answer.update(id=BIG)))
        self.assertEqual((status, self.seen), (401, []))
        self.assertTrue(self.door.throttle.blocked("192.0.2.7"),
                        "a refused shape is a counted failure")
        # (the route still opens for a key: the same route, an answer of text)
        status, payload = self._sign_in(lambda answer: None)
        self.assertEqual((status, payload["ok"], self.seen),
                         (200, True, ["AuthenticationResponse"] * 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
