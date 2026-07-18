"""Hardware-free fixtures for the bounded runtime start supervisor."""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import plistlib
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Iterator, Sequence
from unittest import mock

import runtime_profile
from runtime_control import (
    CommandResult,
    ControlError,
    RuntimeContext,
    RuntimePaths,
    load_control_state,
    process_start_time,
    request_supervisor_ping,
    request_supervisor_stop,
)
from runtime_start import (
    BRIDGE_LOG_NAME,
    DeadlineRunner,
    STARTUP_RESULT_NAME,
    ProfileStartAdmission,
    StartAdmission,
    StartReport,
    SubprocessProducerLauncher,
    _group_is_live,
    _install_plan_digest,
    _inspect_profile_admission,
    _inspect_producer_identity,
    _profile_record,
    _quiesce_producer,
    _require_committed_install_journal,
    _require_installed_plan,
    _require_launch_template_state,
    _resolve_crossover_launcher,
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
        self.launcher_started_at = "Sat Jul 18 04:00:01 2026"
        self.target_started_at = "Sat Jul 18 04:00:02 2026"
        self.producer_pid = 9001
        self.target_pid = 9002
        self.producer_group = 9001
        self.producer_live = False
        self.target_live = False
        self.producer_command = ""
        self.producer_root: pathlib.Path | None = None
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
            if str(self.target_pid) in command and self.producer_root is not None:
                return CommandResult(
                    command,
                    0 if self.target_live else 1,
                    stdout=f"p{self.target_pid}\nn{self.producer_root / 'FreedomLocomotion.exe'}\n"
                    if self.target_live
                    else "",
                )
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
            if "-axo" in command and "pid=,pgid=,command=" in command:
                output = (
                    f"{self.target_pid} {self.producer_group} {self.producer_command}\n"
                    if self.target_live
                    else ""
                )
                return CommandResult(command, 0, stdout=output)
            if "-axo" in command and "pid=,pgid=,stat=" in command:
                members: list[str] = []
                if self.producer_live:
                    members.append(f"{self.producer_pid} {self.producer_group} S")
                if self.target_live:
                    members.append(f"{self.target_pid} {self.producer_group} S")
                return CommandResult(command, 0, stdout="\n".join(members) + ("\n" if members else ""))
            pid = int(command[command.index("-p") + 1])
            if "lstart=" in command:
                started_at = {
                    os.getpid(): self.owner_started_at,
                    self.producer_pid: self.launcher_started_at,
                    self.target_pid: self.target_started_at,
                }.get(pid)
                return CommandResult(command, 0 if started_at else 1, stdout=f"{started_at}\n" if started_at else "")
            if "pgid=" in command:
                live = self.producer_live if pid == self.producer_pid else self.target_live
                return CommandResult(
                    command,
                    0 if live else 1,
                    stdout=f"{self.producer_group}\n" if live else "",
                )
            if "command=" in command and pid == self.target_pid and self.target_live:
                return CommandResult(command, 0, stdout=f"{self.producer_command}\n")
            return CommandResult(command, 1)
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
        install_root = self.root / "game"
        executable = install_root / "FreedomLocomotion.exe"
        graphics_directory = install_root / "FreedomLocomotion/Binaries/Win64"
        openvr_directory = install_root / "Engine/OpenVR"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture game")
        graphics_directory.mkdir(parents=True)
        openvr_directory.mkdir(parents=True)
        loaded_profile = runtime_profile.LoadedProfile(
            path=REPO_ROOT / "runtime/profiles/freedom-locomotion.json",
            data={
                "id": "freedom-locomotion",
                "source": {
                    "appId": "584170",
                    "buildId": "1797135",
                    "payload": {"fileCount": 1, "treeSha256": "e" * 64},
                },
                "launch": {
                    "entrypointTarget": "game",
                    "arguments": [],
                    "environment": {},
                    "startupTimeoutSeconds": 5,
                },
                "geometry": {"maximumStereo": {"width": 3240, "height": 1800}},
            },
            sha256="b" * 64,
        )
        installed_profile = runtime_profile.InstalledProfile(
            loaded=loaded_profile,
            install_root=install_root,
            steam_manifest=self.root / "appmanifest.acf",
            steam_manifest_sha256="c" * 64,
            targets=(
                runtime_profile.ResolvedProfileTarget(
                    id="game",
                    role="game",
                    executable=executable,
                    working_directory=install_root,
                    openvr_directory=openvr_directory,
                    graphics_directory=graphics_directory,
                    stock_openvr_sha256="d" * 64,
                    process_pattern="[F]reedomLocomotion",
                ),
            ),
        )
        self.profile = ProfileStartAdmission(
            installed=installed_profile,
            crossover_launcher=self.root / "cxstart",
            bottle_name="Steam",
            bridge_root=self.root / "bridge",
        )
        self.runner.producer_root = install_root
        self.admission = StartAdmission(
            manifest={},
            bindings={"CROSSOVER_APP": str(self.root / "CrossOver.app")},
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
            profile=self.profile,
        )

    def cleanup(self) -> None:
        self.temporary.cleanup()


class ImmediateProcess:
    def __init__(self, pid: int = 9000) -> None:
        self.pid = pid

    def poll(self) -> int | None:
        return None


class FixtureProducerProcess(ImmediateProcess):
    def __init__(self, runner: SupervisorRunner) -> None:
        super().__init__(runner.producer_pid)
        self.runner = runner

    def poll(self) -> int | None:
        return None if self.runner.producer_live else 0


class FixtureProducerLauncher:
    def __init__(self, fixture: StartFixture, *, publish_markers: bool = True) -> None:
        self.fixture = fixture
        self.publish_markers = publish_markers
        self.process = FixtureProducerProcess(fixture.runner)
        self.command: tuple[str, ...] | None = None

    def launch(
        self,
        argv: Sequence[str],
        working_directory: pathlib.Path,
        environment: dict[str, str],
        log_path: pathlib.Path,
    ) -> FixtureProducerProcess:
        del working_directory, environment
        self.command = tuple(str(item) for item in argv)
        log_path.write_text("fixture producer\n")
        self.fixture.runner.producer_live = True
        self.fixture.runner.target_live = True
        self.fixture.runner.producer_command = str(self.fixture.profile.installed.entrypoint.executable)
        if self.publish_markers:
            bridge_log = log_path.parent / BRIDGE_LOG_NAME
            with bridge_log.open("a") as stream:
                stream.write(
                    f"native_source producer handshake accepted service={self.fixture.paths.service_label} source=3240x1800\n"
                )
                stream.write("native_source startup self-tests passed slots=3\n")
        return self.process

    def group_id(self, pid: int) -> int:
        if pid != self.process.pid or not self.fixture.runner.producer_live:
            raise ProcessLookupError(pid)
        return self.fixture.runner.producer_group

    def group_signaler(self, process_group: int, signal_number: int) -> None:
        del signal_number
        if process_group != self.fixture.runner.producer_group:
            raise ProcessLookupError(process_group)
        self.fixture.runner.producer_live = False
        self.fixture.runner.target_live = False

    def group_live(self, process_group: int) -> bool:
        return process_group == self.fixture.runner.producer_group and (
            self.fixture.runner.producer_live or self.fixture.runner.target_live
        )


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
    def __init__(
        self,
        artifact: dict[str, object],
        profile: ProfileStartAdmission,
        *,
        generation_offset: int = 0,
    ) -> None:
        self.artifact = artifact
        self.profile = profile
        self.generation_offset = generation_offset
        self.command: tuple[str, ...] | None = None

    def launch(self, argv: Sequence[str], log_path: pathlib.Path) -> ImmediateProcess:
        self.command = tuple(str(item) for item in argv)
        log_path.write_text("fixture supervisor log\n")
        run_dir = pathlib.Path(self.command[self.command.index("--run-dir") + 1])
        generation = int(self.command[self.command.index("--generation") + 1])
        report = StartReport(
            True,
            "waiting",
            "runtime.waiting",
            "fixture supervisor ready",
            self.artifact,
            generation + self.generation_offset,
            9000,
            run_dir,
            log_path,
            (),
            _profile_record(self.profile),
            {"status": "ready"},
        )
        (run_dir / STARTUP_RESULT_NAME).write_text(json.dumps(report.to_dict()))
        return ImmediateProcess()


class ImmediateFailureLauncher:
    def __init__(self, artifact: dict[str, object]) -> None:
        self.artifact = artifact

    def launch(self, argv: Sequence[str], log_path: pathlib.Path) -> ImmediateProcess:
        command = tuple(str(item) for item in argv)
        log_path.write_text("fixture supervisor failure\n")
        run_dir = pathlib.Path(command[command.index("--run-dir") + 1])
        generation = int(command[command.index("--generation") + 1])
        report = StartReport(
            False,
            "failed",
            "producer.start_timeout",
            "fixture producer did not become ready",
            self.artifact,
            generation,
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

    def supervise_fixture(
        self,
        generation: int,
        run_dir: pathlib.Path,
        producer_launcher: FixtureProducerLauncher | None = None,
    ) -> StartReport:
        arguments = (
            self.fixture.context,
            self.fixture.artifact,
            self.fixture.profile.installed.loaded.data["id"],
            self.fixture.profile.installed.loaded.sha256,
            generation,
            run_dir,
        )
        if producer_launcher is None:
            return supervise_runtime(*arguments)
        return supervise_runtime(
            *arguments,
            producer_launcher=producer_launcher,
            group_id=producer_launcher.group_id,
            group_signaler=producer_launcher.group_signaler,
            group_live=producer_launcher.group_live,
        )

    def exact_profile_plan(self) -> dict[str, object]:
        target = self.fixture.profile.installed.entrypoint
        stock_openvr = target.openvr_directory / "openvr_api.dll"
        created = (
            target.openvr_directory / "openvr_api.real.dll",
            target.graphics_directory / "d3d11.dll",
            target.graphics_directory / "dxgi.dll",
            target.graphics_directory / "alvr_iosurface_bridge.dll",
        )
        install: list[dict[str, object]] = [
            {
                "action": "replace_file",
                "target": str(stock_openvr),
                "ready": True,
            }
        ]
        uninstall: list[dict[str, object]] = [
            {
                "action": "restore",
                "target": str(stock_openvr),
                "expectedSha256": target.stock_openvr_sha256,
                "ready": True,
            }
        ]
        for path in created:
            install.append({"action": "create_file", "target": str(path), "ready": True})
            uninstall.append({"action": "remove", "target": str(path), "ready": True})
        bridge_root = self.fixture.root / "runtime-bridge"
        install.extend(
            [
                {
                    "action": "create_file",
                    "resource": "wine_bridge_windows",
                    "target": str(bridge_root / "x86_64-windows/alvr_iosurface_bridge.dll"),
                    "ready": True,
                },
                {
                    "action": "create_file",
                    "resource": "wine_bridge_unix",
                    "target": str(bridge_root / "x86_64-unix/alvr_iosurface_bridge.so"),
                    "ready": True,
                },
            ]
        )
        return {"install": install, "uninstall": uninstall}

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

    def test_profile_admission_matches_every_exact_game_overlay_operation(self) -> None:
        crossover_app = self.fixture.root / "CrossOver.app"
        crossover_launcher = crossover_app / (
            "Contents/SharedSupport/CrossOver/CrossOver-Hosted Application/cxstart"
        )
        crossover_launcher.parent.mkdir(parents=True)
        crossover_target = crossover_launcher.parent / "wine"
        crossover_target.write_text("#!/bin/sh\n")
        crossover_target.chmod(0o755)
        crossover_launcher.symlink_to("wine")
        bindings = {
            "CROSSOVER_APP": str(crossover_app),
            "HOME": str(self.fixture.root),
            "STEAM_BOTTLE": str(
                self.fixture.root / "Library/Application Support/CrossOver/Bottles/Steam"
            ),
        }
        with mock.patch(
            "runtime_start.runtime_profile.load_curated_profile",
            return_value=self.fixture.profile.installed.loaded,
        ), mock.patch(
            "runtime_start.runtime_profile.resolve_installed_profile",
            return_value=self.fixture.profile.installed,
        ), mock.patch(
            "runtime_start.runtime_profile.payload_tree_identity",
            return_value=(1, "e" * 64),
        ):
            admission = _inspect_profile_admission(
                {},
                bindings,
                self.exact_profile_plan(),
                self.fixture.artifact,
                "freedom-locomotion",
            )
        self.assertEqual(admission.installed.loaded.data["id"], "freedom-locomotion")
        self.assertEqual(admission.crossover_launcher, crossover_launcher)

    def test_crossover_launcher_rejects_foreign_symlink_target(self) -> None:
        crossover_app = self.fixture.root / "CrossOver.app"
        launcher = crossover_app / (
            "Contents/SharedSupport/CrossOver/CrossOver-Hosted Application/cxstart"
        )
        launcher.parent.mkdir(parents=True)
        foreign = launcher.parent / "foreign"
        foreign.write_text("#!/bin/sh\n")
        foreign.chmod(0o755)
        launcher.symlink_to("foreign")
        with self.assertRaises(ControlError) as raised:
            _resolve_crossover_launcher(crossover_app)
        self.assertEqual(raised.exception.code, "producer.launch_invalid")

    def test_profile_admission_rejects_partial_game_overlay(self) -> None:
        plan = self.exact_profile_plan()
        install = plan["install"]
        assert isinstance(install, list)
        install.pop()
        with mock.patch(
            "runtime_start.runtime_profile.load_curated_profile",
            return_value=self.fixture.profile.installed.loaded,
        ), mock.patch(
            "runtime_start.runtime_profile.resolve_installed_profile",
            return_value=self.fixture.profile.installed,
        ):
            with self.assertRaises(ControlError) as raised:
                _inspect_profile_admission(
                    {},
                    {
                        "CROSSOVER_APP": str(self.fixture.root / "CrossOver.app"),
                        "HOME": str(self.fixture.root),
                        "STEAM_BOTTLE": str(
                            self.fixture.root
                            / "Library/Application Support/CrossOver/Bottles/Steam"
                        ),
                    },
                    plan,
                    self.fixture.artifact,
                    "freedom-locomotion",
                )
        self.assertEqual(raised.exception.code, "profile.artifact_mismatch")

    def test_supervisor_reaches_idle_and_cooperatively_cleans(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000002a"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []
        producer_launcher = FixtureProducerLauncher(self.fixture)

        def supervise() -> None:
            result.append(self.supervise_fixture(42, run_dir, producer_launcher))

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
            state = load_control_state(self.fixture.paths.state_path)
            while time.monotonic() < deadline and (
                not state.valid
                or state.record is None
                or state.record.get("state") != "waiting"
            ):
                time.sleep(0.01)
                state = load_control_state(self.fixture.paths.state_path)
            self.assertTrue(state.valid)
            assert state.record is not None
            self.assertEqual(state.record["state"], "waiting")
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

    def test_stop_during_producer_startup_quiesces_before_bridge_cleanup(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000002b"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []
        producer_launcher = FixtureProducerLauncher(self.fixture, publish_markers=False)

        def supervise() -> None:
            result.append(self.supervise_fixture(43, run_dir, producer_launcher))

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
            state = load_control_state(self.fixture.paths.state_path)
            while time.monotonic() < deadline and (
                not state.valid
                or state.record is None
                or state.record.get("state") != "idle"
            ):
                time.sleep(0.01)
                state = load_control_state(self.fixture.paths.state_path)
            self.assertTrue(state.valid)
            assert state.record is not None
            self.assertEqual(state.record["producer"]["status"], "starting")
            accepted, error = request_supervisor_stop(state.record)
            self.assertTrue(accepted, error)
            self.fixture.runner.loaded = False
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].state, "stopped")
        self.assertFalse(producer_launcher.group_live(self.fixture.runner.producer_group))
        self.assertFalse(run_dir.exists())

    def test_producer_start_timeout_quiesces_and_cleans_exact_generation(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000002c"
        run_dir.mkdir(mode=0o700)
        producer_launcher = FixtureProducerLauncher(self.fixture, publish_markers=False)
        self.fixture.profile.installed.loaded.data["launch"]["startupTimeoutSeconds"] = 1
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
            report = self.supervise_fixture(44, run_dir, producer_launcher)
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "producer.start_timeout")
        self.assertFalse(producer_launcher.group_live(self.fixture.runner.producer_group))
        self.assertFalse(self.fixture.runner.loaded)
        self.assertFalse(self.fixture.paths.state_path.exists())
        self.assertFalse(self.fixture.paths.lock_path.exists())

    def test_unrequested_producer_exit_preserves_diagnostic_state(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000002d"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []
        producer_launcher = FixtureProducerLauncher(self.fixture)

        def supervise() -> None:
            result.append(self.supervise_fixture(45, run_dir, producer_launcher))

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
            state = load_control_state(self.fixture.paths.state_path)
            while time.monotonic() < deadline and (
                not state.valid
                or state.record is None
                or state.record.get("state") != "waiting"
            ):
                time.sleep(0.01)
                state = load_control_state(self.fixture.paths.state_path)
            self.fixture.runner.producer_live = False
            self.fixture.runner.target_live = False
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].reason_code, "producer.exited")
        self.assertTrue(self.fixture.paths.state_path.exists())
        self.assertTrue(self.fixture.paths.lock_path.exists())
        self.assertTrue(self.fixture.runner.loaded)

    def test_producer_identity_drift_preserves_diagnostic_state(self) -> None:
        run_dir = self.fixture.state_root / "r-000000000000002e"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []
        producer_launcher = FixtureProducerLauncher(self.fixture)

        def supervise() -> None:
            result.append(self.supervise_fixture(46, run_dir, producer_launcher))

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
            state = load_control_state(self.fixture.paths.state_path)
            while time.monotonic() < deadline and (
                not state.valid
                or state.record is None
                or state.record.get("state") != "waiting"
            ):
                time.sleep(0.01)
                state = load_control_state(self.fixture.paths.state_path)
            self.fixture.runner.target_started_at = "Sat Jul 18 05:00:02 2026"
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].reason_code, "producer.identity_changed")
        self.assertTrue(self.fixture.paths.state_path.exists())
        self.assertTrue(self.fixture.runner.loaded)

    def test_real_process_group_cleanup_reaps_parent_and_child(self) -> None:
        run_dir = self.fixture.root / "process-group"
        run_dir.mkdir()
        producer_log = run_dir / "producer.log"
        launcher = SubprocessProducerLauncher()
        process = launcher.launch(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess,sys,time; "
                    "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                    "time.sleep(60)"
                ),
            ],
            run_dir,
            os.environ.copy(),
            producer_log,
        )
        context = RuntimeContext()
        try:
            started_at, error = process_start_time(process.pid, context.runner)
            self.assertIsNotNone(started_at, error)
            assert started_at is not None
            process_group = os.getpgid(process.pid)
            _quiesce_producer(
                process,
                started_at,
                process_group,
                self.fixture.profile,
                context,
                time.monotonic,
                os.getpgid,
                os.killpg,
                _group_is_live,
            )
            self.assertIsNotNone(process.poll())
            self.assertFalse(_group_is_live(process_group))
        finally:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, 9)

    def test_producer_identity_filters_process_group_before_lsof(self) -> None:
        self.fixture.runner.target_live = True
        self.fixture.runner.producer_command = str(
            self.fixture.profile.installed.entrypoint.executable
        )
        original_run = self.fixture.runner.run

        def run(argv: Sequence[str], *, timeout: float = 10.0) -> CommandResult:
            command = tuple(str(item) for item in argv)
            if command[:3] == ("/usr/bin/env", "LC_ALL=C", "/bin/ps") and (
                "pid=,pgid=,command=" in command
            ):
                self.fixture.runner.commands.append(command)
                return CommandResult(
                    command,
                    0,
                    stdout=(
                        "7001 7001 unrelated-daemon\n"
                        f"{self.fixture.runner.target_pid} "
                        f"{self.fixture.runner.producer_group} "
                        f"{self.fixture.runner.producer_command}\n"
                    ),
                )
            return original_run(command, timeout=timeout)

        with mock.patch.object(self.fixture.runner, "run", side_effect=run):
            identity = _inspect_producer_identity(
                self.fixture.profile,
                self.fixture.runner.producer_group,
                self.fixture.runner,
            )
        self.assertIsNotNone(identity)
        lsof_commands = [
            command
            for command in self.fixture.runner.commands
            if command and command[0] == "/usr/sbin/lsof"
        ]
        self.assertEqual(
            lsof_commands,
            [
                (
                    "/usr/sbin/lsof",
                    "-a",
                    "-p",
                    str(self.fixture.runner.target_pid),
                    "-Fn",
                )
            ],
        )

    def test_start_parent_returns_generation_bound_child_result(self) -> None:
        launcher = ImmediateLauncher(self.fixture.admission.artifact, self.fixture.profile)
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
                "freedom-locomotion",
                launcher=launcher,
                generation_factory=lambda: 7,
            )
        self.assertTrue(report.ok)
        self.assertEqual(report.state, "waiting")
        self.assertEqual(report.generation, 7)
        assert launcher.command is not None
        self.assertTrue(launcher.command[1].endswith("tools/runtime_start.py"))

    def test_start_parent_preserves_generation_bound_child_failure(self) -> None:
        launcher = ImmediateFailureLauncher(self.fixture.admission.artifact)
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
                "freedom-locomotion",
                launcher=launcher,
                generation_factory=lambda: 7,
            )
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "producer.start_timeout")
        self.assertIn("did not become ready", report.message)

    def test_start_parent_rejects_another_generation_result(self) -> None:
        launcher = ImmediateLauncher(
            self.fixture.admission.artifact,
            self.fixture.profile,
            generation_offset=1,
        )
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
                "freedom-locomotion",
                launcher=launcher,
                generation_factory=lambda: 7,
            )
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "runtime.start_failed")
        self.assertIn("another generation", report.message)

    def test_idempotent_live_start_does_not_spawn(self) -> None:
        live = StartReport(
            True,
            "waiting",
            "runtime.waiting",
            "already live",
            self.fixture.admission.artifact,
            4,
            123,
        )
        with mock.patch("runtime_start._idempotent_live_start", return_value=live):
            report = start_runtime(
                self.fixture.context,
                self.fixture.artifact,
                "freedom-locomotion",
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
                "freedom-locomotion",
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
            report = self.supervise_fixture(9, run_dir)
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
            report = self.supervise_fixture(11, run_dir)
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
            report = self.supervise_fixture(12, run_dir)
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
            report = self.supervise_fixture(13, run_dir)
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
        producer_launcher = FixtureProducerLauncher(self.fixture)

        def supervise() -> None:
            result.append(self.supervise_fixture(10, run_dir, producer_launcher))

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
            state = load_control_state(self.fixture.paths.state_path)
            while time.monotonic() < deadline and (
                not state.valid
                or state.record is None
                or state.record.get("state") != "waiting"
            ):
                time.sleep(0.01)
                state = load_control_state(self.fixture.paths.state_path)
            self.assertTrue(state.valid)
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
