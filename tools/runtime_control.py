"""Runtime control-plane diagnostics and lifecycle helpers."""

from __future__ import annotations

import json
import os
import pathlib
import plistlib
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, Sequence

import build_runtime_artifact as artifact_contract


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "runtime" / "manifest.json"
DEFAULT_LOCK = REPO_ROOT / "runtime" / "manifest.lock.json"
DEFAULT_BINDINGS = REPO_ROOT / ".code" / "runtime-bindings.json"
CONTROL_STATE_NAME = "runtime-state.json"
LIVE_STATES = frozenset({"waiting", "connected", "streaming", "recovering"})
CHECK_STATUSES = frozenset({"pass", "fail", "unknown"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ControlError(Exception):
    """Stable machine-readable control-plane failure."""

    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    error: Literal["unavailable", "timeout", "os_error"] | None = None


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, timeout: float = 10.0) -> CommandResult:
        """Run a bounded command without raising for ordinary execution failures."""

        ...


class SubprocessRunner:
    def run(self, argv: Sequence[str], *, timeout: float = 10.0) -> CommandResult:
        command = tuple(str(item) for item in argv)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as error:
            return CommandResult(command, None, stderr=str(error), error="unavailable")
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout if isinstance(error.stdout, str) else ""
            stderr = error.stderr if isinstance(error.stderr, str) else ""
            return CommandResult(command, None, stdout=stdout, stderr=stderr, error="timeout")
        except OSError as error:
            return CommandResult(command, None, stderr=str(error), error="os_error")
        return CommandResult(command, result.returncode, result.stdout, result.stderr)


@dataclass(frozen=True)
class CheckResult:
    id: str
    status: Literal["pass", "fail", "unknown"]
    message: str
    remediation: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in CHECK_STATUSES:
            raise ValueError(f"unsupported check status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "remediation": self.remediation,
            "details": self.details,
        }


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[CheckResult, ...]
    artifact: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.status == "pass" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        summary = {
            status: sum(check.status == status for check in self.checks)
            for status in ("pass", "fail", "unknown")
        }
        return {
            "schemaVersion": 1,
            "command": "doctor",
            "ok": self.ok,
            "artifact": self.artifact,
            "summary": summary,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class RuntimePaths:
    state_root: pathlib.Path
    lock_path: pathlib.Path
    state_path: pathlib.Path
    launch_agent_plist: pathlib.Path
    bridge_bundle: pathlib.Path
    bridge_program: pathlib.Path
    bridge_owner_marker: pathlib.Path
    bridge_owner_content: dict[str, Any]
    service_domain: str
    service_label: str
    service_target: str


@dataclass(frozen=True)
class LockInspection:
    exists: bool
    valid_path: bool
    pid: int | None = None
    alive: bool = False
    run_dir: str | None = None
    entries: tuple[str, ...] = ()
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "validPath": self.valid_path,
            "pid": self.pid,
            "alive": self.alive,
            "runDir": self.run_dir,
            "entries": list(self.entries),
            "errorCode": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class LaunchdSnapshot:
    present: bool
    path: str | None = None
    program: str | None = None
    pid: int | None = None
    state: str | None = None
    runs: int | None = None
    raw: str = ""
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "path": self.path,
            "program": self.program,
            "pid": self.pid,
            "state": self.state,
            "runs": self.runs,
            "errorCode": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class ServiceInspection:
    snapshot: LaunchdSnapshot
    owned: bool
    identity_valid: bool
    live_program: str | None = None
    file_identity: dict[str, Any] | None = None
    live_identity: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        if not self.snapshot.present:
            return self.snapshot.error_code is None
        return self.owned and self.identity_valid and self.error_code is None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.snapshot.to_dict(),
            "owned": self.owned,
            "identityValid": self.identity_valid,
            "liveProgram": self.live_program,
            "fileIdentity": self.file_identity,
            "liveIdentity": self.live_identity,
            "inspectionErrorCode": self.error_code,
            "inspectionMessage": self.message,
        }


@dataclass(frozen=True)
class ControlStateInspection:
    exists: bool
    valid: bool
    record: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "valid": self.valid,
            "record": self.record,
            "errorCode": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class StatusReport:
    state: str
    reason_code: str
    message: str
    artifact: dict[str, Any] | None
    service: dict[str, Any]
    owner: dict[str, Any]
    control_state: dict[str, Any]
    diagnostics: tuple[CheckResult, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state != "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "command": "status",
            "ok": self.ok,
            "state": self.state,
            "reasonCode": self.reason_code,
            "message": self.message,
            "artifact": self.artifact,
            "service": self.service,
            "owner": self.owner,
            "controlState": self.control_state,
            "diagnostics": [check.to_dict() for check in self.diagnostics],
        }


@dataclass(frozen=True)
class StopReport:
    ok: bool
    state: Literal["stopped", "failed"]
    reason_code: str
    message: str
    actions: tuple[str, ...]
    service: dict[str, Any]
    owner: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "command": "stop",
            "ok": self.ok,
            "state": self.state,
            "reasonCode": self.reason_code,
            "message": self.message,
            "actions": list(self.actions),
            "service": self.service,
            "owner": self.owner,
        }


@dataclass
class RuntimeContext:
    manifest_path: pathlib.Path = DEFAULT_MANIFEST
    lock_path: pathlib.Path = DEFAULT_LOCK
    bindings_path: pathlib.Path = DEFAULT_BINDINGS
    runner: CommandRunner = field(default_factory=SubprocessRunner)
    pid_alive: Callable[[int], bool] = field(default=lambda pid: process_is_alive(pid))
    sleeper: Callable[[float], None] = time.sleep


PREREQUISITE_REMEDIATIONS = {
    "host_architecture": "Use the qualified arm64 Apple silicon host.",
    "host_model": "Use the qualified Mac16,9 host or run a separately reviewed hardware qualification.",
    "macos_version": "Install and boot the exact macOS version pinned by the runtime manifest.",
    "macos_build": "Install the exact macOS build pinned by the runtime manifest.",
    "xcode_build": "Install and select the exact Xcode build pinned by the runtime manifest.",
    "crossover_short_version": "Install the exact CrossOver release pinned by the runtime manifest.",
    "crossover_build_version": "Install the exact CrossOver build pinned by the runtime manifest.",
}


