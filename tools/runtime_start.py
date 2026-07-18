"""Bounded detached start supervision for the Mac ALVR runtime."""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import pathlib
import plistlib
import secrets
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

import build_runtime_artifact as artifact_contract
import runtime_descriptor
import runtime_transaction
from runtime_control import (
    CONTROL_SOCKET_NAME,
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
    paths_match,
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
ALVR_ROOT_NAME = "alvr-root"
STARTUP_TIMEOUT_SECONDS = 30.0
SERVICE_READY_TIMEOUT_SECONDS = 10.0
READINESS_SAMPLE_INTERVAL_SECONDS = 0.1
MONITOR_INTERVAL_SECONDS = 0.25
SERVICE_STATUS_INTERVAL_SECONDS = 1.0
SERVICE_IDENTITY_INTERVAL_SECONDS = 30.0
MAX_RUNTIME_FRAMES = (1 << 64) - 1
INSTALLED_FALSE_ACTIONS = frozenset({"assert_sha256", "backup", "assert_absent"})


class SpawnedProcess(Protocol):
    pid: int

    def poll(self) -> int | None:
        """Return the child status or None while it remains live."""


class ProcessLauncher(Protocol):
    def launch(self, argv: Sequence[str], log_path: pathlib.Path) -> SpawnedProcess:
        """Launch one detached supervisor child."""

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
    operations = plan.get("install")
    if not isinstance(operations, list):
        raise ControlError("plan.invalid", "Resolved plan is missing install operations")
    return artifact_contract.sha256_bytes(
        artifact_contract.canonical_json_bytes(
            [runtime_transaction.semantic_operation(operation) for operation in operations]
        )
    )


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
    expected_digest = _install_plan_digest(plan)
    if (
        not isinstance(journal, dict)
        or journal.get("kind") != "install"
        or journal.get("state") != "committed"
        or journal.get("planDigest") != expected_digest
        or journal.get("cleanupFailures") != []
        or journal.get("rollbackFailures") != []
        or journal.get("failure") is not None
    ):
        raise ControlError(
            "runtime.not_installed",
            "Active lifecycle journal does not prove an exact committed install",
            path=str(journal_path),
            expectedPlanDigest=expected_digest,
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


def inspect_start_admission(context: RuntimeContext, artifact: pathlib.Path) -> StartAdmission:
    manifest, _, manifest_hash, lock_hash = load_runtime_contract(context)
    try:
        bindings = artifact_contract.resolve_bindings(manifest, context.bindings_path, "plan")
        paths = resolve_runtime_paths(manifest, bindings)
        artifact_summary = verify_artifact_reference(context, artifact, require_sealed=True)
        artifact_path = pathlib.Path(artifact_summary["path"])
        plan = artifact_contract.build_plan(
            manifest,
            artifact_path,
            context.bindings_path,
            expected_manifest_hash=manifest_hash,
            expected_lock_hash=lock_hash,
        )
        allowed_roots = tuple(
            artifact_contract.resolve_path(template, bindings, f"allowedTargetRoots[{index}]")
            for index, template in enumerate(manifest["allowedTargetRoots"])
        )
    except artifact_contract.ArtifactError as error:
        raise ControlError(error.code, error.message, **error.context) from error
    admission = StartAdmission(
        manifest,
        bindings,
        paths,
        allowed_roots,
        plan,
        artifact_summary,
        artifact_path,
    )
    _require_installed_plan(plan)
    _require_launch_template_state(admission)
    _require_committed_install_journal(plan)
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
            "ALVR_BRIDGE_CONNECT": "false",
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


def _state_payload(
    admission: StartAdmission,
    run_dir: pathlib.Path,
    generation: int,
    service: Any,
    owner_started_at: str,
    control_socket: pathlib.Path,
    plist_sha256: str,
) -> dict[str, Any]:
    if service.snapshot.pid is None or service.file_identity is None:
        raise ControlError("runtime.start_failed", "Live service identity is incomplete")
    return {
        "schemaVersion": 2,
        "state": "idle",
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
        "updatedAt": datetime.datetime.now(datetime.UTC).isoformat(),
        "diagnostic": {
            "phase": "awaiting-producer",
            "bridgeLog": str(run_dir / BRIDGE_LOG_NAME),
            "supervisorLog": str(run_dir / SUPERVISOR_LOG_NAME),
        },
    }


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
    }
    if set(value) != required or value.get("schemaVersion") != 1 or value.get("command") != "start":
        return None
    if (
        not isinstance(value.get("ok"), bool)
        or value.get("state") not in {"idle", "failed"}
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
        or (value["ok"] and value["state"] != "idle")
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
    )


def _idempotent_live_start(context: RuntimeContext, artifact: pathlib.Path) -> StartReport | None:
    status = status_runtime(context, artifact)
    if not status.ok or status.state not in LIVE_STATES:
        return None
    record = status.control_state.get("record")
    if not isinstance(record, dict):
        return None
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
    )


