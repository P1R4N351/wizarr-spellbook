"""The workbench connection token must not be the gitea token.

Until 2026-09-01 create() wrote the sha1 from gitea.mint_token straight in as
the connection token, so one secret did two jobs and anything that revealed a
workbench URL also handed out a push-scoped (write:repository) gitea
credential. That is not theoretical: the container's own startup banner prints
`Web UI available at ...?tkn=<token>`, and that line put a live gitea token
into an agent transcript.

These tests drive the REAL create() and the REAL _write_token into a temp
dir -- only gitea/container/route/store are faked. build_deps()'s docstring
records why that matters here: an earlier bug survived a green suite precisely
because every test faked the thing production got wrong, so the assertions
below read the bytes actually written to disk rather than trusting a mock.
"""
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import container as container_mod
import provision

GITEA_SECRET = "deadbeef" * 5          # 40 chars, the shape gitea returns
GITEA_TOKEN_ID = 57


class FakeGitea:
    def __init__(self):
        self.calls = []

    def create_user(self, *a, **k):
        self.calls.append("create_user")

    def add_collaborator(self, *a, **k):
        self.calls.append("add_collaborator")

    def fork_repo(self, *a, **k):
        return "http://gitea.example/alice/repo.git"

    def mint_token(self, *a, **k):
        self.calls.append("mint_token")
        return GITEA_TOKEN_ID, GITEA_SECRET


class FakeContainer:
    def __init__(self):
        self.launched = None

    def launch(self, collab, port, token_path, volume, **kwargs):
        self.launched = {"collab": collab, "port": port,
                         "token_path": token_path, "volume": volume,
                         "kwargs": kwargs}

    def ip_of(self, collab):
        return "10.89.7.99"

    def destroy(self, collab):
        pass


class FakeStore:
    def __init__(self):
        self.records = {}

    def get(self, name):
        return self.records.get(name)

    def all(self):
        return list(self.records.values())

    def next_port(self):
        return 47100

    def put(self, rec):
        self.records[rec["name"]] = rec

    def drop(self, name):
        self.records.pop(name, None)


class FakeRoute:
    def add(self, collab, target):
        return "/c/" + collab

    def remove(self, collab):
        pass


def make_deps(token_dir):
    return types.SimpleNamespace(
        store=FakeStore(),
        gitea=FakeGitea(),
        container=FakeContainer(),
        route=FakeRoute(),
        available_mb=lambda: 64000,
        now=lambda: "2026-09-01T00:00:00Z",
        parse_time=lambda s: None,
        secret=lambda which: "admin-token",
        token_dir=token_dir,
    )


class ConnectionTokenTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_token_is_forty_hex_and_unique(self):
        a = provision.new_connection_token()
        b = provision.new_connection_token()
        self.assertEqual(len(a), 40)
        self.assertNotEqual(a, b, "a CSPRNG must not repeat across calls")
        int(a, 16)  # raises if it is not hex

    def test_token_directory_allows_broker_traversal_without_listing(self):
        broker = types.SimpleNamespace(gr_gid=988)
        with mock.patch.object(provision.grp, "getgrnam", return_value=broker), \
                mock.patch.object(provision.os, "chown") as chown:
            token = provision._write_token("alice", "a" * 40, self.dir)
        self.assertTrue(os.path.isfile(token))
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o710)
        chown.assert_called_once_with(self.dir, -1, 988)

    def test_connection_token_is_not_the_gitea_token(self):
        """THE regression guard. Reads the bytes on disk, not a mock."""
        deps = make_deps(self.dir)
        provision.create("alice", "pw", "e@x", "owner", "repo", deps)

        token_path = deps.container.launched["token_path"]
        with open(token_path, encoding="utf-8") as fh:
            written = fh.read()

        self.assertNotEqual(written, GITEA_SECRET,
                            "connection token must not be the gitea token")
        self.assertEqual(len(written), 40)
        int(written, 16)

    def test_gitea_token_is_surfaced_in_its_own_file(self):
        # DECIDE:C -- keep minting and hand the token to the collaborator, so
        # decoupling does not leave a live write:repository token nobody holds.
        deps = make_deps(self.dir)
        provision.create("alice", "pw", "e@x", "owner", "repo", deps)

        gitea_path = deps.container.launched["kwargs"].get("gitea_token_path")
        self.assertTrue(gitea_path, "gitea token must be mounted for the user")
        self.assertNotEqual(gitea_path, deps.container.launched["token_path"],
                            "the two credentials must be separate files")
        with open(gitea_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), GITEA_SECRET)

    def test_gitea_token_is_still_minted_and_recorded(self):
        deps = make_deps(self.dir)
        rec = provision.create("alice", "pw", "e@x", "owner", "repo", deps)
        self.assertIn("mint_token", deps.gitea.calls)
        self.assertEqual(rec["gitea_token_id"], GITEA_TOKEN_ID)

    def test_no_secret_is_written_into_the_state_record(self):
        # state.json is 0644 root-owned on the host; a credential there would
        # be readable by every local process.
        deps = make_deps(self.dir)
        rec = provision.create("alice", "pw", "e@x", "owner", "repo", deps)
        blob = repr(rec)
        self.assertNotIn(GITEA_SECRET, blob)
        with open(deps.container.launched["token_path"], encoding="utf-8") as fh:
            self.assertNotIn(fh.read(), blob)