def artifact_failure_details(error: artifact_contract.ArtifactError) -> dict[str, Any]:
    return {"code": error.code, **error.context}


def load_runtime_contract(
    context: RuntimeContext,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    try:
        return artifact_contract.load_contract(context.manifest_path, context.lock_path)
    except artifact_contract.ArtifactError as error:
        raise ControlError(
            "repository.contract",
            "Runtime manifest or lockfile validation failed",
            artifactError=artifact_failure_details(error),
        ) from error


def mutable_state_item(manifest: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in manifest["mutableState"]:
        if item["id"] == item_id:
            return item
    raise ControlError("repository.contract", "Runtime manifest is missing mutable state", itemId=item_id)


def resolve_runtime_paths(manifest: dict[str, Any], bindings: dict[str, str]) -> RuntimePaths:
    def path_for(item_id: str) -> pathlib.Path:
        item = mutable_state_item(manifest, item_id)
        expanded = artifact_contract.expand_template(item["location"], bindings, f"mutableState.{item_id}.location")
        path = pathlib.Path(expanded).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return pathlib.Path(os.path.abspath(path))

    state_root = path_for("runtime_state")
    lock_path = path_for("runtime_lock")
    launch_agent_plist = path_for("launch_agent_plist")
    bridge_bundle = path_for("bridge_bundle")
    launchd_item = mutable_state_item(manifest, "launchd_job")
    target = artifact_contract.expand_template(
        launchd_item["location"], bindings, "mutableState.launchd_job.location"
    ).replace("<uid>", str(os.getuid()))
    target_parts = target.split("/", 2)
    if len(target_parts) != 3 or target_parts[0] not in {"gui", "user"}:
        raise ControlError("repository.contract", "Launchd target has an invalid format", target=target)
    service_domain = "/".join(target_parts[:2])
    service_label = target_parts[2]
    mach_service = mutable_state_item(manifest, "mach_service")["location"]
    if mach_service != service_label:
        raise ControlError(
            "repository.contract",
            "Launchd and Mach service labels disagree",
            launchdLabel=service_label,
            machService=mach_service,
        )
    owner_item = next(
        (item for item in manifest["generatedFiles"] if item["id"] == "native_bundle_owner"),
        None,
    )
    if owner_item is None or not isinstance(owner_item.get("content"), dict):
        raise ControlError(
            "repository.contract",
            "Runtime manifest is missing the native bundle ownership marker",
        )
    paths = RuntimePaths(
        state_root=state_root,
        lock_path=lock_path,
        state_path=state_root / CONTROL_STATE_NAME,
        launch_agent_plist=launch_agent_plist,
        bridge_bundle=bridge_bundle,
        bridge_program=bridge_bundle / "Contents" / "MacOS" / "alvr_macos_bridge",
        bridge_owner_marker=bridge_bundle / "Contents" / "Resources" / "runtime-owner.json",
        bridge_owner_content=owner_item["content"],
        service_domain=service_domain,
        service_label=service_label,
        service_target=target,
    )
    allowed_roots = [
        artifact_contract.resolve_path(template, bindings, f"allowedTargetRoots[{index}]")
        for index, template in enumerate(manifest["allowedTargetRoots"])
    ]
    for item_id, path in (
        ("runtime_state", paths.state_root),
        ("runtime_lock", paths.lock_path),
        ("launch_agent_plist", paths.launch_agent_plist),
        ("bridge_bundle", paths.bridge_bundle),
    ):
        artifact_contract.ensure_target_allowed(path, allowed_roots, item_id)
    return paths


def prerequisite_remediation(item: dict[str, Any]) -> str:
    return PREREQUISITE_REMEDIATIONS.get(
        item["id"], "Restore the exact prerequisite value declared by runtime/manifest.json."
    )


def evaluate_command_prerequisite(item: dict[str, Any], runner: CommandRunner) -> CheckResult:
    check_id = f"prerequisite.{item['id']}"
    remediation = prerequisite_remediation(item)
    result = runner.run(item["argv"], timeout=15.0)
    if result.error == "unavailable":
        return CheckResult(
            check_id,
            "fail",
            "Required prerequisite command is unavailable",
            remediation,
            {"argv": list(result.argv), "error": result.stderr},
        )
    if result.error is not None:
        return CheckResult(
            check_id,
            "unknown",
            "Prerequisite command could not be evaluated",
            remediation,
            {"argv": list(result.argv), "errorKind": result.error, "error": result.stderr},
        )
    if result.returncode != 0:
        return CheckResult(
            check_id,
            "unknown",
            "Prerequisite command exited unsuccessfully",
            remediation,
            {
                "argv": list(result.argv),
                "exitCode": result.returncode,
                "stderr": result.stderr.strip(),
            },
        )
    actual = result.stdout.strip()
    expected = item.get("equals")
    contains = item.get("contains")
    if expected is not None:
        matches = actual == expected
    elif isinstance(contains, str):
        matches = contains in actual
    else:
        matches = False
    expectation = {"equals": expected} if expected is not None else {"contains": contains}
    if not matches:
        return CheckResult(
            check_id,
            "fail",
            "Prerequisite value does not match the runtime manifest",
            remediation,
            {"actual": actual, **expectation},
        )
    return CheckResult(
        check_id,
        "pass",
        "Prerequisite matches the runtime manifest",
        remediation,
        {"actual": actual, **expectation},
    )


def plist_value(payload: Any, key: str) -> Any:
    current = payload
    for component in key.replace(":", ".").split("."):
        if not isinstance(current, dict) or component not in current:
            raise KeyError(component)
        current = current[component]
    return current


def evaluate_plist_prerequisite(item: dict[str, Any], bindings: dict[str, str]) -> CheckResult:
    check_id = f"prerequisite.{item['id']}"
    remediation = prerequisite_remediation(item)
    try:
        expanded = artifact_contract.expand_template(item["path"], bindings, f"prerequisite.{item['id']}.path")
    except artifact_contract.ArtifactError as error:
        return CheckResult(
            check_id,
            "unknown",
            "Prerequisite path could not be resolved",
            remediation,
            artifact_failure_details(error),
        )
    path = pathlib.Path(expanded).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = pathlib.Path(os.path.abspath(path))
    if not path.is_file():
        return CheckResult(
            check_id,
            "fail",
            "Required prerequisite plist is missing",
            remediation,
            {"path": str(path), "key": item["key"], "equals": item["equals"]},
        )
    try:
        artifact_contract.reject_symlink_components(path)
        with path.open("rb") as stream:
            payload = plistlib.load(stream)
        actual = plist_value(payload, item["key"])
    except KeyError:
        return CheckResult(
            check_id,
            "fail",
            "Required prerequisite plist key is missing",
            remediation,
            {"path": str(path), "key": item["key"], "equals": item["equals"]},
        )
    except (OSError, plistlib.InvalidFileException, artifact_contract.ArtifactError) as error:
        return CheckResult(
            check_id,
            "unknown",
            "Prerequisite plist could not be evaluated",
            remediation,
            {"path": str(path), "key": item["key"], "error": str(error)},
        )
    if actual != item["equals"]:
        return CheckResult(
            check_id,
            "fail",
            "Prerequisite plist value does not match the runtime manifest",
            remediation,
            {"path": str(path), "key": item["key"], "actual": actual, "equals": item["equals"]},
        )
    return CheckResult(
        check_id,
        "pass",
        "Prerequisite plist value matches the runtime manifest",
        remediation,
        {"path": str(path), "key": item["key"], "actual": actual},
    )


def check_control_tool(check_id: str, path: pathlib.Path) -> CheckResult:
    if path.is_file() and os.access(path, os.X_OK):
        return CheckResult(
            f"tool.{check_id}",
            "pass",
            "Required control-plane tool is executable",
            f"Restore the macOS system tool at {path}.",
            {"path": str(path)},
        )
    return CheckResult(
        f"tool.{check_id}",
        "fail",
        "Required control-plane tool is unavailable",
        f"Restore the macOS system tool at {path}.",
        {"path": str(path)},
    )


def nearest_existing_ancestor(path: pathlib.Path) -> pathlib.Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def path_lexists(path: pathlib.Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def check_state_root(paths: RuntimePaths) -> CheckResult:
    remediation = f"Restore a writable, non-symlink runtime state path at {paths.state_root}."
    try:
        artifact_contract.reject_symlink_components(paths.state_root)
    except artifact_contract.ArtifactError as error:
        return CheckResult(
            "runtime.state_root",
            "fail",
            "Runtime state path contains a symlink component",
            remediation,
            artifact_failure_details(error),
        )
    if paths.state_root.exists() and not paths.state_root.is_dir():
        return CheckResult(
            "runtime.state_root",
            "fail",
            "Runtime state path is not a directory",
            remediation,
            {"path": str(paths.state_root)},
        )
    ancestor = nearest_existing_ancestor(paths.state_root)
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK | os.X_OK):
        return CheckResult(
            "runtime.state_root",
            "fail",
            "Runtime state path is not writable",
            remediation,
            {"path": str(paths.state_root), "nearestExistingAncestor": str(ancestor)},
        )
    return CheckResult(
        "runtime.state_root",
        "pass",
        "Runtime state path is safe and writable",
        remediation,
        {"path": str(paths.state_root), "nearestExistingAncestor": str(ancestor)},
    )


def doctor_runtime(context: RuntimeContext, artifact: pathlib.Path) -> DoctorReport:
    checks: list[CheckResult] = []
    artifact_summary: dict[str, Any] | None = None
    try:
        manifest, _, manifest_hash, lock_hash = load_runtime_contract(context)
    except ControlError as error:
        checks.append(
            CheckResult(
                "repository.contract",
                "fail",
                error.message,
                "Restore the checked-in runtime manifest and lockfile before continuing.",
                error.context,
            )
        )
        return DoctorReport(tuple(checks))
    checks.append(
        CheckResult(
            "repository.contract",
            "pass",
            "Checked-in runtime manifest and lockfile are valid",
            "Restore the checked-in runtime manifest and lockfile before continuing.",
            {"manifestSha256": manifest_hash, "lockSha256": lock_hash},
        )
    )

    metadata: dict[str, Any] | None = None
    artifact_path = pathlib.Path(os.path.abspath(artifact.expanduser()))
    try:
        metadata = artifact_contract.verify_artifact(artifact_path)
    except artifact_contract.ArtifactError as error:
        checks.append(
            CheckResult(
                "artifact.verify",
                "fail",
                "Sealed runtime artifact verification failed",
                "Replace the artifact with an exact verified build; do not bypass seal verification.",
                {"path": str(artifact_path), **artifact_failure_details(error)},
            )
        )
        checks.append(
            CheckResult(
                "artifact.contract",
                "unknown",
                "Artifact contract identity could not be evaluated",
                "Verify the artifact before comparing its manifest and lock identity.",
                {"path": str(artifact_path)},
            )
        )
    else:
        artifact_summary = {
            "path": str(artifact_path),
            "id": metadata["artifact"]["id"],
            "version": metadata["artifact"]["version"],
            "sealId": metadata["sealId"],
        }
        checks.append(
            CheckResult(
                "artifact.verify",
                "pass",
                "Sealed runtime artifact is exact and immutable",
                "Replace the artifact with an exact verified build; do not bypass seal verification.",
                artifact_summary,
            )
        )
        if metadata["manifestSha256"] == manifest_hash and metadata["lockSha256"] == lock_hash:
            checks.append(
                CheckResult(
                    "artifact.contract",
                    "pass",
                    "Artifact was built from the checked-in runtime contract",
                    "Rebuild the artifact from the current manifest and lockfile.",
                    {"manifestSha256": manifest_hash, "lockSha256": lock_hash},
                )
            )
        else:
            checks.append(
                CheckResult(
                    "artifact.contract",
                    "fail",
                    "Artifact runtime contract does not match this checkout",
                    "Rebuild the artifact from the current manifest and lockfile.",
                    {
                        "artifactManifestSha256": metadata["manifestSha256"],
                        "artifactLockSha256": metadata["lockSha256"],
                        "expectedManifestSha256": manifest_hash,
                        "expectedLockSha256": lock_hash,
                    },
                )
            )

    bindings: dict[str, str] | None = None
    try:
        bindings = artifact_contract.resolve_bindings(manifest, context.bindings_path, "plan")
    except artifact_contract.ArtifactError as error:
        checks.append(
            CheckResult(
                "bindings.plan",
                "fail",
                "Runtime plan bindings could not be resolved",
                "Fix .code/runtime-bindings.json or restore manifest defaults.",
                artifact_failure_details(error),
            )
        )
    else:
        checks.append(
            CheckResult(
                "bindings.plan",
                "pass",
                "Runtime plan bindings resolve without mutation",
                "Fix .code/runtime-bindings.json or restore manifest defaults.",
                {"path": str(context.bindings_path), "bindingCount": len(bindings)},
            )
        )

    for item in manifest["prerequisites"]:
        if item["kind"] == "command":
            checks.append(evaluate_command_prerequisite(item, context.runner))
        elif bindings is None:
            checks.append(
                CheckResult(
                    f"prerequisite.{item['id']}",
                    "unknown",
                    "Prerequisite could not be evaluated because bindings failed",
                    prerequisite_remediation(item),
                    {"kind": item["kind"]},
                )
            )
        else:
            checks.append(evaluate_plist_prerequisite(item, bindings))

    for tool_id, tool_path in (
        ("launchctl", pathlib.Path("/bin/launchctl")),
        ("codesign", pathlib.Path("/usr/bin/codesign")),
        ("lsof", pathlib.Path("/usr/sbin/lsof")),
        ("ps", pathlib.Path("/bin/ps")),
    ):
        checks.append(check_control_tool(tool_id, tool_path))

    if bindings is None:
        checks.append(
            CheckResult(
                "runtime.state_root",
                "unknown",
                "Runtime state path could not be evaluated because bindings failed",
                "Fix the plan bindings before starting the runtime.",
            )
        )
    else:
        try:
            paths = resolve_runtime_paths(manifest, bindings)
        except (ControlError, artifact_contract.ArtifactError) as error:
            details = error.context if isinstance(error, ControlError) else artifact_failure_details(error)
            checks.append(
                CheckResult(
                    "runtime.state_root",
                    "fail",
                    "Runtime state paths could not be resolved safely",
                    "Fix the runtime manifest and plan bindings before starting the runtime.",
                    details,
                )
            )
        else:
            checks.append(check_state_root(paths))

    return DoctorReport(tuple(checks), artifact_summary)


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def inspect_lock(path: pathlib.Path, pid_alive: Callable[[int], bool]) -> LockInspection:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return LockInspection(False, True)
    except OSError as error:
        return LockInspection(True, False, error_code="lock.unreadable", message=str(error))
    if path.is_symlink() or not path.is_dir():
        return LockInspection(
            True,
            False,
            error_code="lock.invalid_path",
            message="Runtime lock is not a real directory",
        )
    try:
        entries = tuple(sorted(item.name for item in path.iterdir()))
    except OSError as error:
        return LockInspection(True, False, error_code="lock.unreadable", message=str(error))
    pid: int | None = None
    try:
        raw_pid = (path / "pid").read_text().strip()
        if raw_pid and raw_pid.isdecimal() and int(raw_pid) > 0:
            pid = int(raw_pid)
    except OSError:
        pid = None
    run_dir: str | None = None
    try:
        run_dir = (path / "run-dir").read_text().strip() or None
    except OSError:
        run_dir = None
    alive = pid_alive(pid) if pid is not None else False
    error_code = None if pid is not None else "lock.incomplete"
    message = None if pid is not None else "Runtime lock has no valid owner PID"
    return LockInspection(
        True,
        True,
        pid=pid,
        alive=alive,
        run_dir=run_dir,
        entries=entries,
        error_code=error_code,
        message=message,
    )


def parse_launchd_value(output: str, key: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*?)\s*$")
    for line in output.splitlines():
        match = pattern.match(line)
        if match:
            value = match.group(1)
            if len(value) >= 2 and value[0] == value[-1] == '"':
                return value[1:-1]
            return value
    return None


def parse_positive_int(value: str | None) -> int | None:
    if value is None or not value.isdecimal():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def launchd_service_not_found(result: CommandResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    return "could not find service" in text or "service not found" in text or "no such process" in text


def read_launchd_snapshot(paths: RuntimePaths, runner: CommandRunner) -> LaunchdSnapshot:
    result = runner.run(["/bin/launchctl", "print", paths.service_target], timeout=5.0)
    if result.error is not None:
        return LaunchdSnapshot(
            False,
            error_code=f"launchd.{result.error}",
            message="Launchd service state could not be read",
        )
    if result.returncode != 0:
        if launchd_service_not_found(result):
            return LaunchdSnapshot(False, raw=f"{result.stdout}{result.stderr}")
        return LaunchdSnapshot(
            False,
            raw=f"{result.stdout}{result.stderr}",
            error_code="launchd.query_failed",
            message="Launchd service query failed",
        )
    output = result.stdout
    return LaunchdSnapshot(
        True,
        path=parse_launchd_value(output, "path"),
        program=parse_launchd_value(output, "program"),
        pid=parse_positive_int(parse_launchd_value(output, "pid")),
        state=parse_launchd_value(output, "state"),
        runs=parse_positive_int(parse_launchd_value(output, "runs")),
        raw=output,
    )


def paths_match(left: str | pathlib.Path | None, right: str | pathlib.Path | None) -> bool:
    if left is None or right is None:
        return False
    left_path = pathlib.Path(left).expanduser()
    right_path = pathlib.Path(right).expanduser()
    try:
        if left_path.exists() and right_path.exists():
            return os.path.samefile(left_path, right_path)
    except OSError:
        return False
    return os.path.abspath(left_path) == os.path.abspath(right_path)


def codesign_identity(target: str | pathlib.Path, runner: CommandRunner) -> tuple[dict[str, Any] | None, str | None]:
    result = runner.run(["/usr/bin/codesign", "-dv", "--verbose=4", str(target)], timeout=10.0)
    if result.error is not None or result.returncode != 0:
        return None, "Code-sign identity could not be read"
    payload = f"{result.stdout}\n{result.stderr}"
    identity: dict[str, Any] = {}
    for key in ("Identifier", "TeamIdentifier"):
        match = re.search(rf"^{key}=(.*)$", payload, flags=re.MULTILINE)
        if match:
            identity[key] = match.group(1).strip()
    cd_hashes = sorted(set(re.findall(r"^CDHash=(.*)$", payload, flags=re.MULTILINE)))
    identity["CDHashes"] = cd_hashes
    if not identity.get("Identifier") or not cd_hashes:
        return None, "Code-sign identity is incomplete"
    identity.setdefault("TeamIdentifier", "")
    return identity, None


def verify_codesign_target(target: str | pathlib.Path, runner: CommandRunner) -> str | None:
    result = runner.run(["/usr/bin/codesign", "--verify", "--strict", str(target)], timeout=10.0)
    if result.error is not None or result.returncode != 0:
        return "Code-sign verification failed"
    return None


def identity_record(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "identifier": identity["Identifier"],
        "teamIdentifier": identity["TeamIdentifier"],
        "cdHashes": identity["CDHashes"],
    }


def live_program_for_pid(
    pid: int,
    expected_program: pathlib.Path,
    runner: CommandRunner,
) -> tuple[pathlib.Path | None, str | None]:
    result = runner.run(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "txt", "-Fn"], timeout=10.0)
    if result.error is not None or result.returncode != 0:
        return None, "Live executable path could not be read"
    candidates: list[pathlib.Path] = []
    for line in result.stdout.splitlines():
        if line.startswith("n") and len(line) > 1:
            candidate = pathlib.Path(line[1:])
            candidates.append(candidate)
            if paths_match(candidate, expected_program):
                return candidate, None
    if candidates:
        return None, "No live text mapping matches the installed bridge"
    return None, "Live executable path is missing"


def process_start_time(pid: int, runner: CommandRunner) -> tuple[str | None, str | None]:
    result = runner.run(["/bin/ps", "-p", str(pid), "-o", "lstart="], timeout=5.0)
    if result.error is not None or result.returncode != 0:
        return None, "Owner process start time could not be read"
    started_at = result.stdout.strip()
    if not started_at:
        return None, "Owner process start time is missing"
    return started_at, None


def validate_bridge_owner_marker(paths: RuntimePaths) -> tuple[bool, str | None]:
    try:
        metadata = paths.bridge_owner_marker.lstat()
    except FileNotFoundError:
        return False, "Installed bridge ownership marker is missing"
    except OSError as error:
        return False, f"Installed bridge ownership marker is unreadable: {error}"
    if paths.bridge_owner_marker.is_symlink() or not paths.bridge_owner_marker.is_file():
        return False, "Installed bridge ownership marker is not a real file"
    try:
        payload = artifact_contract.load_json(paths.bridge_owner_marker)
    except artifact_contract.ArtifactError as error:
        return False, f"Installed bridge ownership marker is invalid: {error.code}"
    if payload != paths.bridge_owner_content:
        return False, "Installed bridge ownership marker does not match the runtime contract"
    return True, None


def inspect_service(paths: RuntimePaths, runner: CommandRunner) -> ServiceInspection:
    snapshot = read_launchd_snapshot(paths, runner)
    if snapshot.error_code is not None:
        return ServiceInspection(
            snapshot,
            False,
            False,
            error_code=snapshot.error_code,
            message=snapshot.message,
        )
    if not snapshot.present:
        return ServiceInspection(snapshot, False, False)
    marker_valid, marker_error = validate_bridge_owner_marker(paths)
    if not marker_valid:
        return ServiceInspection(
            snapshot,
            False,
            False,
            error_code="service.owner_marker_mismatch",
            message=marker_error,
        )
    for expected_path in (paths.launch_agent_plist, paths.bridge_program):
        try:
            artifact_contract.reject_symlink_components(expected_path)
        except artifact_contract.ArtifactError as error:
            return ServiceInspection(
                snapshot,
                False,
                False,
                error_code="service.path_unsafe",
                message=f"Expected runtime service path is unsafe: {error.context.get('path', expected_path)}",
            )
    if not paths_match(snapshot.path, paths.launch_agent_plist) or not paths_match(
        snapshot.program, paths.bridge_program
    ):
        return ServiceInspection(
            snapshot,
            False,
            False,
            error_code="service.foreign",
            message="Launchd service path or program is not owned by this runtime",
        )
    if snapshot.state != "running" or snapshot.pid is None:
        return ServiceInspection(snapshot, True, True)
    live_program, live_error = live_program_for_pid(snapshot.pid, paths.bridge_program, runner)
    if live_program is None:
        return ServiceInspection(
            snapshot,
            True,
            False,
            error_code="service.identity_unknown",
            message=live_error,
        )
    if not paths_match(live_program, paths.bridge_program):
        return ServiceInspection(
            snapshot,
            True,
            False,
            live_program=str(live_program),
            error_code="service.identity_mismatch",
            message="Live launchd executable does not match the installed bridge",
        )
    expected_verify_error = verify_codesign_target(paths.bridge_program, runner)
    actual_verify_error = verify_codesign_target(f"+{snapshot.pid}", runner)
    if expected_verify_error is not None or actual_verify_error is not None:
        return ServiceInspection(
            snapshot,
            True,
            False,
            live_program=str(live_program),
            error_code="service.signature_invalid",
            message=expected_verify_error or actual_verify_error,
        )
    expected_identity, expected_error = codesign_identity(paths.bridge_program, runner)
    actual_identity, actual_error = codesign_identity(f"+{snapshot.pid}", runner)
    if expected_identity is None or actual_identity is None:
        return ServiceInspection(
            snapshot,
            True,
            False,
            live_program=str(live_program),
            file_identity=expected_identity,
            live_identity=actual_identity,
            error_code="service.signature_unknown",
            message=expected_error or actual_error,
        )
    identity_matches = (
        expected_identity["Identifier"] == actual_identity["Identifier"]
        and expected_identity["TeamIdentifier"] == actual_identity["TeamIdentifier"]
        and bool(set(expected_identity["CDHashes"]) & set(actual_identity["CDHashes"]))
    )
    if not identity_matches:
        return ServiceInspection(
            snapshot,
            True,
            False,
            live_program=str(live_program),
            file_identity=expected_identity,
            live_identity=actual_identity,
            error_code="service.signature_mismatch",
            message="Live launchd executable signature does not match the installed bridge",
        )
    return ServiceInspection(
        snapshot,
        True,
        True,
        live_program=str(live_program),
        file_identity=expected_identity,
        live_identity=actual_identity,
    )


def load_control_state(path: pathlib.Path) -> ControlStateInspection:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return ControlStateInspection(False, True)
    except OSError as error:
        return ControlStateInspection(True, False, error_code="state.unreadable", message=str(error))
    if path.is_symlink() or not path.is_file():
        return ControlStateInspection(
            True,
            False,
            error_code="state.invalid_path",
            message="Control state is not a real file",
        )
    try:
        record = artifact_contract.load_json(path)
    except artifact_contract.ArtifactError as error:
        return ControlStateInspection(
            True,
            False,
            error_code="state.invalid_json",
            message=f"{error.code}: {error.message}",
        )
    required = {
        "schemaVersion",
        "state",
        "generation",
        "ownerPid",
        "ownerStartedAt",
        "serviceLabel",
        "servicePid",
        "artifactPath",
        "artifactSeal",
        "bridgeIdentity",
    }
    allowed = required | {"updatedAt", "diagnostic"}
    if not isinstance(record, dict) or set(record) - allowed or required - set(record):
        return ControlStateInspection(
            True,
            False,
            error_code="state.invalid_schema",
            message="Control state keys do not match schema version 1",
        )
    valid = (
        record["schemaVersion"] == 1
        and record["state"] in LIVE_STATES
        and isinstance(record["generation"], int)
        and record["generation"] > 0
        and isinstance(record["ownerPid"], int)
        and record["ownerPid"] > 0
        and isinstance(record["ownerStartedAt"], str)
        and bool(record["ownerStartedAt"])
        and isinstance(record["serviceLabel"], str)
        and isinstance(record["servicePid"], int)
        and record["servicePid"] > 0
        and isinstance(record["artifactPath"], str)
        and pathlib.Path(record["artifactPath"]).is_absolute()
        and isinstance(record["artifactSeal"], str)
        and SHA256_PATTERN.fullmatch(record["artifactSeal"]) is not None
        and isinstance(record["bridgeIdentity"], dict)
        and set(record["bridgeIdentity"]) == {"identifier", "teamIdentifier", "cdHashes"}
        and isinstance(record["bridgeIdentity"]["identifier"], str)
        and bool(record["bridgeIdentity"]["identifier"])
        and isinstance(record["bridgeIdentity"]["teamIdentifier"], str)
        and isinstance(record["bridgeIdentity"]["cdHashes"], list)
        and bool(record["bridgeIdentity"]["cdHashes"])
        and all(
            isinstance(value, str) and bool(value)
            for value in record["bridgeIdentity"]["cdHashes"]
        )
    )
    if not valid:
        return ControlStateInspection(
            True,
            False,
            error_code="state.invalid_schema",
            message="Control state values do not match schema version 1",
        )
    return ControlStateInspection(True, True, record=record)


def failed_status(
    reason_code: str,
    message: str,
    service: ServiceInspection,
    lock: LockInspection,
    control_state: ControlStateInspection,
    diagnostics: Sequence[CheckResult] = (),
    artifact: dict[str, Any] | None = None,
) -> StatusReport:
    return StatusReport(
        "failed",
        reason_code,
        message,
        artifact,
        service.to_dict(),
        lock.to_dict(),
        control_state.to_dict(),
        tuple(diagnostics),
    )


def resolve_context_paths(
    context: RuntimeContext,
) -> tuple[dict[str, Any], dict[str, str], RuntimePaths]:
    manifest, _, _, _ = load_runtime_contract(context)
    try:
        bindings = artifact_contract.resolve_bindings(manifest, context.bindings_path, "plan")
    except artifact_contract.ArtifactError as error:
        raise ControlError(
            "bindings.plan",
            "Runtime plan bindings could not be resolved",
            artifactError=artifact_failure_details(error),
        ) from error
    try:
        paths = resolve_runtime_paths(manifest, bindings)
    except artifact_contract.ArtifactError as error:
        raise ControlError(
            error.code,
            error.message,
            artifactError=artifact_failure_details(error),
        ) from error
    return manifest, bindings, paths


def verify_artifact_reference(context: RuntimeContext, artifact: pathlib.Path) -> dict[str, Any]:
    _, _, manifest_hash, lock_hash = load_runtime_contract(context)
    artifact_path = pathlib.Path(os.path.abspath(artifact.expanduser()))
    try:
        metadata = artifact_contract.verify_artifact(artifact_path)
    except artifact_contract.ArtifactError as error:
        raise ControlError(
            "artifact.invalid",
            "Runtime state references an invalid sealed artifact",
            artifactError=artifact_failure_details(error),
            path=str(artifact_path),
        ) from error
    if metadata["manifestSha256"] != manifest_hash or metadata["lockSha256"] != lock_hash:
        raise ControlError(
            "artifact.contract_mismatch",
            "Runtime state artifact belongs to another manifest or lockfile",
            path=str(artifact_path),
            artifactManifestSha256=metadata["manifestSha256"],
            artifactLockSha256=metadata["lockSha256"],
            expectedManifestSha256=manifest_hash,
            expectedLockSha256=lock_hash,
        )
    return {
        "path": str(artifact_path),
        "id": metadata["artifact"]["id"],
        "version": metadata["artifact"]["version"],
        "sealId": metadata["sealId"],
    }


def status_runtime(context: RuntimeContext, artifact: pathlib.Path | None = None) -> StatusReport:
    try:
        _, _, paths = resolve_context_paths(context)
    except ControlError as error:
        empty_service = ServiceInspection(LaunchdSnapshot(False), False, False)
        empty_lock = LockInspection(False, True)
        empty_state = ControlStateInspection(False, True)
        return failed_status(error.code, error.message, empty_service, empty_lock, empty_state)

    service = inspect_service(paths, context.runner)
    lock = inspect_lock(paths.lock_path, context.pid_alive)
    control_state = load_control_state(paths.state_path)
    if service.error_code is not None:
        return failed_status(
            service.error_code,
            service.message or "Launchd service identity is invalid",
            service,
            lock,
            control_state,
        )

    if service.snapshot.present:
        if not paths.launch_agent_plist.is_file():
            return failed_status(
                "service.plist_missing",
                "Owned launchd service is loaded without its registered plist",
                service,
                lock,
                control_state,
            )
        plist_owned, plist_error = validate_plist_ownership(paths)
        if not plist_owned:
            return failed_status(
                "service.plist_foreign",
                plist_error or "Launch agent plist is not owned by this runtime",
                service,
                lock,
                control_state,
            )
        if service.snapshot.state != "running" or service.snapshot.pid is None:
            return failed_status(
                "service.not_running",
                "Owned launchd service is loaded but not running",
                service,
                lock,
                control_state,
            )
        if not lock.exists or not lock.valid_path or lock.pid is None or not lock.alive:
            return failed_status(
                "owner.missing",
                "Owned launchd service has no live global owner lock",
                service,
                lock,
                control_state,
            )
        if not control_state.exists or not control_state.valid or control_state.record is None:
            return failed_status(
                control_state.error_code or "state.missing",
                control_state.message or "Owned launchd service has no synchronized control state",
                service,
                lock,
                control_state,
            )
        record = control_state.record
        if (
            record["ownerPid"] != lock.pid
            or record["serviceLabel"] != paths.service_label
            or record["servicePid"] != service.snapshot.pid
        ):
            return failed_status(
                "state.identity_mismatch",
                "Control state does not match the live owner and launchd service",
                service,
                lock,
                control_state,
            )
        started_at, start_error = process_start_time(lock.pid, context.runner)
        if started_at is None or started_at != record["ownerStartedAt"]:
            return failed_status(
                "owner.identity_mismatch",
                start_error or "Live owner process start time does not match control state",
                service,
                lock,
                control_state,
            )
        if service.file_identity is None or service.live_identity is None:
            return failed_status(
                "service.signature_unknown",
                "Live service signature identity is unavailable",
                service,
                lock,
                control_state,
            )
        expected_bridge_identity = {
            **record["bridgeIdentity"],
            "cdHashes": sorted(set(record["bridgeIdentity"]["cdHashes"])),
        }
        if (
            identity_record(service.file_identity) != expected_bridge_identity
            or identity_record(service.live_identity) != expected_bridge_identity
        ):
            return failed_status(
                "state.signature_mismatch",
                "Control state bridge identity does not match the installed and live bridge",
                service,
                lock,
                control_state,
            )
        try:
            artifact_summary = verify_artifact_reference(context, pathlib.Path(record["artifactPath"]))
        except ControlError as error:
            return failed_status(
                error.code,
                error.message,
                service,
                lock,
                control_state,
            )
        if artifact_summary["sealId"] != record["artifactSeal"]:
            return failed_status(
                "state.artifact_mismatch",
                "Control state artifact seal does not match its verified artifact",
                service,
                lock,
                control_state,
                artifact=artifact_summary,
            )
        if artifact is not None:
            try:
                requested_artifact = verify_artifact_reference(context, artifact)
            except ControlError as error:
                return failed_status(
                    error.code,
                    error.message,
                    service,
                    lock,
                    control_state,
                    artifact=artifact_summary,
                )
            if requested_artifact["sealId"] != record["artifactSeal"]:
                return failed_status(
                    "state.artifact_mismatch",
                    "Control state artifact seal does not match the requested artifact",
                    service,
                    lock,
                    control_state,
                    artifact=artifact_summary,
                )
        return StatusReport(
            record["state"],
            f"runtime.{record['state']}",
            f"Runtime is {record['state']} with synchronized live identity",
            artifact_summary,
            service.to_dict(),
            lock.to_dict(),
            control_state.to_dict(),
        )

    stale_paths = []
    if lock.exists:
        stale_paths.append(str(paths.lock_path))
    if control_state.exists:
        stale_paths.append(str(paths.state_path))
    if path_lexists(paths.launch_agent_plist):
        stale_paths.append(str(paths.launch_agent_plist))
    if stale_paths:
        return failed_status(
            "runtime.stale_state",
            "Runtime is stopped but transient owned state remains",
            service,
            lock,
            control_state,
            (
                CheckResult(
                    "runtime.stale_state",
                    "fail",
                    "Transient runtime state remains without a launchd job",
                    "Run the idempotent stop command to remove validated stale state.",
                    {"paths": stale_paths},
                ),
            ),
        )

    if artifact is None:
        return StatusReport(
            "stopped",
            "runtime.stopped",
            "Runtime is stopped and has no transient owned state",
            None,
            service.to_dict(),
            lock.to_dict(),
            control_state.to_dict(),
        )

    doctor = doctor_runtime(context, artifact)
    core_checks = {
        check.id: check.status
        for check in doctor.checks
        if check.id in {"repository.contract", "artifact.verify", "artifact.contract"}
    }
    if any(
        core_checks.get(check_id) != "pass"
        for check_id in ("repository.contract", "artifact.verify", "artifact.contract")
    ):
        return failed_status(
            "artifact.invalid",
            "Requested runtime artifact is invalid or belongs to another contract",
            service,
            lock,
            control_state,
            doctor.checks,
            doctor.artifact,
        )
    state = "ready" if doctor.ok else "installed"
    reason = "runtime.ready" if doctor.ok else "runtime.prerequisites_incomplete"
    message = (
        "Runtime artifact and host prerequisites are ready"
        if doctor.ok
        else "Runtime artifact is installed but one or more readiness checks do not pass"
    )
    return StatusReport(
        state,
        reason,
        message,
        doctor.artifact,
        service.to_dict(),
        lock.to_dict(),
        control_state.to_dict(),
        doctor.checks,
    )


def validate_plist_ownership(paths: RuntimePaths) -> tuple[bool, str | None]:
    path = paths.launch_agent_plist
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return True, None
    except OSError as error:
        return False, str(error)
    if path.is_symlink() or not path.is_file():
        return False, "Launch agent plist is not a real file"
    try:
        with path.open("rb") as stream:
            payload = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        return False, f"Launch agent plist is unreadable: {error}"
    arguments = payload.get("ProgramArguments")
    mach_services = payload.get("MachServices")
    associated_bundles = payload.get("AssociatedBundleIdentifiers")
    environment = payload.get("EnvironmentVariables")
    standard_output = payload.get("StandardOutPath")
    standard_error = payload.get("StandardErrorPath")
    marker_valid, marker_error = validate_bridge_owner_marker(paths)
    if not marker_valid:
        return False, marker_error
    owned = (
        payload.get("Label") == paths.service_label
        and isinstance(arguments, list)
        and arguments == [str(paths.bridge_program)]
        and mach_services == {paths.service_label: True}
        and associated_bundles == [paths.service_label]
        and payload.get("ProcessType") == "Interactive"
        and isinstance(environment, dict)
        and environment.get("ALVR_IOSURFACE_POOL_SERVICE") == paths.service_label
        and environment.get("ALVR_BRIDGE_INPUT") == "iosurface"
        and isinstance(environment.get("ALVR_IOSURFACE_POOL_NONCE"), str)
        and environment["ALVR_IOSURFACE_POOL_NONCE"].isdecimal()
        and isinstance(standard_output, str)
        and pathlib.Path(standard_output).is_absolute()
        and standard_output == standard_error
    )
    return (True, None) if owned else (False, "Launch agent plist ownership fields do not match")


def validate_lock_removal(lock: LockInspection) -> tuple[bool, str | None]:
    if not lock.exists:
        return True, None
    if not lock.valid_path:
        return False, lock.message or "Runtime lock path is invalid"
    if lock.alive:
        return False, f"Runtime lock owner pid={lock.pid} is still alive"
    unknown_entries = sorted(set(lock.entries) - {"pid", "run-dir"})
    if unknown_entries:
        return False, f"Runtime lock contains unexpected entries: {', '.join(unknown_entries)}"
    return True, None


def validate_state_removal(path: pathlib.Path) -> tuple[bool, str | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return True, None
    except OSError as error:
        return False, str(error)
    if path.is_symlink() or not path.is_file():
        return False, "Control state path is not a real file"
    return True, None


def remove_stale_lock(path: pathlib.Path) -> None:
    for name in ("pid", "run-dir"):
        try:
            (path / name).unlink()
        except FileNotFoundError:
            pass
    path.rmdir()


def stop_failure(
    code: str,
    message: str,
    actions: Sequence[str],
    service: ServiceInspection,
    lock: LockInspection,
) -> StopReport:
    return StopReport(
        False,
        "failed",
        code,
        message,
        tuple(actions),
        service.to_dict(),
        lock.to_dict(),
    )


def stop_runtime(context: RuntimeContext) -> StopReport:
    actions: list[str] = []
    try:
        _, _, paths = resolve_context_paths(context)
    except ControlError as error:
        return stop_failure(
            error.code,
            error.message,
            actions,
            ServiceInspection(LaunchdSnapshot(False), False, False),
            LockInspection(False, True),
        )

    service = inspect_service(paths, context.runner)
    lock = inspect_lock(paths.lock_path, context.pid_alive)
    if service.error_code is not None:
        return stop_failure(
            service.error_code,
            service.message or "Launchd service identity is invalid",
            actions,
            service,
            lock,
        )

    service_was_owned = service.snapshot.present and service.owned and service.identity_valid
    if service.snapshot.present:
        registered_path = service.snapshot.path
        if not service_was_owned or registered_path is None:
            return stop_failure(
                "service.foreign",
                "Refusing to stop a launchd job without exact runtime ownership",
                actions,
                service,
                lock,
            )
        plist_owned, plist_error = validate_plist_ownership(paths)
        if not plist_owned:
            return stop_failure(
                "service.plist_foreign",
                plist_error or "Launch agent plist is not owned by this runtime",
                actions,
                service,
                lock,
            )
        result = context.runner.run(
            ["/bin/launchctl", "bootout", paths.service_domain, registered_path],
            timeout=10.0,
        )
        if result.error is not None:
            return stop_failure(
                f"launchd.{result.error}",
                "Owned launchd job could not be booted out",
                actions,
                service,
                lock,
            )
        actions.append(f"bootout {paths.service_target}")
        service_after = inspect_service(paths, context.runner)
        for _ in range(50):
            if service_after.error_code is not None or not service_after.snapshot.present:
                break
            context.sleeper(0.1)
            service_after = inspect_service(paths, context.runner)
        if service_after.error_code is not None:
            return stop_failure(
                service_after.error_code,
                service_after.message or "Launchd service state could not be verified after bootout",
                actions,
                service_after,
                lock,
            )
        if service_after.snapshot.present:
            return stop_failure(
                "launchd.bootout_failed",
                "Owned launchd job remained after bootout",
                actions,
                service_after,
                lock,
            )
        service = service_after

    lock = inspect_lock(paths.lock_path, context.pid_alive)
    lock_safe, lock_error = validate_lock_removal(lock)
    if not lock_safe:
        return stop_failure("lock.active_or_foreign", lock_error or "Runtime lock is not removable", actions, service, lock)
    plist_safe, plist_error = (True, None) if service_was_owned else validate_plist_ownership(paths)
    if not plist_safe:
        return stop_failure(
            "service.plist_foreign",
            plist_error or "Launch agent plist is not owned by this runtime",
            actions,
            service,
            lock,
        )
    state_safe, state_error = validate_state_removal(paths.state_path)
    if not state_safe:
        return stop_failure(
            "state.foreign",
            state_error or "Control state is not removable",
            actions,
            service,
            lock,
        )

    cleanup_errors: list[str] = []
    if path_lexists(paths.state_path):
        try:
            paths.state_path.unlink()
            actions.append(f"remove {paths.state_path}")
        except OSError as error:
            cleanup_errors.append(f"{paths.state_path}: {error}")
    if path_lexists(paths.launch_agent_plist):
        try:
            paths.launch_agent_plist.unlink()
            actions.append(f"remove {paths.launch_agent_plist}")
        except OSError as error:
            cleanup_errors.append(f"{paths.launch_agent_plist}: {error}")
    if lock.exists:
        try:
            remove_stale_lock(paths.lock_path)
            actions.append(f"remove {paths.lock_path}")
        except OSError as error:
            cleanup_errors.append(f"{paths.lock_path}: {error}")
    if cleanup_errors:
        return stop_failure(
            "runtime.cleanup_failed",
            "Owned runtime state could not be removed: " + "; ".join(cleanup_errors),
            actions,
            service,
            inspect_lock(paths.lock_path, context.pid_alive),
        )

    final_service = inspect_service(paths, context.runner)
    final_lock = inspect_lock(paths.lock_path, context.pid_alive)
    final_state_present = path_lexists(paths.state_path)
    final_plist_present = path_lexists(paths.launch_agent_plist)
    if (
        final_service.error_code is not None
        or final_service.snapshot.present
        or final_lock.exists
        or final_state_present
        or final_plist_present
    ):
        return stop_failure(
            "runtime.stop_incomplete",
            "Runtime owned state remained after stop"
            f" (state={final_state_present}, plist={final_plist_present})",
            actions,
            final_service,
            final_lock,
        )
    return StopReport(
        True,
        "stopped",
        "runtime.stopped",
        "Runtime is stopped and transient owned state is absent",
        tuple(actions),
        final_service.to_dict(),
        final_lock.to_dict(),
    )