def start_runtime(
    context: RuntimeContext,
    artifact: pathlib.Path,
    *,
    launcher: ProcessLauncher | None = None,
    generation_factory: Callable[[], int] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> StartReport:
    live = _idempotent_live_start(context, artifact)
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
        admission = inspect_start_admission(context, artifact)
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
    deadline = monotonic() + STARTUP_TIMEOUT_SECONDS
    command = [
        sys.executable,
        str(RUNTIME_START),
        "--artifact",
        str(_absolute(artifact)),
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
                if (
                    report.generation != generation
                    or report.owner_pid != child.pid
                    or report.run_dir != run_dir
                    or report.supervisor_log != supervisor_log
                ):
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
        response = artifact_contract.canonical_json_bytes(
            {"schemaVersion": 1, "ok": accepted, "generation": generation}
        )
        with contextlib.suppress(OSError):
            connection.sendall(response)
        return command if accepted else None


def supervise_runtime(
    context: RuntimeContext,
    artifact: pathlib.Path,
    generation: int,
    run_dir: pathlib.Path,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    startup_deadline: float | None = None,
) -> StartReport:
    actions: list[str] = []
    supervisor_log = run_dir / SUPERVISOR_LOG_NAME
    admission: StartAdmission | None = None
    plist_sha256: str | None = None
    control_socket = run_dir / CONTROL_SOCKET_NAME
    deadline = startup_deadline or (monotonic() + STARTUP_TIMEOUT_SECONDS)
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
            admission = inspect_start_admission(context, artifact)
            _remaining_timeout(deadline, STARTUP_TIMEOUT_SECONDS, monotonic)
            startup_runner = DeadlineRunner(context.runner, deadline, monotonic)
            existing_service = inspect_service(admission.paths, startup_runner)
            if existing_service.error_code is not None or existing_service.snapshot.present:
                raise ControlError(
                    existing_service.error_code or "service.foreign",
                    existing_service.message or "Launchd label is already occupied",
                )
            if inspect_lock(admission.paths.lock_path, context.pid_alive).exists:
                raise ControlError(
                    "runtime.stale_state",
                    "Runtime owner lock already exists; run stop before starting",
                    path=str(admission.paths.lock_path),
                )
            if load_control_state(admission.paths.state_path).exists:
                raise ControlError(
                    "runtime.stale_state",
                    "Runtime control state already exists; run stop before starting",
                    path=str(admission.paths.state_path),
                )
            _remaining_timeout(deadline, STARTUP_TIMEOUT_SECONDS, monotonic)
            _create_owner_lock(admission.paths, run_dir)
            owner_started_at, owner_error = process_start_time(os.getpid(), startup_runner)
            if owner_started_at is None:
                raise ControlError(
                    "owner.identity_unavailable",
                    owner_error or "Supervisor process start time is unavailable",
                )
            _remaining_timeout(deadline, STARTUP_TIMEOUT_SECONDS, monotonic)
            bridge_log = run_dir / BRIDGE_LOG_NAME
            _write_new_file(run_dir, bridge_log, b"")
            _make_private_directory(run_dir, run_dir / ALVR_ROOT_NAME)
            launch_plist = render_launch_agent(admission, run_dir, generation)
            plist_sha256 = artifact_contract.sha256_bytes(launch_plist)
            _write_file_atomic(
                admission.paths.launch_agent_plist.parent,
                admission.paths.launch_agent_plist,
                launch_plist,
            )
            if artifact_contract.sha256_file(admission.paths.launch_agent_plist) != plist_sha256:
                raise ControlError(
                    "runtime.start_failed",
                    "Rendered launch agent plist changed during publication",
                )
            plist_owned, plist_error = validate_plist_ownership(admission.paths)
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
                        admission,
                        startup_runner,
                        deadline,
                        monotonic,
                    )
                )
                ready_window = _remaining_timeout(
                    deadline,
                    SERVICE_READY_TIMEOUT_SECONDS,
                    monotonic,
                )
                service = _service_ready(
                    admission,
                    startup_runner,
                    bridge_log,
                    monotonic() + ready_window,
                    monotonic,
                    context.sleeper,
                )
                state_payload = _state_payload(
                    admission,
                    run_dir,
                    generation,
                    service,
                    owner_started_at,
                    control_socket,
                    plist_sha256,
                )
                _remaining_timeout(deadline, STARTUP_TIMEOUT_SECONDS, monotonic)
                _write_json_atomic(
                    admission.paths.state_root,
                    admission.paths.state_path,
                    state_payload,
                )
                report = StartReport(
                    True,
                    "idle",
                    "runtime.idle",
                    "Runtime supervisor and exact Mach bridge are awaiting a game producer",
                    admission.artifact,
                    generation,
                    os.getpid(),
                    run_dir,
                    supervisor_log,
                    tuple(actions),
                )
                _write_startup_result(run_dir, report)
                stop_requested = False
                live_pid = service.snapshot.pid
                next_status_check = monotonic()
                next_identity_check = monotonic() + SERVICE_IDENTITY_INTERVAL_SECONDS
                while True:
                    if _receive_control_request(listener, generation) == "stop":
                        stop_requested = True
                    now = monotonic()
                    if not stop_requested and now < next_status_check:
                        continue
                    next_status_check = now + SERVICE_STATUS_INTERVAL_SECONDS
                    snapshot = read_launchd_snapshot(admission.paths, context.runner)
                    if snapshot.error_code is not None:
                        failure = start_failure(
                            snapshot.error_code,
                            snapshot.message or "Live launchd state inspection failed",
                            artifact=admission.artifact,
                            generation=generation,
                            owner_pid=os.getpid(),
                            run_dir=run_dir,
                            supervisor_log=supervisor_log,
                            actions=actions,
                        )
                        _write_supervisor_exit(run_dir, failure)
                        return failure
                    if not snapshot.present:
                        if stop_requested:
                            _cleanup_started_state(
                                admission,
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
                                admission.artifact,
                                generation,
                                os.getpid(),
                                run_dir,
                                supervisor_log,
                                tuple(actions),
                            )
                            return stopped
                        failure = start_failure(
                            "service.exited",
                            "Owned launchd service disappeared without a stop request",
                            artifact=admission.artifact,
                            generation=generation,
                            owner_pid=os.getpid(),
                            run_dir=run_dir,
                            supervisor_log=supervisor_log,
                            actions=actions,
                        )
                        _write_supervisor_exit(run_dir, failure)
                        return failure
                    if (
                        snapshot.pid != live_pid
                        or snapshot.runs != 1
                        or snapshot.state != "running"
                        or not paths_match(snapshot.path, admission.paths.launch_agent_plist)
                        or not paths_match(snapshot.program, admission.paths.bridge_program)
                    ):
                        failure = start_failure(
                            "service.identity_changed",
                            "Owned launchd service identity changed after startup",
                            artifact=admission.artifact,
                            generation=generation,
                            owner_pid=os.getpid(),
                            run_dir=run_dir,
                            supervisor_log=supervisor_log,
                            actions=actions,
                        )
                        _write_supervisor_exit(run_dir, failure)
                        return failure
                    if now < next_identity_check:
                        continue
                    next_identity_check = now + SERVICE_IDENTITY_INTERVAL_SECONDS
                    current = inspect_service(admission.paths, context.runner)
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
                            artifact=admission.artifact,
                            generation=generation,
                            owner_pid=os.getpid(),
                            run_dir=run_dir,
                            supervisor_log=supervisor_log,
                            actions=actions,
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
        )
        cleanup_failures: list[str] = []
        service_clean = admission is None
        if admission is not None:
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
            )
        with contextlib.suppress(ControlError, OSError):
            _write_startup_result(run_dir, failure)
        return failure


def build_supervisor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal Mac ALVR runtime supervisor")
    parser.add_argument("--bindings", type=pathlib.Path, required=True)
    parser.add_argument("--artifact", type=pathlib.Path, required=True)
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
        arguments.generation,
        arguments.run_dir,
        startup_deadline=arguments.deadline,
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
