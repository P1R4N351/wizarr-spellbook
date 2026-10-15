#!/usr/bin/env python3
"""conmon must land in its own transient scope, never the caller's unit. [c3ede23]"""
import types
import unittest

import container


def _capture():
    seen = {}

    def runner(argv, **_):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return seen, runner


class ScopeTest(unittest.TestCase):
    def test_start_runs_in_transient_scope(self):
        seen, runner = _capture()
        container.start("alice", runner=runner)
        self.assertEqual(seen["argv"][:2], ["systemd-run", "--scope"])
        self.assertEqual(seen["argv"][-3:], ["podman", "start", "collab-alice"])

    def test_launch_runs_in_transient_scope(self):
        seen, runner = _capture()
        container.launch("alice", 47100, "/run/collab/alice.tok", "collab-alice", runner=runner)
        argv = seen["argv"]
        self.assertEqual(argv[:2], ["systemd-run", "--scope"])
        self.assertEqual(argv[argv.index("podman"):][:3], ["podman", "run", "-d"])

    def test_read_only_queries_are_not_scoped(self):
        seen, runner = _capture()
        container.exists("alice", runner=runner)
        self.assertEqual(seen["argv"][0], "podman")

    def test_launch_mounts_machine_settings_read_only(self):
        seen, runner = _capture()
        container.launch("alice", 47100, "/run/collab/alice.tok", "collab-alice", runner=runner,
                         machine_settings_path="/etc/pscode-collab/machine-settings.json")
        argv = seen["argv"]
        mount = "/etc/pscode-collab/machine-settings.json:%s:ro" % container.config.MACHINE_SETTINGS_MOUNT
        self.assertIn(mount, argv)
        self.assertLess(argv.index(mount), argv.index(container.config.IMAGE))

    def test_launch_without_settings_file_adds_no_mount(self):
        seen, runner = _capture()
        original = container.os.path.exists
        container.os.path.exists = lambda path: False
        try:
            container.launch("alice", 47100, "/run/collab/alice.tok", "collab-alice", runner=runner)
        finally:
            container.os.path.exists = original
        self.assertFalse(any("workstationhine/settings.json" in a for a in seen["argv"]))


if __name__ == "__main__":
    unittest.main()
