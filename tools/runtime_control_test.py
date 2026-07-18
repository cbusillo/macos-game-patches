"""Hardware-free fixtures for the runtime control plane."""

from __future__ import annotations

import json
import contextlib
import io
import pathlib
import plistlib
import tempfile
import unittest
from collections.abc import Sequence
from unittest import mock

import runtime_cli

from runtime_control import (
    CheckResult,
    CommandResult,
    DoctorReport,
    RuntimeContext,
    evaluate_command_prerequisite,
    evaluate_plist_prerequisite,
    resolve_context_paths,
    status_runtime,
    stop_runtime,
)


class StaticRunner:
    def __init__(self, result: CommandResult) -> None:
        self.result = result

    def run(self, argv: Sequence[str], *, timeout: float = 10.0) -> CommandResult:
        return CommandResult(
            tuple(str(item) for item in argv),
            self.result.returncode,
            self.result.stdout,
            self.result.stderr,
            self.result.error,
        )


class LifecycleRunner:
    def __init__(self) -> None:
        self.service_output: str | None = None
        self.live_program: pathlib.Path | None = None
        self.extra_text_paths: list[pathlib.Path] = []
        self.owner_start_time = "Sat Jul 18 03:00:00 2026"
        self.codesign_error = False
        self.query_error: CommandResult | None = None
        self.bootout_returncode = 0
        self.commands: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], *, timeout: float = 10.0) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        if command[:2] == ("/bin/launchctl", "print"):
            if self.query_error is not None:
                return CommandResult(
                    command,
                    self.query_error.returncode,
                    self.query_error.stdout,
                    self.query_error.stderr,
                    self.query_error.error,
                )
            if self.service_output is None:
                return CommandResult(command, 113, stderr="Could not find service")
            return CommandResult(command, 0, stdout=self.service_output)
        if command[:2] == ("/bin/launchctl", "bootout"):
            self.service_output = None
            return CommandResult(command, self.bootout_returncode, stderr="fixture bootout result")
        if command and command[0] == "/usr/sbin/lsof":
            if self.live_program is None:
                return CommandResult(command, 1, stderr="missing fixture program")
            paths = [*self.extra_text_paths, self.live_program]
            return CommandResult(
                command,
                0,
                stdout="p4321\n" + "".join(f"n{path}\n" for path in paths),
            )
        if command and command[0] == "/usr/bin/codesign":
            if self.codesign_error:
                return CommandResult(command, 1, stderr="invalid signature")
            return CommandResult(
                command,
                0,
                stderr=(
                    "Identifier=com.alvr.macos-bridge\n"
                    "TeamIdentifier=TESTTEAM\n"
                    "CDHash=0123456789abcdef0123456789abcdef01234567\n"
                ),
            )
        if command and command[0] == "/bin/ps":
            return CommandResult(command, 0, stdout=f"{self.owner_start_time}\n")
        return CommandResult(command, 0)


