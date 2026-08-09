#!/usr/bin/env python3
"""Deterministic tests for the Every Code app-server LaunchAgent manager."""

from __future__ import annotations

import json
import os
import pathlib
import plistlib
import tempfile
import unittest
from unittest import mock

import macos_app_server_launch_agent as manager


class LaunchAgentTests(unittest.TestCase):
    def test_plist_is_aqua_loopback_and_shell_free(self) -> None:
        home = pathlib.Path("/Users/fixture")
        paths = manager.paths_for(home)
        working_directory = home / "Developer" / "macos-game-patches-m2"
        payload = manager.build_plist(paths, working_directory)

        self.assertEqual(payload["Label"], manager.LABEL)
        self.assertEqual(payload["ProgramArguments"], manager.expected_arguments(paths))
        self.assertEqual(payload["LimitLoadToSessionType"], "Aqua")
        self.assertTrue(payload["RunAtLoad"])
        self.assertEqual(payload["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(payload["StandardInPath"], "/dev/null")
        self.assertNotIn("ProcessType", payload)
        self.assertNotIn("Program", payload)
        self.assertNotIn("sh", payload["ProgramArguments"])

    def test_lsof_parser_groups_addresses_by_pid(self) -> None:
        output = "\n".join(
            [
                "p42",
                "ccode",
                "n127.0.0.1:8765",
                "p43",
                "cforeign",
                "n*:8765",
            ]
        )
        self.assertEqual(
            manager.parse_lsof(output),
            [
                manager.Listener(42, "code", ("127.0.0.1:8765",)),
                manager.Listener(43, "foreign", ("*:8765",)),
            ],
        )

    def test_listener_admission_rejects_foreign_and_wildcard(self) -> None:
        paths = manager.paths_for(pathlib.Path("/Users/fixture"))
        expected = manager.Listener(
            pid=42,
            command_name="code",
            addresses=("127.0.0.1:8765",),
            uid=501,
            command=manager.expected_command(paths),
        )
        self.assertEqual(manager.validate_listeners([expected], paths, 501), [42])

        foreign = manager.Listener(43, "nc", ("127.0.0.1:8765",), 501, "nc -l 8765")
        with self.assertRaises(manager.AgentError):
            manager.validate_listeners([foreign], paths, 501)

        wildcard = manager.Listener(44, "code", ("*:8765",), 501, manager.expected_command(paths))
        with self.assertRaises(manager.AgentError):
            manager.validate_listeners([wildcard], paths, 501)

        duplicate = manager.Listener(
            45,
            "code",
            ("127.0.0.1:8765",),
            501,
            manager.expected_command(paths),
        )
        with self.assertRaises(manager.AgentError):
            manager.validate_listeners([expected, duplicate], paths, 501)

    def test_launchctl_parser_extracts_runtime_identity(self) -> None:
        output = """
gui/501/com.cbusillo.every-code-lab.app-server = {
    path = /Users/fixture/Library/LaunchAgents/example.plist
    state = running
    pid = 1234
}
"""
        self.assertEqual(
            manager.parse_launchctl_print(output),
            {
                "state": "running",
                "pid": 1234,
                "path": "/Users/fixture/Library/LaunchAgents/example.plist",
            },
        )

    def test_service_readiness_requires_exact_loaded_plist(self) -> None:
        paths = manager.paths_for(pathlib.Path("/Users/fixture"))
        service = {
            "loaded": True,
            "path": str(paths.plist),
            "state": "running",
            "pid": 42,
        }
        self.assertTrue(manager.service_ready(paths, service, [42]))
        service["path"] = "/Users/fixture/Library/LaunchAgents/foreign.plist"
        self.assertFalse(manager.service_ready(paths, service, [42]))

    def test_managed_installation_rejects_tampered_plist(self) -> None:
        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temporary:
            home = pathlib.Path(temporary)
            paths = manager.paths_for(home)
            working_directory = home / "Developer" / "repo"
            working_directory.mkdir(parents=True)
            (working_directory / ".git").mkdir()
            paths.plist.parent.mkdir(parents=True)
            paths.state_root.mkdir(parents=True)
            payload = manager.build_plist(paths, working_directory)
            paths.plist.write_bytes(plistlib.dumps(payload))
            os.chmod(paths.plist, 0o600)
            paths.contract.write_text(
                json.dumps(
                    {
                        "codeSha256": "0" * 64,
                        "workingDirectory": str(working_directory),
                    }
                )
            )
            os.chmod(paths.contract, 0o600)

            self.assertIsNotNone(
                manager.load_managed_installation(
                    paths,
                    os.getuid(),
                    current_code_sha256=None,
                )
            )
            payload["ProgramArguments"] = ["/usr/bin/false"]
            paths.plist.write_bytes(plistlib.dumps(payload))
            with self.assertRaises(manager.AgentError):
                manager.load_managed_installation(
                    paths,
                    os.getuid(),
                    current_code_sha256=None,
                )

    def test_birth_token_change_blocks_process_termination(self) -> None:
        paths = manager.paths_for(pathlib.Path("/Users/fixture"))
        record = manager.Listener(
            pid=42,
            command_name="code",
            addresses=("127.0.0.1:8765",),
            uid=501,
            command=manager.expected_command(paths),
            start_token=100,
        )
        with mock.patch.object(
            manager,
            "process_identity",
            return_value=(501, manager.expected_command(paths), 101),
        ), mock.patch.object(manager.os, "kill") as kill:
            with self.assertRaises(manager.AgentError):
                manager.terminate_exact_processes([record], paths, 501)
            kill.assert_not_called()

    def test_first_migration_failure_removes_autoload_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = pathlib.Path(temporary)
            paths = manager.paths_for(home)
            paths.plist.parent.mkdir(parents=True)
            paths.log_root.mkdir(parents=True)
            working_directory = home / "Developer" / "repo"
            working_directory.mkdir(parents=True)
            (working_directory / ".git").mkdir()
            record = manager.Listener(
                42,
                "code",
                ("127.0.0.1:8765",),
                os.getuid(),
                manager.expected_command(paths),
                100,
            )
            baseline = {
                "uid": os.getuid(),
                "workingDirectory": str(working_directory),
                "launchd": {"loaded": False, "path": None, "pid": None, "state": None},
                "listenerPids": [42],
                "codeSha256": "0" * 64,
            }

            def publish(_: manager.Paths, payload: bytes) -> None:
                paths.plist.write_bytes(payload)
                os.chmod(paths.plist, 0o600)
                return None

            with mock.patch.object(manager, "validate_host", return_value=baseline), mock.patch.object(
                manager,
                "listeners",
                return_value=[record],
            ), mock.patch.object(manager, "load_managed_installation", return_value=None), mock.patch.object(
                manager,
                "prepare_private_file",
            ), mock.patch.object(manager, "write_plist_atomic", side_effect=publish), mock.patch.object(
                manager,
                "terminate_exact_processes",
            ), mock.patch.object(
                manager,
                "bootstrap",
                side_effect=manager.AgentError("launchd.bootstrap_failed", "fixture"),
            ), mock.patch.object(manager, "bootout"):
                with self.assertRaises(manager.AgentError) as raised:
                    manager.install(paths, working_directory)

            self.assertEqual(raised.exception.code, "migration.manual_recovery_required")
            self.assertFalse(paths.plist.exists())
            self.assertEqual(len(list(paths.log_root.glob("failed-app-server-*.plist"))), 1)

    def test_symlinked_component_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temporary:
            root = pathlib.Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(manager.AgentError):
                manager.reject_symlink_components(link / "child")


if __name__ == "__main__":
    unittest.main()
