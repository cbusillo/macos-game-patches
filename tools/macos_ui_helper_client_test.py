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

    def write_result(self, payload: bytes, *, mode: int = 0o600, name: str = "result.json") -> Path:
        path = self.results / name
        path.write_bytes(payload)
        path.chmod(mode)
        return path

    def test_result_permissions_must_be_private_and_readable(self) -> None:
        for mode in (0o644, 0o400, 0o700):
            with self.subTest(mode=oct(mode)):
                path = self.write_result(b'{"schema":1,"ok":true}', mode=mode, name=f"result-{mode:o}.json")
                with self.assertRaises(RuntimeError):
                    client.read_result(path)

    def test_foreign_owned_result_is_refused(self) -> None:
        path = self.write_result(b'{"schema":1,"ok":true}')
        metadata = path.stat()
        foreign = SimpleNamespace(
            st_mode=metadata.st_mode,
            st_uid=metadata.st_uid + 1,
            st_size=metadata.st_size,
        )
        with mock.patch.object(client.os, "fstat", return_value=foreign):
            with self.assertRaises(RuntimeError):
                client.read_result(path)

    def test_nonregular_result_is_refused(self) -> None:
        directory = self.root / "directory-result"
        directory.mkdir()
        directory.chmod(0o600)
        self.addCleanup(directory.chmod, 0o700)
        with self.assertRaises(RuntimeError):
            client.read_result(directory)

    def run_reader_child(self, source: str, path: Path) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); " + source,
             str(Path(client.__file__).resolve().parent), str(path)],
            capture_output=True, text=True, timeout=5, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def test_fifo_result_is_refused_without_a_writer(self) -> None:
        path = self.results / "fifo-result.json"
        os.mkfifo(path, 0o600)
        completed = self.run_reader_child('''
from pathlib import Path
import macos_ui_helper_client as client
try:
    client.read_result(Path(sys.argv[2]))
except RuntimeError as error:
    print(error)
else:
    raise AssertionError("FIFO was admitted")
''', path)
        self.assertIn("regular file", completed.stdout)

    def test_oversized_result_is_refused_before_read(self) -> None:
        # Distinct from the implementation limit; valid JSON if size admission breaks.
        path = self.write_result(json.dumps({"schema": 1, "ok": True, "data": "x" * (client.MAX_RESULT_BYTES * 2)}).encode())
        with mock.patch.object(client.os, "read", wraps=os.read) as read:
            with self.assertRaises(RuntimeError):
                client.read_result(path)
        read.assert_not_called()

    def test_growth_after_stat_is_still_bounded(self) -> None:
        prefix = b'{"schema":1,"ok":true}'
        path = self.write_result(prefix)
        padding = b' ' * client.MAX_RESULT_BYTES
        with path.open("ab") as stream:
            for _ in range(12):
                stream.write(padding)
        completed = self.run_reader_child('''
import json
import tracemalloc
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import macos_ui_helper_client as client
path = Path(sys.argv[2])
metadata = path.stat()
earlier = SimpleNamespace(st_mode=metadata.st_mode, st_uid=metadata.st_uid, st_size=20)
with mock.patch.object(client.os, "fstat", return_value=earlier):
    tracemalloc.start()
    refused = False
    try:
        client.read_result(path)
    except RuntimeError:
        refused = True
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
print(json.dumps({"refused": refused, "peak": peak, "limit": client.MAX_RESULT_BYTES}))
''', path)
        evidence = json.loads(completed.stdout)
        self.assertTrue(evidence["refused"], "grew beyond the limit but was admitted")
        # Allow copies/buffering while ruling out materializing the whole file.
        self.assertLess(evidence["peak"], 8 * (evidence["limit"] + 1))

    def test_malformed_json_and_schema_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            client.read_result(self.write_result(b'{'))
        payloads = [[], {"ok": True}, {"schema": 1, "ok": 1}]
        payloads.extend({"schema": value, "ok": True} for value in (73, True, False, 1.0, "1", None, [], {}))
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(RuntimeError):
                    client.read_result(self.write_result(json.dumps(payload).encode()))

    def test_helper_failure_is_preserved_and_result_removed(self) -> None:
        token = "1" * 32
        payload = {"schema": 1, "ok": False, "error": {"code": "fixture.denied"}}

        def launch(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            self.write_result(json.dumps(payload).encode(), name=f"{token}.json")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(client.uuid, "uuid4", return_value=SimpleNamespace(hex=token)), mock.patch.object(
            client.subprocess, "run", side_effect=launch
        ):
            status, result = self.run_main(["status"])
        self.assertEqual(status, 1)
        self.assertEqual(result, payload)
        self.assertFalse((self.results / f"{token}.json").exists())

    def test_invalid_result_is_reported_and_removed(self) -> None:
        token = "2" * 32

        def launch(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            self.write_result(b'{"schema":1,"ok":true}', mode=0o644, name=f"{token}.json")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(client.uuid, "uuid4", return_value=SimpleNamespace(hex=token)), mock.patch.object(
            client.subprocess, "run", side_effect=launch
        ):
            status, payload = self.run_main(["status"])
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "client.result_invalid")
        self.assertFalse((self.results / f"{token}.json").exists())

    def test_result_wait_expires_without_real_sleep(self) -> None:
        clock = 40.0

        def advance(duration: float) -> None:
            nonlocal clock
            clock += duration

        def monotonic() -> float:
            advance(0.01)
            if clock > 44.0:
                raise AssertionError("result waiting exceeded the fixture deadline")
            return clock

        with mock.patch.object(client.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), mock.patch.object(
            client.time, "monotonic", side_effect=monotonic
        ), mock.patch.object(client.time, "sleep", side_effect=advance):
            status, payload = self.run_main(["--timeout", "1", "status"])
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "client.timeout")
        self.assertLess(clock, 42.0)

    def test_launch_failure_is_reported(self) -> None:
        with mock.patch.object(client.subprocess, "run", return_value=subprocess.CompletedProcess([], 9, "", "fixture launch failure")), mock.patch.object(
            client.time, "sleep", side_effect=AssertionError("waited after a rejected launch")
        ):
            status, payload = self.run_main(["status"])
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "client.launch_failed")
        self.assertIn("fixture launch failure", payload["error"]["details"]["message"])

    def test_preexisting_result_is_preserved_without_launch(self) -> None:
        token = "3" * 32
        original = b'{"schema":1,"ok":true,"result":{"owner":"previous run"}}'
        path = self.write_result(original, name=f"{token}.json")
        with mock.patch.object(client.uuid, "uuid4", return_value=SimpleNamespace(hex=token)), mock.patch.object(
            client.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        ) as launch:
            status, payload = self.run_main(["status"])
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "client.result_collision")
        launch.assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_unallowed_command_never_launches(self) -> None:
        with mock.patch.object(
            client.subprocess, "run", return_value=subprocess.CompletedProcess([], 9, "", "fixture")
        ) as launch:
            status, payload = self.run_main(["fixture-unapproved-command"])
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "client.command_not_allowed")
        launch.assert_not_called()



if __name__ == "__main__":
    unittest.main()
