"""Bounded detached start supervision for the Mac ALVR runtime."""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import pathlib
import plistlib
import re
import secrets
import shlex
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

import build_runtime_artifact as artifact_contract
import runtime_descriptor
import runtime_install
import runtime_profile
import runtime_transaction
from runtime_control import (
    CONTROL_SOCKET_NAME,
    LEGACY_LIVE_STATES,
    LIVE_STATES,
    MAX_RUNTIME_GENERATION,
    CommandResult,
    CommandRunner,
    ControlError,
    RuntimeContext,
    RuntimePaths,
    doctor_runtime,
    global_lifecycle_lock,
    identity_record,
    inspect_lock,
    inspect_service,
    load_control_state,
    load_runtime_contract,
    mutable_state_item,
    paths_match,
    process_command,
    process_group_id,
    process_start_time,
    read_launchd_snapshot,
    receive_json_frame,
    remove_owned_run_directory,
    resolve_runtime_paths,
    status_runtime,
    validate_plist_ownership,
    verify_artifact_reference,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME_START = REPO_ROOT / "tools" / "runtime_start.py"
STARTUP_RESULT_NAME = "startup-result.json"
SUPERVISOR_EXIT_NAME = "supervisor-exit.json"
SUPERVISOR_LOG_NAME = "supervisor.log"
BRIDGE_LOG_NAME = "native-bridge.log"
PRODUCER_LOG_NAME = "producer.log"
ALVR_ROOT_NAME = "alvr-root"
DXVK_LOG_ROOT_NAME = "dxvk-logs"
MVK_SHADER_ROOT_NAME = "mvk-shaders"
STARTUP_TIMEOUT_SECONDS = 30.0
SERVICE_READY_TIMEOUT_SECONDS = 10.0
READINESS_SAMPLE_INTERVAL_SECONDS = 0.1
MONITOR_INTERVAL_SECONDS = 0.25
SERVICE_STATUS_INTERVAL_SECONDS = 1.0
SERVICE_IDENTITY_INTERVAL_SECONDS = 30.0
PRODUCER_STOP_GRACE_SECONDS = 5.0
PRODUCER_KILL_WAIT_SECONDS = 1.0
PRODUCER_QUIESCE_TIMEOUT_SECONDS = 40.0
PRODUCER_QUIESCE_MARGIN_SECONDS = 10.0
MAX_RUNTIME_FRAMES = (1 << 64) - 1
ALVR_SHM_PATH = pathlib.Path("/tmp/alvr_frame_buffer.shm")
ALVR_SHM_MAGIC = 0x414C5652
ALVR_SHM_VERSION = 7
ALVR_SHM_HEADER_SIZE = 1064
ALVR_SHM_TELEMETRY_SEQUENCE_OFFSET = 992
ALVR_CLIENT_STATE_WAITING = 0
ALVR_CLIENT_STATE_CONNECTED = 1
ALVR_CLIENT_STATE_STREAMING = 2
CLIENT_TELEMETRY_READY_TIMEOUT_SECONDS = 5.0
CLIENT_HEARTBEAT_TIMEOUT_SECONDS = 5.0
CLIENT_STREAM_ACTIVITY_TIMEOUT_SECONDS = 2.0
CLIENT_RECOVERY_TIMEOUT_SECONDS = 30.0
CLIENT_STATE_SNAPSHOT_INTERVAL_SECONDS = 30.0
CLIENT_STATE_PHASES = {
    "waiting": "waiting-for-client",
    "connected": "client-connected",
    "streaming": "client-streaming",
    "recovering": "client-recovering",
}
CLIENT_STATE_MESSAGES = {
    "waiting": "Exact curated game producer is ready and waiting for Vision Pro",
    "connected": "Vision Pro is connected with an exact negotiated stream contract",
    "streaming": "Vision Pro stream transport is active",
    "recovering": "Vision Pro disconnected; bounded recovery is in progress",
}
INSTALLED_FALSE_ACTIONS = frozenset({"assert_sha256", "backup", "assert_absent"})


class SpawnedProcess(Protocol):
    pid: int

    def poll(self) -> int | None:
        """Return the child status or None while it remains live."""


class ProcessLauncher(Protocol):
    def launch(self, argv: Sequence[str], log_path: pathlib.Path) -> SpawnedProcess:
        """Launch one detached supervisor child."""

        ...


class ProducerProcess(SpawnedProcess, Protocol):
    """Live direct CrossOver launcher retained by the supervisor."""


class ProducerLauncher(Protocol):
    def launch(
        self,
        argv: Sequence[str],
        working_directory: pathlib.Path,
        environment: dict[str, str],
        log_path: pathlib.Path,
    ) -> ProducerProcess:
        """Launch one producer in a new process session."""

        ...


class SubprocessLauncher:
    def launch(self, argv: Sequence[str], log_path: pathlib.Path) -> subprocess.Popen[bytes]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(log_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            return subprocess.Popen(
                [str(item) for item in argv],
                stdin=subprocess.DEVNULL,
                stdout=descriptor,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            os.close(descriptor)


class SubprocessProducerLauncher:
    def launch(
        self,
        argv: Sequence[str],
        working_directory: pathlib.Path,
        environment: dict[str, str],
        log_path: pathlib.Path,
    ) -> subprocess.Popen[bytes]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(log_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            return subprocess.Popen(
                [str(item) for item in argv],
                cwd=working_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=descriptor,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            os.close(descriptor)


class DeadlineRunner:
    def __init__(
        self,
        runner: CommandRunner,
        deadline: float,
        clock: Callable[[], float],
    ) -> None:
        self.runner = runner
        self.deadline = deadline
        self.clock = clock

    def run(self, argv: Sequence[str], *, timeout: float = 10.0) -> CommandResult:
        command = tuple(str(item) for item in argv)
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            return CommandResult(
                command,
                None,
                stderr="Runtime supervisor startup deadline expired",
                error="timeout",
            )
        return self.runner.run(command, timeout=min(timeout, remaining))


@dataclass(frozen=True)
class StartAdmission:
    manifest: dict[str, Any]
    bindings: dict[str, str]
    paths: RuntimePaths
    allowed_roots: tuple[pathlib.Path, ...]
    plan: dict[str, Any]
    artifact: dict[str, Any]
    artifact_path: pathlib.Path
    profile: ProfileStartAdmission | None = None


@dataclass(frozen=True)
class ProfileStartAdmission:
    installed: runtime_profile.InstalledProfile
    crossover_launcher: pathlib.Path
    bottle_name: str
    bridge_root: pathlib.Path


@dataclass(frozen=True)
class ProducerIdentity:
    pid: int
    birth_token: int
    pid_version: int
    started_at: str
    process_group_id: int
    command: str
    executable: pathlib.Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "birthToken": self.birth_token,
            "pidVersion": self.pid_version,
            "startedAt": self.started_at,
            "processGroupId": self.process_group_id,
            "command": self.command,
            "executable": str(self.executable),
        }


@dataclass(frozen=True)
class OwnedProducerIdentity:
    target_id: str
    identity: ProducerIdentity

    def to_dict(self) -> dict[str, Any]:
        return {"targetId": self.target_id, **self.identity.to_dict()}


@dataclass(frozen=True)
class _OwnedProducerObservation:
    identities: tuple[OwnedProducerIdentity, ...]
    process_ids: frozenset[int]


@dataclass(frozen=True)
class _OwnedProducerTransition:
    live: tuple[OwnedProducerIdentity, ...]
    retained: tuple[OwnedProducerIdentity, ...]
    deadline: float | None


@dataclass(frozen=True)
class ClientTelemetry:
    sequence: int
    client_state: int
    stream_contract_valid: bool
    runtime_generation: int
    bridge_pid: int
    bridge_session_id: int
    bridge_heartbeat_ns: int
    stream_epoch: int
    frames_transported: int
    connect_events: int
    disconnect_events: int
    contract_failure_events: int


class ClientTelemetryMonitor:
    def __init__(
        self,
        runtime_generation: int,
        bridge_pid: int,
        *,
        heartbeat_timeout: float = CLIENT_HEARTBEAT_TIMEOUT_SECONDS,
        stream_activity_timeout: float = CLIENT_STREAM_ACTIVITY_TIMEOUT_SECONDS,
        recovery_timeout: float = CLIENT_RECOVERY_TIMEOUT_SECONDS,
    ) -> None:
        self.runtime_generation = runtime_generation
        self.bridge_pid = bridge_pid
        self.heartbeat_timeout = heartbeat_timeout
        self.stream_activity_timeout = stream_activity_timeout
        self.recovery_timeout = recovery_timeout
        self.bridge_session_id: int | None = None
        self.last_heartbeat_ns: int | None = None
        self.last_heartbeat_progress_at: float | None = None
        self.last_stream_epoch: int | None = None
        self.last_frames_transported: int | None = None
        self.last_connect_events: int | None = None
        self.last_disconnect_events: int | None = None
        self.last_contract_failure_events: int | None = None
        self.last_frame_progress_at: float | None = None
        self.last_bridge_state: int | None = None
        self.state = "waiting"
        self.recovery_deadline: float | None = None

    def observe(
        self,
        telemetry: ClientTelemetry,
        now: float,
    ) -> tuple[str, dict[str, Any]]:
        if (
            telemetry.runtime_generation != self.runtime_generation
            or telemetry.bridge_pid != self.bridge_pid
            or telemetry.bridge_session_id <= 0
        ):
            raise ControlError(
                "client.telemetry_mismatch",
                "Bridge telemetry identity does not match the active runtime",
                expectedGeneration=self.runtime_generation,
                observedGeneration=telemetry.runtime_generation,
                expectedBridgePid=self.bridge_pid,
                observedBridgePid=telemetry.bridge_pid,
            )
        if self.bridge_session_id is None:
            self.bridge_session_id = telemetry.bridge_session_id
        elif telemetry.bridge_session_id != self.bridge_session_id:
            raise ControlError(
                "client.telemetry_mismatch",
                "Bridge telemetry session changed without a service transition",
                expectedBridgeSessionId=self.bridge_session_id,
                observedBridgeSessionId=telemetry.bridge_session_id,
            )
        if telemetry.bridge_heartbeat_ns <= 0:
            raise ControlError(
                "client.telemetry_stale",
                "Bridge telemetry heartbeat is absent",
            )
        if telemetry.bridge_heartbeat_ns != self.last_heartbeat_ns:
            self.last_heartbeat_ns = telemetry.bridge_heartbeat_ns
            self.last_heartbeat_progress_at = now
        elif (
            self.last_heartbeat_progress_at is not None
            and now - self.last_heartbeat_progress_at >= self.heartbeat_timeout
        ):
            raise ControlError(
                "client.telemetry_stale",
                "Bridge telemetry heartbeat stopped advancing",
            )

        if telemetry.client_state not in {
            ALVR_CLIENT_STATE_WAITING,
            ALVR_CLIENT_STATE_CONNECTED,
            ALVR_CLIENT_STATE_STREAMING,
        }:
            raise ControlError(
                "client.telemetry_mismatch",
                "Bridge telemetry reported an unknown client state",
                clientState=telemetry.client_state,
            )
        if (
            self.last_connect_events is not None
            and telemetry.connect_events < self.last_connect_events
        ) or (
            self.last_disconnect_events is not None
            and telemetry.disconnect_events < self.last_disconnect_events
        ) or (
            self.last_contract_failure_events is not None
            and telemetry.contract_failure_events < self.last_contract_failure_events
        ):
            raise ControlError(
                "client.telemetry_mismatch",
                "ALVR client event counter regressed",
            )
        if telemetry.contract_failure_events > 0:
            raise ControlError(
                "client.contract_failed",
                "ALVR client negotiated a stream outside the sealed contract",
                contractFailureEvents=telemetry.contract_failure_events,
            )
        if telemetry.stream_epoch != telemetry.connect_events + telemetry.disconnect_events:
            raise ControlError(
                "client.telemetry_mismatch",
                "ALVR stream epoch does not match client event counters",
            )
        if telemetry.client_state == ALVR_CLIENT_STATE_WAITING:
            if (
                telemetry.stream_contract_valid
                or telemetry.stream_epoch % 2 != 0
                or telemetry.connect_events != telemetry.disconnect_events
            ):
                raise ControlError(
                    "client.telemetry_mismatch",
                    "Waiting telemetry has an invalid stream contract or epoch",
                )
        else:
            if not telemetry.stream_contract_valid:
                raise ControlError(
                    "client.contract_failed",
                    "ALVR client negotiated a stream outside the sealed contract",
                )
            if (
                telemetry.stream_epoch == 0
                or telemetry.stream_epoch % 2 == 0
                or telemetry.connect_events != telemetry.disconnect_events + 1
            ):
                raise ControlError(
                    "client.telemetry_mismatch",
                    "Connected telemetry has an invalid stream epoch",
                )
        if (
            telemetry.client_state == ALVR_CLIENT_STATE_STREAMING
            and telemetry.frames_transported == 0
        ):
            raise ControlError(
                "client.telemetry_mismatch",
                "Streaming telemetry has no transported frames",
            )
        if (
            self.last_stream_epoch is not None
            and telemetry.stream_epoch < self.last_stream_epoch
        ):
            raise ControlError(
                "client.epoch_regressed",
                "ALVR client stream epoch regressed",
                previousEpoch=self.last_stream_epoch,
                observedEpoch=telemetry.stream_epoch,
            )
        if (
            self.last_frames_transported is not None
            and telemetry.frames_transported < self.last_frames_transported
        ):
            raise ControlError(
                "client.telemetry_mismatch",
                "ALVR transported-frame counter regressed",
            )
        frames_advanced = (
            self.last_frames_transported is not None
            and telemetry.frames_transported > self.last_frames_transported
        )
        disconnect_advanced = (
            telemetry.disconnect_events > 0
            if self.last_disconnect_events is None
            else telemetry.disconnect_events > self.last_disconnect_events
        )
        if (
            frames_advanced
            and telemetry.client_state != ALVR_CLIENT_STATE_STREAMING
            and not disconnect_advanced
        ):
            raise ControlError(
                "client.telemetry_mismatch",
                "Transported frames advanced outside the streaming state",
            )
        if (
            self.last_stream_epoch == telemetry.stream_epoch
            and self.last_bridge_state is not None
            and self.last_bridge_state != telemetry.client_state
            and (self.last_bridge_state, telemetry.client_state)
            != (ALVR_CLIENT_STATE_CONNECTED, ALVR_CLIENT_STATE_STREAMING)
        ):
            raise ControlError(
                "client.telemetry_mismatch",
                "ALVR client state changed without a new stream epoch",
            )

        previous_state = self.state
        if telemetry.client_state == ALVR_CLIENT_STATE_WAITING:
            if previous_state in {"connected", "streaming"} or (
                disconnect_advanced and previous_state != "recovering"
            ):
                self.recovery_deadline = now + self.recovery_timeout
                self.state = "recovering"
            elif previous_state == "recovering":
                if self.recovery_deadline is None:
                    raise ControlError(
                        "client.telemetry_mismatch",
                        "Recovery state has no monotonic deadline",
                    )
                if now >= self.recovery_deadline:
                    self.recovery_deadline = None
                    self.state = "waiting"
            else:
                self.state = "waiting"
        else:
            self.recovery_deadline = None
            if telemetry.client_state == ALVR_CLIENT_STATE_CONNECTED:
                self.state = "connected"
            else:
                if self.last_frames_transported is None or frames_advanced:
                    self.last_frame_progress_at = now
                if self.last_frame_progress_at is None:
                    self.last_frame_progress_at = now
                self.state = (
                    "streaming"
                    if now - self.last_frame_progress_at < self.stream_activity_timeout
                    else "connected"
                )

        self.last_stream_epoch = telemetry.stream_epoch
        self.last_frames_transported = telemetry.frames_transported
        self.last_connect_events = telemetry.connect_events
        self.last_disconnect_events = telemetry.disconnect_events
        self.last_contract_failure_events = telemetry.contract_failure_events
        self.last_bridge_state = telemetry.client_state
        return self.state, {
            "status": self.state,
            "telemetryVersion": ALVR_SHM_VERSION,
            "runtimeGeneration": telemetry.runtime_generation,
            "bridgePid": telemetry.bridge_pid,
            "bridgeSessionId": telemetry.bridge_session_id,
            "streamEpoch": telemetry.stream_epoch,
            "streamContractValid": telemetry.stream_contract_valid,
            "framesTransported": telemetry.frames_transported,
            "connectEvents": telemetry.connect_events,
            "disconnectEvents": telemetry.disconnect_events,
            "contractFailureEvents": telemetry.contract_failure_events,
        }


def read_client_telemetry(path: pathlib.Path = ALVR_SHM_PATH) -> ClientTelemetry:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as error:
        raise ControlError(
            "client.telemetry_missing",
            "Bridge telemetry mapping is absent",
            path=str(path),
        ) from error
    except OSError as error:
        raise ControlError(
            "client.telemetry_mismatch",
            "Bridge telemetry mapping could not be opened safely",
            path=str(path),
            detail=str(error),
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size < ALVR_SHM_HEADER_SIZE
        ):
            raise ControlError(
                "client.telemetry_mismatch",
                "Bridge telemetry mapping has unsafe metadata",
                path=str(path),
            )
        header: bytes | None = None
        sequence = 0
        for _ in range(8):
            before = os.pread(descriptor, 4, ALVR_SHM_TELEMETRY_SEQUENCE_OFFSET)
            if len(before) != 4:
                break
            sequence = struct.unpack("<I", before)[0]
            if sequence % 2 != 0:
                continue
            candidate = os.pread(descriptor, ALVR_SHM_HEADER_SIZE, 0)
            after = os.pread(descriptor, 4, ALVR_SHM_TELEMETRY_SEQUENCE_OFFSET)
            if len(candidate) != ALVR_SHM_HEADER_SIZE or len(after) != 4:
                break
            observed_sequence = struct.unpack("<I", after)[0]
            candidate_sequence = struct.unpack_from(
                "<I", candidate, ALVR_SHM_TELEMETRY_SEQUENCE_OFFSET
            )[0]
            if sequence == observed_sequence == candidate_sequence and sequence % 2 == 0:
                header = candidate
                break
        if header is None:
            raise ControlError(
                "client.telemetry_stale",
                "Bridge telemetry could not be read consistently",
            )
    finally:
        os.close(descriptor)

    magic, version, initialized, shutdown = struct.unpack_from("<IIII", header, 0)
    if magic != ALVR_SHM_MAGIC or version != ALVR_SHM_VERSION:
        raise ControlError(
            "client.telemetry_mismatch",
            "Bridge telemetry protocol does not match the sealed runtime",
            expectedVersion=ALVR_SHM_VERSION,
            observedVersion=version,
        )
    if initialized != 1 or shutdown != 0:
        raise ControlError(
            "client.telemetry_missing",
            "Bridge telemetry mapping is not initialized",
        )
    bridge_session_id, bridge_heartbeat_ns = struct.unpack_from("<QQ", header, 72)
    client_state, stream_contract_valid = struct.unpack_from("<II", header, 996)
    if stream_contract_valid not in {0, 1}:
        raise ControlError(
            "client.telemetry_mismatch",
            "Bridge telemetry stream-contract flag is invalid",
        )
    runtime_generation, bridge_pid, stream_epoch, frames_transported = struct.unpack_from(
        "<QQQQ", header, 1008
    )
    connect_events, disconnect_events, contract_failure_events = struct.unpack_from(
        "<QQQ", header, 1040
    )
    return ClientTelemetry(
        sequence,
        client_state,
        stream_contract_valid == 1,
        runtime_generation,
        bridge_pid,
        bridge_session_id,
        bridge_heartbeat_ns,
        stream_epoch,
        frames_transported,
        connect_events,
        disconnect_events,
        contract_failure_events,
    )


def _wait_for_client_state(
    telemetry_reader: Callable[[], ClientTelemetry],
    monitor: ClientTelemetryMonitor,
    deadline: float,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> tuple[str, dict[str, Any]]:
    while True:
        now = monotonic()
        try:
            return monitor.observe(telemetry_reader(), now)
        except ControlError as error:
            if error.code not in {"client.telemetry_missing", "client.telemetry_stale"} or now >= deadline:
                raise
        sleeper(READINESS_SAMPLE_INTERVAL_SECONDS)


def _process_start_datetime(value: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(value, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None


@dataclass(frozen=True)
class StartReport:
    ok: bool
    state: str
    reason_code: str
    message: str
    artifact: dict[str, Any] | None = None
    generation: int | None = None
    owner_pid: int | None = None
    run_dir: pathlib.Path | None = None
    supervisor_log: pathlib.Path | None = None
    actions: tuple[str, ...] = ()
    profile: dict[str, Any] | None = None
    producer: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "command": "start",
            "ok": self.ok,
            "state": self.state,
            "reasonCode": self.reason_code,
            "message": self.message,
            "artifact": self.artifact,
            "generation": self.generation,
            "ownerPid": self.owner_pid,
            "runDir": str(self.run_dir) if self.run_dir is not None else None,
            "supervisorLog": (
                str(self.supervisor_log) if self.supervisor_log is not None else None
            ),
            "actions": list(self.actions),
            "profile": self.profile,
            "producer": self.producer,
        }


def start_failure(
    code: str,
    message: str,
    *,
    artifact: dict[str, Any] | None = None,
    generation: int | None = None,
    owner_pid: int | None = None,
    run_dir: pathlib.Path | None = None,
    supervisor_log: pathlib.Path | None = None,
    actions: Sequence[str] = (),
    profile: dict[str, Any] | None = None,
    producer: dict[str, Any] | None = None,
) -> StartReport:
    return StartReport(
        False,
        "failed",
        code,
        message,
        artifact,
        generation,
        owner_pid,
        run_dir,
        supervisor_log,
        tuple(actions),
        profile,
        producer,
    )


def _absolute(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(os.path.abspath(path.expanduser()))


def _remaining_timeout(
    deadline: float,
    maximum: float,
    clock: Callable[[], float],
) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise ControlError(
            "runtime.start_timeout",
            "Runtime supervisor exceeded its total startup deadline",
        )
    return min(maximum, remaining)


def _private_directory(path: pathlib.Path, mode: int = 0o700) -> None:
    try:
        artifact_contract.reject_symlink_components(path)
    except artifact_contract.ArtifactError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ControlError(
            "runtime.not_installed",
            "Runtime state root is absent; install the sealed artifact first",
            path=str(path),
        ) from error
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ControlError(
            "transaction.path_unsafe",
            "Runtime state root has unsafe ownership or type",
            path=str(path),
        )
    actual_mode = stat.S_IMODE(metadata.st_mode)
    if actual_mode != mode:
        raise ControlError(
            "transaction.mode_unsafe",
            f"Runtime state root must use mode {mode:04o}",
            path=str(path),
            mode=f"{actual_mode:04o}",
        )


def _resolved_state_path(plan: dict[str, Any], item_id: str) -> pathlib.Path:
    records = plan.get("mutableState")
    if not isinstance(records, list):
        raise ControlError("plan.invalid", "Resolved plan is missing mutable state")
    record = next(
        (
            item
            for item in records
            if isinstance(item, dict) and item.get("id") == item_id
        ),
        None,
    )
    if record is None or not isinstance(record.get("location"), str):
        raise ControlError(
            "plan.invalid",
            "Resolved plan is missing required runtime state",
            itemId=item_id,
        )
    path = pathlib.Path(record["location"])
    if not path.is_absolute():
        raise ControlError(
            "plan.invalid",
            "Resolved runtime state path is not absolute",
            itemId=item_id,
            path=str(path),
        )
    return _absolute(path)


def _install_plan_digest(plan: dict[str, Any]) -> str:
    try:
        return runtime_install.resolved_plan_digest(plan, "install")
    except runtime_install.RuntimeInstallError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    except runtime_transaction.TransactionError as error:
        raise ControlError(error.code, error.message, **error.context) from error


def _require_committed_install_journal(plan: dict[str, Any]) -> None:
    journal_path = _resolved_state_path(plan, "transaction_journal")
    try:
        metadata = journal_path.lstat()
    except FileNotFoundError as error:
        raise ControlError(
            "runtime.not_installed",
            "No committed install journal is active for this runtime",
            path=str(journal_path),
        ) from error
    if (
        journal_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ControlError(
            "transaction.journal_invalid",
            "Active install journal has unsafe ownership, type, or mode",
            path=str(journal_path),
        )
    try:
        journal = artifact_contract.load_json(journal_path)
    except artifact_contract.ArtifactError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    try:
        journal = runtime_install.validate_resolved_plan_journal(
            plan,
            "install",
            journal,
        )
    except runtime_install.RuntimeInstallError as error:
        if error.code == "transaction.journal_mismatch":
            raise ControlError(
                "runtime.not_installed",
                "Active lifecycle journal does not prove an exact committed install",
                path=str(journal_path),
                expectedPlanDigest=_install_plan_digest(plan),
            ) from error
        raise ControlError(error.code, error.message, **error.context) from error
    if (
        journal.get("state") != "committed"
        or journal.get("cleanupFailures") != []
        or journal.get("rollbackFailures") != []
        or journal.get("failure") is not None
        or journal.get("cleanupInProgress") != {}
    ):
        raise ControlError(
            "runtime.not_installed",
            "Active lifecycle journal does not prove an exact committed install",
            path=str(journal_path),
            expectedPlanDigest=_install_plan_digest(plan),
        )


def _select_committed_install_plan(
    candidates: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    attempted_digests: list[str] = []
    for plan in candidates:
        digest = _install_plan_digest(plan)
        if digest in attempted_digests:
            continue
        attempted_digests.append(digest)
        try:
            _require_committed_install_journal(plan)
        except ControlError as error:
            if error.code != "runtime.not_installed":
                raise
        else:
            return plan
    raise ControlError(
        "runtime.not_installed",
        "No committed install journal matches an admitted lifecycle plan",
        expectedPlanDigests=attempted_digests,
    )


def _require_installed_plan(plan: dict[str, Any]) -> None:
    operations = plan.get("install")
    if not isinstance(operations, list):
        raise ControlError("plan.invalid", "Resolved plan is missing install operations")
    mismatches: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ControlError("plan.invalid", "Resolved install operation is invalid")
        if operation.get("resource") == "launch_agent_template":
            continue
        action = operation.get("action")
        expected_ready = action not in INSTALLED_FALSE_ACTIONS
        if operation.get("ready") is not expected_ready:
            mismatches.append(
                {
                    "id": operation.get("id"),
                    "action": action,
                    "expectedReady": expected_ready,
                    "actualReady": operation.get("ready"),
                    "reason": operation.get("blockedReason"),
                }
            )
    uninstall = plan.get("uninstall")
    if not isinstance(uninstall, list) or any(
        not isinstance(operation, dict) or operation.get("ready") is not True
        for operation in uninstall
    ):
        mismatches.append({"id": "uninstall", "reason": "uninstall plan is not exact"})
    stable = next(
        (
            operation
            for operation in operations
            if isinstance(operation, dict) and operation.get("id") == "verify_native_bridge_owned"
        ),
        None,
    )
    if (
        stable is None
        or stable.get("ownership") != "artifact-owned"
        or stable.get("targetTreeSha256") != stable.get("sourceTreeSha256")
    ):
        mismatches.append({"id": "verify_native_bridge_owned", "reason": "stable bridge is not exact"})
    if mismatches:
        raise ControlError(
            "runtime.not_installed",
            "Runtime targets do not match the exact committed installed layout",
            blockers=mismatches,
        )


def _require_launch_template_state(admission: StartAdmission) -> None:
    target = admission.paths.launch_agent_plist
    try:
        artifact_contract.reject_symlink_components(target)
    except artifact_contract.ArtifactError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return
    if target.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ControlError(
            "service.plist_foreign",
            "Launch agent target has unsafe ownership or type before start",
            path=str(target),
        )
    template = admission.artifact_path / "config" / "launch-agent.plist.template"
    try:
        expected_sha256 = artifact_contract.sha256_file(template)
        actual_sha256 = artifact_contract.sha256_file(target)
    except OSError as error:
        raise ControlError(
            "service.plist_foreign",
            "Launch agent template identity could not be inspected",
            path=str(target),
            detail=str(error),
        ) from error
    if actual_sha256 != expected_sha256:
        raise ControlError(
            "service.plist_foreign",
            "Launch agent target is neither absent nor the exact installed template",
            path=str(target),
            expectedSha256=expected_sha256,
            actualSha256=actual_sha256,
        )


def _plan_operations(plan: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    raw_operations = plan.get(kind)
    if not isinstance(raw_operations, list) or not all(
        isinstance(operation, dict) for operation in raw_operations
    ):
        raise ControlError("plan.invalid", f"Resolved plan has no valid {kind} operations")
    return raw_operations


def _require_plan_operation(
    plan: dict[str, Any],
    kind: str,
    action: str,
    target: pathlib.Path,
) -> dict[str, Any]:
    matches = [
        operation
        for operation in _plan_operations(plan, kind)
        if operation.get("action") == action
        and isinstance(operation.get("target"), str)
        and _absolute(pathlib.Path(operation["target"])) == target
    ]
    if len(matches) != 1 or matches[0].get("ready") is not True:
        raise ControlError(
            "profile.artifact_mismatch",
            "Selected profile does not match one exact installed lifecycle operation",
            kind=kind,
            action=action,
            target=str(target),
            matches=len(matches),
        )
    return matches[0]


def _require_resource_operation(
    plan: dict[str, Any],
    kind: str,
    action: str,
    resource: str,
) -> dict[str, Any]:
    matches = [
        operation
        for operation in _plan_operations(plan, kind)
        if operation.get("action") == action
        and operation.get("resource") == resource
        and operation.get("ready") is True
        and isinstance(operation.get("target"), str)
    ]
    if len(matches) != 1:
        raise ControlError(
            "profile.artifact_mismatch",
            "Installed runtime bridge resources are incomplete or ambiguous",
            kind=kind,
            action=action,
            resource=resource,
            matches=len(matches),
        )
    return matches[0]


def _relative_profile_path(root: pathlib.Path, target: pathlib.Path) -> str:
    try:
        return target.relative_to(root).as_posix()
    except ValueError as error:
        raise ControlError(
            "profile.artifact_mismatch",
            "Profile lifecycle target is outside the declared game install root",
            root=str(root),
            target=str(target),
        ) from error


def _resolve_crossover_launcher(crossover_root: pathlib.Path) -> pathlib.Path:
    launcher = crossover_root / (
        "Contents/SharedSupport/CrossOver/CrossOver-Hosted Application/cxstart"
    )
    target = launcher.parent / "wine"
    try:
        artifact_contract.reject_symlink_components(launcher.parent)
        launcher_metadata = launcher.lstat()
        launcher_target = os.readlink(launcher) if stat.S_ISLNK(launcher_metadata.st_mode) else None
        artifact_contract.reject_symlink_components(target)
        target_metadata = target.lstat()
    except (artifact_contract.ArtifactError, OSError) as error:
        raise ControlError(
            "producer.launch_invalid",
            "Exact CrossOver launcher could not be inspected",
            path=str(launcher),
            detail=str(error),
        ) from error
    if (
        launcher_target != "wine"
        or not stat.S_ISREG(target_metadata.st_mode)
        or not os.access(target, os.X_OK)
    ):
        raise ControlError(
            "producer.launch_invalid",
            "Exact CrossOver cxstart link does not select its executable sibling",
            path=str(launcher),
            target=launcher_target,
        )
    return launcher


def _inspect_profile_admission(
    manifest: dict[str, Any],
    bindings: dict[str, str],
    plan: dict[str, Any],
    artifact_path: pathlib.Path,
    profile_id: str,
) -> ProfileStartAdmission:
    try:
        loaded = runtime_profile.load_curated_profile(profile_id, manifest, artifact_path)
        installed = runtime_profile.resolve_installed_profile(loaded, bindings)
    except runtime_profile.ProfileError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    if not installed.owned_processes:
        raise ControlError(
            "profile.artifact_mismatch",
            "Curated runtime profile does not declare an owned producer process",
            profile=loaded.data["id"],
        )

    expected_install_targets: set[pathlib.Path] = set()
    expected_uninstall_targets: set[pathlib.Path] = set()
    substitutions: dict[str, str] = {}
    excluded: set[str] = set()
    for target in installed.targets:
        stock_openvr = target.openvr_directory / "openvr_api.dll"
        created = (
            target.openvr_directory / "openvr_api.real.dll",
            target.graphics_directory / "d3d11.dll",
            target.graphics_directory / "dxgi.dll",
            target.graphics_directory / "alvr_iosurface_bridge.dll",
        )
        _require_plan_operation(plan, "install", "replace_file", stock_openvr)
        restore = _require_plan_operation(plan, "uninstall", "restore", stock_openvr)
        if restore.get("expectedSha256") != target.stock_openvr_sha256:
            raise ControlError(
                "profile.artifact_mismatch",
                "Profile stock OpenVR identity differs from the uninstall contract",
                target=target.id,
                expected=target.stock_openvr_sha256,
                actual=restore.get("expectedSha256"),
            )
        expected_install_targets.add(stock_openvr)
        expected_uninstall_targets.add(stock_openvr)
        substitutions[_relative_profile_path(installed.install_root, stock_openvr)] = (
            target.stock_openvr_sha256
        )
        for path in created:
            _require_plan_operation(plan, "install", "create_file", path)
            _require_plan_operation(plan, "uninstall", "remove", path)
            expected_install_targets.add(path)
            expected_uninstall_targets.add(path)
            excluded.add(_relative_profile_path(installed.install_root, path))

    actual_install_targets = {
        _absolute(pathlib.Path(operation["target"]))
        for operation in _plan_operations(plan, "install")
        if operation.get("action") in runtime_transaction.INSTALL_EFFECTS
        and isinstance(operation.get("target"), str)
        and (
            _absolute(pathlib.Path(operation["target"])) == installed.install_root
            or installed.install_root in _absolute(pathlib.Path(operation["target"])).parents
        )
    }
    actual_uninstall_targets = {
        _absolute(pathlib.Path(operation["target"]))
        for operation in _plan_operations(plan, "uninstall")
        if operation.get("action") in runtime_transaction.UNINSTALL_EFFECTS
        and isinstance(operation.get("target"), str)
        and (
            _absolute(pathlib.Path(operation["target"])) == installed.install_root
            or installed.install_root in _absolute(pathlib.Path(operation["target"])).parents
        )
    }
    if (
        actual_install_targets != expected_install_targets
        or actual_uninstall_targets != expected_uninstall_targets
    ):
        raise ControlError(
            "profile.artifact_mismatch",
            "Selected profile does not own every installed game-root mutation",
            profile=loaded.data["id"],
            expectedInstall=sorted(str(path) for path in expected_install_targets),
            actualInstall=sorted(str(path) for path in actual_install_targets),
            expectedUninstall=sorted(str(path) for path in expected_uninstall_targets),
            actualUninstall=sorted(str(path) for path in actual_uninstall_targets),
        )

    try:
        file_count, tree_sha256 = runtime_profile.payload_tree_identity(
            installed.install_root,
            substitutions=substitutions,
            excluded=excluded,
        )
    except runtime_profile.ProfileError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    expected_payload = loaded.data["source"]["payload"]
    if (
        file_count != expected_payload["fileCount"]
        or tree_sha256 != expected_payload["treeSha256"]
    ):
        raise ControlError(
            "profile.not_installed",
            "Installed game payload does not match the curated profile beneath the runtime overlay",
            profile=loaded.data["id"],
            expected={
                "fileCount": expected_payload["fileCount"],
                "treeSha256": expected_payload["treeSha256"],
            },
            actual={"fileCount": file_count, "treeSha256": tree_sha256},
        )

    crossover_root = _absolute(pathlib.Path(bindings["CROSSOVER_APP"]))
    crossover_launcher = _resolve_crossover_launcher(crossover_root)

    steam_bottle = _absolute(pathlib.Path(bindings["STEAM_BOTTLE"]))
    expected_bottle_root = _absolute(
        pathlib.Path(bindings["HOME"])
        / "Library/Application Support/CrossOver/Bottles"
    )
    if steam_bottle.parent != expected_bottle_root or not steam_bottle.name:
        raise ControlError(
            "producer.launch_invalid",
            "Configured Steam bottle is not addressable by the exact CrossOver launcher",
            path=str(steam_bottle),
            expectedRoot=str(expected_bottle_root),
        )

    windows_bridge = _require_resource_operation(
        plan,
        "install",
        "create_file",
        "wine_bridge_windows",
    )
    unix_bridge = _require_resource_operation(
        plan,
        "install",
        "create_file",
        "wine_bridge_unix",
    )
    windows_bridge_path = _absolute(pathlib.Path(windows_bridge["target"]))
    unix_bridge_path = _absolute(pathlib.Path(unix_bridge["target"]))
    if (
        windows_bridge_path.parent.name != "x86_64-windows"
        or unix_bridge_path.parent.name != "x86_64-unix"
        or windows_bridge_path.parents[1] != unix_bridge_path.parents[1]
    ):
        raise ControlError(
            "profile.artifact_mismatch",
            "Installed Wine bridge roots do not share one exact runtime directory",
        )
    return ProfileStartAdmission(
        installed=installed,
        crossover_launcher=crossover_launcher,
        bottle_name=steam_bottle.name,
        bridge_root=windows_bridge_path.parents[1],
    )


def inspect_start_admission(
    context: RuntimeContext,
    artifact: pathlib.Path,
    profile_id: str,
) -> StartAdmission:
    manifest, _, manifest_hash, lock_hash = load_runtime_contract(context)
    try:
        bindings = artifact_contract.resolve_bindings(manifest, context.bindings_path, "plan")
        paths = resolve_runtime_paths(manifest, bindings)
        artifact_summary = verify_artifact_reference(context, artifact, require_sealed=True)
        artifact_path = pathlib.Path(artifact_summary["path"])
        legacy_plan = artifact_contract.build_plan(
            manifest,
            artifact_path,
            context.bindings_path,
            expected_manifest_hash=manifest_hash,
            expected_lock_hash=lock_hash,
        )
        profile_plan = runtime_install.build_runtime_plan(
            context,
            artifact_path,
            manifest,
            manifest_hash,
            lock_hash,
            profile_id,
        )
        plan = _select_committed_install_plan((profile_plan, legacy_plan))
        allowed_roots = tuple(
            artifact_contract.resolve_path(template, bindings, f"allowedTargetRoots[{index}]")
            for index, template in enumerate(manifest["allowedTargetRoots"])
        )
    except artifact_contract.ArtifactError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    except runtime_install.RuntimeInstallError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    admission = StartAdmission(
        manifest,
        bindings,
        paths,
        allowed_roots,
        plan,
        artifact_summary,
        artifact_path,
        _inspect_profile_admission(
            manifest,
            bindings,
            plan,
            artifact_path,
            profile_id,
        ),
    )
    _require_installed_plan(plan)
    _require_launch_template_state(admission)
    _private_directory(paths.state_root)
    return admission


def _make_private_directory(root: pathlib.Path, path: pathlib.Path) -> None:
    try:
        with runtime_descriptor.DescriptorSession([root]) as session:
            session.bind(path).make_directory(0o700)
    except runtime_descriptor.DescriptorError as error:
        raise ControlError(error.code, error.message, **error.context) from error


def _write_new_file(root: pathlib.Path, path: pathlib.Path, payload: bytes, mode: int = 0o600) -> None:
    try:
        with runtime_descriptor.DescriptorSession([root]) as session:
            session.bind(path).write_bytes(payload, mode)
    except runtime_descriptor.DescriptorError as error:
        raise ControlError(error.code, error.message, **error.context) from error


def _seed_alvr_session(admission: StartAdmission, run_dir: pathlib.Path) -> int:
    item = mutable_state_item(admission.manifest, "alvr_session_state")
    try:
        expanded = artifact_contract.expand_template(
            item["location"],
            admission.bindings,
            "mutableState.alvr_session_state.location",
        )
    except artifact_contract.ArtifactError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    source_root = _absolute(pathlib.Path(expanded))
    source_allowed = False
    for root in admission.allowed_roots:
        try:
            if os.path.commonpath([str(source_root), str(root)]) == str(root):
                source_allowed = True
                break
        except ValueError:
            continue
    if not source_allowed:
        raise ControlError(
            "client.trust_invalid",
            "Retained ALVR session state is outside the admitted roots",
            path=str(source_root),
        )
    try:
        artifact_contract.reject_symlink_components(source_root)
    except artifact_contract.ArtifactError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    source_session = source_root / "session.json"
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(source_session, flags)
    except OSError as error:
        raise ControlError(
            "client.trust_missing",
            "Retained ALVR session state is unavailable",
            path=str(source_session),
            detail=str(error),
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_size <= 0
            or metadata.st_size > 4 * 1024 * 1024
        ):
            raise ControlError(
                "client.trust_invalid",
                "Retained ALVR session state has unsafe metadata",
                path=str(source_session),
            )
        data = bytearray()
        while len(data) <= metadata.st_size:
            chunk = os.read(descriptor, min(65536, metadata.st_size - len(data)))
            if not chunk:
                break
            data.extend(chunk)
    finally:
        os.close(descriptor)
    if len(data) != metadata.st_size:
        raise ControlError(
            "client.trust_invalid",
            "Retained ALVR session state changed while being read",
            path=str(source_session),
        )
    try:
        session = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControlError(
            "client.trust_invalid",
            "Retained ALVR session state is not valid JSON",
            path=str(source_session),
        ) from error
    connections = session.get("client_connections") if isinstance(session, dict) else None
    if not isinstance(connections, dict):
        raise ControlError(
            "client.trust_invalid",
            "Retained ALVR session state has no client connection map",
            path=str(source_session),
        )
    trusted_connections: dict[str, dict[str, Any]] = {}
    for client_id, connection in connections.items():
        if (
            isinstance(client_id, str)
            and bool(client_id)
            and isinstance(connection, dict)
            and connection.get("trusted") is True
        ):
            trusted_connection = dict(connection)
            trusted_connection["connection_state"] = "Disconnected"
            trusted_connections[client_id] = trusted_connection
    if not trusted_connections:
        raise ControlError(
            "client.trust_missing",
            "Retained ALVR session state has no trusted client",
            path=str(source_session),
        )
    assert isinstance(session, dict)
    seeded_session = dict(session)
    seeded_session["client_connections"] = trusted_connections
    alvr_root = run_dir / ALVR_ROOT_NAME
    _write_new_file(
        alvr_root,
        alvr_root / "session.json",
        artifact_contract.canonical_json_bytes(seeded_session),
    )
    return len(trusted_connections)


def _write_file_atomic(root: pathlib.Path, path: pathlib.Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.start-tmp")
    try:
        with runtime_descriptor.DescriptorSession([root]) as session:
            temporary_bound = session.bind(temporary)
            target_bound = session.bind(path)
            if temporary_bound.exists():
                raise ControlError(
                    "runtime.start_foreign",
                    "Start publication temporary path already exists",
                    path=str(temporary),
                )
            temporary_bound.write_bytes(payload, 0o600)
            target_bound.replace_with(temporary_bound)
    except runtime_descriptor.DescriptorError as error:
        raise ControlError(error.code, error.message, **error.context) from error


def _write_json_atomic(root: pathlib.Path, path: pathlib.Path, value: dict[str, Any]) -> None:
    try:
        with runtime_transaction.descriptor_scope([root]):
            runtime_transaction.atomic_write_json(path, value)
    except runtime_transaction.TransactionError as error:
        raise ControlError(error.code, error.message, **error.context) from error


def _read_json_file(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        value = artifact_contract.load_json(path)
    except FileNotFoundError:
        return None
    except artifact_contract.ArtifactError:
        return None
    return value if isinstance(value, dict) else None


def _template_replace(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for token, replacement in replacements.items():
            result = result.replace(f"${{{token}}}", replacement)
        if "${" in result:
            raise ControlError(
                "repository.contract",
                "Launch agent template contains an unresolved placeholder",
                value=result,
            )
        return result
    if isinstance(value, list):
        return [_template_replace(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _template_replace(item, replacements) for key, item in value.items()}
    return value


def render_launch_agent(
    admission: StartAdmission,
    run_dir: pathlib.Path,
    generation: int,
) -> bytes:
    template_path = admission.artifact_path / "config" / "launch-agent.plist.template"
    try:
        with template_path.open("rb") as stream:
            template = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        raise ControlError(
            "artifact.invalid",
            "Sealed launch agent template is unreadable",
            path=str(template_path),
            detail=str(error),
        ) from error
    rendered = _template_replace(
        template,
        {
            "NATIVE_BRIDGE_PROGRAM": str(admission.paths.bridge_program),
            "NATIVE_BRIDGE_LOG": str(run_dir / BRIDGE_LOG_NAME),
            "ALVR_BRIDGE_ROOT": str(run_dir / ALVR_ROOT_NAME),
            "ALVR_BRIDGE_CONNECT": "true",
            "ALVR_BRIDGE_FRAMES": str(MAX_RUNTIME_FRAMES),
            "ALVR_IOSURFACE_POOL_NONCE": str(generation),
        },
    )
    return plistlib.dumps(rendered, fmt=plistlib.FMT_XML, sort_keys=True)


def _create_owner_lock(paths: RuntimePaths, run_dir: pathlib.Path) -> None:
    _make_private_directory(paths.state_root, paths.lock_path)
    try:
        _write_new_file(paths.lock_path, paths.lock_path / "pid", f"{os.getpid()}\n".encode())
        _write_new_file(
            paths.lock_path,
            paths.lock_path / "run-dir",
            f"{run_dir}\n".encode(),
        )
    except BaseException:
        with contextlib.suppress(OSError):
            for name in ("pid", "run-dir"):
                (paths.lock_path / name).unlink(missing_ok=True)
            paths.lock_path.rmdir()
        raise


def _remove_owner_lock(paths: RuntimePaths, run_dir: pathlib.Path) -> None:
    lock = inspect_lock(paths.lock_path, lambda pid: pid == os.getpid())
    if (
        not lock.exists
        or not lock.valid_path
        or lock.pid != os.getpid()
        or lock.run_dir != str(run_dir)
    ):
        raise ControlError(
            "owner.identity_mismatch",
            "Supervisor owner lock no longer matches this generation",
            path=str(paths.lock_path),
        )
    for name in ("pid", "run-dir"):
        (paths.lock_path / name).unlink()
    paths.lock_path.rmdir()


def _remove_file_if_exact(path: pathlib.Path, expected_sha256: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ControlError(
            "runtime.start_foreign",
            "Supervisor cleanup target has unsafe ownership or type",
            path=str(path),
        )
    if artifact_contract.sha256_file(path) != expected_sha256:
        raise ControlError(
            "runtime.start_foreign",
            "Supervisor cleanup target changed after publication",
            path=str(path),
        )
    path.unlink()


def _profile_record(profile: ProfileStartAdmission) -> dict[str, Any]:
    source = profile.installed.loaded.data["source"]
    launch = profile.installed.loaded.data["launch"]
    return {
        "id": profile.installed.loaded.data["id"],
        "sha256": profile.installed.loaded.sha256,
        "appId": source["appId"],
        "buildId": source["buildId"],
        "entrypointTarget": launch["entrypointTarget"],
    }


def _producer_record(
    profile: ProfileStartAdmission,
    status: str,
    launcher_pid: int,
    launcher_birth_token: int,
    launcher_started_at: str,
    process_group_id: int,
    producer_log: pathlib.Path,
    target: ProducerIdentity | None,
) -> dict[str, Any]:
    owned_process = profile.installed.owned_process
    return {
        "status": status,
        "launcher": {
            "pid": launcher_pid,
            "birthToken": launcher_birth_token,
            "startedAt": launcher_started_at,
            "processGroupId": process_group_id,
        },
        "expectedOwnedProcess": (
            {
                "executable": str(owned_process.executable),
                "processPattern": owned_process.process_pattern,
            }
            if owned_process is not None
            else None
        ),
        "ownedProcess": target.to_dict() if target is not None else None,
        "log": str(producer_log),
    }


def _launching_producer_record(
    profile: ProfileStartAdmission,
    producer_log: pathlib.Path,
) -> dict[str, Any]:
    owned_process = profile.installed.owned_process
    return {
        "status": "launching",
        "launcher": None,
        "expectedOwnedProcess": (
            {
                "executable": str(owned_process.executable),
                "processPattern": owned_process.process_pattern,
            }
            if owned_process is not None
            else None
        ),
        "ownedProcess": None,
        "log": str(producer_log),
    }


def _declared_owned_processes(
    profile: ProfileStartAdmission,
) -> tuple[runtime_profile.ResolvedOwnedProcess, ...]:
    owned_processes = profile.installed.owned_processes
    if len(owned_processes) <= 1:
        raise ControlError(
            "producer.identity_unavailable",
            "Multi-target producer helpers require more than one declared target",
        )
    target_ids: set[str] = set()
    for owned_process in owned_processes:
        target_id = owned_process.target_id
        if target_id is None or target_id in target_ids:
            raise ControlError(
                "producer.identity_unavailable",
                "Multi-target producer declaration is incomplete or duplicated",
            )
        target_ids.add(target_id)
    return owned_processes


def _ordered_owned_producer_identities(
    profile: ProfileStartAdmission,
    identities: Sequence[OwnedProducerIdentity],
) -> tuple[OwnedProducerIdentity, ...]:
    target_order = {
        owned_process.target_id: index
        for index, owned_process in enumerate(_declared_owned_processes(profile))
    }
    ordered: dict[str, OwnedProducerIdentity] = {}
    pids: set[int] = set()
    for identity in identities:
        if identity.target_id not in target_order or identity.target_id in ordered:
            raise ControlError(
                "producer.identity_changed",
                "Multi-target producer identities are undeclared or duplicated",
                targetId=identity.target_id,
            )
        if identity.identity.pid in pids:
            raise ControlError(
                "producer.identity_changed",
                "One PID cannot own multiple producer targets",
                pid=identity.identity.pid,
            )
        ordered[identity.target_id] = identity
        pids.add(identity.identity.pid)
    return tuple(sorted(ordered.values(), key=lambda item: target_order[item.target_id]))


def _owned_producer_record(
    profile: ProfileStartAdmission,
    status: str,
    launcher_pid: int,
    launcher_birth_token: int,
    launcher_started_at: str,
    process_group_id: int,
    producer_log: pathlib.Path,
    identities: Sequence[OwnedProducerIdentity],
) -> dict[str, Any]:
    owned_processes = _declared_owned_processes(profile)
    ordered_identities = _ordered_owned_producer_identities(profile, identities)
    return {
        "status": status,
        "launcher": {
            "pid": launcher_pid,
            "birthToken": launcher_birth_token,
            "startedAt": launcher_started_at,
            "processGroupId": process_group_id,
        },
        "expectedOwnedProcesses": [
            {
                "targetId": owned_process.target_id,
                "executable": str(owned_process.executable),
                "processPattern": owned_process.process_pattern,
            }
            for owned_process in owned_processes
        ],
        "ownedProcesses": [identity.to_dict() for identity in ordered_identities],
        "log": str(producer_log),
    }


def _launching_owned_producer_record(
    profile: ProfileStartAdmission,
    producer_log: pathlib.Path,
) -> dict[str, Any]:
    owned_processes = _declared_owned_processes(profile)
    return {
        "status": "launching",
        "launcher": None,
        "expectedOwnedProcesses": [
            {
                "targetId": owned_process.target_id,
                "executable": str(owned_process.executable),
                "processPattern": owned_process.process_pattern,
            }
            for owned_process in owned_processes
        ],
        "ownedProcesses": [],
        "log": str(producer_log),
    }


def _state_payload(
    admission: StartAdmission,
    run_dir: pathlib.Path,
    generation: int,
    service: Any,
    owner_started_at: str,
    control_socket: pathlib.Path,
    plist_sha256: str,
    state: str,
    phase: str,
    producer: dict[str, Any],
    client: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if service.snapshot.pid is None or service.file_identity is None or admission.profile is None:
        raise ControlError("runtime.start_failed", "Live service identity is incomplete")
    return {
        "schemaVersion": 6 if "expectedOwnedProcesses" in producer else 5,
        "state": state,
        "generation": generation,
        "ownerPid": os.getpid(),
        "ownerStartedAt": owner_started_at,
        "serviceLabel": admission.paths.service_label,
        "servicePid": service.snapshot.pid,
        "serviceRuns": service.snapshot.runs,
        "artifactPath": str(admission.artifact_path),
        "artifactSeal": admission.artifact["sealId"],
        "bridgeIdentity": identity_record(service.file_identity),
        "runDir": str(run_dir),
        "controlSocket": str(control_socket),
        "plistSha256": plist_sha256,
        "bridgeExecutableSha256": artifact_contract.sha256_file(
            admission.paths.bridge_program
        ),
        "profile": _profile_record(admission.profile),
        "producer": producer,
        "client": client,
        "updatedAt": datetime.datetime.now(datetime.UTC).isoformat(),
        "diagnostic": {
            "phase": phase,
            "bridgeLog": str(run_dir / BRIDGE_LOG_NAME),
            "producerLog": producer["log"],
            "supervisorLog": str(run_dir / SUPERVISOR_LOG_NAME),
        },
    }


def _producer_launch(
    admission: StartAdmission,
    run_dir: pathlib.Path,
    generation: int,
    launcher: ProducerLauncher,
) -> tuple[ProducerProcess, pathlib.Path]:
    if admission.profile is None:
        raise ControlError("profile.artifact_mismatch", "Start admission has no curated profile")
    profile = admission.profile
    entrypoint = profile.installed.entrypoint
    dxvk_log_root = run_dir / DXVK_LOG_ROOT_NAME
    mvk_shader_root = run_dir / MVK_SHADER_ROOT_NAME
    _make_private_directory(run_dir, dxvk_log_root)
    _make_private_directory(run_dir, mvk_shader_root)
    runtime_environment = {
        "ALVR_IOSURFACE_POOL_NONCE": str(generation),
        "ALVR_IOSURFACE_POOL_SERVICE": admission.paths.service_label,
        "ALVR_IOSURFACE_SOURCE_HEIGHT": str(
            profile.installed.loaded.data["geometry"]["maximumStereo"]["height"]
        ),
        "ALVR_IOSURFACE_SOURCE_WIDTH": str(
            profile.installed.loaded.data["geometry"]["maximumStereo"]["width"]
        ),
        "ALVR_MOLTENVK_PATH": str(
            pathlib.Path(admission.bindings["CROSSOVER_APP"])
            / "Contents/SharedSupport/CrossOver/lib64/libMoltenVK.dylib"
        ),
        "CX_GRAPHICS_BACKEND": "dxvk",
        "DXVK_LOG_LEVEL": "debug",
        "DXVK_LOG_PATH": str(dxvk_log_root),
        "DXVK_STATE_CACHE": "0",
        "MVK_CONFIG_LOG_LEVEL": "3",
        "MVK_CONFIG_SHADER_DUMP_DIR": str(mvk_shader_root),
        "MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS": "0",
        "WINEDLLPATH": str(profile.bridge_root),
        "WINEDLLOVERRIDES": "d3d11,dxgi=n",
        "WINEDEBUG": "-all,+loaddll",
        **profile.installed.loaded.data["launch"]["environment"],
    }
    cx_environment = " ".join(
        f"{name}={shlex.quote(value)}" for name, value in sorted(runtime_environment.items())
    )
    command = [
        str(profile.crossover_launcher),
        "--bottle",
        profile.bottle_name,
        "--no-update",
        "--no-gui",
        "--wait-children",
        "--workdir",
        str(entrypoint.working_directory),
        "--env",
        cx_environment,
        str(entrypoint.executable),
        *profile.installed.loaded.data["launch"]["arguments"],
    ]
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    producer_log = run_dir / PRODUCER_LOG_NAME
    try:
        process = launcher.launch(
            command,
            entrypoint.working_directory,
            environment,
            producer_log,
        )
    except OSError as error:
        raise ControlError(
            "producer.launch_failed",
            f"CrossOver producer could not be launched: {error}",
        ) from error
    return process, producer_log


def _process_profile_executable(
    pid: int,
    profile: ProfileStartAdmission,
    runner: CommandRunner,
) -> tuple[pathlib.Path | None, str | None]:
    owned_process = profile.installed.owned_process
    if owned_process is None:
        return None, None
    result = runner.run(
        ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "txt", "-Fn"],
        timeout=5.0,
    )
    if result.error is not None or result.returncode != 0:
        return None, "Owned process executable mapping could not be inspected"
    for line in result.stdout.splitlines():
        if not line.startswith("n") or len(line) == 1:
            continue
        candidate = _absolute(pathlib.Path(line[1:]))
        if paths_match(candidate, owned_process.executable):
            return owned_process.executable, None
    return None, None


def _owned_process_candidates(
    profile: ProfileStartAdmission,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
) -> list[ProducerIdentity]:
    owned_process = profile.installed.owned_process
    if owned_process is None:
        return []
    result = runner.run(
        [
            "/usr/bin/env",
            "LC_ALL=C",
            "/bin/ps",
            "-ww",
            "-axo",
            "pid=,pgid=,command=",
        ],
        timeout=5.0,
    )
    if result.error is not None or result.returncode != 0:
        raise ControlError(
            "producer.identity_unavailable",
            "Producer process table could not be inspected",
        )
    pattern = re.compile(owned_process.process_pattern)
    candidates: list[ProducerIdentity] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        try:
            pid = int(fields[0])
            group_id = int(fields[1])
        except ValueError:
            continue
        command = fields[2]
        if (
            not pattern.search(command)
            or owned_process.executable.name.casefold() not in command.casefold()
        ):
            continue
        executable, executable_error = _process_profile_executable(pid, profile, runner)
        if executable_error is not None:
            raise ControlError(
                "producer.identity_unavailable",
                executable_error,
                pid=pid,
            )
        if executable is None:
            continue
        started_at, start_error = process_start_time(pid, runner)
        if started_at is None:
            raise ControlError(
                "producer.identity_unavailable",
                start_error or "Producer process start time is unavailable",
                pid=pid,
            )
        birth_token, birth_error = birth_token_reader(pid)
        if birth_token is None:
            raise ControlError(
                "producer.identity_unavailable",
                birth_error or "Producer process birth token is unavailable",
                pid=pid,
            )
        pid_version, pid_version_error = pid_version_reader(pid)
        if pid_version is None:
            raise ControlError(
                "producer.identity_unavailable",
                pid_version_error or "Producer process PID version is unavailable",
                pid=pid,
            )
        candidates.append(
            ProducerIdentity(
                pid,
                birth_token,
                pid_version,
                started_at,
                group_id,
                command,
                executable,
            )
        )
    return candidates


def _process_owned_target_mappings(
    pid: int,
    owned_processes: Sequence[runtime_profile.ResolvedOwnedProcess],
    runner: CommandRunner,
) -> tuple[tuple[runtime_profile.ResolvedOwnedProcess, ...] | None, str | None]:
    result = runner.run(
        ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "txt", "-Fn"],
        timeout=5.0,
    )
    if result.error is not None or result.returncode != 0:
        return None, "Owned process executable mapping could not be inspected"
    mappings = tuple(
        _absolute(pathlib.Path(line[1:]))
        for line in result.stdout.splitlines()
        if line.startswith("n") and len(line) > 1
    )
    return (
        tuple(
            owned_process
            for owned_process in owned_processes
            if any(paths_match(mapping, owned_process.executable) for mapping in mappings)
        ),
        None,
    )


def _owned_producer_observation(
    profile: ProfileStartAdmission,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
) -> _OwnedProducerObservation:
    owned_processes = _declared_owned_processes(profile)
    patterns = {
        owned_process.target_id: re.compile(owned_process.process_pattern)
        for owned_process in owned_processes
    }
    result = runner.run(
        [
            "/usr/bin/env",
            "LC_ALL=C",
            "/bin/ps",
            "-ww",
            "-axo",
            "pid=,pgid=,command=",
        ],
        timeout=5.0,
    )
    if result.error is not None or result.returncode != 0:
        raise ControlError(
            "producer.identity_unavailable",
            "Producer process table could not be inspected",
        )
    process_ids: set[int] = set()
    candidates: dict[str, OwnedProducerIdentity] = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) < 2:
            continue
        try:
            pid = int(fields[0])
            process_group = int(fields[1])
        except ValueError:
            continue
        process_ids.add(pid)
        if len(fields) != 3:
            continue
        command = fields[2]
        pattern_matches = tuple(
            owned_process
            for owned_process in owned_processes
            if patterns[owned_process.target_id].search(command)
        )
        if not pattern_matches:
            continue
        mapped_targets, mapping_error = _process_owned_target_mappings(
            pid,
            owned_processes,
            runner,
        )
        if mapped_targets is None:
            raise ControlError(
                "producer.identity_unavailable",
                mapping_error or "Owned process executable mapping is unavailable",
                pid=pid,
            )
        exact_matches = tuple(
            owned_process
            for owned_process in mapped_targets
            if owned_process in pattern_matches
        )
        if not exact_matches:
            continue
        if (
            len(pattern_matches) > 1
            or len(mapped_targets) > 1
            or len(exact_matches) > 1
        ):
            raise ControlError(
                "producer.identity_changed",
                "One PID ambiguously matches multiple declared producer targets",
                pid=pid,
                targetIds=sorted(
                    owned_process.target_id
                    for owned_process in pattern_matches
                    if owned_process.target_id is not None
                ),
            )
        owned_process = exact_matches[0]
        target_id = owned_process.target_id
        assert target_id is not None
        if target_id in candidates:
            raise ControlError(
                "producer.identity_changed",
                "Multiple exact candidates were discovered for one producer target",
                targetId=target_id,
                pids=sorted((candidates[target_id].identity.pid, pid)),
            )
        started_at, start_error = process_start_time(pid, runner)
        if started_at is None:
            raise ControlError(
                "producer.identity_unavailable",
                start_error or "Producer process start time is unavailable",
                pid=pid,
            )
        birth_token, birth_error = birth_token_reader(pid)
        if birth_token is None:
            raise ControlError(
                "producer.identity_unavailable",
                birth_error or "Producer process birth token is unavailable",
                pid=pid,
            )
        pid_version, pid_version_error = pid_version_reader(pid)
        if pid_version is None:
            raise ControlError(
                "producer.identity_unavailable",
                pid_version_error or "Producer process PID version is unavailable",
                pid=pid,
            )
        candidates[target_id] = OwnedProducerIdentity(
            target_id,
            ProducerIdentity(
                pid,
                birth_token,
                pid_version,
                started_at,
                process_group,
                command,
                owned_process.executable,
            ),
        )
    return _OwnedProducerObservation(
        _ordered_owned_producer_identities(profile, tuple(candidates.values())),
        frozenset(process_ids),
    )


def _owned_producer_candidates(
    profile: ProfileStartAdmission,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
) -> list[OwnedProducerIdentity]:
    return list(
        _owned_producer_observation(
            profile,
            runner,
            birth_token_reader,
            pid_version_reader,
        ).identities
    )


def _inspect_owned_producer_observation(
    profile: ProfileStartAdmission,
    launcher_started_at: str,
    launcher_process_group_id: int,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
) -> _OwnedProducerObservation:
    launcher_started = _process_start_datetime(launcher_started_at)
    if launcher_started is None:
        raise ControlError(
            "producer.identity_unavailable",
            "Producer launcher start time could not be parsed",
        )
    observation = _owned_producer_observation(
        profile,
        runner,
        birth_token_reader,
        pid_version_reader,
    )
    for candidate in observation.identities:
        identity = candidate.identity
        candidate_started = _process_start_datetime(identity.started_at)
        if candidate_started is None:
            raise ControlError(
                "producer.identity_unavailable",
                "Producer process start time could not be parsed",
                targetId=candidate.target_id,
                pid=identity.pid,
            )
        if candidate_started < launcher_started:
            raise ControlError(
                "producer.identity_changed",
                "Exact owned process predates the active launcher",
                targetId=candidate.target_id,
                pid=identity.pid,
            )
        if identity.process_group_id not in {
            identity.pid,
            launcher_process_group_id,
        }:
            raise ControlError(
                "producer.identity_changed",
                "Exact owned process is not in an admitted process group",
                targetId=candidate.target_id,
                pid=identity.pid,
                processGroupId=identity.process_group_id,
            )
    return observation


def _inspect_owned_producer_identities(
    profile: ProfileStartAdmission,
    launcher_started_at: str,
    launcher_process_group_id: int,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
) -> tuple[OwnedProducerIdentity, ...]:
    return _inspect_owned_producer_observation(
        profile,
        launcher_started_at,
        launcher_process_group_id,
        runner,
        birth_token_reader,
        pid_version_reader,
    ).identities


def _revalidate_owned_producer_identity(
    expected: OwnedProducerIdentity,
    profile: ProfileStartAdmission,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
    *,
    error_code: str = "producer.quiesce_failed",
) -> OwnedProducerIdentity:
    owned_by_id = {
        owned_process.target_id: owned_process
        for owned_process in _declared_owned_processes(profile)
    }
    owned_process = owned_by_id.get(expected.target_id)
    identity = expected.identity
    if owned_process is None:
        raise ControlError(
            error_code,
            "Recorded producer target is no longer declared",
            targetId=expected.target_id,
        )
    birth_token, birth_error = birth_token_reader(identity.pid)
    pid_version, pid_version_error = pid_version_reader(identity.pid)
    started_at, start_error = process_start_time(identity.pid, runner)
    current_group, group_error = process_group_id(identity.pid, runner)
    command, command_error = process_command(identity.pid, runner)
    exact_matches, executable_error = _process_owned_target_mappings(
        identity.pid,
        tuple(owned_by_id.values()),
        runner,
    )
    confirmed_started_at, confirmed_start_error = process_start_time(identity.pid, runner)
    confirmed_birth_token, confirmed_birth_error = birth_token_reader(identity.pid)
    confirmed_pid_version, confirmed_pid_version_error = pid_version_reader(identity.pid)
    if birth_token != identity.birth_token:
        detail = birth_error or "Owned process birth token changed"
    elif pid_version != identity.pid_version:
        detail = pid_version_error or "Owned process PID version changed"
    elif started_at != identity.started_at:
        detail = start_error or "Owned process start time changed"
    elif current_group != identity.process_group_id:
        detail = group_error or "Owned process group changed"
    elif command != identity.command:
        detail = command_error or "Owned process command changed"
    elif executable_error is not None:
        detail = executable_error
    elif exact_matches != (owned_process,):
        detail = "Owned process executable mapping changed"
    elif not paths_match(identity.executable, owned_process.executable):
        detail = "Recorded owned process executable is not the declared target"
    elif confirmed_started_at != identity.started_at:
        detail = confirmed_start_error or "Owned process lifetime changed during validation"
    elif confirmed_birth_token != identity.birth_token:
        detail = confirmed_birth_error or "Owned process birth token changed during validation"
    elif confirmed_pid_version != identity.pid_version:
        detail = (
            confirmed_pid_version_error
            or "Owned process PID version changed during validation"
        )
    else:
        detail = None
    if detail is not None:
        raise ControlError(
            error_code,
            detail,
            targetId=expected.target_id,
            pid=identity.pid,
        )
    assert confirmed_started_at is not None
    assert confirmed_birth_token is not None
    assert confirmed_pid_version is not None
    assert current_group is not None
    assert command is not None
    return OwnedProducerIdentity(
        expected.target_id,
        ProducerIdentity(
            identity.pid,
            confirmed_birth_token,
            confirmed_pid_version,
            confirmed_started_at,
            current_group,
            command,
            owned_process.executable,
        ),
    )


def _confirm_owned_producer_observation(
    observation: _OwnedProducerObservation,
    profile: ProfileStartAdmission,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
    *,
    error_code: str = "producer.identity_changed",
) -> _OwnedProducerObservation:
    confirmed = tuple(
        _revalidate_owned_producer_identity(
            identity,
            profile,
            runner,
            birth_token_reader,
            pid_version_reader,
            error_code=error_code,
        )
        for identity in observation.identities
    )
    if confirmed != observation.identities:
        raise ControlError(
            error_code,
            "Owned process collection changed during exact validation",
        )
    return _OwnedProducerObservation(confirmed, observation.process_ids)


def _confirm_owned_producer_identities(
    expected: Sequence[OwnedProducerIdentity],
    profile: ProfileStartAdmission,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
    *,
    error_code: str = "producer.identity_changed",
) -> tuple[OwnedProducerIdentity, ...]:
    ordered = _ordered_owned_producer_identities(profile, expected)
    return tuple(
        _revalidate_owned_producer_identity(
            identity,
            profile,
            runner,
            birth_token_reader,
            pid_version_reader,
            error_code=error_code,
        )
        for identity in ordered
    )


def _owned_producer_handshake_intersection(
    bridge_log: pathlib.Path,
    service_label: str,
    generation: int,
    bridge_pid: int,
    identities: Sequence[OwnedProducerIdentity],
) -> tuple[OwnedProducerIdentity, ...]:
    try:
        payload = bridge_log.read_text(errors="replace")
    except OSError:
        return ()
    handshake = re.compile(
        "^native_source producer handshake accepted "
        f"service={re.escape(service_label)} nonce={generation} "
        f"bridge_pid={bridge_pid} producer_pid=([1-9][0-9]*) "
        "producer_pidversion=([1-9][0-9]*) "
        "producer_start_token=([1-9][0-9]*) source=[1-9][0-9]*x[1-9][0-9]*$",
        re.MULTILINE,
    )
    self_tests = "native_source startup self-tests passed slots=3"
    if payload.count(self_tests) != 1:
        return ()
    authenticated = {
        (int(pid), int(pid_version), int(birth_token))
        for pid, pid_version, birth_token in handshake.findall(payload)
    }
    return tuple(
        identity
        for identity in identities
        if (
            identity.identity.pid,
            identity.identity.pid_version,
            identity.identity.birth_token,
        )
        in authenticated
    )


def _owned_producer_markers_ready(
    bridge_log: pathlib.Path,
    service_label: str,
    generation: int,
    bridge_pid: int,
    identities: Sequence[OwnedProducerIdentity],
) -> tuple[OwnedProducerIdentity, ...]:
    return _owned_producer_handshake_intersection(
        bridge_log,
        service_label,
        generation,
        bridge_pid,
        identities,
    )


def _require_owned_producer_absence(
    expected: OwnedProducerIdentity,
    observation: _OwnedProducerObservation,
    launcher_process_group_id: int,
    group_live: Callable[[int], bool],
    *,
    error_code: str = "producer.identity_changed",
) -> None:
    if expected in observation.identities:
        return
    identity = expected.identity
    if identity.pid in observation.process_ids:
        raise ControlError(
            error_code,
            "Outgoing producer PID remains live without its exact identity",
            targetId=expected.target_id,
            pid=identity.pid,
        )
    if (
        identity.process_group_id != launcher_process_group_id
        and group_live(identity.process_group_id)
    ):
        raise ControlError(
            error_code,
            "Outgoing detached producer group remains live after its exact identity disappeared",
            targetId=expected.target_id,
            processGroupId=identity.process_group_id,
        )


def _merge_owned_producer_evidence(
    profile: ProfileStartAdmission,
    retained: Sequence[OwnedProducerIdentity],
    authenticated: Sequence[OwnedProducerIdentity],
    observation: _OwnedProducerObservation,
    launcher_process_group_id: int,
    group_live: Callable[[int], bool],
    *,
    error_code: str = "producer.identity_changed",
) -> tuple[OwnedProducerIdentity, ...]:
    retained_by_target = {identity.target_id: identity for identity in retained}
    for identity in authenticated:
        previous = retained_by_target.get(identity.target_id)
        if previous is not None and previous != identity:
            _require_owned_producer_absence(
                previous,
                observation,
                launcher_process_group_id,
                group_live,
                error_code=error_code,
            )
        retained_by_target[identity.target_id] = identity
    return _ordered_owned_producer_identities(
        profile,
        tuple(retained_by_target.values()),
    )


def _reconcile_owned_producer_transition(
    profile: ProfileStartAdmission,
    previous: _OwnedProducerTransition,
    observation: _OwnedProducerObservation,
    bridge_log: pathlib.Path,
    service_label: str,
    generation: int,
    bridge_pid: int,
    launcher_process_group_id: int,
    now: float,
    transition_timeout: float,
    group_live: Callable[[int], bool],
) -> _OwnedProducerTransition:
    for identity in previous.live:
        if identity not in observation.identities:
            _require_owned_producer_absence(
                identity,
                observation,
                launcher_process_group_id,
                group_live,
            )
    authenticated = _owned_producer_handshake_intersection(
        bridge_log,
        service_label,
        generation,
        bridge_pid,
        observation.identities,
    )
    retained = _merge_owned_producer_evidence(
        profile,
        previous.retained,
        authenticated,
        observation,
        launcher_process_group_id,
        group_live,
    )
    pending = (
        len(observation.identities) != 1
        or len(authenticated) != len(observation.identities)
    )
    transition_deadline = previous.deadline
    if pending:
        if transition_deadline is None:
            transition_deadline = now + transition_timeout
        if now >= transition_deadline:
            raise ControlError(
                "producer.transition_timeout",
                "Declared producer targets did not complete one exact authenticated transition before the profile deadline",
                liveTargetIds=[identity.target_id for identity in observation.identities],
                authenticatedTargetIds=[identity.target_id for identity in authenticated],
            )
    else:
        transition_deadline = None
    return _OwnedProducerTransition(authenticated, retained, transition_deadline)


def _inspect_producer_identity(
    profile: ProfileStartAdmission,
    launcher_started_at: str,
    launcher_process_group_id: int,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
) -> ProducerIdentity | None:
    launcher_started = _process_start_datetime(launcher_started_at)
    if launcher_started is None:
        raise ControlError(
            "producer.identity_unavailable",
            "Producer launcher start time could not be parsed",
        )
    candidates = _owned_process_candidates(
        profile,
        runner,
        birth_token_reader,
        pid_version_reader,
    )
    if len(candidates) > 1:
        raise ControlError(
            "producer.identity_changed",
            "Multiple exact owned-process candidates were discovered",
            pids=sorted(candidate.pid for candidate in candidates),
        )
    if not candidates:
        return None
    candidate = candidates[0]
    candidate_started = _process_start_datetime(candidate.started_at)
    if candidate_started is None:
        raise ControlError(
            "producer.identity_unavailable",
            "Producer process start time could not be parsed",
            pid=candidate.pid,
        )
    if candidate_started < launcher_started:
        raise ControlError(
            "producer.identity_changed",
            "Exact owned process predates the active launcher",
            pid=candidate.pid,
        )
    if candidate.process_group_id not in {candidate.pid, launcher_process_group_id}:
        raise ControlError(
            "producer.identity_changed",
            "Exact owned process is not in an admitted process group",
            pid=candidate.pid,
            processGroupId=candidate.process_group_id,
        )
    return candidate


def _revalidate_producer_identity(
    expected: ProducerIdentity,
    profile: ProfileStartAdmission,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
    *,
    error_code: str = "producer.quiesce_failed",
) -> ProducerIdentity:
    birth_token, birth_error = birth_token_reader(expected.pid)
    pid_version, pid_version_error = pid_version_reader(expected.pid)
    started_at, start_error = process_start_time(expected.pid, runner)
    current_group, group_error = process_group_id(expected.pid, runner)
    command, command_error = process_command(expected.pid, runner)
    executable, executable_error = _process_profile_executable(
        expected.pid,
        profile,
        runner,
    )
    confirmed_started_at, confirmed_start_error = process_start_time(expected.pid, runner)
    confirmed_birth_token, confirmed_birth_error = birth_token_reader(expected.pid)
    confirmed_pid_version, confirmed_pid_version_error = pid_version_reader(expected.pid)
    if birth_token != expected.birth_token:
        detail = birth_error or "Owned process birth token changed before stop"
    elif pid_version != expected.pid_version:
        detail = pid_version_error or "Owned process PID version changed before stop"
    elif started_at != expected.started_at:
        detail = start_error or "Owned process start time changed before stop"
    elif current_group != expected.process_group_id:
        detail = group_error or "Owned process group changed before stop"
    elif command != expected.command:
        detail = command_error or "Owned process command changed before stop"
    elif executable_error is not None:
        detail = executable_error
    elif executable is None or not paths_match(executable, expected.executable):
        detail = "Owned process executable mapping changed before stop"
    elif confirmed_started_at != expected.started_at:
        detail = confirmed_start_error or "Owned process lifetime changed during validation"
    elif confirmed_birth_token != expected.birth_token:
        detail = confirmed_birth_error or "Owned process birth token changed during validation"
    elif confirmed_pid_version != expected.pid_version:
        detail = (
            confirmed_pid_version_error
            or "Owned process PID version changed during validation"
        )
    else:
        detail = None
    if detail is not None:
        raise ControlError(
            error_code,
            detail,
            pid=expected.pid,
        )
    assert confirmed_started_at is not None
    assert confirmed_birth_token is not None
    assert confirmed_pid_version is not None
    assert current_group is not None
    assert command is not None
    assert executable is not None
    return ProducerIdentity(
        expected.pid,
        confirmed_birth_token,
        confirmed_pid_version,
        confirmed_started_at,
        current_group,
        command,
        executable,
    )


def _confirm_producer_identity(
    expected: ProducerIdentity,
    profile: ProfileStartAdmission,
    launcher_started_at: str,
    launcher_process_group_id: int,
    runner: CommandRunner,
    birth_token_reader: Callable[[int], tuple[int | None, str | None]],
    pid_version_reader: Callable[[int], tuple[int | None, str | None]],
) -> ProducerIdentity:
    current = _revalidate_producer_identity(
        expected,
        profile,
        runner,
        birth_token_reader,
        pid_version_reader,
        error_code="producer.identity_changed",
    )
    rediscovered = _inspect_producer_identity(
        profile,
        launcher_started_at,
        launcher_process_group_id,
        runner,
        birth_token_reader,
        pid_version_reader,
    )
    if rediscovered is None or rediscovered != current:
        raise ControlError(
            "producer.identity_changed",
            "Owned process identity changed during admission",
            expectedPid=current.pid,
            observedPid=rediscovered.pid if rediscovered is not None else None,
        )
    return _revalidate_producer_identity(
        rediscovered,
        profile,
        runner,
        birth_token_reader,
        pid_version_reader,
        error_code="producer.identity_changed",
    )


def _producer_markers_ready(
    bridge_log: pathlib.Path,
    service_label: str,
    generation: int,
    bridge_pid: int,
    producer_pid: int,
    producer_pid_version: int,
    producer_birth_token: int,
) -> bool:
    try:
        payload = bridge_log.read_text(errors="replace")
    except OSError:
        return False
    handshake = re.compile(
        "^native_source producer handshake accepted "
        f"service={re.escape(service_label)} nonce={generation} "
        f"bridge_pid={bridge_pid} producer_pid={producer_pid} "
        f"producer_pidversion={producer_pid_version} "
        f"producer_start_token={producer_birth_token} source=[1-9][0-9]*x[1-9][0-9]*$",
        re.MULTILINE,
    )
    self_tests = "native_source startup self-tests passed slots=3"
    return len(handshake.findall(payload)) == 1 and payload.count(self_tests) == 1


def _group_is_live(process_group_id: int) -> bool:
    try:
        result = subprocess.run(
            [
                "/usr/bin/env",
                "LC_ALL=C",
                "/bin/ps",
                "-axo",
                "pgid=,stat=",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        for line in result.stdout.splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) != 2:
                continue
            try:
                group_id = int(fields[0])
            except ValueError:
                continue
            if group_id == process_group_id and not fields[1].startswith("Z"):
                return True
        return False
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _quiesce_producer(
    process: ProducerProcess,
    launcher_birth_token: int,
    launcher_started_at: str,
    launcher_process_group_id: int,
    target: ProducerIdentity | None,
    profile: ProfileStartAdmission,
    bridge_log: pathlib.Path,
    service_label: str,
    generation: int,
    bridge_pid: int,
    context: RuntimeContext,
    monotonic: Callable[[], float],
    group_id: Callable[[int], int],
    group_signaler: Callable[[int, int], None],
    group_live: Callable[[int], bool],
) -> ProducerIdentity | None:
    transition_timeout = float(
        profile.installed.loaded.data["launch"]["transitionTimeoutSeconds"]
    )
    quiesce_deadline = monotonic() + max(
        PRODUCER_QUIESCE_TIMEOUT_SECONDS,
        transition_timeout + PRODUCER_QUIESCE_MARGIN_SECONDS,
    )
    quiesce_runner = DeadlineRunner(context.runner, quiesce_deadline, monotonic)

    def stop_owned_process(identity: ProducerIdentity) -> None:
        if not group_live(identity.process_group_id):
            return
        try:
            current = _revalidate_producer_identity(
                identity,
                profile,
                quiesce_runner,
                context.birth_token_reader,
                context.pid_version_reader,
            )
        except ControlError:
            if not group_live(identity.process_group_id):
                return
            raise
        try:
            group_signaler(current.process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        target_deadline = min(
            monotonic() + PRODUCER_STOP_GRACE_SECONDS,
            quiesce_deadline,
        )
        while monotonic() < target_deadline and group_live(current.process_group_id):
            context.sleeper(MONITOR_INTERVAL_SECONDS)
        if group_live(current.process_group_id):
            try:
                current = _revalidate_producer_identity(
                    identity,
                    profile,
                    quiesce_runner,
                    context.birth_token_reader,
                    context.pid_version_reader,
                )
            except ControlError:
                if not group_live(identity.process_group_id):
                    return
                raise
            try:
                group_signaler(current.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            target_deadline = min(
                monotonic() + PRODUCER_KILL_WAIT_SECONDS,
                quiesce_deadline,
            )
            while monotonic() < target_deadline and group_live(current.process_group_id):
                context.sleeper(MONITOR_INTERVAL_SECONDS)
        if group_live(current.process_group_id):
            raise ControlError(
                "producer.quiesce_failed",
                "Owned detached process group remained live after bounded stop",
                processGroupId=current.process_group_id,
            )

    if target is not None and target.process_group_id != launcher_process_group_id:
        stop_owned_process(target)
    if process.poll() is None:
        current_birth_token, birth_error = context.birth_token_reader(process.pid)
        current_started_at, start_error = process_start_time(process.pid, quiesce_runner)
        try:
            current_group_id = group_id(process.pid)
        except OSError as error:
            raise ControlError(
                "producer.quiesce_failed",
                "Producer launcher process group could not be revalidated",
                detail=str(error),
            ) from error
        if (
            current_birth_token != launcher_birth_token
            or current_started_at != launcher_started_at
            or current_group_id != launcher_process_group_id
        ):
            raise ControlError(
                "producer.quiesce_failed",
                birth_error
                or start_error
                or "Producer launcher identity changed before stop",
            )
        try:
            group_signaler(launcher_process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = min(
            monotonic() + PRODUCER_STOP_GRACE_SECONDS,
            quiesce_deadline,
        )
        while monotonic() < deadline and (
            process.poll() is None or group_live(launcher_process_group_id)
        ):
            context.sleeper(MONITOR_INTERVAL_SECONDS)
        if process.poll() is None or group_live(launcher_process_group_id):
            if process.poll() is not None:
                if target is not None and target.process_group_id == launcher_process_group_id:
                    stop_owned_process(target)
                else:
                    raise ControlError(
                        "producer.quiesce_failed",
                        "Producer launcher exited while its process group remained live",
                        processGroupId=launcher_process_group_id,
                    )
                current_group_id = launcher_process_group_id
            else:
                current_birth_token, birth_error = context.birth_token_reader(process.pid)
                current_started_at, start_error = process_start_time(process.pid, quiesce_runner)
                try:
                    current_group_id = group_id(process.pid)
                except OSError as error:
                    raise ControlError(
                        "producer.quiesce_failed",
                        "Producer launcher process group could not be revalidated",
                        detail=str(error),
                    ) from error
                if (
                    current_birth_token != launcher_birth_token
                    or current_started_at != launcher_started_at
                    or current_group_id != launcher_process_group_id
                ):
                    raise ControlError(
                        "producer.quiesce_failed",
                        birth_error
                        or start_error
                        or "Producer launcher identity changed before forced stop",
                    )
                try:
                    group_signaler(launcher_process_group_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = min(
                    monotonic() + PRODUCER_KILL_WAIT_SECONDS,
                    quiesce_deadline,
                )
                while monotonic() < deadline and (
                    process.poll() is None or group_live(launcher_process_group_id)
                ):
                    context.sleeper(MONITOR_INTERVAL_SECONDS)
    if (
        process.poll() is not None
        and group_live(launcher_process_group_id)
        and target is not None
        and target.process_group_id == launcher_process_group_id
    ):
        stop_owned_process(target)
    if process.poll() is None or group_live(launcher_process_group_id):
        raise ControlError(
            "producer.quiesce_failed",
            "Owned launcher process group remained live after bounded stop",
            processGroupId=launcher_process_group_id,
        )

    if target is None and profile.installed.owned_process is not None:
        reconciliation_deadline = min(
            monotonic() + transition_timeout,
            quiesce_deadline,
        )
        while monotonic() < reconciliation_deadline:
            observed = _inspect_producer_identity(
                profile,
                launcher_started_at,
                launcher_process_group_id,
                quiesce_runner,
                context.birth_token_reader,
                context.pid_version_reader,
            )
            if observed is None:
                context.sleeper(MONITOR_INTERVAL_SECONDS)
                continue
            target = observed
            target = _confirm_producer_identity(
                target,
                profile,
                launcher_started_at,
                launcher_process_group_id,
                quiesce_runner,
                context.birth_token_reader,
                context.pid_version_reader,
            )
            if not _producer_markers_ready(
                bridge_log,
                service_label,
                generation,
                bridge_pid,
                target.pid,
                target.pid_version,
                target.birth_token,
            ):
                context.sleeper(MONITOR_INTERVAL_SECONDS)
                continue
            if target.process_group_id != launcher_process_group_id:
                stop_owned_process(target)
            break

    launcher_started = _process_start_datetime(launcher_started_at)
    if launcher_started is None:
        raise ControlError(
            "producer.quiesce_failed",
            "Producer launcher start time could not be parsed after stop",
        )
    remaining: list[ProducerIdentity] = []
    for candidate in _owned_process_candidates(
        profile,
        quiesce_runner,
        context.birth_token_reader,
        context.pid_version_reader,
    ):
        candidate_started = _process_start_datetime(candidate.started_at)
        if candidate_started is None:
            raise ControlError(
                "producer.quiesce_failed",
                "Owned process start time could not be parsed after stop",
                pid=candidate.pid,
            )
        if candidate_started >= launcher_started:
            remaining.append(candidate)
    if remaining:
        raise ControlError(
            "producer.quiesce_failed",
            "Exact owned process remained after bounded stop",
            pids=sorted(candidate.pid for candidate in remaining),
        )
    return target


def _quiesce_owned_producers(
    process: ProducerProcess,
    launcher_birth_token: int,
    launcher_started_at: str,
    launcher_process_group_id: int,
    identities: Sequence[OwnedProducerIdentity],
    profile: ProfileStartAdmission,
    bridge_log: pathlib.Path,
    service_label: str,
    generation: int,
    bridge_pid: int,
    context: RuntimeContext,
    monotonic: Callable[[], float],
    group_id: Callable[[int], int],
    group_signaler: Callable[[int, int], None],
    group_live: Callable[[int], bool],
    *,
    transition_deadline: float | None = None,
) -> tuple[OwnedProducerIdentity, ...]:
    transition_timeout = float(
        profile.installed.loaded.data["launch"]["transitionTimeoutSeconds"]
    )
    quiesce_deadline = monotonic() + max(
        PRODUCER_QUIESCE_TIMEOUT_SECONDS,
        transition_timeout + PRODUCER_QUIESCE_MARGIN_SECONDS,
    )
    quiesce_runner = DeadlineRunner(context.runner, quiesce_deadline, monotonic)
    retained = _ordered_owned_producer_identities(profile, identities)
    term_signaled: set[int] = set()
    kill_signaled: set[int] = set()
    absence_deadline = transition_deadline
    absence_observed = False

    def observe() -> _OwnedProducerObservation:
        observation = _inspect_owned_producer_observation(
            profile,
            launcher_started_at,
            launcher_process_group_id,
            quiesce_runner,
            context.birth_token_reader,
            context.pid_version_reader,
        )
        return _confirm_owned_producer_observation(
            observation,
            profile,
            quiesce_runner,
            context.birth_token_reader,
            context.pid_version_reader,
            error_code="producer.quiesce_failed",
        )

    def launcher_is_live() -> bool:
        if process.poll() is not None:
            return False
        current_birth_token, birth_error = context.birth_token_reader(process.pid)
        current_started_at, start_error = process_start_time(process.pid, quiesce_runner)
        try:
            current_group_id = group_id(process.pid)
        except OSError as error:
            if process.poll() is not None:
                return False
            raise ControlError(
                "producer.quiesce_failed",
                "Producer launcher process group could not be revalidated",
                detail=str(error),
            ) from error
        if (
            current_birth_token != launcher_birth_token
            or current_started_at != launcher_started_at
            or current_group_id != launcher_process_group_id
        ):
            raise ControlError(
                "producer.quiesce_failed",
                birth_error
                or start_error
                or "Producer launcher identity changed before stop",
            )
        return process.poll() is None

    def signal_group(process_group: int, signal_number: int) -> None:
        try:
            group_signaler(process_group, signal_number)
        except ProcessLookupError:
            return
        except OSError as error:
            raise ControlError(
                "producer.quiesce_failed",
                "Owned producer process group could not be signaled",
                processGroupId=process_group,
                signal=signal_number,
                detail=str(error),
            ) from error

    def wait_for_groups(process_groups: set[int], duration: float) -> None:
        deadline = min(monotonic() + duration, quiesce_deadline)
        while monotonic() < deadline and any(
            group_live(process_group) for process_group in process_groups
        ):
            context.sleeper(MONITOR_INTERVAL_SECONDS)

    while monotonic() < quiesce_deadline:
        observation = observe()
        for identity in retained:
            if identity not in observation.identities:
                _require_owned_producer_absence(
                    identity,
                    observation,
                    launcher_process_group_id,
                    group_live,
                    error_code="producer.quiesce_failed",
                )
        authenticated = _owned_producer_handshake_intersection(
            bridge_log,
            service_label,
            generation,
            bridge_pid,
            observation.identities,
        )
        if len(authenticated) != len(observation.identities):
            authenticated_set = set(authenticated)
            unauthenticated = [
                identity
                for identity in observation.identities
                if identity not in authenticated_set
            ]
            raise ControlError(
                "producer.quiesce_failed",
                "Exact producer targets without a current authenticated handshake cannot be signaled",
                targetIds=[identity.target_id for identity in unauthenticated],
                pids=[identity.identity.pid for identity in unauthenticated],
            )
        retained = _merge_owned_producer_evidence(
            profile,
            retained,
            authenticated,
            observation,
            launcher_process_group_id,
            group_live,
            error_code="producer.quiesce_failed",
        )
        launcher_live = launcher_is_live()
        current_groups = {
            identity.identity.process_group_id
            for identity in authenticated
            if group_live(identity.identity.process_group_id)
        }
        if launcher_live:
            if not group_live(launcher_process_group_id):
                raise ControlError(
                    "producer.quiesce_failed",
                    "Live producer launcher is missing from its exact process group",
                    processGroupId=launcher_process_group_id,
                )
            current_groups.add(launcher_process_group_id)
        elif (
            group_live(launcher_process_group_id)
            and launcher_process_group_id not in current_groups
        ):
            raise ControlError(
                "producer.quiesce_failed",
                "Producer launcher group remains live without an exact authenticated anchor",
                processGroupId=launcher_process_group_id,
            )
        if not current_groups:
            final_observation = observe()
            final_authenticated = _owned_producer_handshake_intersection(
                bridge_log,
                service_label,
                generation,
                bridge_pid,
                final_observation.identities,
            )
            if len(final_authenticated) != len(final_observation.identities):
                raise ControlError(
                    "producer.quiesce_failed",
                    "A late exact producer target lacks an authenticated handshake",
                    targetIds=[
                        identity.target_id
                        for identity in final_observation.identities
                        if identity not in set(final_authenticated)
                    ],
                )
            if final_observation.identities or launcher_is_live():
                retained = _merge_owned_producer_evidence(
                    profile,
                    retained,
                    final_authenticated,
                    final_observation,
                    launcher_process_group_id,
                    group_live,
                    error_code="producer.quiesce_failed",
                )
                continue
            if group_live(launcher_process_group_id):
                raise ControlError(
                    "producer.quiesce_failed",
                    "Owned launcher process group remained live after bounded stop",
                    processGroupId=launcher_process_group_id,
                )
            now = monotonic()
            if not absence_observed:
                if absence_deadline is None:
                    absence_deadline = now + transition_timeout
                absence_deadline = min(
                    max(absence_deadline, now + MONITOR_INTERVAL_SECONDS),
                    quiesce_deadline,
                )
                absence_observed = True
            assert absence_deadline is not None
            if now < absence_deadline:
                context.sleeper(
                    min(MONITOR_INTERVAL_SECONDS, absence_deadline - now)
                )
                continue
            return retained

        new_term_groups = current_groups - term_signaled
        if new_term_groups:
            for process_group in sorted(new_term_groups):
                signal_group(process_group, signal.SIGTERM)
                term_signaled.add(process_group)
            wait_for_groups(new_term_groups, PRODUCER_STOP_GRACE_SECONDS)
            continue

        new_kill_groups = {
            process_group
            for process_group in current_groups - kill_signaled
            if group_live(process_group)
        }
        if new_kill_groups:
            anchored_groups = {
                identity.identity.process_group_id for identity in authenticated
            }
            if launcher_live:
                anchored_groups.add(launcher_process_group_id)
            unanchored = new_kill_groups - anchored_groups
            if unanchored:
                raise ControlError(
                    "producer.quiesce_failed",
                    "Owned producer group lost every exact anchor before escalation",
                    processGroupIds=sorted(unanchored),
                )
            for process_group in sorted(new_kill_groups):
                signal_group(process_group, signal.SIGKILL)
                kill_signaled.add(process_group)
            wait_for_groups(new_kill_groups, PRODUCER_KILL_WAIT_SECONDS)
            continue

        raise ControlError(
            "producer.quiesce_failed",
            "One or more exact owned producer groups remained live after bounded termination",
            processGroupIds=sorted(current_groups),
        )
    raise ControlError(
        "producer.quiesce_failed",
        "Multi-target producer quiescence exceeded its bounded deadline",
    )


def _service_ready(
    admission: StartAdmission,
    runner: CommandRunner,
    bridge_log: pathlib.Path,
    deadline: float,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> Any:
    marker = f"native_source launchd service checked in name={admission.paths.service_label}"
    first_pid: int | None = None
    while monotonic() < deadline:
        service = inspect_service(admission.paths, runner)
        if (
            service.error_code is None
            and service.snapshot.present
            and service.snapshot.state == "running"
            and service.snapshot.pid is not None
            and service.snapshot.runs == 1
            and service.snapshot.path is not None
            and pathlib.Path(service.snapshot.path) == admission.paths.launch_agent_plist
            and service.snapshot.program is not None
            and pathlib.Path(service.snapshot.program) == admission.paths.bridge_program
            and service.owned
            and service.identity_valid
        ):
            try:
                checked_in = marker in bridge_log.read_text(errors="replace")
            except OSError:
                checked_in = False
            if checked_in:
                if first_pid == service.snapshot.pid:
                    return service
                first_pid = service.snapshot.pid
                sleeper(READINESS_SAMPLE_INTERVAL_SECONDS)
                continue
        first_pid = None
        sleeper(MONITOR_INTERVAL_SECONDS)
    raise ControlError(
        "runtime.start_timeout",
        "Exact launchd service and bridge check-in were not ready before the deadline",
        target=admission.paths.service_target,
    )


def _revalidate_waiting_service(
    admission: StartAdmission,
    expected_pid: int,
    expected_runs: int,
    plist_sha256: str,
    bridge_sha256: str,
    runner: CommandRunner,
) -> Any:
    current = inspect_service(admission.paths, runner)
    try:
        current_plist_sha256 = artifact_contract.sha256_file(
            admission.paths.launch_agent_plist
        )
        current_bridge_sha256 = artifact_contract.sha256_file(admission.paths.bridge_program)
    except OSError as error:
        raise ControlError(
            "service.identity_changed",
            f"Ready service content could not be revalidated: {error}",
        ) from error
    if (
        current.error_code is not None
        or not current.snapshot.present
        or current.snapshot.pid != expected_pid
        or current.snapshot.runs != expected_runs
        or current.snapshot.state != "running"
        or not current.owned
        or not current.identity_valid
        or current_plist_sha256 != plist_sha256
        or current_bridge_sha256 != bridge_sha256
    ):
        raise ControlError(
            "service.identity_changed",
            current.message or "Launchd service identity changed before waiting publication",
        )
    return current


def _write_startup_result(run_dir: pathlib.Path, report: StartReport) -> None:
    _write_json_atomic(run_dir, run_dir / STARTUP_RESULT_NAME, report.to_dict())


def _write_supervisor_exit(run_dir: pathlib.Path, report: StartReport) -> None:
    _write_json_atomic(run_dir, run_dir / SUPERVISOR_EXIT_NAME, report.to_dict())


def _parse_start_report(value: dict[str, Any]) -> StartReport | None:
    required = {
        "schemaVersion",
        "command",
        "ok",
        "state",
        "reasonCode",
        "message",
        "artifact",
        "generation",
        "ownerPid",
        "runDir",
        "supervisorLog",
        "actions",
        "profile",
        "producer",
    }
    if set(value) != required or value.get("schemaVersion") != 1 or value.get("command") != "start":
        return None
    if (
        not isinstance(value.get("ok"), bool)
        or value.get("state") not in {*LEGACY_LIVE_STATES, "failed"}
        or not isinstance(value.get("reasonCode"), str)
        or not isinstance(value.get("message"), str)
        or (value.get("artifact") is not None and not isinstance(value.get("artifact"), dict))
        or not isinstance(value.get("generation"), int)
        or isinstance(value.get("generation"), bool)
        or not 0 < value["generation"] <= MAX_RUNTIME_GENERATION
        or not isinstance(value.get("ownerPid"), int)
        or isinstance(value.get("ownerPid"), bool)
        or value["ownerPid"] <= 0
        or not isinstance(value.get("runDir"), str)
        or not pathlib.Path(value["runDir"]).is_absolute()
        or not isinstance(value.get("supervisorLog"), str)
        or not pathlib.Path(value["supervisorLog"]).is_absolute()
        or (value.get("profile") is not None and not isinstance(value.get("profile"), dict))
        or (value.get("producer") is not None and not isinstance(value.get("producer"), dict))
        or (value["ok"] and value["state"] not in LEGACY_LIVE_STATES)
        or (value["ok"] and not isinstance(value.get("profile"), dict))
        or (value["ok"] and not isinstance(value.get("producer"), dict))
        or (not value["ok"] and value["state"] != "failed")
    ):
        return None
    actions = value.get("actions")
    if not isinstance(actions, list) or not all(isinstance(item, str) for item in actions):
        return None
    return StartReport(
        value["ok"],
        value["state"],
        value["reasonCode"],
        value["message"],
        value["artifact"],
        value["generation"],
        value["ownerPid"],
        pathlib.Path(value["runDir"]),
        pathlib.Path(value["supervisorLog"]),
        tuple(actions),
        value["profile"],
        value["producer"],
    )


def _idempotent_live_start(
    context: RuntimeContext,
    artifact: pathlib.Path,
    profile_id: str,
) -> StartReport | None:
    status = status_runtime(context, artifact)
    if not status.ok or status.state not in LIVE_STATES:
        return None
    record = status.control_state.get("record")
    if not isinstance(record, dict):
        return None
    profile_record = record.get("profile")
    if not isinstance(profile_record, dict) or profile_record.get("id") != profile_id:
        return start_failure(
            "profile.conflict",
            "Another curated profile already owns the live runtime",
            artifact=status.artifact,
            generation=record.get("generation"),
            owner_pid=record.get("ownerPid"),
            run_dir=(
                pathlib.Path(record["runDir"])
                if isinstance(record.get("runDir"), str)
                else None
            ),
            profile=profile_record if isinstance(profile_record, dict) else None,
            producer=record.get("producer") if isinstance(record.get("producer"), dict) else None,
        )
    try:
        manifest, _, _, _ = load_runtime_contract(context)
        current_profile = runtime_profile.load_curated_profile(
            profile_id,
            manifest,
            pathlib.Path(record["artifactPath"]),
        )
    except (ControlError, runtime_profile.ProfileError) as error:
        code = error.code
        message = error.message
        return start_failure(
            code,
            message,
            artifact=status.artifact,
            generation=record.get("generation"),
            owner_pid=record.get("ownerPid"),
            run_dir=(
                pathlib.Path(record["runDir"])
                if isinstance(record.get("runDir"), str)
                else None
            ),
            profile=profile_record,
            producer=record.get("producer") if isinstance(record.get("producer"), dict) else None,
        )
    if current_profile.sha256 != profile_record.get("sha256"):
        return start_failure(
            "profile.conflict",
            "Live runtime profile digest differs from the requested curated profile",
            artifact=status.artifact,
            generation=record.get("generation"),
            owner_pid=record.get("ownerPid"),
            run_dir=(
                pathlib.Path(record["runDir"])
                if isinstance(record.get("runDir"), str)
                else None
            ),
            profile=profile_record,
            producer=record.get("producer") if isinstance(record.get("producer"), dict) else None,
        )
    return StartReport(
        True,
        status.state,
        f"runtime.{status.state}",
        "Runtime supervisor is already live for the requested artifact",
        status.artifact,
        record.get("generation"),
        record.get("ownerPid"),
        pathlib.Path(record["runDir"]) if isinstance(record.get("runDir"), str) else None,
        (
            pathlib.Path(record["diagnostic"]["supervisorLog"])
            if isinstance(record.get("diagnostic"), dict)
            and isinstance(record["diagnostic"].get("supervisorLog"), str)
            else None
        ),
        (),
        profile_record,
        record.get("producer") if isinstance(record.get("producer"), dict) else None,
    )


def start_runtime(
    context: RuntimeContext,
    artifact: pathlib.Path,
    profile_id: str,
    *,
    launcher: ProcessLauncher | None = None,
    generation_factory: Callable[[], int] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> StartReport:
    live = _idempotent_live_start(context, artifact, profile_id)
    if live is not None:
        return live
    doctor = doctor_runtime(context, artifact)
    if not doctor.ok:
        return start_failure(
            "runtime.doctor_failed",
            "Runtime prerequisites are not ready for start",
            artifact=doctor.artifact,
        )
    try:
        admission = inspect_start_admission(context, artifact, profile_id)
    except ControlError as error:
        return start_failure(error.code, error.message, artifact=doctor.artifact)
    paths = admission.paths
    existing_service = inspect_service(paths, context.runner)
    existing_lock = inspect_lock(paths.lock_path, context.pid_alive)
    existing_state = load_control_state(paths.state_path)
    if (
        existing_service.error_code is not None
        or existing_service.snapshot.present
        or existing_lock.exists
        or existing_state.exists
    ):
        status = status_runtime(context, artifact)
        return start_failure(
            status.reason_code,
            status.message,
            artifact=status.artifact,
        )
    factory = generation_factory or (lambda: secrets.randbits(63) or 1)
    generation = factory()
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation <= 0
        or generation > MAX_RUNTIME_GENERATION
    ):
        return start_failure(
            "runtime.start_failed",
            "Start generation must be a positive signed 63-bit integer",
        )
    run_dir = paths.state_root / f"r-{generation:016x}"
    supervisor_log = run_dir / SUPERVISOR_LOG_NAME
    try:
        _make_private_directory(paths.state_root, run_dir)
    except ControlError as error:
        return start_failure(error.code, error.message, generation=generation, run_dir=run_dir)
    effective_launcher = launcher or SubprocessLauncher()
    if admission.profile is None:
        return start_failure("profile.artifact_mismatch", "Start admission has no curated profile")
    deadline = monotonic() + STARTUP_TIMEOUT_SECONDS + float(
        admission.profile.installed.loaded.data["launch"]["startupTimeoutSeconds"]
    )
    command = [
        sys.executable,
        str(RUNTIME_START),
        "--artifact",
        str(_absolute(artifact)),
        "--profile",
        admission.profile.installed.loaded.data["id"],
        "--profile-sha256",
        admission.profile.installed.loaded.sha256,
        "--bindings",
        str(_absolute(context.bindings_path)),
        "--generation",
        str(generation),
        "--run-dir",
        str(run_dir),
        "--deadline",
        f"{deadline:.6f}",
    ]
    try:
        child = effective_launcher.launch(command, supervisor_log)
    except OSError as error:
        return start_failure(
            "runtime.start_failed",
            f"Runtime supervisor could not be launched: {error}",
            generation=generation,
            run_dir=run_dir,
            supervisor_log=supervisor_log,
        )
    result_path = run_dir / STARTUP_RESULT_NAME
    while monotonic() < deadline:
        if result_path.exists():
            payload = _read_json_file(result_path)
            if payload is None:
                return start_failure(
                    "runtime.start_failed",
                    "Runtime supervisor returned an unreadable startup result",
                    generation=generation,
                    owner_pid=child.pid,
                    run_dir=run_dir,
                    supervisor_log=supervisor_log,
                )
            report = _parse_start_report(payload)
            if report is not None:
                identity_mismatch = (
                    report.generation != generation
                    or report.owner_pid != child.pid
                    or report.run_dir != run_dir
                    or report.supervisor_log != supervisor_log
                )
                if report.ok:
                    identity_mismatch = identity_mismatch or (
                        report.profile != _profile_record(admission.profile)
                        or not isinstance(report.producer, dict)
                        or report.producer.get("status") != "ready"
                    )
                elif report.profile is not None:
                    identity_mismatch = identity_mismatch or (
                        report.profile != _profile_record(admission.profile)
                    )
                if identity_mismatch:
                    return start_failure(
                        "runtime.start_failed",
                        "Runtime supervisor returned startup state for another generation",
                        generation=generation,
                        owner_pid=child.pid,
                        run_dir=run_dir,
                        supervisor_log=supervisor_log,
                    )
                return report
            return start_failure(
                "runtime.start_failed",
                "Runtime supervisor returned an invalid startup result",
                generation=generation,
                owner_pid=child.pid,
                run_dir=run_dir,
                supervisor_log=supervisor_log,
            )
        if child.poll() is not None:
            return start_failure(
                "runtime.start_failed",
                "Runtime supervisor exited before publishing startup state",
                generation=generation,
                owner_pid=child.pid,
                run_dir=run_dir,
                supervisor_log=supervisor_log,
            )
        context.sleeper(MONITOR_INTERVAL_SECONDS)
    return start_failure(
        "runtime.start_timeout",
        "Runtime supervisor did not publish startup state before the deadline",
        generation=generation,
        owner_pid=child.pid,
        run_dir=run_dir,
        supervisor_log=supervisor_log,
    )


def resolve_context_paths_for_start(
    context: RuntimeContext,
) -> tuple[dict[str, Any], dict[str, str], RuntimePaths]:
    manifest, _, _, _ = load_runtime_contract(context)
    try:
        bindings = artifact_contract.resolve_bindings(manifest, context.bindings_path, "plan")
        paths = resolve_runtime_paths(manifest, bindings)
    except artifact_contract.ArtifactError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    return manifest, bindings, paths


def _bootstrap_service(
    admission: StartAdmission,
    runner: CommandRunner,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[str, ...]:
    service = inspect_service(admission.paths, runner)
    if service.error_code is not None:
        raise ControlError(service.error_code, service.message or "Launchd service is invalid")
    if service.snapshot.present:
        raise ControlError(
            "service.foreign",
            "Launchd label is already occupied before supervisor bootstrap",
            target=admission.paths.service_target,
        )
    actions: list[str] = []
    bootstrap = runner.run(
        [
            "/bin/launchctl",
            "bootstrap",
            admission.paths.service_domain,
            str(admission.paths.launch_agent_plist),
        ],
        timeout=_remaining_timeout(deadline, 10.0, clock),
    )
    if bootstrap.error is not None or bootstrap.returncode != 0:
        raise ControlError(
            f"launchd.{bootstrap.error or 'bootstrap_failed'}",
            "Runtime launch agent could not be bootstrapped",
            stderr=bootstrap.stderr,
        )
    actions.append(f"bootstrap {admission.paths.service_target}")
    kickstart = runner.run(
        ["/bin/launchctl", "kickstart", "-k", admission.paths.service_target],
        timeout=_remaining_timeout(deadline, 10.0, clock),
    )
    if kickstart.error is not None or kickstart.returncode != 0:
        raise ControlError(
            f"launchd.{kickstart.error or 'kickstart_failed'}",
            "Runtime launch agent could not be kickstarted",
            stderr=kickstart.stderr,
        )
    actions.append(f"kickstart {admission.paths.service_target}")
    return tuple(actions)


def _bootout_created_service(
    admission: StartAdmission,
    runner: CommandRunner,
    plist_sha256: str | None,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    snapshot = read_launchd_snapshot(admission.paths, runner)
    if snapshot.error_code is not None:
        raise ControlError(
            snapshot.error_code,
            snapshot.message or "Launchd service could not be inspected during cleanup",
        )
    if not snapshot.present:
        return
    if (
        plist_sha256 is None
        or snapshot.path is None
        or not paths_match(snapshot.path, admission.paths.launch_agent_plist)
        or not paths_match(snapshot.program, admission.paths.bridge_program)
    ):
        raise ControlError(
            "service.foreign",
            "Refusing to clean a launchd service not created by this generation",
        )
    try:
        current_plist_sha256 = artifact_contract.sha256_file(admission.paths.launch_agent_plist)
    except OSError as error:
        raise ControlError(
            "service.plist_foreign",
            "Created launch agent plist could not be hashed during cleanup",
            detail=str(error),
        ) from error
    if current_plist_sha256 != plist_sha256:
        raise ControlError(
            "service.plist_foreign",
            "Created launch agent plist changed before cleanup",
        )
    result = runner.run(
        [
            "/bin/launchctl",
            "bootout",
            admission.paths.service_domain,
            snapshot.path,
        ],
        timeout=10.0,
    )
    after = read_launchd_snapshot(admission.paths, runner)
    for _ in range(50):
        if after.error_code is not None or not after.present:
            break
        sleeper(0.1)
        after = read_launchd_snapshot(admission.paths, runner)
    if after.error_code is not None or after.present:
        raise ControlError(
            after.error_code or f"launchd.{result.error or 'bootout_failed'}",
            after.message or "Created launchd service remained after cleanup bootout",
        )


def _cleanup_started_state(
    admission: StartAdmission,
    run_dir: pathlib.Path,
    generation: int,
    plist_sha256: str | None,
    control_socket: pathlib.Path,
    *,
    remove_run_dir: bool,
) -> None:
    state = load_control_state(admission.paths.state_path)
    if state.exists:
        if not state.valid or state.record is None or state.record.get("generation") != generation:
            raise ControlError(
                "state.foreign",
                "Supervisor control state changed before cleanup",
                path=str(admission.paths.state_path),
            )
    with contextlib.suppress(FileNotFoundError):
        metadata = control_socket.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ControlError(
                "owner.control_foreign",
                "Supervisor control socket changed before cleanup",
                path=str(control_socket),
            )
        control_socket.unlink()
    if plist_sha256 is not None:
        _remove_file_if_exact(admission.paths.launch_agent_plist, plist_sha256)
    _remove_owner_lock(admission.paths, run_dir)
    if remove_run_dir:
        remove_owned_run_directory(admission.paths.state_root, run_dir)
    if state.exists:
        admission.paths.state_path.unlink()


def _receive_control_request(
    listener: socket.socket,
    generation: int,
    stop_handler: Callable[[], None] | None = None,
) -> str | None:
    try:
        connection, _ = listener.accept()
    except TimeoutError:
        return None
    with connection:
        connection.settimeout(1.0)
        try:
            payload = receive_json_frame(connection)
            request = json.loads(
                payload,
                object_pairs_hook=artifact_contract.reject_duplicate_keys,
                parse_constant=artifact_contract.reject_json_constant,
            )
        except (
            OSError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            artifact_contract.ArtifactError,
        ):
            request = None
        command = request.get("command") if isinstance(request, dict) else None
        accepted = (
            command in {"ping", "stop"}
            and request
            == {
                "schemaVersion": 1,
                "command": command,
                "generation": generation,
                "ownerPid": os.getpid(),
            }
        )
        response_value: dict[str, Any] = {
            "schemaVersion": 1,
            "ok": accepted,
            "generation": generation,
        }
        if accepted and command == "stop" and stop_handler is not None:
            try:
                stop_handler()
            except ControlError as error:
                accepted = False
                response_value = {
                    "schemaVersion": 1,
                    "ok": False,
                    "generation": generation,
                    "error": {"code": error.code, "message": error.message},
                }
        response = artifact_contract.canonical_json_bytes(response_value)
        with contextlib.suppress(OSError):
            connection.sendall(response)
        return command if accepted else None


def supervise_runtime(
    context: RuntimeContext,
    artifact: pathlib.Path,
    profile_id: str,
    profile_sha256: str,
    generation: int,
    run_dir: pathlib.Path,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    startup_deadline: float | None = None,
    producer_launcher: ProducerLauncher | None = None,
    telemetry_reader: Callable[[], ClientTelemetry] = read_client_telemetry,
    group_id: Callable[[int], int] = os.getpgid,
    group_signaler: Callable[[int, int], None] = os.killpg,
    group_live: Callable[[int], bool] = _group_is_live,
) -> StartReport:
    actions: list[str] = []
    supervisor_log = run_dir / SUPERVISOR_LOG_NAME
    admission: StartAdmission | None = None
    plist_sha256: str | None = None
    control_socket = run_dir / CONTROL_SOCKET_NAME
    deadline = startup_deadline or (monotonic() + STARTUP_TIMEOUT_SECONDS)
    producer_process: ProducerProcess | None = None
    producer_log: pathlib.Path | None = None
    bridge_pid: int | None = None
    launcher_birth_token: int | None = None
    launcher_started_at: str | None = None
    process_group_id: int | None = None
    producer_identity: ProducerIdentity | None = None
    owned_producer_transition = _OwnedProducerTransition((), (), None)
    multi_target_producer = False
    producer_state: dict[str, Any] | None = None
    client_monitor: ClientTelemetryMonitor | None = None
    published_client_state: str | None = None
    published_client_record: dict[str, Any] | None = None
    next_client_snapshot = 0.0
    try:
        _remaining_timeout(deadline, STARTUP_TIMEOUT_SECONDS, monotonic)
        manifest, bindings, initial_paths = resolve_context_paths_for_start(context)
        try:
            initial_allowed_roots = tuple(
                artifact_contract.resolve_path(
                    template,
                    bindings,
                    f"allowedTargetRoots[{index}]",
                )
                for index, template in enumerate(manifest["allowedTargetRoots"])
            )
        except artifact_contract.ArtifactError as error:
            raise ControlError(error.code, error.message, **error.context) from error
        expected_run_dir = initial_paths.state_root / f"r-{generation:016x}"
        if (
            generation <= 0
            or generation > MAX_RUNTIME_GENERATION
            or run_dir != expected_run_dir
        ):
            raise ControlError(
                "runtime.start_foreign",
                "Supervisor run directory does not match its exact generation",
                path=str(run_dir),
                expectedPath=str(expected_run_dir),
            )
        _private_directory(run_dir)
        with global_lifecycle_lock(context.lifecycle_lock_path, initial_allowed_roots):
            _remaining_timeout(deadline, STARTUP_TIMEOUT_SECONDS, monotonic)
            active_admission = inspect_start_admission(context, artifact, profile_id)
            admission = active_admission
            profile_admission = active_admission.profile
            if profile_admission is None or profile_admission.installed.loaded.sha256 != profile_sha256:
                raise ControlError(
                    "profile.artifact_mismatch",
                    "Parent and supervisor profile identities do not match",
                    profile=profile_id,
                    expected=profile_sha256,
                    actual=(
                        profile_admission.installed.loaded.sha256
                        if profile_admission is not None
                        else None
                    ),
                )
            multi_target_producer = len(
                profile_admission.installed.owned_processes
            ) > 1
            _remaining_timeout(deadline, STARTUP_TIMEOUT_SECONDS, monotonic)
            host_deadline = min(deadline, monotonic() + STARTUP_TIMEOUT_SECONDS)
            startup_runner = DeadlineRunner(context.runner, host_deadline, monotonic)
            existing_service = inspect_service(active_admission.paths, startup_runner)
            if existing_service.error_code is not None or existing_service.snapshot.present:
                raise ControlError(
                    existing_service.error_code or "service.foreign",
                    existing_service.message or "Launchd label is already occupied",
                )
            if inspect_lock(active_admission.paths.lock_path, context.pid_alive).exists:
                raise ControlError(
                    "runtime.stale_state",
                    "Runtime owner lock already exists; run stop before starting",
                    path=str(active_admission.paths.lock_path),
                )
            if load_control_state(active_admission.paths.state_path).exists:
                raise ControlError(
                    "runtime.stale_state",
                    "Runtime control state already exists; run stop before starting",
                    path=str(active_admission.paths.state_path),
                )
            if multi_target_producer:
                existing_owned_process_pids = [
                    candidate.identity.pid
                    for candidate in _owned_producer_candidates(
                        profile_admission,
                        startup_runner,
                        context.birth_token_reader,
                        context.pid_version_reader,
                    )
                ]
            else:
                existing_owned_process_pids = [
                    candidate.pid
                    for candidate in _owned_process_candidates(
                        profile_admission,
                        startup_runner,
                        context.birth_token_reader,
                        context.pid_version_reader,
                    )
                ]
            if existing_owned_process_pids:
                raise ControlError(
                    "producer.already_running",
                    "An exact owned-process candidate was already live before launch",
                    pids=sorted(existing_owned_process_pids),
                )
            _remaining_timeout(host_deadline, STARTUP_TIMEOUT_SECONDS, monotonic)
            _create_owner_lock(active_admission.paths, run_dir)
            owner_started_at, owner_error = process_start_time(os.getpid(), startup_runner)
            if owner_started_at is None:
                raise ControlError(
                    "owner.identity_unavailable",
                    owner_error or "Supervisor process start time is unavailable",
                )
            _remaining_timeout(host_deadline, STARTUP_TIMEOUT_SECONDS, monotonic)
            bridge_log = run_dir / BRIDGE_LOG_NAME
            _write_new_file(run_dir, bridge_log, b"")
            _make_private_directory(run_dir, run_dir / ALVR_ROOT_NAME)
            trusted_client_count = _seed_alvr_session(active_admission, run_dir)
            actions.append(f"seed trusted ALVR clients count={trusted_client_count}")
            launch_plist = render_launch_agent(active_admission, run_dir, generation)
            plist_sha256 = artifact_contract.sha256_bytes(launch_plist)
            _write_file_atomic(
                active_admission.paths.launch_agent_plist.parent,
                active_admission.paths.launch_agent_plist,
                launch_plist,
            )
            if (
                artifact_contract.sha256_file(active_admission.paths.launch_agent_plist)
                != plist_sha256
            ):
                raise ControlError(
                    "runtime.start_failed",
                    "Rendered launch agent plist changed during publication",
                )
            plist_owned, plist_error = validate_plist_ownership(active_admission.paths)
            if not plist_owned:
                raise ControlError(
                    "service.plist_foreign",
                    plist_error or "Rendered launch agent plist is not exact",
                )
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(control_socket))
                os.chmod(control_socket, 0o600)
                listener.listen(1)
                listener.settimeout(MONITOR_INTERVAL_SECONDS)
                actions.extend(
                    _bootstrap_service(
                        active_admission,
                        startup_runner,
                        host_deadline,
                        monotonic,
                    )
                )
                ready_window = _remaining_timeout(
                    host_deadline,
                    SERVICE_READY_TIMEOUT_SECONDS,
                    monotonic,
                )
                service = _service_ready(
                    active_admission,
                    startup_runner,
                    bridge_log,
                    monotonic() + ready_window,
                    monotonic,
                    context.sleeper,
                )
                bridge_pid = service.snapshot.pid
                if bridge_pid is None:
                    raise ControlError(
                        "service.identity_unknown",
                        "Ready launchd service has no live PID",
                    )
                producer_log = run_dir / PRODUCER_LOG_NAME
                if multi_target_producer:
                    producer_state = _launching_owned_producer_record(
                        profile_admission,
                        producer_log,
                    )
                else:
                    producer_state = _launching_producer_record(
                        profile_admission,
                        producer_log,
                    )
                launching_payload = _state_payload(
                    active_admission,
                    run_dir,
                    generation,
                    service,
                    owner_started_at,
                    control_socket,
                    plist_sha256,
                    "idle",
                    "launching-producer",
                    producer_state,
                )
                _write_json_atomic(
                    active_admission.paths.state_root,
                    active_admission.paths.state_path,
                    launching_payload,
                )
                effective_producer_launcher = producer_launcher or SubprocessProducerLauncher()
                producer_process, producer_log = _producer_launch(
                    active_admission,
                    run_dir,
                    generation,
                    effective_producer_launcher,
                )
                launcher_started_at, launcher_error = process_start_time(
                    producer_process.pid,
                    context.runner,
                )
                if launcher_started_at is None:
                    raise ControlError(
                        "producer.identity_unavailable",
                        launcher_error or "Producer launcher process start time is unavailable",
                    )
                launcher_birth_token, launcher_birth_error = context.birth_token_reader(
                    producer_process.pid
                )
                if launcher_birth_token is None:
                    raise ControlError(
                        "producer.identity_unavailable",
                        launcher_birth_error
                        or "Producer launcher process birth token is unavailable",
                    )
                try:
                    process_group_id = group_id(producer_process.pid)
                except OSError as error:
                    raise ControlError(
                        "producer.identity_unavailable",
                        "Producer launcher process group is unavailable",
                        detail=str(error),
                    ) from error
                if process_group_id != producer_process.pid or producer_process.poll() is not None:
                    raise ControlError(
                        "producer.identity_unavailable",
                        "Producer launcher did not retain its exact new process group",
                        pid=producer_process.pid,
                        processGroupId=process_group_id,
                    )
                if multi_target_producer:
                    producer_state = _owned_producer_record(
                        profile_admission,
                        "starting",
                        producer_process.pid,
                        launcher_birth_token,
                        launcher_started_at,
                        process_group_id,
                        producer_log,
                        (),
                    )
                else:
                    producer_state = _producer_record(
                        profile_admission,
                        "starting",
                        producer_process.pid,
                        launcher_birth_token,
                        launcher_started_at,
                        process_group_id,
                        producer_log,
                        None,
                    )
                starting_payload = _state_payload(
                    active_admission,
                    run_dir,
                    generation,
                    service,
                    owner_started_at,
                    control_socket,
                    plist_sha256,
                    "idle",
                    "starting-producer",
                    producer_state,
                )
                _write_json_atomic(
                    active_admission.paths.state_root,
                    active_admission.paths.state_path,
                    starting_payload,
                )
                producer_quiesced = False

                def quiesce_and_publish() -> None:
                    nonlocal owned_producer_transition
                    nonlocal producer_identity, producer_quiesced, producer_state
                    if not producer_quiesced:
                        if multi_target_producer:
                            retained = _quiesce_owned_producers(
                                producer_process,
                                launcher_birth_token,
                                launcher_started_at,
                                process_group_id,
                                owned_producer_transition.retained,
                                profile_admission,
                                bridge_log,
                                active_admission.paths.service_label,
                                generation,
                                bridge_pid,
                                context,
                                monotonic,
                                group_id,
                                group_signaler,
                                group_live,
                                transition_deadline=owned_producer_transition.deadline,
                            )
                            owned_producer_transition = _OwnedProducerTransition(
                                (), retained, None
                            )
                        else:
                            producer_identity = _quiesce_producer(
                                producer_process,
                                launcher_birth_token,
                                launcher_started_at,
                                process_group_id,
                                producer_identity,
                                profile_admission,
                                bridge_log,
                                active_admission.paths.service_label,
                                generation,
                                bridge_pid,
                                context,
                                monotonic,
                                group_id,
                                group_signaler,
                                group_live,
                            )
                        producer_quiesced = True
                    if multi_target_producer:
                        producer_state = _owned_producer_record(
                            profile_admission,
                            "quiesced",
                            producer_process.pid,
                            launcher_birth_token,
                            launcher_started_at,
                            process_group_id,
                            producer_log,
                            owned_producer_transition.retained,
                        )
                    else:
                        producer_state = _producer_record(
                            profile_admission,
                            "quiesced",
                            producer_process.pid,
                            launcher_birth_token,
                            launcher_started_at,
                            process_group_id,
                            producer_log,
                            producer_identity,
                        )
                    quiesced_payload = _state_payload(
                        active_admission,
                        run_dir,
                        generation,
                        service,
                        owner_started_at,
                        control_socket,
                        plist_sha256,
                        "idle",
                        "producer-quiesced",
                        producer_state,
                    )
                    try:
                        _write_json_atomic(
                            active_admission.paths.state_root,
                            active_admission.paths.state_path,
                            quiesced_payload,
                        )
                    except ControlError as error:
                        actions.append(
                            "retain terminal producer quiescence after state "
                            f"publication failure code={error.code}"
                        )

                stop_requested = False
                provisional_identity: ProducerIdentity | None = None
                producer_transition_timeout = float(
                    profile_admission.installed.loaded.data["launch"][
                        "transitionTimeoutSeconds"
                    ]
                )
                producer_deadline = min(
                    deadline,
                    monotonic()
                    + float(
                        profile_admission.installed.loaded.data["launch"][
                            "startupTimeoutSeconds"
                        ]
                    ),
                )
                producer_runner = DeadlineRunner(
                    context.runner,
                    producer_deadline,
                    monotonic,
                )

                def await_multi_target_ready() -> None:
                    nonlocal owned_producer_transition
                    while monotonic() < producer_deadline:
                        if producer_process.poll() is not None:
                            raise ControlError(
                                "producer.exited",
                                "CrossOver producer exited before authenticated readiness",
                            )
                        observation = _inspect_owned_producer_observation(
                            profile_admission,
                            launcher_started_at,
                            process_group_id,
                            producer_runner,
                            context.birth_token_reader,
                            context.pid_version_reader,
                        )
                        observation = _confirm_owned_producer_observation(
                            observation,
                            profile_admission,
                            producer_runner,
                            context.birth_token_reader,
                            context.pid_version_reader,
                        )
                        owned_producer_transition = (
                            _reconcile_owned_producer_transition(
                                profile_admission,
                                owned_producer_transition,
                                observation,
                                bridge_log,
                                active_admission.paths.service_label,
                                generation,
                                bridge_pid,
                                process_group_id,
                                monotonic(),
                                producer_transition_timeout,
                                group_live,
                            )
                        )
                        if owned_producer_transition.live:
                            return
                        context.sleeper(MONITOR_INTERVAL_SECONDS)
                    raise ControlError(
                        "producer.start_timeout",
                        "No authenticated declared producer target stabilized before the startup deadline",
                    )

                while monotonic() < producer_deadline:
                    if (
                        _receive_control_request(
                            listener,
                            generation,
                            quiesce_and_publish,
                        )
                        == "stop"
                    ):
                        stop_requested = True
                        break
                    if producer_process.poll() is not None:
                        raise ControlError(
                            "producer.exited",
                            "CrossOver producer exited before authenticated readiness",
                        )
                    if multi_target_producer:
                        try:
                            observation = _inspect_owned_producer_observation(
                                profile_admission,
                                launcher_started_at,
                                process_group_id,
                                producer_runner,
                                context.birth_token_reader,
                                context.pid_version_reader,
                            )
                            observation = _confirm_owned_producer_observation(
                                observation,
                                profile_admission,
                                producer_runner,
                                context.birth_token_reader,
                                context.pid_version_reader,
                            )
                        except ControlError as error:
                            if (
                                error.code == "producer.identity_unavailable"
                                and monotonic() >= producer_deadline
                            ):
                                break
                            raise
                        authenticated = _owned_producer_handshake_intersection(
                            bridge_log,
                            active_admission.paths.service_label,
                            generation,
                            bridge_pid,
                            observation.identities,
                        )
                        if not authenticated:
                            continue
                        retained = _merge_owned_producer_evidence(
                            profile_admission,
                            (),
                            authenticated,
                            observation,
                            process_group_id,
                            group_live,
                        )
                        transition_pending = (
                            len(observation.identities) != 1
                            or len(authenticated) != len(observation.identities)
                        )
                        owned_producer_transition = _OwnedProducerTransition(
                            authenticated,
                            retained,
                            (
                                monotonic() + producer_transition_timeout
                                if transition_pending
                                else None
                            ),
                        )
                        break
                    try:
                        observed = _inspect_producer_identity(
                            profile_admission,
                            launcher_started_at,
                            process_group_id,
                            producer_runner,
                            context.birth_token_reader,
                            context.pid_version_reader,
                        )
                        if observed is None:
                            if provisional_identity is not None:
                                raise ControlError(
                                    "producer.identity_changed",
                                    "Provisional owned process disappeared before authenticated readiness",
                                    pid=provisional_identity.pid,
                                )
                            continue
                        observed = _confirm_producer_identity(
                            observed,
                            profile_admission,
                            launcher_started_at,
                            process_group_id,
                            producer_runner,
                            context.birth_token_reader,
                            context.pid_version_reader,
                        )
                    except ControlError as error:
                        if (
                            error.code == "producer.identity_unavailable"
                            and monotonic() >= producer_deadline
                        ):
                            break
                        raise
                    if provisional_identity is None:
                        provisional_identity = observed
                    elif observed != provisional_identity:
                        raise ControlError(
                            "producer.identity_changed",
                            "Owned process changed before authenticated readiness",
                            expectedPid=provisional_identity.pid,
                            observedPid=observed.pid,
                        )
                    if _producer_markers_ready(
                        bridge_log,
                        active_admission.paths.service_label,
                        generation,
                        bridge_pid,
                        provisional_identity.pid,
                        provisional_identity.pid_version,
                        provisional_identity.birth_token,
                    ):
                        producer_identity = _confirm_producer_identity(
                            provisional_identity,
                            profile_admission,
                            launcher_started_at,
                            process_group_id,
                            producer_runner,
                            context.birth_token_reader,
                            context.pid_version_reader,
                        )
                        break
                if not stop_requested:
                    if (
                        multi_target_producer
                        and not owned_producer_transition.live
                    ) or (
                        not multi_target_producer and producer_identity is None
                    ):
                        raise ControlError(
                            "producer.start_timeout",
                            "Exact producer and authenticated bridge readiness did not complete before the profile deadline",
                        )
                    service_runs = service.snapshot.runs
                    if service_runs is None:
                        raise ControlError(
                            "service.identity_unknown",
                            "Ready launchd service has no run count",
                        )
                    service = _revalidate_waiting_service(
                        active_admission,
                        bridge_pid,
                        service_runs,
                        plist_sha256,
                        starting_payload["bridgeExecutableSha256"],
                        producer_runner,
                    )
                    if multi_target_producer:
                        await_multi_target_ready()
                        producer_state = _owned_producer_record(
                            profile_admission,
                            "ready",
                            producer_process.pid,
                            launcher_birth_token,
                            launcher_started_at,
                            process_group_id,
                            producer_log,
                            owned_producer_transition.live,
                        )
                    else:
                        assert producer_identity is not None
                        producer_identity = _confirm_producer_identity(
                            producer_identity,
                            profile_admission,
                            launcher_started_at,
                            process_group_id,
                            producer_runner,
                            context.birth_token_reader,
                            context.pid_version_reader,
                        )
                        producer_state = _producer_record(
                            profile_admission,
                            "ready",
                            producer_process.pid,
                            launcher_birth_token,
                            launcher_started_at,
                            process_group_id,
                            producer_log,
                            producer_identity,
                        )
                    client_monitor = ClientTelemetryMonitor(generation, bridge_pid)
                    telemetry_deadline = min(
                        deadline,
                        monotonic() + CLIENT_TELEMETRY_READY_TIMEOUT_SECONDS,
                    )
                    published_client_state, published_client_record = _wait_for_client_state(
                        telemetry_reader,
                        client_monitor,
                        telemetry_deadline,
                        monotonic,
                        context.sleeper,
                    )
                    next_client_snapshot = (
                        monotonic() + CLIENT_STATE_SNAPSHOT_INTERVAL_SECONDS
                    )
                    if multi_target_producer:
                        await_multi_target_ready()
                        producer_state = _owned_producer_record(
                            profile_admission,
                            "ready",
                            producer_process.pid,
                            launcher_birth_token,
                            launcher_started_at,
                            process_group_id,
                            producer_log,
                            owned_producer_transition.live,
                        )
                    live_payload = _state_payload(
                        active_admission,
                        run_dir,
                        generation,
                        service,
                        owner_started_at,
                        control_socket,
                        plist_sha256,
                        published_client_state,
                        CLIENT_STATE_PHASES[published_client_state],
                        producer_state,
                        published_client_record,
                    )
                    _write_json_atomic(
                        active_admission.paths.state_root,
                        active_admission.paths.state_path,
                        live_payload,
                    )
                    service = _revalidate_waiting_service(
                        active_admission,
                        bridge_pid,
                        service_runs,
                        plist_sha256,
                        starting_payload["bridgeExecutableSha256"],
                        producer_runner,
                    )
                    if multi_target_producer:
                        while True:
                            confirmation_observation = (
                                _inspect_owned_producer_observation(
                                    profile_admission,
                                    launcher_started_at,
                                    process_group_id,
                                    producer_runner,
                                    context.birth_token_reader,
                                    context.pid_version_reader,
                                )
                            )
                            confirmation_observation = (
                                _confirm_owned_producer_observation(
                                    confirmation_observation,
                                    profile_admission,
                                    producer_runner,
                                    context.birth_token_reader,
                                    context.pid_version_reader,
                                )
                            )
                            confirmed_transition = (
                                _reconcile_owned_producer_transition(
                                    profile_admission,
                                    owned_producer_transition,
                                    confirmation_observation,
                                    bridge_log,
                                    active_admission.paths.service_label,
                                    generation,
                                    bridge_pid,
                                    process_group_id,
                                    monotonic(),
                                    producer_transition_timeout,
                                    group_live,
                                )
                            )
                            if (
                                confirmed_transition.live
                                == owned_producer_transition.live
                            ):
                                owned_producer_transition = confirmed_transition
                                break
                            owned_producer_transition = confirmed_transition
                            if not owned_producer_transition.live:
                                producer_state = _owned_producer_record(
                                    profile_admission,
                                    "starting",
                                    producer_process.pid,
                                    launcher_birth_token,
                                    launcher_started_at,
                                    process_group_id,
                                    producer_log,
                                    (),
                                )
                                transition_payload = _state_payload(
                                    active_admission,
                                    run_dir,
                                    generation,
                                    service,
                                    owner_started_at,
                                    control_socket,
                                    plist_sha256,
                                    "idle",
                                    "starting-producer-transition",
                                    producer_state,
                                )
                                _write_json_atomic(
                                    active_admission.paths.state_root,
                                    active_admission.paths.state_path,
                                    transition_payload,
                                )
                                await_multi_target_ready()
                            producer_state = _owned_producer_record(
                                profile_admission,
                                "ready",
                                producer_process.pid,
                                launcher_birth_token,
                                launcher_started_at,
                                process_group_id,
                                producer_log,
                                owned_producer_transition.live,
                            )
                            assert client_monitor is not None
                            published_client_state, published_client_record = (
                                client_monitor.observe(
                                    telemetry_reader(),
                                    monotonic(),
                                )
                            )
                            corrected_payload = _state_payload(
                                active_admission,
                                run_dir,
                                generation,
                                service,
                                owner_started_at,
                                control_socket,
                                plist_sha256,
                                published_client_state,
                                CLIENT_STATE_PHASES[published_client_state],
                                producer_state,
                                published_client_record,
                            )
                            _write_json_atomic(
                                active_admission.paths.state_root,
                                active_admission.paths.state_path,
                                corrected_payload,
                            )
                            next_client_snapshot = (
                                monotonic() + CLIENT_STATE_SNAPSHOT_INTERVAL_SECONDS
                            )
                            service = _revalidate_waiting_service(
                                active_admission,
                                bridge_pid,
                                service_runs,
                                plist_sha256,
                                starting_payload["bridgeExecutableSha256"],
                                producer_runner,
                            )
                    else:
                        assert producer_identity is not None
                        producer_identity = _confirm_producer_identity(
                            producer_identity,
                            profile_admission,
                            launcher_started_at,
                            process_group_id,
                            producer_runner,
                            context.birth_token_reader,
                            context.pid_version_reader,
                        )
                    report = StartReport(
                        True,
                        published_client_state,
                        f"runtime.{published_client_state}",
                        CLIENT_STATE_MESSAGES[published_client_state],
                        active_admission.artifact,
                        generation,
                        os.getpid(),
                        run_dir,
                        supervisor_log,
                        tuple(actions),
                        _profile_record(profile_admission),
                        producer_state,
                    )
                    _write_startup_result(run_dir, report)
                live_pid = bridge_pid
                next_status_check = monotonic()
                next_identity_check = monotonic() + SERVICE_IDENTITY_INTERVAL_SECONDS
                while True:
                    if (
                        _receive_control_request(
                            listener,
                            generation,
                            quiesce_and_publish,
                        )
                        == "stop"
                    ):
                        stop_requested = True
                    now = monotonic()
                    if not stop_requested and now < next_status_check:
                        continue
                    next_status_check = now + SERVICE_STATUS_INTERVAL_SECONDS
                    producer_state_changed = False
                    if not producer_quiesced:
                        if producer_process.poll() is not None:
                            failure = start_failure(
                                "producer.exited",
                                "Owned producer exited without a stop request",
                                artifact=active_admission.artifact,
                                generation=generation,
                                owner_pid=os.getpid(),
                                run_dir=run_dir,
                                supervisor_log=supervisor_log,
                                actions=actions,
                                profile=_profile_record(profile_admission),
                                producer=producer_state,
                            )
                            _write_supervisor_exit(run_dir, failure)
                            return failure
                        current_producer = producer_identity
                        try:
                            if multi_target_producer:
                                observation = _inspect_owned_producer_observation(
                                    profile_admission,
                                    launcher_started_at,
                                    process_group_id,
                                    context.runner,
                                    context.birth_token_reader,
                                    context.pid_version_reader,
                                )
                                observation = _confirm_owned_producer_observation(
                                    observation,
                                    profile_admission,
                                    context.runner,
                                    context.birth_token_reader,
                                    context.pid_version_reader,
                                )
                                next_transition = (
                                    _reconcile_owned_producer_transition(
                                        profile_admission,
                                        owned_producer_transition,
                                        observation,
                                        bridge_log,
                                        active_admission.paths.service_label,
                                        generation,
                                        bridge_pid,
                                        process_group_id,
                                        now,
                                        producer_transition_timeout,
                                        group_live,
                                    )
                                )
                                next_producer_state = _owned_producer_record(
                                    profile_admission,
                                    (
                                        "ready"
                                        if next_transition.live
                                        else "starting"
                                    ),
                                    producer_process.pid,
                                    launcher_birth_token,
                                    launcher_started_at,
                                    process_group_id,
                                    producer_log,
                                    next_transition.live,
                                )
                                producer_state_changed = (
                                    next_producer_state != producer_state
                                )
                                owned_producer_transition = next_transition
                                producer_state = next_producer_state
                            else:
                                assert producer_identity is not None
                                current_producer = _confirm_producer_identity(
                                    producer_identity,
                                    profile_admission,
                                    launcher_started_at,
                                    process_group_id,
                                    context.runner,
                                    context.birth_token_reader,
                                    context.pid_version_reader,
                                )
                        except ControlError as error:
                            failure = start_failure(
                                error.code,
                                error.message,
                                artifact=active_admission.artifact,
                                generation=generation,
                                owner_pid=os.getpid(),
                                run_dir=run_dir,
                                supervisor_log=supervisor_log,
                                actions=actions,
                                profile=_profile_record(profile_admission),
                                producer=producer_state,
                            )
                            _write_supervisor_exit(run_dir, failure)
                            return failure
                        if (
                            not multi_target_producer
                            and current_producer != producer_identity
                        ):
                            failure = start_failure(
                                "producer.identity_changed",
                                "Owned producer identity changed after startup",
                                artifact=active_admission.artifact,
                                generation=generation,
                                owner_pid=os.getpid(),
                                run_dir=run_dir,
                                supervisor_log=supervisor_log,
                                actions=actions,
                                profile=_profile_record(profile_admission),
                                producer=producer_state,
                            )
                            _write_supervisor_exit(run_dir, failure)
                            return failure
                    snapshot = read_launchd_snapshot(active_admission.paths, context.runner)
                    if snapshot.error_code is not None:
                        failure = start_failure(
                            snapshot.error_code,
                            snapshot.message or "Live launchd state inspection failed",
                            artifact=active_admission.artifact,
                            generation=generation,
                            owner_pid=os.getpid(),
                            run_dir=run_dir,
                            supervisor_log=supervisor_log,
                            actions=actions,
                            profile=_profile_record(profile_admission),
                            producer=producer_state,
                        )
                        _write_supervisor_exit(run_dir, failure)
                        return failure
                    if not snapshot.present:
                        if stop_requested:
                            _cleanup_started_state(
                                active_admission,
                                run_dir,
                                generation,
                                plist_sha256,
                                control_socket,
                                remove_run_dir=True,
                            )
                            stopped = StartReport(
                                True,
                                "stopped",
                                "runtime.stopped",
                                "Runtime supervisor completed cooperative cleanup",
                                active_admission.artifact,
                                generation,
                                os.getpid(),
                                run_dir,
                                supervisor_log,
                                tuple(actions),
                                _profile_record(profile_admission),
                                producer_state,
                            )
                            return stopped
                        failure = start_failure(
                            "service.exited",
                            "Owned launchd service disappeared without a stop request",
                            artifact=active_admission.artifact,
                            generation=generation,
                            owner_pid=os.getpid(),
                            run_dir=run_dir,
                            supervisor_log=supervisor_log,
                            actions=actions,
                            profile=_profile_record(profile_admission),
                            producer=producer_state,
                        )
                        _write_supervisor_exit(run_dir, failure)
                        return failure
                    if stop_requested:
                        transitioning_service = inspect_service(
                            active_admission.paths,
                            context.runner,
                        )
                        if not transitioning_service.snapshot.present:
                            continue
                        try:
                            transition_plist_sha256 = artifact_contract.sha256_file(
                                active_admission.paths.launch_agent_plist
                            )
                            transition_bridge_sha256 = artifact_contract.sha256_file(
                                active_admission.paths.bridge_program
                            )
                        except OSError:
                            transition_plist_sha256 = None
                            transition_bridge_sha256 = None
                        if (
                            transitioning_service.error_code is not None
                            or not transitioning_service.owned
                            or not transitioning_service.identity_valid
                            or transition_plist_sha256 != plist_sha256
                            or transition_bridge_sha256
                            != starting_payload["bridgeExecutableSha256"]
                        ):
                            failure = start_failure(
                                "service.identity_changed",
                                transitioning_service.message
                                or "Owned launchd service identity changed during cooperative stop",
                                artifact=active_admission.artifact,
                                generation=generation,
                                owner_pid=os.getpid(),
                                run_dir=run_dir,
                                supervisor_log=supervisor_log,
                                actions=actions,
                                profile=_profile_record(profile_admission),
                                producer=producer_state,
                            )
                            _write_supervisor_exit(run_dir, failure)
                            return failure
                        continue
                    if (
                        snapshot.pid != live_pid
                        or snapshot.runs != 1
                        or snapshot.state != "running"
                        or not paths_match(snapshot.path, active_admission.paths.launch_agent_plist)
                        or not paths_match(snapshot.program, active_admission.paths.bridge_program)
                    ):
                        failure = start_failure(
                            "service.identity_changed",
                            "Owned launchd service identity changed after startup",
                            artifact=active_admission.artifact,
                            generation=generation,
                            owner_pid=os.getpid(),
                            run_dir=run_dir,
                            supervisor_log=supervisor_log,
                            actions=actions,
                            profile=_profile_record(profile_admission),
                            producer=producer_state,
                        )
                        _write_supervisor_exit(run_dir, failure)
                        return failure
                    producer_ready = (
                        not multi_target_producer
                        or producer_state.get("status") == "ready"
                    )
                    if not producer_ready:
                        if producer_state_changed:
                            transition_payload = _state_payload(
                                active_admission,
                                run_dir,
                                generation,
                                service,
                                owner_started_at,
                                control_socket,
                                plist_sha256,
                                "idle",
                                "starting-producer-transition",
                                producer_state,
                            )
                            _write_json_atomic(
                                active_admission.paths.state_root,
                                active_admission.paths.state_path,
                                transition_payload,
                            )
                    else:
                        assert client_monitor is not None
                        assert published_client_state is not None
                        assert published_client_record is not None
                        try:
                            observed_client_state, observed_client_record = (
                                client_monitor.observe(
                                    telemetry_reader(),
                                    now,
                                )
                            )
                        except ControlError as error:
                            failure = start_failure(
                                error.code,
                                error.message,
                                artifact=active_admission.artifact,
                                generation=generation,
                                owner_pid=os.getpid(),
                                run_dir=run_dir,
                                supervisor_log=supervisor_log,
                                actions=actions,
                                profile=_profile_record(profile_admission),
                                producer=producer_state,
                            )
                            _write_supervisor_exit(run_dir, failure)
                            return failure
                        if (
                            producer_state_changed
                            or observed_client_state != published_client_state
                            or observed_client_record["streamEpoch"]
                            != published_client_record["streamEpoch"]
                            or now >= next_client_snapshot
                        ):
                            client_payload = _state_payload(
                                active_admission,
                                run_dir,
                                generation,
                                service,
                                owner_started_at,
                                control_socket,
                                plist_sha256,
                                observed_client_state,
                                CLIENT_STATE_PHASES[observed_client_state],
                                producer_state,
                                observed_client_record,
                            )
                            _write_json_atomic(
                                active_admission.paths.state_root,
                                active_admission.paths.state_path,
                                client_payload,
                            )
                            published_client_state = observed_client_state
                            published_client_record = observed_client_record
                            next_client_snapshot = (
                                now + CLIENT_STATE_SNAPSHOT_INTERVAL_SECONDS
                            )
                    if now < next_identity_check:
                        continue
                    next_identity_check = now + SERVICE_IDENTITY_INTERVAL_SECONDS
                    current = inspect_service(active_admission.paths, context.runner)
                    if (
                        current.error_code is not None
                        or not current.snapshot.present
                        or current.snapshot.pid != live_pid
                        or current.snapshot.runs != 1
                        or current.snapshot.state != "running"
                        or not current.owned
                        or not current.identity_valid
                    ):
                        failure = start_failure(
                            current.error_code or "service.identity_changed",
                            current.message or "Owned launchd service identity changed after startup",
                            artifact=active_admission.artifact,
                            generation=generation,
                            owner_pid=os.getpid(),
                            run_dir=run_dir,
                            supervisor_log=supervisor_log,
                            actions=actions,
                            profile=_profile_record(profile_admission),
                            producer=producer_state,
                        )
                        _write_supervisor_exit(run_dir, failure)
                        return failure
            finally:
                listener.close()
    except Exception as error:
        code = error.code if isinstance(error, ControlError) else "runtime.start_failed"
        message = (
            error.message
            if isinstance(error, ControlError)
            else f"Unexpected supervisor failure: {type(error).__name__}: {error}"
        )
        failure = start_failure(
            code,
            message,
            artifact=admission.artifact if admission is not None else None,
            generation=generation,
            owner_pid=os.getpid(),
            run_dir=run_dir,
            supervisor_log=supervisor_log,
            actions=actions,
            profile=(
                _profile_record(admission.profile)
                if admission is not None and admission.profile is not None
                else None
            ),
            producer=producer_state,
        )
        cleanup_failures: list[str] = []
        producer_clean = producer_process is None
        if (
            producer_process is not None
            and launcher_birth_token is not None
            and launcher_started_at is not None
            and process_group_id is not None
            and bridge_pid is not None
            and admission is not None
            and admission.profile is not None
        ):
            try:
                if multi_target_producer:
                    _quiesce_owned_producers(
                        producer_process,
                        launcher_birth_token,
                        launcher_started_at,
                        process_group_id,
                        owned_producer_transition.retained,
                        admission.profile,
                        run_dir / BRIDGE_LOG_NAME,
                        admission.paths.service_label,
                        generation,
                        bridge_pid,
                        context,
                        monotonic,
                        group_id,
                        group_signaler,
                        group_live,
                        transition_deadline=owned_producer_transition.deadline,
                    )
                else:
                    _quiesce_producer(
                        producer_process,
                        launcher_birth_token,
                        launcher_started_at,
                        process_group_id,
                        producer_identity,
                        admission.profile,
                        run_dir / BRIDGE_LOG_NAME,
                        admission.paths.service_label,
                        generation,
                        bridge_pid,
                        context,
                        monotonic,
                        group_id,
                        group_signaler,
                        group_live,
                    )
            except (ControlError, OSError) as cleanup_error:
                cleanup_failures.append(str(cleanup_error))
            else:
                producer_clean = True
        service_clean = admission is None
        if producer_clean and admission is not None:
            try:
                _bootout_created_service(
                    admission,
                    context.runner,
                    plist_sha256,
                    context.sleeper,
                )
            except (ControlError, OSError) as cleanup_error:
                cleanup_failures.append(str(cleanup_error))
            else:
                service_clean = True
        if service_clean and admission is not None and inspect_lock(
            admission.paths.lock_path, lambda pid: pid == os.getpid()
        ).exists:
            try:
                _cleanup_started_state(
                    admission,
                    run_dir,
                    generation,
                    plist_sha256,
                    control_socket,
                    remove_run_dir=False,
                )
            except (ControlError, OSError) as cleanup_error:
                cleanup_failures.append(str(cleanup_error))
        if cleanup_failures:
            failure = start_failure(
                "runtime.cleanup_failed",
                f"{code}: {message}; cleanup failed: {'; '.join(cleanup_failures)}",
                artifact=admission.artifact if admission is not None else None,
                generation=generation,
                owner_pid=os.getpid(),
                run_dir=run_dir,
                supervisor_log=supervisor_log,
                actions=actions,
                profile=(
                    _profile_record(admission.profile)
                    if admission is not None and admission.profile is not None
                    else None
                ),
                producer=producer_state,
            )
        with contextlib.suppress(ControlError, OSError):
            _write_startup_result(run_dir, failure)
        return failure


def build_supervisor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal Mac ALVR runtime supervisor")
    parser.add_argument("--bindings", type=pathlib.Path, required=True)
    parser.add_argument("--artifact", type=pathlib.Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--profile-sha256", required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--run-dir", type=pathlib.Path, required=True)
    parser.add_argument("--deadline", type=float, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_supervisor_parser().parse_args(argv)
    context = RuntimeContext(bindings_path=arguments.bindings)
    report = supervise_runtime(
        context,
        arguments.artifact,
        arguments.profile,
        arguments.profile_sha256,
        arguments.generation,
        arguments.run_dir,
        startup_deadline=arguments.deadline,
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