class LaunchArgvTest(unittest.TestCase):
    """The mount is assembled by hand, so its POSITION is worth pinning."""

    def _argv(self, **kwargs):
        seen = {}

        def runner(argv, **_):
            seen["argv"] = argv
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        container_mod.launch("alice", 47100, "/run/collab/alice.tok", "collab-alice",
                             runner=runner, **kwargs)
        return seen["argv"]

    def test_every_flag_precedes_the_image(self):
        """podman takes flags in any order, but NOTHING may follow the image
        except entrypoint args -- an option appended after it would be passed
        to code-server instead of to podman. An earlier draft of the gitea
        mount spliced itself in at argv[-1:-1] and landed between --restart
        and its value ("no"), which is why this is pinned.
        """
        import config
        argv = self._argv(gitea_token_path="/run/collab/alice.gitea")
        image_at = argv.index(config.IMAGE)

        # --restart must still be immediately followed by its own value.
        self.assertEqual(argv[argv.index("--restart") + 1], "no")

        for i, token in enumerate(argv):
            if token.startswith("--") and i > image_at:
                self.assertEqual(token, "--server-base-path",
                                 "%s must not follow the image" % token)
        # Every --volume pairs with its own value, ahead of the image.
        for i, token in enumerate(argv):
            if token == "--volume":
                self.assertLess(i + 1, image_at, "volume value precedes image")
                self.assertIn(":", argv[i + 1], "volume value must be a spec")

    def test_gitea_mount_present_and_read_only(self):
        argv = self._argv(gitea_token_path="/run/collab/alice.gitea")
        self.assertIn("/run/collab/alice.gitea:/run/collab/gitea-token:ro", argv)

    def test_absent_by_default_so_existing_callers_are_unchanged(self):
        argv = self._argv()
        self.assertNotIn("gitea-token", " ".join(argv))
        self.assertIn("/run/collab/alice.tok:/run/collab/token:ro", argv)


class ContainerStatusTest(unittest.TestCase):

    def test_running_status_accepts_only_podman_booleans(self):
        def runner(argv, **_):
            self.assertEqual(argv[-1], "{{.State.Running}}")
            return types.SimpleNamespace(returncode=0, stdout="true\n", stderr="")

        self.assertTrue(container_mod.is_running("alice", runner=runner))

    def test_running_status_fails_closed_on_unexpected_output(self):
        def runner(argv, **_):
            return types.SimpleNamespace(returncode=0, stdout="unknown\n", stderr="")

        with self.assertRaises(RuntimeError):
            container_mod.is_running("alice", runner=runner)


if __name__ == "__main__":
    unittest.main()
