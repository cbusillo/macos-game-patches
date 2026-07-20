"""Hardware-free fixtures for the bounded runtime start supervisor."""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import plistlib
import shlex
import struct
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from typing import Any
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
    stop_runtime,
)
from runtime_start import (
    BRIDGE_LOG_NAME,
    ALVR_CLIENT_STATE_CONNECTED,
    ALVR_CLIENT_STATE_STREAMING,
    ALVR_CLIENT_STATE_WAITING,
    ALVR_SHM_HEADER_SIZE,
    ALVR_SHM_MAGIC,
    ALVR_SHM_TELEMETRY_SEQUENCE_OFFSET,
    ALVR_SHM_VERSION,
    ClientTelemetry,
    ClientTelemetryMonitor,
    DeadlineRunner,
    ProducerIdentity,
    STARTUP_RESULT_NAME,
    ProfileStartAdmission,
    StartAdmission,
    StartReport,
    SubprocessProducerLauncher,
    _confirm_producer_identity,
    _group_is_live,
    _install_plan_digest,
    _inspect_profile_admission,
    _inspect_producer_identity,
    _profile_record,
    _producer_markers_ready,
    _quiesce_producer,
    _require_committed_install_journal,
    _require_installed_plan,
    _require_launch_template_state,
    _resolve_crossover_launcher,
    read_client_telemetry,
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
        self.producer_birth_token = 9_001_001
        self.target_birth_token = 9_002_002
        self.producer_pid_version = 101
        self.target_pid_version = 202
        self.producer_group = 9001
        self.target_group = 9002
        self.producer_live = False
        self.target_live = False
        self.target_lsof_error = False
        self.producer_command = ""
        self.producer_root: pathlib.Path | None = None
        self.service_state = "running"
        self.service_pid: int | None = 4321
        self.transition_on_quiesce = False
        self.foreign_on_quiesce = False
        self.mutate_bridge_on_quiesce = False
        self.transition_observed = threading.Event()
        self.service_path = self.paths.launch_agent_plist
        self.service_program = self.paths.bridge_program
        self.signals: list[tuple[int, int]] = []
        self.commands: list[tuple[str, ...]] = []

    def birth_token(self, pid: int) -> tuple[int | None, str | None]:
        token = {
            self.producer_pid: self.producer_birth_token,
            self.target_pid: self.target_birth_token,
        }.get(pid)
        return (token, None) if token is not None else (None, "fixture process missing")

    def pid_version(self, pid: int) -> tuple[int | None, str | None]:
        version = {
            self.producer_pid: self.producer_pid_version,
            self.target_pid: self.target_pid_version,
        }.get(pid)
        return (version, None) if version is not None else (None, "fixture process missing")

    def run(self, argv: Sequence[str], *, timeout: float = 10.0) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        if command[:2] == ("/bin/launchctl", "print"):
            if not self.loaded:
                return CommandResult(command, 113, stderr="Could not find service")
            if self.service_state != "running":
                self.transition_observed.set()
            pid_line = f"pid = {self.service_pid}\n" if self.service_pid is not None else ""
            return CommandResult(
                command,
                0,
                stdout=(
                    f"path = {self.service_path}\n"
                    f"state = {self.service_state}\n"
                    f"program = {self.service_program}\n"
                    f"{pid_line}"
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
                if self.target_lsof_error:
                    return CommandResult(command, None, error="timeout")
                return CommandResult(
                    command,
                    0 if self.target_live else 1,
                    stdout=(
                        f"p{self.target_pid}\n"
                        f"n{self.producer_root / 'FreedomLocomotion/Binaries/Win64/FreedomLocomotion-Win64-Shipping.exe'}\n"
                    )
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
                    f"{self.target_pid} {self.target_group} {self.producer_command}\n"
                    if self.target_live
                    else ""
                )
                return CommandResult(command, 0, stdout=output)
            if "-axo" in command and "pid=,pgid=,stat=" in command:
                members: list[str] = []
                if self.producer_live:
                    members.append(f"{self.producer_pid} {self.producer_group} S")
                if self.target_live:
                    members.append(f"{self.target_pid} {self.target_group} S")
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
                process_group = (
                    self.producer_group if pid == self.producer_pid else self.target_group
                )
                return CommandResult(
                    command,
                    0 if live else 1,
                    stdout=f"{process_group}\n" if live else "",
                )
            if "command=" in command and pid == self.target_pid and self.target_live:
                return CommandResult(command, 0, stdout=f"{self.producer_command}\n")
            return CommandResult(command, 1)
        return CommandResult(command, 0)


class FixtureClientTelemetry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.runtime_generation = 1
        self.bridge_pid = 4321
        self.bridge_session_id = 7_001
        self.bridge_heartbeat_ns = 1
        self.sequence = 2
        self.client_state = ALVR_CLIENT_STATE_WAITING
        self.stream_contract_valid = False
        self.stream_epoch = 0
        self.frames_transported = 0
        self.connect_events = 0
        self.disconnect_events = 0
        self.contract_failure_events = 0
        self.advance_heartbeat = True

    def bind(self, runtime_generation: int, bridge_pid: int) -> None:
        with self.lock:
            self.runtime_generation = runtime_generation
            self.bridge_pid = bridge_pid

    def read(self) -> ClientTelemetry:
        with self.lock:
            if self.advance_heartbeat:
                self.bridge_heartbeat_ns += 1
            return ClientTelemetry(
                self.sequence,
                self.client_state,
                self.stream_contract_valid,
                self.runtime_generation,
                self.bridge_pid,
                self.bridge_session_id,
                self.bridge_heartbeat_ns,
                self.stream_epoch,
                self.frames_transported,
                self.connect_events,
                self.disconnect_events,
                self.contract_failure_events,
            )

    def connect(self) -> None:
        with self.lock:
            self.stream_epoch += 1
            self.connect_events += 1
            self.client_state = ALVR_CLIENT_STATE_CONNECTED
            self.stream_contract_valid = True
            self.sequence += 2

    def transport_frame(self) -> None:
        with self.lock:
            self.client_state = ALVR_CLIENT_STATE_STREAMING
            self.frames_transported += 1
            self.sequence += 2

    def disconnect(self) -> None:
        with self.lock:
            self.stream_epoch += 1
            self.disconnect_events += 1
            self.client_state = ALVR_CLIENT_STATE_WAITING
            self.stream_contract_valid = False
            self.sequence += 2


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
        self.telemetry = FixtureClientTelemetry()
        lifecycle_root = self.root / "lifecycle"
        lifecycle_root.mkdir(mode=0o700)
        self.context = RuntimeContext(
            bindings_path=self.root / "bindings.json",
            lifecycle_lock_path=lifecycle_root / "runtime.lock",
            runner=self.runner,
            pid_alive=lambda pid: pid == os.getpid(),
            birth_token_reader=self.runner.birth_token,
            pid_version_reader=self.runner.pid_version,
            sleeper=time.sleep,
        )
        install_root = self.root / "game"
        executable = install_root / "FreedomLocomotion.exe"
        owned_executable = (
            install_root
            / "FreedomLocomotion/Binaries/Win64/FreedomLocomotion-Win64-Shipping.exe"
        )
        graphics_directory = install_root / "FreedomLocomotion/Binaries/Win64"
        openvr_directory = install_root / "Engine/OpenVR"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture game")
        graphics_directory.mkdir(parents=True)
        owned_executable.write_bytes(b"fixture shipping game")
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
                    "ownedProcess": {
                        "executable": "FreedomLocomotion/Binaries/Win64/FreedomLocomotion-Win64-Shipping.exe",
                        "processPattern": "[F]reedomLocomotion-Win64-Shipping\\.exe",
                    },
                    "arguments": [],
                    "environment": {},
                    "startupTimeoutSeconds": 5,
                    "transitionTimeoutSeconds": 1,
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
            owned_process=runtime_profile.ResolvedOwnedProcess(
                executable=owned_executable,
                process_pattern="[F]reedomLocomotion-Win64-Shipping\\.exe",
            ),
        )
        self.profile = ProfileStartAdmission(
            installed=installed_profile,
            crossover_launcher=self.root / "cxstart",
            bottle_name="Steam",
            bridge_root=self.root / "bridge",
        )
        self.alvr_session_root = self.root / "retained-alvr-session"
        self.alvr_session_root.mkdir(mode=0o700)
        self.alvr_session_path = self.alvr_session_root / "session.json"
        self.alvr_session_path.write_text(
            json.dumps(
                {
                    "client_connections": {
                        "fixture.client.alvr": {
                            "display_name": "Fixture",
                            "current_ip": "192.0.2.1",
                            "manual_ips": ["192.0.2.1"],
                            "trusted": True,
                            "connection_state": "Streaming",
                        },
                        "untrusted.client.alvr": {
                            "display_name": "Untrusted",
                            "current_ip": None,
                            "manual_ips": [],
                            "trusted": False,
                            "connection_state": "Disconnected",
                        },
                    }
                }
            )
        )
        self.runner.producer_root = install_root
        self.admission = StartAdmission(
            manifest={
                "mutableState": [
                    {
                        "id": "alvr_session_state",
                        "location": str(self.alvr_session_root),
                    }
                ]
            },
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
    def __init__(
        self,
        fixture: StartFixture,
        *,
        publish_markers: bool = True,
        launch_target: bool | None = None,
        replace_service_after_markers: bool = False,
    ) -> None:
        self.fixture = fixture
        self.publish_markers = publish_markers
        self.launch_target = publish_markers if launch_target is None else launch_target
        self.replace_service_after_markers = replace_service_after_markers
        self.process = FixtureProducerProcess(fixture.runner)
        self.command: tuple[str, ...] | None = None

    def launch(
        self,
        argv: Sequence[str],
        working_directory: pathlib.Path,
        environment: dict[str, str],
        log_path: pathlib.Path,
    ) -> FixtureProducerProcess:
        del working_directory
        self.command = tuple(str(item) for item in argv)
        log_path.write_text("fixture producer\n")
        self.fixture.runner.producer_live = True
        self.fixture.runner.target_live = self.launch_target
        assert self.fixture.profile.installed.owned_process is not None
        self.fixture.runner.producer_command = str(
            self.fixture.profile.installed.owned_process.executable
        )
        if self.publish_markers:
            generation = "1"
            if "--env" in self.command:
                environment_values = dict(
                    item.split("=", 1)
                    for item in shlex.split(self.command[self.command.index("--env") + 1])
                )
                generation = environment_values["ALVR_IOSURFACE_POOL_NONCE"]
            bridge_log = log_path.parent / BRIDGE_LOG_NAME
            with bridge_log.open("a") as stream:
                stream.write(
                    "native_source producer handshake accepted "
                    f"service={self.fixture.paths.service_label} "
                    f"nonce={generation} bridge_pid=4321 "
                    f"producer_pid={self.fixture.runner.target_pid} "
                    f"producer_pidversion={self.fixture.runner.target_pid_version} "
                    f"producer_start_token={self.fixture.runner.target_birth_token} "
                    "source=3240x1800\n"
                )
                stream.write("native_source startup self-tests passed slots=3\n")
        if self.replace_service_after_markers:
            self.fixture.runner.service_pid = 4322
        return self.process

    def group_id(self, pid: int) -> int:
        if pid != self.process.pid or not self.fixture.runner.producer_live:
            raise ProcessLookupError(pid)
        return self.fixture.runner.producer_group

    def group_signaler(self, process_group: int, signal_number: int) -> None:
        self.fixture.runner.signals.append((process_group, signal_number))
        if (
            process_group == self.fixture.runner.producer_group
            and process_group == self.fixture.runner.target_group
        ):
            self.fixture.runner.producer_live = False
            self.fixture.runner.target_live = False
            return
        if process_group == self.fixture.runner.target_group:
            self.fixture.runner.target_live = False
            return
        if process_group != self.fixture.runner.producer_group:
            raise ProcessLookupError(process_group)
        self.fixture.runner.producer_live = False
        if self.fixture.runner.transition_on_quiesce:
            self.fixture.runner.service_state = "exited"
            self.fixture.runner.service_pid = None
        if self.fixture.runner.foreign_on_quiesce:
            self.fixture.runner.service_path = self.fixture.root / "foreign.plist"
            self.fixture.runner.service_program = self.fixture.root / "foreign-bridge"
        if self.fixture.runner.mutate_bridge_on_quiesce:
            self.fixture.paths.bridge_program.write_bytes(b"foreign replacement")

    def group_live(self, process_group: int) -> bool:
        if process_group == self.fixture.runner.producer_group:
            return self.fixture.runner.producer_live or (
                self.fixture.runner.target_group == process_group
                and self.fixture.runner.target_live
            )
        if process_group == self.fixture.runner.target_group:
            return self.fixture.runner.target_live
        return False


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
        telemetry_reader: Callable[[], ClientTelemetry] | None = None,
    ) -> StartReport:
        self.fixture.telemetry.bind(generation, 4321)
        read_telemetry = telemetry_reader or self.fixture.telemetry.read
        arguments = (
            self.fixture.context,
            self.fixture.artifact,
            self.fixture.profile.installed.loaded.data["id"],
            self.fixture.profile.installed.loaded.sha256,
            generation,
            run_dir,
        )
        if producer_launcher is None:
            return supervise_runtime(*arguments, telemetry_reader=read_telemetry)
        return supervise_runtime(
            *arguments,
            producer_launcher=producer_launcher,
            telemetry_reader=read_telemetry,
            group_id=producer_launcher.group_id,
            group_signaler=producer_launcher.group_signaler,
            group_live=producer_launcher.group_live,
        )

    def wait_for_state(
        self,
        state_name: str,
        *,
        producer_status: str | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = load_control_state(self.fixture.paths.state_path)
            if state.valid and state.record is not None:
                producer = state.record.get("producer")
                if state.record.get("state") == state_name and (
                    producer_status is None
                    or (
                        isinstance(producer, dict)
                        and producer.get("status") == producer_status
                    )
                ):
                    return state.record
            time.sleep(0.01)
        self.fail(
            f"runtime did not reach state={state_name} producer={producer_status}"
        )

    def wait_for_waiting_startup(self, run_dir: pathlib.Path) -> dict[str, Any]:
        result_path = run_dir / STARTUP_RESULT_NAME
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not result_path.exists():
            time.sleep(0.01)
        self.assertTrue(result_path.exists())
        result = json.loads(result_path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "waiting")
        return self.wait_for_state("waiting", producer_status="ready")

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

    def test_profile_without_owned_process_fails_before_plan_inspection(self) -> None:
        incomplete = replace(self.fixture.profile.installed, owned_process=None)
        with mock.patch(
            "runtime_start.runtime_profile.load_curated_profile",
            return_value=self.fixture.profile.installed.loaded,
        ), mock.patch(
            "runtime_start.runtime_profile.resolve_installed_profile",
            return_value=incomplete,
        ), mock.patch(
            "runtime_start._require_plan_operation",
        ) as inspect_plan:
            with self.assertRaises(ControlError) as raised:
                _inspect_profile_admission(
                    {},
                    {},
                    {},
                    self.fixture.artifact,
                    "freedom-locomotion",
                )
        self.assertEqual(raised.exception.code, "profile.artifact_mismatch")
        inspect_plan.assert_not_called()

    def test_preexisting_owned_process_fails_before_service_or_launch(self) -> None:
        run_dir = self.fixture.state_root / "r-0000000000000033"
        run_dir.mkdir(mode=0o700)
        producer_launcher = FixtureProducerLauncher(self.fixture)
        assert self.fixture.profile.installed.owned_process is not None
        self.fixture.runner.target_live = True
        self.fixture.runner.producer_command = str(
            self.fixture.profile.installed.owned_process.executable
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
            report = self.supervise_fixture(51, run_dir, producer_launcher)
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "producer.already_running")
        self.assertIsNone(producer_launcher.command)
        self.assertFalse(self.fixture.runner.loaded)
        self.assertFalse(self.fixture.paths.lock_path.exists())
        self.fixture.runner.target_live = False

    def test_unreadable_preexisting_owned_process_fails_before_launch(self) -> None:
        run_dir = self.fixture.state_root / "r-0000000000000034"
        run_dir.mkdir(mode=0o700)
        producer_launcher = FixtureProducerLauncher(self.fixture)
        assert self.fixture.profile.installed.owned_process is not None
        self.fixture.runner.target_live = True
        self.fixture.runner.target_lsof_error = True
        self.fixture.runner.producer_command = str(
            self.fixture.profile.installed.owned_process.executable
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
            report = self.supervise_fixture(52, run_dir, producer_launcher)
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "producer.identity_unavailable")
        self.assertIsNone(producer_launcher.command)
        self.assertFalse(self.fixture.runner.loaded)
        self.assertFalse(self.fixture.paths.lock_path.exists())
        self.fixture.runner.target_lsof_error = False
        self.fixture.runner.target_live = False

    def test_service_replacement_after_markers_blocks_waiting_publication(self) -> None:
        run_dir = self.fixture.state_root / "r-0000000000000035"
        run_dir.mkdir(mode=0o700)
        producer_launcher = FixtureProducerLauncher(
            self.fixture,
            replace_service_after_markers=True,
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
            report = self.supervise_fixture(53, run_dir, producer_launcher)
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "service.identity_changed")
        state = load_control_state(self.fixture.paths.state_path)
        self.assertTrue(state.record is None or state.record["state"] != "waiting")
        self.fixture.runner.loaded = False
        self.fixture.runner.producer_live = False
        self.fixture.runner.target_live = False

    def test_post_launch_identity_failure_preserves_ownership_evidence(self) -> None:
        run_dir = self.fixture.state_root / "r-0000000000000036"
        run_dir.mkdir(mode=0o700)
        producer_launcher = FixtureProducerLauncher(
            self.fixture,
            publish_markers=False,
            launch_target=False,
        )

        def unavailable_launcher_birth(pid: int) -> tuple[int | None, str | None]:
            if pid == self.fixture.runner.producer_pid:
                return None, "fixture launcher identity unavailable"
            return self.fixture.runner.birth_token(pid)

        self.fixture.context = replace(
            self.fixture.context,
            birth_token_reader=unavailable_launcher_birth,
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
            report = self.supervise_fixture(54, run_dir, producer_launcher)
        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "producer.identity_unavailable")
        state = load_control_state(self.fixture.paths.state_path)
        self.assertTrue(state.valid)
        assert state.record is not None
        producer = state.record["producer"]
        assert isinstance(producer, dict)
        self.assertEqual(producer["status"], "launching")
        self.assertIsNone(producer["launcher"])
        with mock.patch(
            "runtime_control.resolve_context_paths",
            return_value=({}, {}, self.fixture.paths),
        ):
            stopped = stop_runtime(
                replace(
                    self.fixture.context,
                    pid_alive=lambda _: False,
                )
            )
        self.assertFalse(stopped.ok)
        self.assertEqual(stopped.reason_code, "producer.identity_unavailable")
        self.assertTrue(self.fixture.paths.state_path.exists())
        self.fixture.runner.loaded = False
        self.fixture.runner.producer_live = False

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
            record = self.wait_for_waiting_startup(run_dir)
            producer = record["producer"]
            assert isinstance(producer, dict)
            launcher_state = producer["launcher"]
            owned_process = producer["ownedProcess"]
            assert isinstance(launcher_state, dict)
            assert isinstance(owned_process, dict)
            self.assertNotIn("pidVersion", launcher_state)
            self.assertEqual(
                owned_process["pidVersion"],
                self.fixture.runner.target_pid_version,
            )
            self.fixture.runner.producer_pid_version += 1
            responsive, ping_error = request_supervisor_ping(record)
            self.assertTrue(responsive, ping_error)
            accepted, error = request_supervisor_stop(record)
            self.assertTrue(accepted, error)
            self.fixture.runner.loaded = False
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].state, "stopped")
        self.assertFalse(self.fixture.paths.state_path.exists())
        self.assertFalse(self.fixture.paths.lock_path.exists())
        self.assertFalse(self.fixture.paths.launch_agent_plist.exists())
        self.assertFalse(run_dir.exists())

    def test_cooperative_stop_tolerates_launchd_transition(self) -> None:
        run_dir = self.fixture.state_root / "r-0000000000000030"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []
        producer_launcher = FixtureProducerLauncher(self.fixture)
        self.fixture.runner.transition_on_quiesce = True

        def supervise() -> None:
            result.append(self.supervise_fixture(48, run_dir, producer_launcher))

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
            record = self.wait_for_waiting_startup(run_dir)
            accepted, error = request_supervisor_stop(record)
            self.assertTrue(accepted, error)
            self.assertTrue(self.fixture.runner.transition_observed.wait(timeout=5))
            self.assertTrue(thread.is_alive())
            self.fixture.runner.loaded = False
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].state, "stopped")
        self.assertFalse(self.fixture.paths.state_path.exists())
        self.assertFalse(self.fixture.paths.lock_path.exists())
        self.assertFalse(self.fixture.paths.launch_agent_plist.exists())
        self.assertFalse(run_dir.exists())

    def test_cooperative_stop_rejects_foreign_launchd_replacement(self) -> None:
        run_dir = self.fixture.state_root / "r-0000000000000031"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []
        producer_launcher = FixtureProducerLauncher(self.fixture)
        self.fixture.runner.transition_on_quiesce = True
        self.fixture.runner.foreign_on_quiesce = True

        def supervise() -> None:
            result.append(self.supervise_fixture(49, run_dir, producer_launcher))

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
            record = self.wait_for_waiting_startup(run_dir)
            accepted, error = request_supervisor_stop(record)
            self.assertTrue(accepted, error)
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0].ok)
        self.assertEqual(result[0].reason_code, "service.identity_changed")
        self.assertTrue(self.fixture.paths.state_path.exists())
        self.assertTrue(self.fixture.paths.lock_path.exists())
        self.assertTrue(self.fixture.paths.launch_agent_plist.exists())
        self.assertTrue(run_dir.exists())

    def test_cooperative_stop_rejects_same_path_bridge_replacement(self) -> None:
        run_dir = self.fixture.state_root / "r-0000000000000032"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []
        producer_launcher = FixtureProducerLauncher(self.fixture)
        self.fixture.runner.transition_on_quiesce = True
        self.fixture.runner.mutate_bridge_on_quiesce = True

        def supervise() -> None:
            result.append(self.supervise_fixture(50, run_dir, producer_launcher))

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
            record = self.wait_for_waiting_startup(run_dir)
            accepted, error = request_supervisor_stop(record)
            self.assertTrue(accepted, error)
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0].ok)
        self.assertEqual(result[0].reason_code, "service.identity_changed")
        self.assertTrue(self.fixture.paths.state_path.exists())
        self.assertTrue(self.fixture.paths.lock_path.exists())
        self.assertTrue(self.fixture.paths.launch_agent_plist.exists())
        self.assertTrue(run_dir.exists())

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
            record = self.wait_for_state("idle", producer_status="starting")
            accepted, error = request_supervisor_stop(record)
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
            self.wait_for_waiting_startup(run_dir)
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
            self.wait_for_waiting_startup(run_dir)
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
            launcher_birth_token, birth_error = context.birth_token_reader(process.pid)
            self.assertIsNotNone(launcher_birth_token, birth_error)
            assert launcher_birth_token is not None
            process_group = os.getpgid(process.pid)
            _quiesce_producer(
                process,
                launcher_birth_token,
                started_at,
                process_group,
                None,
                self.fixture.profile,
                producer_log,
                self.fixture.paths.service_label,
                42,
                4321,
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

    @unittest.skipUnless(
        pathlib.Path("/usr/sbin/lsof").is_file(),
        "requires macOS lsof process identity",
    )
    def test_real_setsid_target_cleanup_reaps_both_groups(self) -> None:
        run_dir = self.fixture.root / "detached-process-groups"
        run_dir.mkdir()
        producer_log = run_dir / "producer.log"
        bridge_log = run_dir / BRIDGE_LOG_NAME
        target_pid_path = run_dir / "target.pid"
        assert self.fixture.profile.installed.owned_process is not None
        owned_executable = self.fixture.profile.installed.owned_process.executable
        child_script = (
            "import mmap,os,pathlib,sys,time; "
            "handle=open(sys.argv[1],'rb'); "
            "mapping=mmap.mmap(handle.fileno(),0,access=mmap.ACCESS_READ); "
            "pathlib.Path(sys.argv[2]).write_text(str(os.getpid())); "
            "time.sleep(60)"
        )
        parent_script = (
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]], "
            "start_new_session=True); time.sleep(60)"
        )
        launcher = SubprocessProducerLauncher()
        process = launcher.launch(
            [
                sys.executable,
                "-c",
                parent_script,
                child_script,
                str(owned_executable),
                str(target_pid_path),
            ],
            run_dir,
            os.environ.copy(),
            producer_log,
        )
        context = RuntimeContext()
        target_pid: int | None = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not target_pid_path.exists():
                time.sleep(0.01)
            self.assertTrue(target_pid_path.exists())
            target_pid = int(target_pid_path.read_text())
            started_at, error = process_start_time(process.pid, context.runner)
            self.assertIsNotNone(started_at, error)
            assert started_at is not None
            launcher_birth_token, birth_error = context.birth_token_reader(process.pid)
            self.assertIsNotNone(launcher_birth_token, birth_error)
            assert launcher_birth_token is not None
            launcher_group = os.getpgid(process.pid)
            target = _inspect_producer_identity(
                self.fixture.profile,
                started_at,
                launcher_group,
                context.runner,
                context.birth_token_reader,
                context.pid_version_reader,
            )
            self.assertIsNotNone(target)
            assert target is not None
            self.assertEqual(target.pid, target_pid)
            self.assertEqual(target.process_group_id, target_pid)
            bridge_log.write_text(
                "native_source producer handshake accepted "
                f"service={self.fixture.paths.service_label} nonce=42 bridge_pid=4321 "
                f"producer_pid={target_pid} producer_pidversion={target.pid_version} "
                f"producer_start_token={target.birth_token} source=3240x1800\n"
                "native_source startup self-tests passed slots=3\n"
            )
            _quiesce_producer(
                process,
                launcher_birth_token,
                started_at,
                launcher_group,
                target,
                self.fixture.profile,
                bridge_log,
                self.fixture.paths.service_label,
                42,
                4321,
                context,
                time.monotonic,
                os.getpgid,
                os.killpg,
                _group_is_live,
            )
            self.assertIsNotNone(process.poll())
            self.assertFalse(_group_is_live(launcher_group))
            self.assertFalse(_group_is_live(target_pid))
        finally:
            for process_group in (target_pid, process.pid):
                if process_group is not None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process_group, 9)

    def test_startup_stop_preserves_unauthenticated_detached_target(self) -> None:
        run_dir = self.fixture.root / "unauthenticated-target"
        run_dir.mkdir()
        producer_log = run_dir / "producer.log"
        bridge_log = run_dir / BRIDGE_LOG_NAME
        bridge_log.write_text("")
        launcher = FixtureProducerLauncher(
            self.fixture,
            publish_markers=False,
            launch_target=True,
        )
        process = launcher.launch([], run_dir, {}, producer_log)
        with self.assertRaises(ControlError) as raised:
            _quiesce_producer(
                process,
                9_001_001,
                self.fixture.runner.launcher_started_at,
                self.fixture.runner.producer_group,
                None,
                self.fixture.profile,
                bridge_log,
                self.fixture.paths.service_label,
                42,
                4321,
                self.fixture.context,
                time.monotonic,
                launcher.group_id,
                launcher.group_signaler,
                launcher.group_live,
            )
        self.assertEqual(raised.exception.code, "producer.quiesce_failed")
        self.assertFalse(self.fixture.runner.producer_live)
        self.assertTrue(self.fixture.runner.target_live)
        self.assertNotIn(
            self.fixture.runner.target_group,
            [process_group for process_group, _ in self.fixture.runner.signals],
        )
        self.fixture.runner.target_live = False

    def test_startup_stop_reconciles_authenticated_detached_target(self) -> None:
        run_dir = self.fixture.root / "authenticated-reconciliation"
        run_dir.mkdir()
        producer_log = run_dir / "producer.log"
        launcher = FixtureProducerLauncher(self.fixture)
        process = launcher.launch([], run_dir, {}, producer_log)
        target = _quiesce_producer(
            process,
            9_001_001,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            None,
            self.fixture.profile,
            run_dir / BRIDGE_LOG_NAME,
            self.fixture.paths.service_label,
            1,
            4321,
            self.fixture.context,
            time.monotonic,
            launcher.group_id,
            launcher.group_signaler,
            launcher.group_live,
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.pid, self.fixture.runner.target_pid)
        self.assertFalse(self.fixture.runner.producer_live)
        self.assertFalse(self.fixture.runner.target_live)
        self.assertEqual(
            [process_group for process_group, _ in self.fixture.runner.signals],
            [self.fixture.runner.producer_group, self.fixture.runner.target_group],
        )

    def test_startup_stop_reconciles_late_authenticated_detached_target(self) -> None:
        run_dir = self.fixture.root / "late-authenticated-reconciliation"
        run_dir.mkdir()
        producer_log = run_dir / "producer.log"
        launcher = FixtureProducerLauncher(
            self.fixture,
            publish_markers=True,
            launch_target=False,
        )
        process = launcher.launch([], run_dir, {}, producer_log)
        sleep_calls = 0

        def reveal_target(_: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            self.fixture.runner.target_live = True

        context = replace(self.fixture.context, sleeper=reveal_target)
        target = _quiesce_producer(
            process,
            self.fixture.runner.producer_birth_token,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            None,
            self.fixture.profile,
            run_dir / BRIDGE_LOG_NAME,
            self.fixture.paths.service_label,
            1,
            4321,
            context,
            time.monotonic,
            launcher.group_id,
            launcher.group_signaler,
            launcher.group_live,
        )
        self.assertGreater(sleep_calls, 0)
        self.assertIsNotNone(target)
        self.assertFalse(self.fixture.runner.producer_live)
        self.assertFalse(self.fixture.runner.target_live)
        self.assertEqual(
            [process_group for process_group, _ in self.fixture.runner.signals],
            [self.fixture.runner.producer_group, self.fixture.runner.target_group],
        )

    def test_quiesce_rejects_target_identity_drift_before_signal(self) -> None:
        run_dir = self.fixture.root / "target-identity-drift"
        run_dir.mkdir()
        producer_log = run_dir / "producer.log"
        launcher = FixtureProducerLauncher(self.fixture)
        process = launcher.launch([], run_dir, {}, producer_log)
        target = _inspect_producer_identity(
            self.fixture.profile,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            self.fixture.runner,
            self.fixture.context.birth_token_reader,
            self.fixture.context.pid_version_reader,
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.fixture.runner.target_started_at = "Sat Jul 18 05:00:02 2026"
        with self.assertRaises(ControlError) as raised:
            _quiesce_producer(
                process,
                9_001_001,
                self.fixture.runner.launcher_started_at,
                self.fixture.runner.producer_group,
                target,
                self.fixture.profile,
                run_dir / BRIDGE_LOG_NAME,
                self.fixture.paths.service_label,
                42,
                4321,
                self.fixture.context,
                time.monotonic,
                launcher.group_id,
                launcher.group_signaler,
                launcher.group_live,
            )
        self.assertEqual(raised.exception.code, "producer.quiesce_failed")
        self.assertEqual(self.fixture.runner.signals, [])
        self.fixture.runner.producer_live = False
        self.fixture.runner.target_live = False

    def test_quiesce_rejects_target_birth_token_drift_before_signal(self) -> None:
        run_dir = self.fixture.root / "target-birth-token-drift"
        run_dir.mkdir()
        producer_log = run_dir / "producer.log"
        launcher = FixtureProducerLauncher(self.fixture)
        process = launcher.launch([], run_dir, {}, producer_log)
        target = _inspect_producer_identity(
            self.fixture.profile,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            self.fixture.runner,
            self.fixture.context.birth_token_reader,
            self.fixture.context.pid_version_reader,
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.fixture.runner.target_birth_token += 1
        with self.assertRaises(ControlError) as raised:
            _quiesce_producer(
                process,
                self.fixture.runner.producer_birth_token,
                self.fixture.runner.launcher_started_at,
                self.fixture.runner.producer_group,
                target,
                self.fixture.profile,
                run_dir / BRIDGE_LOG_NAME,
                self.fixture.paths.service_label,
                42,
                4321,
                self.fixture.context,
                time.monotonic,
                launcher.group_id,
                launcher.group_signaler,
                launcher.group_live,
            )
        self.assertEqual(raised.exception.code, "producer.quiesce_failed")
        self.assertEqual(self.fixture.runner.signals, [])
        self.fixture.runner.producer_live = False
        self.fixture.runner.target_live = False

    def test_quiesce_accepts_target_exit_during_revalidation(self) -> None:
        run_dir = self.fixture.root / "target-exits-during-revalidation"
        run_dir.mkdir()
        producer_log = run_dir / "producer.log"
        launcher = FixtureProducerLauncher(self.fixture)
        process = launcher.launch([], run_dir, {}, producer_log)
        target = _inspect_producer_identity(
            self.fixture.profile,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            self.fixture.runner,
            self.fixture.context.birth_token_reader,
            self.fixture.context.pid_version_reader,
        )
        self.assertIsNotNone(target)
        assert target is not None

        def process_exits(pid: int) -> tuple[int | None, str | None]:
            if pid == self.fixture.runner.target_pid:
                self.fixture.runner.target_live = False
                return None, "fixture process exited"
            return self.fixture.runner.birth_token(pid)

        context = replace(self.fixture.context, birth_token_reader=process_exits)
        stopped_target = _quiesce_producer(
            process,
            self.fixture.runner.producer_birth_token,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            target,
            self.fixture.profile,
            run_dir / BRIDGE_LOG_NAME,
            self.fixture.paths.service_label,
            42,
            4321,
            context,
            time.monotonic,
            launcher.group_id,
            launcher.group_signaler,
            launcher.group_live,
        )
        self.assertEqual(stopped_target, target)
        self.assertEqual(
            [process_group for process_group, _ in self.fixture.runner.signals],
            [self.fixture.runner.producer_group],
        )

    def test_quiesce_uses_same_group_target_after_launcher_exit(self) -> None:
        run_dir = self.fixture.root / "same-group-launcher-exit"
        run_dir.mkdir()
        producer_log = run_dir / "producer.log"
        self.fixture.runner.target_group = self.fixture.runner.producer_group
        launcher = FixtureProducerLauncher(self.fixture)
        process = launcher.launch([], run_dir, {}, producer_log)
        target = _inspect_producer_identity(
            self.fixture.profile,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            self.fixture.runner,
            self.fixture.context.birth_token_reader,
            self.fixture.context.pid_version_reader,
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.fixture.runner.producer_live = False
        stopped_target = _quiesce_producer(
            process,
            self.fixture.runner.producer_birth_token,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            target,
            self.fixture.profile,
            run_dir / BRIDGE_LOG_NAME,
            self.fixture.paths.service_label,
            42,
            4321,
            self.fixture.context,
            time.monotonic,
            launcher.group_id,
            launcher.group_signaler,
            launcher.group_live,
        )
        self.assertEqual(stopped_target, target)
        self.assertFalse(self.fixture.runner.target_live)
        self.assertEqual(
            [process_group for process_group, _ in self.fixture.runner.signals],
            [self.fixture.runner.producer_group],
        )

    def test_producer_identity_filters_process_group_before_lsof(self) -> None:
        self.fixture.runner.target_live = True
        assert self.fixture.profile.installed.owned_process is not None
        self.fixture.runner.producer_command = str(
            self.fixture.profile.installed.owned_process.executable
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
                        f"{self.fixture.runner.target_group} "
                        f"{self.fixture.runner.producer_command}\n"
                    ),
                )
            return original_run(command, timeout=timeout)

        with mock.patch.object(self.fixture.runner, "run", side_effect=run):
            identity = _inspect_producer_identity(
                self.fixture.profile,
                self.fixture.runner.launcher_started_at,
                self.fixture.runner.producer_group,
                self.fixture.runner,
                self.fixture.context.birth_token_reader,
                self.fixture.context.pid_version_reader,
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
                    "-d",
                    "txt",
                    "-Fn",
                )
            ],
        )

    def test_producer_identity_rejects_multiple_exact_candidates_globally(self) -> None:
        assert self.fixture.profile.installed.owned_process is not None
        executable = self.fixture.profile.installed.owned_process.executable
        candidates = [
            ProducerIdentity(
                9002,
                9_002_002,
                202,
                "Sat Jul 18 04:00:02 2026",
                9002,
                str(executable),
                executable,
            ),
            ProducerIdentity(
                9003,
                9_003_003,
                203,
                "Sat Jul 18 04:00:03 2026",
                7000,
                str(executable),
                executable,
            ),
        ]
        with (
            mock.patch("runtime_start._owned_process_candidates", return_value=candidates),
            self.assertRaises(ControlError) as raised,
        ):
            _inspect_producer_identity(
                self.fixture.profile,
                self.fixture.runner.launcher_started_at,
                self.fixture.runner.producer_group,
                self.fixture.runner,
                self.fixture.context.birth_token_reader,
                self.fixture.context.pid_version_reader,
            )
        self.assertEqual(raised.exception.code, "producer.identity_changed")

    def test_producer_identity_confirmation_rejects_pid_reuse(self) -> None:
        self.fixture.runner.target_live = True
        assert self.fixture.profile.installed.owned_process is not None
        self.fixture.runner.producer_command = str(
            self.fixture.profile.installed.owned_process.executable
        )
        observed = _inspect_producer_identity(
            self.fixture.profile,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            self.fixture.runner,
            self.fixture.context.birth_token_reader,
            self.fixture.context.pid_version_reader,
        )
        assert observed is not None
        with (
            mock.patch(
                "runtime_start.process_start_time",
                side_effect=[
                    (observed.started_at, None),
                    ("Sat Jul 18 05:00:02 2026", None),
                ],
            ),
            self.assertRaises(ControlError) as raised,
        ):
            _confirm_producer_identity(
                observed,
                self.fixture.profile,
                self.fixture.runner.launcher_started_at,
                self.fixture.runner.producer_group,
                self.fixture.runner,
                self.fixture.context.birth_token_reader,
                self.fixture.context.pid_version_reader,
            )
        self.assertEqual(raised.exception.code, "producer.identity_changed")

    def test_producer_identity_confirmation_rejects_pid_version_change(self) -> None:
        self.fixture.runner.target_live = True
        assert self.fixture.profile.installed.owned_process is not None
        self.fixture.runner.producer_command = str(
            self.fixture.profile.installed.owned_process.executable
        )
        observed = _inspect_producer_identity(
            self.fixture.profile,
            self.fixture.runner.launcher_started_at,
            self.fixture.runner.producer_group,
            self.fixture.runner,
            self.fixture.context.birth_token_reader,
            self.fixture.context.pid_version_reader,
        )
        assert observed is not None
        self.fixture.runner.target_pid_version += 1
        with self.assertRaises(ControlError) as raised:
            _confirm_producer_identity(
                observed,
                self.fixture.profile,
                self.fixture.runner.launcher_started_at,
                self.fixture.runner.producer_group,
                self.fixture.runner,
                self.fixture.context.birth_token_reader,
                self.fixture.context.pid_version_reader,
            )
        self.assertEqual(raised.exception.code, "producer.identity_changed")

    def test_bridge_markers_bind_generation_and_authenticated_pid(self) -> None:
        bridge_log = self.fixture.root / "pid-bound-bridge.log"
        bridge_log.write_text(
            "native_source producer handshake accepted "
            f"service={self.fixture.paths.service_label} nonce=42 bridge_pid=4321 "
            "producer_pid=9002 producer_pidversion=77 "
            "producer_start_token=9002002 source=3240x1800\n"
            "native_source startup self-tests passed slots=3\n"
        )
        self.assertTrue(
            _producer_markers_ready(
                bridge_log,
                self.fixture.paths.service_label,
                42,
                4321,
                9002,
                77,
                9_002_002,
            )
        )
        self.assertFalse(
            _producer_markers_ready(
                bridge_log,
                self.fixture.paths.service_label,
                42,
                4321,
                9003,
                77,
                9_003_003,
            )
        )
        self.assertFalse(
            _producer_markers_ready(
                bridge_log,
                self.fixture.paths.service_label,
                43,
                4321,
                9002,
                77,
                9_002_002,
            )
        )
        self.assertFalse(
            _producer_markers_ready(
                bridge_log,
                self.fixture.paths.service_label,
                42,
                4321,
                9002,
                78,
                9_002_002,
            )
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
            self.wait_for_waiting_startup(run_dir)
            self.fixture.runner.loaded = False
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0].ok)
        self.assertEqual(result[0].reason_code, "service.exited")
        self.assertTrue(self.fixture.paths.lock_path.exists())
        self.assertTrue(self.fixture.paths.state_path.exists())
        self.assertTrue(self.fixture.paths.launch_agent_plist.exists())

    def test_client_monitor_tracks_stream_activity_and_bounded_recovery(self) -> None:
        monitor = ClientTelemetryMonitor(
            42,
            4321,
            heartbeat_timeout=5,
            stream_activity_timeout=2,
            recovery_timeout=30,
        )
        telemetry = ClientTelemetry(
            2,
            ALVR_CLIENT_STATE_WAITING,
            False,
            42,
            4321,
            7001,
            1,
            0,
            0,
            0,
            0,
            0,
        )

        state, record = monitor.observe(telemetry, 0)
        self.assertEqual(state, "waiting")
        self.assertEqual(record["status"], "waiting")

        telemetry = replace(
            telemetry,
            sequence=4,
            client_state=ALVR_CLIENT_STATE_CONNECTED,
            stream_contract_valid=True,
            bridge_heartbeat_ns=2,
            stream_epoch=1,
            connect_events=1,
        )
        state, _ = monitor.observe(telemetry, 1)
        self.assertEqual(state, "connected")

        telemetry = replace(
            telemetry,
            sequence=6,
            client_state=ALVR_CLIENT_STATE_STREAMING,
            bridge_heartbeat_ns=3,
            frames_transported=1,
        )
        state, _ = monitor.observe(telemetry, 2)
        self.assertEqual(state, "streaming")

        telemetry = replace(telemetry, sequence=8, bridge_heartbeat_ns=4)
        state, _ = monitor.observe(telemetry, 4.1)
        self.assertEqual(state, "connected")

        telemetry = replace(
            telemetry,
            sequence=10,
            client_state=ALVR_CLIENT_STATE_WAITING,
            stream_contract_valid=False,
            bridge_heartbeat_ns=5,
            stream_epoch=2,
            disconnect_events=1,
        )
        state, _ = monitor.observe(telemetry, 5)
        self.assertEqual(state, "recovering")

        telemetry = replace(telemetry, sequence=12, bridge_heartbeat_ns=6)
        state, _ = monitor.observe(telemetry, 35)
        self.assertEqual(state, "waiting")

        telemetry = replace(
            telemetry,
            sequence=14,
            client_state=ALVR_CLIENT_STATE_CONNECTED,
            stream_contract_valid=True,
            bridge_heartbeat_ns=7,
            stream_epoch=3,
            connect_events=2,
        )
        state, _ = monitor.observe(telemetry, 36)
        self.assertEqual(state, "connected")

    def test_client_monitor_rejects_stale_and_regressed_telemetry(self) -> None:
        telemetry = ClientTelemetry(
            2,
            ALVR_CLIENT_STATE_CONNECTED,
            True,
            42,
            4321,
            7001,
            1,
            1,
            0,
            1,
            0,
            0,
        )
        monitor = ClientTelemetryMonitor(42, 4321, heartbeat_timeout=5)
        monitor.observe(telemetry, 0)
        with self.assertRaisesRegex(ControlError, "heartbeat stopped advancing"):
            monitor.observe(telemetry, 5)

        monitor = ClientTelemetryMonitor(42, 4321)
        monitor.observe(telemetry, 0)
        with self.assertRaisesRegex(ControlError, "event counter regressed"):
            monitor.observe(
                replace(
                    telemetry,
                    sequence=4,
                    client_state=ALVR_CLIENT_STATE_WAITING,
                    stream_contract_valid=False,
                    bridge_heartbeat_ns=2,
                    stream_epoch=0,
                    connect_events=0,
                ),
                1,
            )

    def test_client_monitor_recovers_missed_cycle_and_latches_contract_failure(self) -> None:
        waiting = ClientTelemetry(
            2,
            ALVR_CLIENT_STATE_WAITING,
            False,
            42,
            4321,
            7001,
            1,
            0,
            0,
            0,
            0,
            0,
        )
        monitor = ClientTelemetryMonitor(42, 4321)
        monitor.observe(waiting, 0)

        missed_cycle = replace(
            waiting,
            sequence=8,
            bridge_heartbeat_ns=2,
            stream_epoch=2,
            frames_transported=1,
            connect_events=1,
            disconnect_events=1,
        )
        state, record = monitor.observe(missed_cycle, 1)
        self.assertEqual(state, "recovering")
        self.assertEqual(record["framesTransported"], 1)
        self.assertEqual(record["connectEvents"], 1)
        self.assertEqual(record["disconnectEvents"], 1)

        monitor = ClientTelemetryMonitor(42, 4321)
        monitor.observe(waiting, 0)
        with self.assertRaisesRegex(ControlError, "outside the sealed contract"):
            monitor.observe(
                replace(
                    missed_cycle,
                    contract_failure_events=1,
                ),
                1,
            )

    def test_reads_exact_version_seven_client_telemetry(self) -> None:
        path = self.fixture.root / "client-telemetry.shm"
        header = bytearray(ALVR_SHM_HEADER_SIZE)
        struct.pack_into("<IIII", header, 0, ALVR_SHM_MAGIC, ALVR_SHM_VERSION, 1, 0)
        struct.pack_into("<QQ", header, 72, 7001, 99)
        struct.pack_into("<I", header, ALVR_SHM_TELEMETRY_SEQUENCE_OFFSET, 2)
        struct.pack_into("<II", header, 996, ALVR_CLIENT_STATE_STREAMING, 1)
        struct.pack_into("<QQQQ", header, 1008, 42, 4321, 1, 7)
        struct.pack_into("<QQQ", header, 1040, 1, 0, 0)
        path.write_bytes(header)
        path.chmod(0o600)

        telemetry = read_client_telemetry(path)
        self.assertEqual(telemetry.runtime_generation, 42)
        self.assertEqual(telemetry.bridge_pid, 4321)
        self.assertEqual(telemetry.bridge_session_id, 7001)
        self.assertEqual(telemetry.stream_epoch, 1)
        self.assertEqual(telemetry.frames_transported, 7)

        path.chmod(0o644)
        with self.assertRaisesRegex(ControlError, "unsafe metadata"):
            read_client_telemetry(path)

    def test_supervisor_publishes_client_stream_and_recovery_transitions(self) -> None:
        run_dir = self.fixture.state_root / "r-0000000000000042"
        run_dir.mkdir(mode=0o700)
        result: list[StartReport] = []
        producer_launcher = FixtureProducerLauncher(self.fixture)

        def supervise() -> None:
            result.append(self.supervise_fixture(66, run_dir, producer_launcher))

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
            record = self.wait_for_waiting_startup(run_dir)
            self.assertEqual(record["schemaVersion"], 5)
            self.assertEqual(record["client"]["status"], "waiting")
            seeded_session_path = run_dir / "alvr-root/session.json"
            seeded_session = json.loads(seeded_session_path.read_text())
            self.assertEqual(
                set(seeded_session["client_connections"]),
                {"fixture.client.alvr"},
            )
            self.assertEqual(
                seeded_session["client_connections"]["fixture.client.alvr"][
                    "connection_state"
                ],
                "Disconnected",
            )
            self.assertEqual(seeded_session_path.stat().st_mode & 0o777, 0o600)

            self.fixture.telemetry.connect()
            connected = self.wait_for_state("connected", producer_status="ready")
            self.assertEqual(connected["client"]["streamEpoch"], 1)

            self.fixture.telemetry.transport_frame()
            streaming = self.wait_for_state("streaming", producer_status="ready")
            self.assertEqual(streaming["client"]["framesTransported"], 1)

            self.fixture.telemetry.disconnect()
            recovering = self.wait_for_state("recovering", producer_status="ready")
            self.assertFalse(recovering["client"]["streamContractValid"])

            accepted, error = request_supervisor_stop(recovering)
            self.assertTrue(accepted, error)
            self.fixture.runner.loaded = False
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].state, "stopped")

    def test_supervisor_fails_without_retained_trusted_client(self) -> None:
        run_dir = self.fixture.state_root / "r-0000000000000043"
        run_dir.mkdir(mode=0o700)
        payload = json.loads(self.fixture.alvr_session_path.read_text())
        for connection in payload["client_connections"].values():
            connection["trusted"] = False
        self.fixture.alvr_session_path.write_text(json.dumps(payload))

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
            report = self.supervise_fixture(67, run_dir, FixtureProducerLauncher(self.fixture))

        self.assertFalse(report.ok)
        self.assertEqual(report.reason_code, "client.trust_missing")
        self.assertFalse(self.fixture.runner.loaded)
        self.assertFalse(self.fixture.paths.lock_path.exists())

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
