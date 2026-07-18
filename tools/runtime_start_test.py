"""Hardware-free fixtures for the bounded runtime start supervisor."""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import plistlib
import tempfile
import threading
import time
import unittest
from collections.abc import Iterator, Sequence
from unittest import mock

from runtime_control import (
    CommandResult,
    ControlError,
    RuntimeContext,
    RuntimePaths,
    load_control_state,
    request_supervisor_ping,
    request_supervisor_stop,
)
from runtime_start import (
    BRIDGE_LOG_NAME,
    DeadlineRunner,
    STARTUP_RESULT_NAME,
    StartAdmission,
    StartReport,
    _install_plan_digest,
    _require_committed_install_journal,
    _require_installed_plan,
    _require_launch_template_state,
    start_runtime,
    supervise_runtime,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CODE_ROOT = pathlib.Path(os.environ.get("RUNTIME_FIXTURE_ROOT", REPO_ROOT / ".code"))
CODE_ROOT.mkdir(parents=True, exist_ok=True)


class SupervisorRunner:
    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        self.loaded = False
        self.bootstrap_returncode = 0
        self.kickstart_returncode = 0
        self.owner_started_at = "Sat Jul 18 04:00:00 2026"
        self.commands: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], *, timeout: float = 10.0) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        if command[:2] == ("/bin/launchctl", "print"):
            if not self.loaded:
                return CommandResult(command, 113, stderr="Could not find service")
            return CommandResult(
                command,
                0,
                stdout=(
                    f"path = {self.paths.launch_agent_plist}\n"
                    "state = running\n"
                    f"program = {self.paths.bridge_program}\n"
                    "pid = 4321\n"
                    "runs = 1\n"
                ),
            )
        if command[:2] == ("/bin/launchctl", "bootstrap"):
            if self.bootstrap_returncode == 0:
                self.loaded = True
            return CommandResult(command, self.bootstrap_returncode, stderr="fixture bootstrap")
        if command[:3] == ("/bin/launchctl", "kickstart", "-k"):
            if self.kickstart_returncode != 0:
                return CommandResult(command, self.kickstart_returncode, stderr="fixture kickstart")
            with self.paths.launch_agent_plist.open("rb") as stream:
                payload = plistlib.load(stream)
            log_path = pathlib.Path(payload["StandardOutPath"])
            log_path.write_text(
                f"native_source launchd service checked in name={self.paths.service_label}\n"
            )
            return CommandResult(command, 0)
        if command[:2] == ("/bin/launchctl", "bootout"):
            self.loaded = False
            return CommandResult(command, 0)
        if command and command[0] == "/usr/sbin/lsof":
            return CommandResult(command, 0, stdout=f"p4321\nn{self.paths.bridge_program}\n")
        if command and command[0] == "/usr/bin/codesign":
            return CommandResult(
                command,
                0,
                stderr=(
                    "Identifier=com.alvr.macos-bridge\n"
                    "TeamIdentifier=TESTTEAM\n"
                    "CDHash=0123456789abcdef0123456789abcdef01234567\n"
                ),
            )
        if command[:3] == ("/usr/bin/env", "LC_ALL=C", "/bin/ps"):
            return CommandResult(command, 0, stdout=f"{self.owner_started_at}\n")
        return CommandResult(command, 0)


class StartFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="rs-", dir=CODE_ROOT)
        self.root = pathlib.Path(self.temporary.name)
        self.state_root = self.root / "s"
        self.state_root.mkdir(mode=0o700)
        self.bridge_bundle = self.root / "ALVRMacOSBridge.app"
        self.bridge_program = self.bridge_bundle / "Contents/MacOS/alvr_macos_bridge"
        self.bridge_program.parent.mkdir(parents=True)
        self.bridge_program.write_bytes(b"fixture bridge")
        self.owner_marker = self.bridge_bundle / "Contents/Resources/runtime-owner.json"
        self.owner_marker.parent.mkdir(parents=True)
        self.owner_content = {"owner": "fixture"}
        self.owner_marker.write_text(json.dumps(self.owner_content))
        label = "com.alvr.macos-bridge.iosurface"
        self.paths = RuntimePaths(
            state_root=self.state_root,
            lock_path=self.state_root / "native-probe.lock",
            state_path=self.state_root / "runtime-state.json",
            launch_agent_plist=self.state_root / f"{label}.plist",
            bridge_bundle=self.bridge_bundle,
            bridge_program=self.bridge_program,
            bridge_owner_marker=self.owner_marker,
            bridge_owner_content=self.owner_content,
            service_domain=f"gui/{os.getuid()}",
            service_label=label,
            service_target=f"gui/{os.getuid()}/{label}",
        )
        self.artifact = self.root / "artifact"
        template = self.artifact / "config/launch-agent.plist.template"
        template.parent.mkdir(parents=True)
        with template.open("wb") as stream:
            plistlib.dump(
                {
                    "AssociatedBundleIdentifiers": [label],
                    "EnvironmentVariables": {
                        "ALVR_BRIDGE_CONNECT": "${ALVR_BRIDGE_CONNECT}",
                        "ALVR_BRIDGE_FRAMES": "${ALVR_BRIDGE_FRAMES}",
                        "ALVR_BRIDGE_INPUT": "iosurface",
                        "ALVR_BRIDGE_ROOT": "${ALVR_BRIDGE_ROOT}",
                        "ALVR_IOSURFACE_POOL_NONCE": "${ALVR_IOSURFACE_POOL_NONCE}",
                        "ALVR_IOSURFACE_POOL_SERVICE": label,
                    },
                    "Label": label,
                    "MachServices": {label: True},
                    "ProcessType": "Interactive",
                    "ProgramArguments": ["${NATIVE_BRIDGE_PROGRAM}"],
                    "StandardErrorPath": "${NATIVE_BRIDGE_LOG}",
                    "StandardOutPath": "${NATIVE_BRIDGE_LOG}",
                },
                stream,
                sort_keys=True,
            )
        self.runner = SupervisorRunner(self.paths)
        lifecycle_root = self.root / "lifecycle"
        lifecycle_root.mkdir(mode=0o700)
        self.context = RuntimeContext(
            bindings_path=self.root / "bindings.json",
            lifecycle_lock_path=lifecycle_root / "runtime.lock",
            runner=self.runner,
            pid_alive=lambda pid: pid == os.getpid(),
            sleeper=time.sleep,
        )
        self.admission = StartAdmission(
            manifest={},
            bindings={},
            paths=self.paths,
            allowed_roots=(self.root,),
            plan={},
            artifact={
                "id": "mac-alvr-runtime",
                "path": str(self.artifact),
                "sealId": "a" * 64,
                "stage": "sealed",
                "version": "1.0.0-test",
            },
            artifact_path=self.artifact,
        )

    def cleanup(self) -> None:
        self.temporary.cleanup()


class ImmediateProcess:
    def __init__(self, pid: int = 9000) -> None:
        self.pid = pid

    def poll(self) -> int | None:
        return None


class ExitedProcess(ImmediateProcess):
    def poll(self) -> int | None:
        return 1


class ExitedLauncher:
    def launch(self, argv: Sequence[str], log_path: pathlib.Path) -> ExitedProcess:
        log_path.write_text("fixture child exited\n")
        return ExitedProcess()


class RefusingLauncher:
    def launch(self, argv: Sequence[str], log_path: pathlib.Path) -> ImmediateProcess:
        raise AssertionError("idempotent start must not launch another supervisor")


class ImmediateLauncher:
    def __init__(self, artifact: dict[str, object], *, generation_offset: int = 0) -> None:
        self.artifact = artifact
        self.generation_offset = generation_offset
        self.command: tuple[str, ...] | None = None

    def launch(self, argv: Sequence[str], log_path: pathlib.Path) -> ImmediateProcess:
        self.command = tuple(str(item) for item in argv)
        log_path.write_text("fixture supervisor log\n")
        run_dir = pathlib.Path(self.command[self.command.index("--run-dir") + 1])
        generation = int(self.command[self.command.index("--generation") + 1])
        report = StartReport(
            True,
            "idle",
            "runtime.idle",
            "fixture supervisor ready",
            self.artifact,
            generation + self.generation_offset,
            9000,
            run_dir,
            log_path,
        )
        (run_dir / STARTUP_RESULT_NAME).write_text(json.dumps(report.to_dict()))
        return ImmediateProcess()


class RuntimeStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = StartFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_deadline_runner_caps_and_refuses_commands(self) -> None:
        runner = mock.Mock()
        runner.run.return_value = CommandResult(("fixture",), 0)
        bounded = DeadlineRunner(runner, 7.0, lambda: 5.0)
        result = bounded.run(["fixture"], timeout=10.0)
        self.assertEqual(result.returncode, 0)
        runner.run.assert_called_once_with(("fixture",), timeout=2.0)

        expired = DeadlineRunner(runner, 4.0, lambda: 5.0)
        timed_out = expired.run(["late"])
        self.assertEqual(timed_out.error, "timeout")
        self.assertEqual(runner.run.call_count, 1)

    def test_supervisor_reaches_idle_and_cooperatively_cleans(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000002a"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []

        def supervise() -> None:
            result.append(
                supervise_runtime(
                    self.fixture.context,
                    self.fixture.artifact,
                    42,
                    run_dir,
                )
            )

        with mock.patch(
            "runtime_start.resolve_context_paths_for_start",
            return_value=(
                {"allowedTargetRoots": [str(self.fixture.root)]},
                {},
                self.fixture.paths,
            ),
        ), mock.patch(
            "runtime_start.inspect_start_admission",
            return_value=self.fixture.admission,
        ):
            thread = threading.Thread(target=supervise)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.fixture.paths.state_path.exists():
                time.sleep(0.01)
            self.assertTrue(self.fixture.paths.state_path.exists())
            state = load_control_state(self.fixture.paths.state_path)
            self.assertTrue(state.valid)
            assert state.record is not None
            self.assertEqual(state.record["state"], "idle")
            responsive, ping_error = request_supervisor_ping(state.record)
            self.assertTrue(responsive, ping_error)
            accepted, error = request_supervisor_stop(state.record)
            self.assertTrue(accepted, error)
            self.fixture.runner.loaded = False
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].state, "stopped")
        self.assertFalse(self.fixture.paths.state_path.exists())
        self.assertFalse(self.fixture.paths.lock_path.exists())
        self.assertFalse(self.fixture.paths.launch_agent_plist.exists())
        self.assertFalse(run_dir.exists())

    def test_start_parent_returns_generation_bound_child_result(self) -> None:
        launcher = ImmediateLauncher(self.fixture.admission.artifact)
        with mock.patch(
            "runtime_start.doctor_runtime",
            return_value=mock.Mock(ok=True, artifact=self.fixture.admission.artifact),
        ), mock.patch(
            "runtime_start.inspect_start_admission",
            return_value=self.fixture.admission,
        ), mock.patch("runtime_start._idempotent_live_start", return_value=None):
            report = start_runtime(
                self.fixture.context,
                self.fixture.artifact,
                launcher=launcher,
                generation_factory=lambda: 7,
            )
        self.assertTrue(report.ok)
        self.assertEqual(report.state, "idle")
        self.assertEqual(report.generation, 7)
        assert launcher.command is not None
        self.assertTrue(launcher.command[1].endswith("tools/runtime_start.py"))

    def test_start_parent_rejects_another_generation_result(self) -> None:
        launcher = ImmediateLauncher(self.fixture.admission.artifact, generation_offset=1)
        with mock.patch(
            "runtime_start.doctor_runtime",
            return_value=mock.Mock(ok=True, artifact=self.fixture.admission.artifact),
        ), mock.patch(
            "runtime_start.inspect_start_admission",
            return_value=self.fixture.admission,
        ), mock.patch("runtime_start._idempotent_live_start", return_value=None):
            report = start_runtime(
                self.fixture.context,
                self.fixture.artifact,
                launcher=launcher,
                generation_factory=lambda: 7,
            )
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "runtime.start_failed")
        self.assertIn("another generation", report.message)

    def test_idempotent_live_start_does_not_spawn(self) -> None:
        live = StartReport(
            True,
            "idle",
            "runtime.idle",
            "already live",
            self.fixture.admission.artifact,
            4,
            123,
        )
        with mock.patch("runtime_start._idempotent_live_start", return_value=live):
            report = start_runtime(
                self.fixture.context,
                self.fixture.artifact,
                launcher=RefusingLauncher(),
            )
        self.assertIs(report, live)

    def test_parent_reports_child_exit_before_startup_state(self) -> None:
        with mock.patch(
            "runtime_start.doctor_runtime",
            return_value=mock.Mock(ok=True, artifact=self.fixture.admission.artifact),
        ), mock.patch(
            "runtime_start.inspect_start_admission",
            return_value=self.fixture.admission,
        ), mock.patch("runtime_start._idempotent_live_start", return_value=None):
            report = start_runtime(
                self.fixture.context,
                self.fixture.artifact,
                launcher=ExitedLauncher(),
                generation_factory=lambda: 8,
            )
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "runtime.start_failed")
        self.assertIn("exited before publishing", report.message)

    def test_bootstrap_failure_removes_only_created_live_state(self) -> None:
        self.fixture.runner.bootstrap_returncode = 5
        run_dir = self.fixture.state_root / "r-0000000000000009"
        run_dir.mkdir(mode=0o700)
        with mock.patch(
            "runtime_start.resolve_context_paths_for_start",
            return_value=(
                {"allowedTargetRoots": [str(self.fixture.root)]},
                {},
                self.fixture.paths,
            ),
        ), mock.patch(
            "runtime_start.inspect_start_admission",
            return_value=self.fixture.admission,
        ):
            report = supervise_runtime(
                self.fixture.context,
                self.fixture.artifact,
                9,
                run_dir,
            )
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "launchd.bootstrap_failed")
        self.assertFalse(self.fixture.paths.lock_path.exists())
        self.assertFalse(self.fixture.paths.state_path.exists())
        self.assertFalse(self.fixture.paths.launch_agent_plist.exists())
        self.assertTrue((run_dir / STARTUP_RESULT_NAME).exists())

    def test_lifecycle_lock_contention_blocks_before_owner_mutation(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000000b"
        run_dir.mkdir(mode=0o700)

        @contextlib.contextmanager
        def busy_lock(*_: object) -> Iterator[None]:
            raise ControlError("transaction.busy", "fixture lifecycle lock is busy")
            yield

        with mock.patch(
            "runtime_start.resolve_context_paths_for_start",
            return_value=(
                {"allowedTargetRoots": [str(self.fixture.root)]},
                {},
                self.fixture.paths,
            ),
        ), mock.patch("runtime_start.global_lifecycle_lock", busy_lock):
            report = supervise_runtime(
                self.fixture.context,
                self.fixture.artifact,
                11,
                run_dir,
            )
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "transaction.busy")
        self.assertFalse(self.fixture.paths.lock_path.exists())
        self.assertFalse(self.fixture.paths.launch_agent_plist.exists())

    def test_readiness_failure_boots_out_exact_created_service(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000000c"
        run_dir.mkdir(mode=0o700)
        with mock.patch(
            "runtime_start.resolve_context_paths_for_start",
            return_value=(
                {"allowedTargetRoots": [str(self.fixture.root)]},
                {},
                self.fixture.paths,
            ),
        ), mock.patch(
            "runtime_start.inspect_start_admission",
            return_value=self.fixture.admission,
        ), mock.patch(
            "runtime_start._service_ready",
            side_effect=ControlError("runtime.start_timeout", "fixture readiness timeout"),
        ):
            report = supervise_runtime(
                self.fixture.context,
                self.fixture.artifact,
                12,
                run_dir,
            )
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "runtime.start_timeout")
        self.assertFalse(self.fixture.runner.loaded)
        self.assertTrue(
            any(command[:2] == ("/bin/launchctl", "bootout") for command in self.fixture.runner.commands)
        )
        self.assertFalse(self.fixture.paths.lock_path.exists())
        self.assertFalse(self.fixture.paths.launch_agent_plist.exists())

    def test_cleanup_preserves_plist_changed_after_bootstrap(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000000d"
        run_dir.mkdir(mode=0o700)

        def tamper_then_fail(*_: object) -> object:
            with self.fixture.paths.launch_agent_plist.open("rb") as stream:
                payload = plistlib.load(stream)
            payload["KeepAlive"] = True
            with self.fixture.paths.launch_agent_plist.open("wb") as stream:
                plistlib.dump(payload, stream, sort_keys=True)
            raise ControlError("runtime.start_timeout", "fixture readiness timeout")

        with mock.patch(
            "runtime_start.resolve_context_paths_for_start",
            return_value=(
                {"allowedTargetRoots": [str(self.fixture.root)]},
                {},
                self.fixture.paths,
            ),
        ), mock.patch(
            "runtime_start.inspect_start_admission",
            return_value=self.fixture.admission,
        ), mock.patch(
            "runtime_start._service_ready",
            side_effect=tamper_then_fail,
        ):
            report = supervise_runtime(
                self.fixture.context,
                self.fixture.artifact,
                13,
                run_dir,
            )
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "runtime.cleanup_failed")
        self.assertIn("plist changed before cleanup", report.message)
        self.assertTrue(self.fixture.runner.loaded)
        self.assertTrue(self.fixture.paths.lock_path.exists())
        self.assertTrue(self.fixture.paths.launch_agent_plist.exists())

    def test_unrequested_service_exit_preserves_diagnostic_state(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000000a"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []

        def supervise() -> None:
            result.append(
                supervise_runtime(
                    self.fixture.context,
                    self.fixture.artifact,
                    10,
                    run_dir,
                )
            )

        with mock.patch(
            "runtime_start.resolve_context_paths_for_start",
            return_value=(
                {"allowedTargetRoots": [str(self.fixture.root)]},
                {},
                self.fixture.paths,
            ),
        ), mock.patch(
            "runtime_start.inspect_start_admission",
            return_value=self.fixture.admission,
        ):
            thread = threading.Thread(target=supervise)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.fixture.paths.state_path.exists():
                time.sleep(0.01)
            self.assertTrue(self.fixture.paths.state_path.exists())
            self.fixture.runner.loaded = False
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0].ok)
        self.assertEqual(result[0].reason_code, "service.exited")
        self.assertTrue(self.fixture.paths.lock_path.exists())
        self.assertTrue(self.fixture.paths.state_path.exists())
        self.assertTrue(self.fixture.paths.launch_agent_plist.exists())

    def test_installed_layout_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(Exception, "exact committed installed layout"):
            _require_installed_plan(
                {
                    "install": [
                        {
                            "id": "install_payload",
                            "resource": "payload",
                            "action": "create_file",
                            "ready": False,
                        }
                    ],
                    "uninstall": [],
                }
            )

    def test_committed_install_journal_must_match_semantic_plan(self) -> None:
        journal = self.fixture.root / "transaction.json"
        plan = {
            "mutableState": [
                {"id": "transaction_journal", "location": str(journal)},
            ],
            "install": [
                {
                    "id": "retain_fixture",
                    "resource": "fixture",
                    "action": "retain",
                    "target": str(self.fixture.root / "fixture"),
                    "ready": True,
                }
            ],
        }
        journal.write_text(
            json.dumps(
                {
                    "kind": "install",
                    "state": "committed",
                    "planDigest": _install_plan_digest(plan),
                    "cleanupFailures": [],
                    "rollbackFailures": [],
                    "failure": None,
                }
            )
        )
        journal.chmod(0o600)
        _require_committed_install_journal(plan)
        payload = json.loads(journal.read_text())
        payload["kind"] = "uninstall"
        journal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(Exception, "exact committed install"):
            _require_committed_install_journal(plan)

    def test_foreign_launch_agent_target_is_never_overwritten(self) -> None:
        self.fixture.paths.launch_agent_plist.write_text("foreign plist")
        with self.assertRaisesRegex(Exception, "neither absent nor the exact installed template"):
            _require_launch_template_state(self.fixture.admission)


if __name__ == "__main__":
    unittest.main(verbosity=2)
