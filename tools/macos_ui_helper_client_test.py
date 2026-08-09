#!/usr/bin/env python3
"""Deterministic tests for the Launch Services UI helper client."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import macos_ui_helper_client as client


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / "MacOSUIHelper.app"
        self.app.mkdir()
        self.results = self.root / "Results"
        self.results.mkdir(mode=0o700)
        self.default_app = mock.patch.object(client, "DEFAULT_APP", self.app)
        self.result_root = mock.patch.object(client, "RESULT_ROOT", self.results)
        self.default_app.start()
        self.result_root.start()
        self.addCleanup(self.default_app.stop)
        self.addCleanup(self.result_root.stop)

    def run_main(self, arguments: list[str]) -> tuple[int, dict[str, object]]:
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["macos_ui_helper_client.py", *arguments]), contextlib.redirect_stdout(stdout):
            status = client.main()
        return status, json.loads(stdout.getvalue())

    def test_successful_result_is_validated_and_removed(self) -> None:
        token = "0" * 31 + "1"

        def launch(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            self.assertEqual(command[-1], token)
            result_path = self.results / f"{token}.json"
            result_path.write_text('{"schema":1,"ok":true,"result":{"accessibility":true}}')
            os.chmod(result_path, 0o600)
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(client.uuid, "uuid4", return_value=SimpleNamespace(hex=token)), mock.patch.object(
            client.subprocess,
            "run",
            side_effect=launch,
        ):
            status, payload = self.run_main(["status"])

        self.assertEqual(status, 0)
        self.assertTrue(payload["ok"])
        self.assertFalse((self.results / f"{token}.json").exists())

    def test_launch_timeout_is_bounded(self) -> None:
        with mock.patch.object(
            client.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["open"], 1),
        ):
            status, payload = self.run_main(["--timeout", "1", "status"])

        self.assertEqual(status, 1)
        error = payload.get("error")
        self.assertIsInstance(error, dict)
        self.assertEqual(error.get("code") if isinstance(error, dict) else None, "client.launch_timeout")

    def test_symlinked_result_is_refused(self) -> None:
        target = self.root / "foreign.json"
        target.write_text('{"schema":1,"ok":true}')
        link = self.results / "result.json"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            client.read_result(link)


if __name__ == "__main__":
    unittest.main()