class PrerequisiteTests(unittest.TestCase):
    def command_item(self, **expectation: str) -> dict[str, object]:
        return {
            "id": "fixture_command",
            "kind": "command",
            "argv": ["fixture-command"],
            **expectation,
        }

    def test_command_prerequisite_passes_exact_value(self) -> None:
        result = evaluate_command_prerequisite(
            self.command_item(equals="expected"),
            StaticRunner(CommandResult(("fixture-command",), 0, stdout="expected\n")),
        )
        self.assertEqual(result.status, "pass")

    def test_command_prerequisite_fails_mismatch(self) -> None:
        result = evaluate_command_prerequisite(
            self.command_item(contains="expected"),
            StaticRunner(CommandResult(("fixture-command",), 0, stdout="different")),
        )
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.details["actual"], "different")

    def test_command_prerequisite_fails_missing_command(self) -> None:
        result = evaluate_command_prerequisite(
            self.command_item(equals="expected"),
            StaticRunner(
                CommandResult(
                    ("fixture-command",),
                    None,
                    stderr="not found",
                    error="unavailable",
                )
            ),
        )
        self.assertEqual(result.status, "fail")

    def test_command_prerequisite_is_unknown_on_timeout(self) -> None:
        result = evaluate_command_prerequisite(
            self.command_item(equals="expected"),
            StaticRunner(CommandResult(("fixture-command",), None, error="timeout")),
        )
        self.assertEqual(result.status, "unknown")

    def test_plist_prerequisite_states(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-control-plist-") as temp:
            root = pathlib.Path(temp).resolve()
            path = root / "Info.plist"
            item = {
                "id": "fixture_plist",
                "kind": "plist",
                "path": "${FIXTURE_PLIST}",
                "key": "CFBundleVersion",
                "equals": "1.2.3",
            }
            missing = evaluate_plist_prerequisite(item, {"FIXTURE_PLIST": str(path)})
            self.assertEqual(missing.status, "fail")

            with path.open("wb") as stream:
                plistlib.dump({"CFBundleVersion": "1.2.3"}, stream)
            passing = evaluate_plist_prerequisite(item, {"FIXTURE_PLIST": str(path)})
            self.assertEqual(passing.status, "pass")

            with path.open("wb") as stream:
                plistlib.dump({"CFBundleVersion": "9.9.9"}, stream)
            mismatch = evaluate_plist_prerequisite(item, {"FIXTURE_PLIST": str(path)})
            self.assertEqual(mismatch.status, "fail")

            path.write_text("not a plist")
            invalid = evaluate_plist_prerequisite(item, {"FIXTURE_PLIST": str(path)})
            self.assertEqual(invalid.status, "unknown")


class CliTests(unittest.TestCase):
    def test_json_usage_error_is_machine_readable(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_context:
            runtime_cli.main(["doctor", "--json"])
        self.assertEqual(exit_context.exception.code, 2)
        payload = json.loads(stderr.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "usage.error")


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        code_root = pathlib.Path(__file__).resolve().parents[1] / ".code"
        code_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="runtime-control-lifecycle-", dir=code_root)
        self.root = pathlib.Path(self.temp.name).resolve()
        self.runner = LifecycleRunner()
        self.alive_pids: set[int] = set()
        self.bindings_path = self.root / "bindings.json"
        self.bindings_path.write_text(
            json.dumps(
                {
                    "RUNTIME_STATE_ROOT": str(self.root / "state"),
                    "NATIVE_BRIDGE_BUNDLE": str(self.root / "ALVRMacOSBridge.app"),
                }
            )
        )
        self.context = RuntimeContext(
            bindings_path=self.bindings_path,
            runner=self.runner,
            pid_alive=lambda pid: pid in self.alive_pids,
            sleeper=lambda _: None,
        )
        _, _, self.paths = resolve_context_paths(self.context)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_bridge(self) -> None:
        self.paths.bridge_program.parent.mkdir(parents=True, exist_ok=True)
        self.paths.bridge_program.write_bytes(b"fixture bridge")
        self.paths.bridge_owner_marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.bridge_owner_marker.write_text(json.dumps(self.paths.bridge_owner_content))
        self.runner.live_program = self.paths.bridge_program

    def create_plist(self, *, owned: bool = True) -> None:
        self.paths.launch_agent_plist.parent.mkdir(parents=True, exist_ok=True)
        label = self.paths.service_label if owned else "com.example.foreign"
        program = self.paths.bridge_program if owned else self.root / "foreign-bridge"
        with self.paths.launch_agent_plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": label,
                    "ProgramArguments": [str(program)],
                    "MachServices": {label: True},
                    "AssociatedBundleIdentifiers": [label],
                    "ProcessType": "Interactive",
                    "EnvironmentVariables": {
                        "ALVR_IOSURFACE_POOL_SERVICE": label,
                        "ALVR_IOSURFACE_POOL_NONCE": "12345",
                        "ALVR_BRIDGE_INPUT": "iosurface",
                    },
                    "StandardOutPath": str(self.root / "runtime.log"),
                    "StandardErrorPath": str(self.root / "runtime.log"),
                },
                stream,
            )

    def create_lock(self, pid_text: str, *, run_dir: str = "/tmp/fixture") -> None:
        self.paths.lock_path.mkdir(parents=True, exist_ok=True)
        (self.paths.lock_path / "pid").write_text(pid_text)
        (self.paths.lock_path / "run-dir").write_text(run_dir)

    def create_state(self, *, state: str = "streaming", owner_pid: int = 2000) -> None:
        self.paths.state_root.mkdir(parents=True, exist_ok=True)
        self.paths.state_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "state": state,
                    "generation": 1,
                    "ownerPid": owner_pid,
                    "ownerStartedAt": self.runner.owner_start_time,
                    "serviceLabel": self.paths.service_label,
                    "servicePid": 4321,
                    "artifactPath": str(self.root / "sealed-artifact"),
                    "artifactSeal": "a" * 64,
                    "bridgeIdentity": {
                        "identifier": "com.alvr.macos-bridge",
                        "teamIdentifier": "TESTTEAM",
                        "cdHashes": ["0123456789abcdef0123456789abcdef01234567"],
                    },
                }
            )
        )

    def set_service(self, *, owned: bool = True) -> None:
        path = self.paths.launch_agent_plist if owned else self.root / "foreign.plist"
        program = self.paths.bridge_program if owned else self.root / "foreign-bridge"
        self.runner.service_output = "\n".join(
            [
                f"{self.paths.service_target} = {{",
                f"    path = {path}",
                "    state = running",
                f"    program = {program}",
                "    pid = 4321",
                "    runs = 1",
                "}",
            ]
        )

    def test_status_stopped_and_stop_is_idempotent(self) -> None:
        status = status_runtime(self.context)
        self.assertEqual(status.state, "stopped")
        first = stop_runtime(self.context)
        second = stop_runtime(self.context)
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(first.actions, ())
        self.assertEqual(second.actions, ())

    def test_status_distinguishes_ready_installed_and_invalid_artifact(self) -> None:
        artifact = self.root / "sealed-artifact"
        artifact_summary = {
            "path": str(artifact),
            "id": "mac-alvr-runtime",
            "version": "1.0.0-test",
            "sealId": "a" * 64,
        }
        core_passes = (
            CheckResult("repository.contract", "pass", "pass", "repair"),
            CheckResult("artifact.verify", "pass", "pass", "repair"),
            CheckResult("artifact.contract", "pass", "pass", "repair"),
        )
        with mock.patch(
            "runtime_control.doctor_runtime",
            return_value=DoctorReport(core_passes, artifact_summary),
        ):
            ready = status_runtime(self.context, artifact)
        self.assertEqual(ready.state, "ready")

        with mock.patch(
            "runtime_control.doctor_runtime",
            return_value=DoctorReport(
                (*core_passes, CheckResult("prerequisite.fixture", "fail", "fail", "repair")),
                artifact_summary,
            ),
        ):
            installed = status_runtime(self.context, artifact)
        self.assertEqual(installed.state, "installed")

        with mock.patch(
            "runtime_control.doctor_runtime",
            return_value=DoctorReport(
                (
                    CheckResult("repository.contract", "pass", "pass", "repair"),
                    CheckResult("artifact.verify", "fail", "fail", "repair"),
                    CheckResult("artifact.contract", "unknown", "unknown", "repair"),
                )
            ),
        ):
            invalid = status_runtime(self.context, artifact)
        self.assertEqual(invalid.state, "failed")
        self.assertEqual(invalid.reason_code, "artifact.invalid")

    def test_stale_lock_is_reported_and_removed(self) -> None:
        self.create_lock("2000")
        status = status_runtime(self.context)
        self.assertEqual(status.state, "failed")
        self.assertEqual(status.reason_code, "runtime.stale_state")
        stopped = stop_runtime(self.context)
        self.assertTrue(stopped.ok)
        self.assertFalse(self.paths.lock_path.exists())

    def test_malformed_lock_is_removed(self) -> None:
        self.create_lock("not-a-pid")
        stopped = stop_runtime(self.context)
        self.assertTrue(stopped.ok)
        self.assertFalse(self.paths.lock_path.exists())

    def test_live_owner_lock_is_not_removed(self) -> None:
        self.create_lock("2000")
        self.alive_pids.add(2000)
        stopped = stop_runtime(self.context)
        self.assertFalse(stopped.ok)
        self.assertEqual(stopped.reason_code, "lock.active_or_foreign")
        self.assertTrue(self.paths.lock_path.exists())

    def test_foreign_service_is_not_stopped(self) -> None:
        self.create_bridge()
        self.set_service(owned=False)
        stopped = stop_runtime(self.context)
        self.assertFalse(stopped.ok)
        self.assertEqual(stopped.reason_code, "service.foreign")
        self.assertFalse(any(command[:2] == ("/bin/launchctl", "bootout") for command in self.runner.commands))

    def test_signature_failure_prevents_stop(self) -> None:
        self.create_bridge()
        self.create_plist()
        self.set_service()
        self.runner.codesign_error = True
        stopped = stop_runtime(self.context)
        self.assertFalse(stopped.ok)
        self.assertEqual(stopped.reason_code, "service.signature_invalid")
        self.assertFalse(any(command[:2] == ("/bin/launchctl", "bootout") for command in self.runner.commands))

    def test_launchd_query_failure_is_not_reported_as_stopped(self) -> None:
        self.runner.query_error = CommandResult(
            ("/bin/launchctl", "print", self.paths.service_target),
            1,
            stderr="permission denied",
        )
        status = status_runtime(self.context)
        self.assertEqual(status.state, "failed")
        self.assertEqual(status.reason_code, "launchd.query_failed")

    def test_owned_service_is_booted_out_by_registered_path(self) -> None:
        self.create_bridge()
        self.create_plist()
        self.create_lock("2000")
        self.create_state(owner_pid=2000)
        self.set_service()
        stopped = stop_runtime(self.context)
        self.assertTrue(stopped.ok)
        self.assertIn(
            (
                "/bin/launchctl",
                "bootout",
                self.paths.service_domain,
                str(self.paths.launch_agent_plist),
            ),
            self.runner.commands,
        )
        self.assertFalse(any(command[:2] == ("/bin/launchctl", "kill") for command in self.runner.commands))
        self.assertFalse(self.paths.lock_path.exists())
        self.assertFalse(self.paths.state_path.exists())
        self.assertFalse(self.paths.launch_agent_plist.exists())

    def test_owned_service_absence_wins_over_bootout_exit_code(self) -> None:
        self.create_bridge()
        self.create_plist()
        self.set_service()
        self.runner.bootout_returncode = 5
        stopped = stop_runtime(self.context)
        self.assertTrue(stopped.ok)

    def test_symlinked_owned_service_path_is_refused(self) -> None:
        real_bundle = self.root / "real" / "ALVRMacOSBridge.app"
        real_program = real_bundle / "Contents" / "MacOS" / "alvr_macos_bridge"
        real_program.parent.mkdir(parents=True)
        real_program.write_bytes(b"fixture bridge")
        self.paths.bridge_bundle.symlink_to(real_bundle, target_is_directory=True)
        self.create_plist()
        self.runner.live_program = real_program
        self.set_service()
        stopped = stop_runtime(self.context)
        self.assertFalse(stopped.ok)
        self.assertEqual(stopped.reason_code, "path.symlink")

    def test_live_state_requires_synchronized_identity(self) -> None:
        self.create_bridge()
        self.create_plist()
        self.create_lock("2000")
        self.alive_pids.add(2000)
        self.set_service()
        missing_state = status_runtime(self.context)
        self.assertEqual(missing_state.state, "failed")
        self.assertEqual(missing_state.reason_code, "state.missing")

        self.create_state(owner_pid=2000)
        with mock.patch(
            "runtime_control.verify_artifact_reference",
            return_value={
                "path": str(self.root / "sealed-artifact"),
                "id": "mac-alvr-runtime",
                "version": "1.0.0-test",
                "sealId": "a" * 64,
            },
        ):
            streaming = status_runtime(self.context)
        self.assertEqual(streaming.state, "streaming")
        assert streaming.artifact is not None
        self.assertEqual(streaming.artifact["sealId"], "a" * 64)

    def test_live_identity_ignores_other_text_mappings(self) -> None:
        self.create_bridge()
        self.create_plist()
        self.create_lock("2000")
        self.alive_pids.add(2000)
        self.create_state(owner_pid=2000)
        self.set_service()
        self.runner.extra_text_paths.append(pathlib.Path("/usr/lib/dyld"))
        with mock.patch(
            "runtime_control.verify_artifact_reference",
            return_value={
                "path": str(self.root / "sealed-artifact"),
                "id": "mac-alvr-runtime",
                "version": "1.0.0-test",
                "sealId": "a" * 64,
            },
        ):
            status = status_runtime(self.context)
        self.assertEqual(status.state, "streaming")

    def test_reused_owner_pid_is_rejected_by_start_time(self) -> None:
        self.create_bridge()
        self.create_plist()
        self.create_lock("2000")
        self.alive_pids.add(2000)
        self.create_state(owner_pid=2000)
        self.set_service()
        self.runner.owner_start_time = "Sat Jul 18 04:00:00 2026"
        status = status_runtime(self.context)
        self.assertEqual(status.state, "failed")
        self.assertEqual(status.reason_code, "owner.identity_mismatch")

    def test_foreign_plist_is_preserved(self) -> None:
        self.create_bridge()
        self.create_plist(owned=False)
        stopped = stop_runtime(self.context)
        self.assertFalse(stopped.ok)
        self.assertEqual(stopped.reason_code, "service.plist_foreign")
        self.assertTrue(self.paths.launch_agent_plist.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
