"""Tests for the parts of the door that are routing rather than crypto.

The empty-workspace case gets its own test because it is the state the live
deployment is actually in: a visitor can authenticate perfectly and still have
nowhere to be sent, and the difference between "returns None" and "raises" is
the difference between a page that explains itself and a 500.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import broker


class FakeStore:
    def __init__(self, records):
        self._records = records

    def get(self, name):
        return self._records.get(name)


class StripBaseTest(unittest.TestCase):

    def test_prefix_removed(self):
        self.assertEqual(broker.strip_base("/collab/enroll", "/collab"), "/enroll")

    def test_bare_prefix_becomes_root(self):
        self.assertEqual(broker.strip_base("/collab", "/collab"), "/")

    def test_no_prefix_configured(self):
        self.assertEqual(broker.strip_base("/webauthn/login/begin", ""), "/webauthn/login/begin")

    def test_trailing_slash_on_base_is_tolerated(self):
        self.assertEqual(broker.strip_base("/collab/enroll", "/collab/"), "/enroll")

    def test_unprefixed_path_survives(self):
        """The router should always prefix, but a bare path must still route."""
        self.assertEqual(broker.strip_base("/enroll", "/collab"), "/enroll")

    def test_lookalike_prefix_is_not_eaten(self):
        self.assertEqual(broker.strip_base("/collaborators", "/collab"),
                         "orators")


class WorkspaceDestTest(unittest.TestCase):

    def test_no_record_is_none_not_an_error(self):
        self.assertIsNone(broker.workspace_dest("alice", FakeStore({})))

    def test_revoked_is_none(self):
        store = FakeStore({"alice": {"name": "alice", "path": "/c/alice",
                                   "revoked_at": 123}})
        self.assertIsNone(broker.workspace_dest("alice", store))

    def test_missing_token_file_is_none(self):
        store = FakeStore({"alice": {"name": "alice", "path": "/c/alice"}})

        def no_token(name):
            raise OSError("no token file")

        self.assertIsNone(broker.workspace_dest("alice", store, no_token))

    def test_dest_carries_prefix_and_token(self):
        store = FakeStore({"alice": {"name": "alice", "path": "/c/alice"}})
        dest = broker.workspace_dest("alice", store, lambda name: "tok en/1")
        self.assertTrue(dest.endswith("/c/alice/?tkn=tok%20en%2F1"), dest)


class PasswordLoginTest(unittest.TestCase):

    def test_missing_fields_denied(self):
        status, _, _ = broker.login({}, FakeStore({}), lambda n, p: True)
        self.assertEqual(status, 401)

    def test_unknown_account_denied_without_calling_verify(self):
        called = []
        broker.login({"username": ["ghost"], "password": ["x"]},
                     FakeStore({}), lambda n, p: called.append(n) or True)
        self.assertEqual(called, [])

    def test_bad_password_denied(self):
        store = FakeStore({"alice": {"name": "alice", "path": "/c/alice"}})
        status, _, _ = broker.login({"username": ["alice"], "password": ["x"]},
                                    store, lambda n, p: False)
        self.assertEqual(status, 401)

    def test_good_password_without_workspace_is_200_not_302(self):
        """Authenticated with nowhere to go: a page, not a redirect."""
        store = FakeStore({"alice": {"name": "alice", "path": "/c/alice"}})

        def no_token(name):
            raise OSError("no token file")

        status, headers, _ = broker.login(
            {"username": ["alice"], "password": ["pw"]}, store,
            lambda n, p: True, no_token)
        self.assertEqual(status, 200)
        self.assertNotIn("Location", headers)

    def test_good_password_with_workspace_redirects(self):
        store = FakeStore({"alice": {"name": "alice", "path": "/c/alice"}})
        status, headers, _ = broker.login(
            {"username": ["alice"], "password": ["pw"]}, store,
            lambda n, p: True, lambda name: "tok")
        self.assertEqual(status, 302)
        self.assertIn("/c/alice/?tkn=tok", headers["Location"])


class PageTest(unittest.TestCase):

    def test_door_is_self_contained(self):
        import login_page
        html = login_page.render_login("door.example.test",
                                       "https://door.example.test:8450",
                                       "/collab").decode()
        for scheme in ("http://", "https://fonts", "//cdn"):
            self.assertNotIn(scheme, html.replace(
                "https://door.example.test:8450", ""))

    def test_door_urls_carry_the_mount_prefix(self):
        import login_page
        html = login_page.render_login("d.test", "https://d.test:8450",
                                       "/collab").decode()
        self.assertIn("BASE='/collab'", html)
        self.assertIn("action='/collab/'", html)

    def test_notice_opens_the_password_form(self):
        import login_page
        html = login_page.render_login("d.test", "https://d.test:8450", "",
                                       "That didn't work.").decode()
        self.assertIn("<details id=pwbox open>", html)
        self.assertIn("That didn&#x27;t work.".replace("&#x27;", "'"), html)

    def test_enroll_page_escapes_the_user(self):
        import login_page
        html = login_page.render_enroll("<script>x</script>", "key", "tkt",
                                        "/collab").decode()
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class OriginGateTest(unittest.TestCase):
    """The page must distinguish 'not https' from 'https, but the wrong door'."""

    def _page(self, origins):
        import login_page
        return login_page.render_login(
            "d.test", "https://d.test:8450", "/collab", None, origins).decode()

    def test_allowlist_is_embedded_for_the_client_check(self):
        html = self._page(("https://d.test:8450", "https://other.test:8443"))
        self.assertIn('ORIGINS=["https://d.test:8450", "https://other.test:8443"]',
                      html)

    def test_falls_back_to_the_advertised_origin(self):
        self.assertIn('ORIGINS=["https://d.test:8450"]', self._page(()))

    def test_page_offers_the_right_door_with_its_prefix(self):
        """The 'wrong door' message must link to the mount, not the bare host."""
        html = self._page(("https://d.test:8450",))
        self.assertTrue("+SECURE+BASE+" in html,
                        "recovery link must be built from origin AND prefix")
        self.assertTrue("Security keys work from a different address" in html,
                        "the wrong-door case needs its own sentence")
