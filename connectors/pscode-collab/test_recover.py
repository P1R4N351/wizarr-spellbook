#!/usr/bin/env python3
import os
import tempfile
import types
import unittest
from unittest import mock

import recover


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.record = {"name": "alice", "path": "/c/alice", "revoked_at": None}
        self.store = types.SimpleNamespace(all=lambda: [self.record], put=mock.Mock())
        self.container = types.SimpleNamespace(exists=lambda _: True, is_running=lambda _: False,
                                               start=mock.Mock(), ip_of=lambda _: "10.89.7.13")
        self.deps = types.SimpleNamespace(store=self.store, container=self.container,
                                          token_dir=self.dir.name)

    def test_missing_token_and_stopped_container_are_recovered(self):
        with mock.patch.object(recover.provision, "_write_token") as write, \
                mock.patch.object(recover.provision, "new_connection_token", return_value="x" * 40):
            result = recover.recover(self.deps)
        self.assertEqual(result[0]["status"], "recovered")
        write.assert_called_once_with("alice", "x" * 40, self.dir.name)
        self.container.start.assert_called_once_with("alice")
        self.store.put.assert_called_once_with(self.record)

    def test_healthy_workspace_is_not_restarted_or_rotated(self):
        with open(os.path.join(self.dir.name, "alice.tok"), "w", encoding="utf-8") as handle:
            handle.write("old")
        self.container.is_running = lambda _: True
        with mock.patch.object(recover.provision, "_write_token") as write:
            result = recover.recover(self.deps)
        self.assertEqual(result[0]["status"], "healthy")
        write.assert_not_called()
        self.container.start.assert_not_called()

    def test_revoked_records_are_not_touched(self):
        self.record["revoked_at"] = "2026-09-01T00:00:00Z"
        self.assertEqual(recover.recover(self.deps), [])

    def test_missing_container_does_not_create_a_new_token(self):
        self.container.exists = lambda _: False
        with mock.patch.object(recover.provision, "_write_token") as write:
            result = recover.recover(self.deps)
        self.assertEqual(result[0]["status"], "missing-container")
        self.assertFalse(result[0]["made_token"])
        write.assert_not_called()
