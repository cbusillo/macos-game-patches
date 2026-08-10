"""Bounded, prompt-free Launch Services consent staging for signed macOS apps."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import plistlib
import re
import stat
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Sequence

import runtime_control


PRODUCTION_BUNDLE_ID = "com.alvr.macos-bridge.iosurface"
PRODUCTION_EXECUTABLE = "alvr_macos_bridge"
PRODUCTION_SERVICE = PRODUCTION_BUNDLE_ID
PROCESS_LIST_COMMAND = (
    "/usr/bin/env",
    "LC_ALL=C",
    "/bin/ps",
    "-axo",
    "pid=,comm=",
)
SERVICE_QUERY_PREFIX = ("/bin/launchctl", "print")
QUARANTINE_XATTR = "com.apple.quarantine"
MAX_RECORDED_OUTPUT = 1024


class MatrixError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class BundleEvidence:
    path: pathlib.Path
    bundle_id: str
    executable: str
    executable_path: pathlib.Path
    executable_sha256: str
    codesign_identifier: str
    team_id: str
    cd_hash: str
    uuid: str
    tree_hash: str
    entries: tuple[dict[str, Any], ...]
    quarantine: tuple[str, ...]

    def identity(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "bundleId": self.bundle_id,
            "executable": self.executable,
            "executablePath": str(self.executable_path),
            "executableSha256": self.executable_sha256,
            "codesignIdentifier": self.codesign_identifier,
            "teamId": self.team_id,
            "cdHash": self.cd_hash,
            "uuid": self.uuid,
            "treeHash": self.tree_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.identity(),
            "entries": list(self.entries),
            "quarantine": list(self.quarantine),
        }


def _path(value: str | pathlib.Path, *, role: str, require_exists: bool = True) -> pathlib.Path:
    candidate = pathlib.Path(value).expanduser()
    if not candidate.is_absolute():
        raise MatrixError(
            "consent_stage.path_refused",
            f"{role} must be an absolute path",
            path=str(value),
            reason="not_absolute",
        )
    candidate = pathlib.Path(os.path.abspath(candidate))
    _reject_symlink_components(candidate, role=role, require_exists=require_exists)
    if require_exists and not candidate.exists():
        raise MatrixError(
            "consent_stage.path_refused",
            f"{role} does not exist",
            path=str(candidate),
            reason="missing",
        )
    return candidate


def _reject_symlink_components(
    path: pathlib.Path, *, role: str, require_exists: bool
) -> None:
    current = pathlib.Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            if require_exists:
                raise MatrixError(
                    "consent_stage.path_refused",
                    f"{role} has a missing path component",
                    path=str(path),
                    component=str(current),
                    reason="missing_component",
                ) from None
            break
        except OSError as error:
            raise MatrixError(
                "consent_stage.path_refused",
                f"{role} path could not be inspected",
                path=str(path),
                component=str(current),
                reason="inspection_failed",
                error=str(error),
            ) from error
        if stat.S_ISLNK(info.st_mode):
            raise MatrixError(
                "consent_stage.path_refused",
                f"{role} contains a symlink component",
                path=str(path),
                component=str(current),
                reason="symlink",
            )


def _record_command(
    commands: list[dict[str, Any]],
    argv: Sequence[str],
    result: runtime_control.CommandResult | None = None,
) -> None:
    entry: dict[str, Any] = {"argv": [str(item) for item in argv]}
    if result is None:
        entry["executed"] = False
    else:
        entry.update(
            {
                "executed": True,
                "returncode": result.returncode,
                "error": result.error,
                "stdoutBytes": len(result.stdout.encode()),
                "stderrBytes": len(result.stderr.encode()),
            }
        )
        if result.stderr:
            entry["stderr"] = _bounded_output(result.stderr)
    commands.append(entry)


def _bounded_output(value: str) -> str:
    if len(value) <= MAX_RECORDED_OUTPUT:
        return value
    return f"{value[:MAX_RECORDED_OUTPUT]}...<truncated {len(value) - MAX_RECORDED_OUTPUT} chars>"


def _run(
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
    argv: Sequence[str],
    *,
    timeout: float = 30.0,
) -> runtime_control.CommandResult:
    result = runner.run(argv, timeout=timeout)
    _record_command(commands, argv, result)
    return result


def _read_plist(path: pathlib.Path) -> tuple[str, str]:
    plist_path = path / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as stream:
            payload = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        raise MatrixError(
            "consent_stage.bundle_invalid",
            "Bundle Info.plist could not be read",
            path=str(path),
            plist=str(plist_path),
            error=str(error),
        ) from error
    bundle_id = payload.get("CFBundleIdentifier")
    executable = payload.get("CFBundleExecutable")
    if not isinstance(bundle_id, str) or not bundle_id:
        raise MatrixError(
            "consent_stage.bundle_invalid",
            "Bundle Info.plist has no usable bundle identifier",
            path=str(path),
        )
    if not isinstance(executable, str) or not executable or "/" in executable:
        raise MatrixError(
            "consent_stage.bundle_invalid",
            "Bundle Info.plist has no safe executable name",
            path=str(path),
            executable=executable,
        )
    return bundle_id, executable


def _tree_entries(path: pathlib.Path) -> tuple[tuple[dict[str, Any], ...], str, tuple[str, ...]]:
    hasher = hashlib.sha256()
    entries: list[dict[str, Any]] = []
    quarantine: list[str] = []
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            info = current.lstat()
        except OSError as error:
            raise MatrixError(
                "consent_stage.tree_unsafe",
                "Bundle tree could not be inspected",
                path=str(path),
                entry=str(current),
                error=str(error),
            ) from error
        relative = "." if current == path else current.relative_to(path).as_posix()
        mode = stat.S_IMODE(info.st_mode)
        entry = {
            "path": relative,
            "kind": "directory" if stat.S_ISDIR(info.st_mode) else "file",
            "mode": oct(mode),
            "uid": info.st_uid,
            "gid": info.st_gid,
            "size": info.st_size,
        }
        if stat.S_ISLNK(info.st_mode):
            raise MatrixError(
                "consent_stage.tree_unsafe",
                "Bundle tree contains a symlink",
                path=str(path),
                entry=relative,
            )
        if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
            raise MatrixError(
                "consent_stage.tree_unsafe",
                "Bundle tree contains a non-regular entry",
                path=str(path),
                entry=relative,
                mode=oct(info.st_mode),
            )
        if mode & 0o022:
            raise MatrixError(
                "consent_stage.tree_unsafe",
                "Bundle tree is writable by group or other users",
                path=str(path),
                entry=relative,
                mode=oct(mode),
            )
        if info.st_uid not in {os.getuid(), 0}:
            raise MatrixError(
                "consent_stage.tree_unsafe",
                "Bundle tree is not owned by the operator or root",
                path=str(path),
                entry=relative,
                uid=info.st_uid,
            )
        listxattr = getattr(os, "listxattr", None)
        if listxattr is None:
            xattrs = []
        else:
            try:
                xattrs = listxattr(current, follow_symlinks=False)
            except (AttributeError, OSError) as error:
                if isinstance(error, OSError) and error.errno in {getattr(os, "ENOTSUP", 95), 45}:
                    xattrs = []
                else:
                    raise MatrixError(
                        "consent_stage.xattr_query_failed",
                        "Bundle extended attributes could not be inspected",
                        path=str(path),
                        entry=relative,
                        error=str(error),
                    ) from error
        if QUARANTINE_XATTR in xattrs:
            quarantine.append(relative)
        encoded = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
        if stat.S_ISREG(info.st_mode):
            try:
                with current.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        hasher.update(chunk)
            except OSError as error:
                raise MatrixError(
                    "consent_stage.tree_unsafe",
                    "Bundle file could not be read for hashing",
                    path=str(path),
                    entry=relative,
                    error=str(error),
                ) from error
        entries.append(entry)
        if stat.S_ISDIR(info.st_mode):
            try:
                children = sorted(current.iterdir(), key=lambda item: item.name, reverse=True)
            except OSError as error:
                raise MatrixError(
                    "consent_stage.tree_unsafe",
                    "Bundle directory could not be enumerated",
                    path=str(path),
                    entry=relative,
                    error=str(error),
                ) from error
            pending.extend(children)
    entries.sort(key=lambda item: item["path"])
    return tuple(entries), hasher.hexdigest(), tuple(sorted(quarantine))


def _require_private_parent(path: pathlib.Path) -> None:
    parent = path.parent
    try:
        info = parent.stat()
    except OSError as error:
        raise MatrixError(
            "consent_stage.path_refused",
            "Lane staging root could not be inspected",
            path=str(parent),
            reason="parent_inspection_failed",
            error=str(error),
        ) from error
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid != os.getuid() or mode & 0o077 or mode & 0o700 != 0o700:
        raise MatrixError(
            "consent_stage.path_refused",
            "Lane staging root must be private and operator-owned",
            path=str(parent),
            reason="parent_not_private",
            uid=info.st_uid,
            mode=oct(mode),
        )


def _sha256_file(path: pathlib.Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
    except OSError as error:
        raise MatrixError(
            "consent_stage.tree_unsafe",
            "Bundle executable could not be hashed",
            path=str(path),
            error=str(error),
        ) from error
    return hasher.hexdigest()


def _codesign_identity(
    path: pathlib.Path,
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> tuple[str, str, str]:
    verification = _run(
        runner,
        commands,
        ("/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(path)),
    )
    if verification.error is not None or verification.returncode != 0:
        raise MatrixError(
            "consent_stage.signature_invalid",
            "Bundle code signature verification failed",
            path=str(path),
            returncode=verification.returncode,
            error=verification.error,
            stderr=verification.stderr.strip(),
        )
    display = _run(
        runner,
        commands,
        ("/usr/bin/codesign", "-dv", "--verbose=4", str(path)),
    )
    if display.error is not None or display.returncode != 0:
        raise MatrixError(
            "consent_stage.signature_invalid",
            "Bundle code-sign identity could not be read",
            path=str(path),
            returncode=display.returncode,
            error=display.error,
            stderr=display.stderr.strip(),
        )
    fields: dict[str, str] = {}
    for line in display.stderr.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    identifier = fields.get("Identifier")
    team_id = fields.get("TeamIdentifier")
    cd_hash = fields.get("CDHash")
    if not identifier or not team_id or not cd_hash or not re.fullmatch(r"[0-9a-fA-F]{40}", cd_hash):
        raise MatrixError(
            "consent_stage.signature_invalid",
            "Bundle code-sign identity is incomplete",
            path=str(path),
            fields=fields,
        )
    return identifier, team_id, cd_hash.lower()


def _mach_uuid(
    executable: pathlib.Path,
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> str:
    result = _run(runner, commands, ("/usr/bin/otool", "-l", str(executable)))
    if result.error is not None or result.returncode != 0:
        raise MatrixError(
            "consent_stage.uuid_missing",
            "Mach-O UUID could not be read",
            executable=str(executable),
            returncode=result.returncode,
            error=result.error,
            stderr=result.stderr.strip(),
        )
    match = re.search(
        r"cmd\s+LC_UUID\b.*?\buuid\s+([0-9A-Fa-f-]{36})\b",
        result.stdout,
        flags=re.DOTALL,
    )
    if match is None:
        raise MatrixError(
            "consent_stage.uuid_missing",
            "Mach-O does not declare an LC_UUID value",
            executable=str(executable),
        )
    return match.group(1).upper()


def _inspect_bundle(
    path: pathlib.Path,
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
    *,
    private: bool,
) -> BundleEvidence:
    _reject_symlink_components(path, role="bundle", require_exists=True)
    if path.suffix != ".app" or not path.is_dir() or path.is_symlink():
        raise MatrixError(
            "consent_stage.path_refused",
            "Bundle path must be a real .app directory",
            path=str(path),
            reason="not_app",
        )
    bundle_id, executable = _read_plist(path)
    executable_path = path / "Contents" / "MacOS" / executable
    _reject_symlink_components(executable_path, role="bundle executable", require_exists=True)
    if not executable_path.is_file() or executable_path.is_symlink():
        raise MatrixError(
            "consent_stage.bundle_invalid",
            "Bundle executable is missing or is not a regular file",
            path=str(path),
            executable=str(executable_path),
        )
    entries, tree_hash, quarantine = _tree_entries(path)
    if private and quarantine:
        raise MatrixError(
            "consent_stage.path_refused",
            "Lane bundle has a quarantine extended attribute",
            path=str(path),
            entries=list(quarantine),
            reason="quarantine",
        )
    if private:
        _require_private_parent(path)
    signed_identifier, team_id, cd_hash = _codesign_identity(path, runner, commands)
    if signed_identifier != bundle_id:
        raise MatrixError(
            "consent_stage.signature_invalid",
            "Code-sign identifier does not match bundle identifier",
            path=str(path),
            plistBundleId=bundle_id,
            signedIdentifier=signed_identifier,
        )
    uuid = _mach_uuid(executable_path, runner, commands)
    return BundleEvidence(
        path=path,
        bundle_id=bundle_id,
        executable=executable,
        executable_path=executable_path,
        executable_sha256=_sha256_file(executable_path),
        codesign_identifier=signed_identifier,
        team_id=team_id,
        cd_hash=cd_hash,
        uuid=uuid,
        tree_hash=tree_hash,
        entries=entries,
        quarantine=quarantine,
    )


def _query_processes(
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = _run(runner, commands, PROCESS_LIST_COMMAND, timeout=5.0)
    if result.error is not None or result.returncode != 0:
        raise MatrixError(
            "consent_stage.process_query_failed",
            "Running bridge processes could not be inspected",
            argv=list(result.argv),
            returncode=result.returncode,
            error=result.error,
            stderr=result.stderr.strip(),
        )
    processes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        command = fields[1].strip()
        if pathlib.Path(command.split()[0]).name == PRODUCTION_EXECUTABLE:
            processes.append({"pid": int(fields[0]), "command": command})
    return processes


def _query_service(
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    target = f"gui/{os.getuid()}/{PRODUCTION_SERVICE}"
    argv = (*SERVICE_QUERY_PREFIX, target)
    result = _run(runner, commands, argv, timeout=5.0)
    text = f"{result.stdout}\n{result.stderr}".lower()
    missing = result.returncode != 0 and any(
        marker in text for marker in ("could not find service", "service not found", "no such process")
    )
    if result.error is not None and result.error != "unavailable":
        raise MatrixError(
            "consent_stage.service_query_failed",
            "Bridge launchd service could not be inspected",
            target=target,
            returncode=result.returncode,
            error=result.error,
            stderr=result.stderr.strip(),
        )
    if result.error == "unavailable":
        raise MatrixError(
            "consent_stage.service_query_failed",
            "launchctl is unavailable",
            target=target,
            error=result.error,
        )
    if result.returncode == 0:
        raise MatrixError(
            "consent_stage.active_service",
            "The bridge launchd service is active",
            target=target,
            output=f"{result.stdout}{result.stderr}",
        )
    if not missing:
        raise MatrixError(
            "consent_stage.service_query_failed",
            "Bridge launchd service query failed",
            target=target,
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )
    return {"target": target, "active": False}


def _preflight(
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    processes = _query_processes(runner, commands)
    if processes:
        raise MatrixError(
            "consent_stage.active_process",
            "The bridge process is running",
            processes=processes,
        )
    return {"processes": processes, "service": _query_service(runner, commands)}


def _dump(
    bundle_id: str,
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    argv = (str(runtime_control.LSREGISTER_PATH), "-dump")
    result = _run(runner, commands, argv, timeout=30.0)
    if result.error is not None or result.returncode != 0:
        raise MatrixError(
            "consent_stage.launch_services_query_failed",
            "Launch Services records could not be inspected",
            argv=list(result.argv),
            returncode=result.returncode,
            error=result.error,
            stderr=result.stderr.strip(),
        )
    return {"records": runtime_control.parse_launch_services_records(result.stdout, bundle_id)}


def _record_path(record: dict[str, Any]) -> pathlib.Path:
    value = record.get("path")
    if not isinstance(value, str) or not value.startswith("/"):
        raise MatrixError(
            "consent_stage.path_refused",
            "Launch Services record has no safe absolute path",
            record=record,
            reason="record_path",
        )
    path = pathlib.Path(os.path.abspath(value))
    _reject_symlink_components(path, role="Launch Services record", require_exists=False)
    return path


def _record_matches(record: dict[str, Any], bundle: BundleEvidence) -> bool:
    try:
        record_path = _record_path(record)
    except MatrixError:
        return False
    return (
        record_path == bundle.path
        and record.get("teamId") == bundle.team_id
        and bundle.cd_hash in {str(item).lower() for item in record.get("cdHashes", [])}
    )


def _stable_baseline(
    stable: pathlib.Path,
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> tuple[BundleEvidence, dict[str, Any]]:
    bundle = _inspect_bundle(stable, runner, commands, private=False)
    if bundle.bundle_id != PRODUCTION_BUNDLE_ID or bundle.executable != PRODUCTION_EXECUTABLE:
        raise MatrixError(
            "consent_stage.identity_collision",
            "Stable app is not the production bridge identity",
            stable=bundle.to_dict(),
            expectedBundleId=PRODUCTION_BUNDLE_ID,
            expectedExecutable=PRODUCTION_EXECUTABLE,
        )
    launch_services = _dump(PRODUCTION_BUNDLE_ID, runner, commands)
    records = launch_services["records"]
    if len(records) > 1:
        raise MatrixError(
            "consent_stage.duplicate_baseline",
            "Stable production identity has more than one Launch Services record",
            stable=bundle.identity(),
            records=records,
        )
    if len(records) == 0:
        raise MatrixError(
            "consent_stage.baseline_missing",
            "Stable production identity has no Launch Services record",
            stable=bundle.identity(),
        )
    if not _record_matches(records[0], bundle):
        raise MatrixError(
            "consent_stage.baseline_mismatch",
            "Stable Launch Services record does not match the retained app",
            stable=bundle.identity(),
            records=records,
        )
    return bundle, launch_services


def _stable_candidate(
    stable: pathlib.Path,
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> tuple[BundleEvidence, dict[str, Any]]:
    bundle = _inspect_bundle(stable, runner, commands, private=False)
    if bundle.bundle_id != PRODUCTION_BUNDLE_ID or bundle.executable != PRODUCTION_EXECUTABLE:
        raise MatrixError(
            "consent_stage.identity_collision",
            "Stable app is not the production bridge identity",
            stable=bundle.to_dict(),
            expectedBundleId=PRODUCTION_BUNDLE_ID,
            expectedExecutable=PRODUCTION_EXECUTABLE,
        )
    launch_services = _dump(PRODUCTION_BUNDLE_ID, runner, commands)
    if not launch_services["records"]:
        raise MatrixError(
            "consent_stage.baseline_missing",
            "Stable production identity has no Launch Services record",
            stable=bundle.identity(),
        )
    stable_records = [record for record in launch_services["records"] if _record_matches(record, bundle)]
    if len(stable_records) > 1:
        raise MatrixError(
            "consent_stage.duplicate_baseline",
            "Stable production path has more than one matching Launch Services record",
            stable=bundle.identity(),
            records=launch_services["records"],
        )
    if len(stable_records) == 0:
        raise MatrixError(
            "consent_stage.baseline_mismatch",
            "Launch Services does not contain exactly one matching stable record",
            stable=bundle.identity(),
            records=launch_services["records"],
        )
    return bundle, launch_services


def _revalidate_stable(
    baseline: BundleEvidence,
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
    *,
    allow_duplicates: bool = False,
) -> dict[str, Any]:
    current = _inspect_bundle(baseline.path, runner, commands, private=False)
    if any(
        (
            current.path != baseline.path,
            current.team_id != baseline.team_id,
            current.cd_hash != baseline.cd_hash,
            current.uuid != baseline.uuid,
            current.tree_hash != baseline.tree_hash,
        )
    ):
        raise MatrixError(
            "consent_stage.stable_changed",
            "Retained stable app identity changed during staging",
            before=baseline.identity(),
            after=current.identity(),
        )
    launch_services = _dump(PRODUCTION_BUNDLE_ID, runner, commands)
    records = launch_services["records"]
    matching = [record for record in records if _record_matches(record, baseline)]
    if len(matching) != 1 or (not allow_duplicates and len(records) != 1):
        code = "consent_stage.duplicate_baseline" if len(records) > 1 and not allow_duplicates else "consent_stage.stable_changed"
        raise MatrixError(
            code,
            "Retained stable app no longer has exactly one matching Launch Services record",
            stable=baseline.identity(),
            records=records,
        )
    return launch_services


def _strictly_beneath(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return relative != pathlib.Path(".")


def _select_duplicates(
    records: Iterable[dict[str, Any]], stable: pathlib.Path, roots: Sequence[pathlib.Path]
) -> list[pathlib.Path]:
    selected: list[pathlib.Path] = []
    for record in records:
        candidate = _record_path(record)
        if candidate == stable:
            continue
        if candidate.suffix != ".app":
            raise MatrixError(
                "consent_stage.path_refused",
                "Duplicate Launch Services record is not an app path",
                path=str(candidate),
                reason="not_app",
            )
        if not any(_strictly_beneath(candidate, root) for root in roots):
            raise MatrixError(
                "consent_stage.path_refused",
                "Duplicate Launch Services record is outside the explicitly allowed roots",
                path=str(candidate),
                allowedRoots=[str(root) for root in roots],
                reason="outside_allowed_root",
            )
        selected.append(candidate)
    return sorted(set(selected), key=str)


def _result(
    action: str,
    outcome: str,
    evidence: dict[str, Any],
    commands: list[dict[str, Any]],
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "ok": not errors,
        "action": action,
        "outcome": outcome,
        "evidence": evidence,
        "commands": commands,
        "errors": errors or [],
    }


def _error_result(action: str, error: MatrixError, commands: list[dict[str, Any]], evidence: dict[str, Any]) -> dict[str, Any]:
    return _result(
        action,
        "failed",
        evidence,
        commands,
        [{"code": error.code, "message": error.message, "details": error.details}],
    )


@contextlib.contextmanager
def _lifecycle_guard(
    apply: bool,
    lock_path: pathlib.Path | None,
) -> Iterator[None]:
    if not apply or lock_path is None:
        yield
        return
    try:
        _reject_symlink_components(lock_path, role="lifecycle lock", require_exists=False)
        parent = lock_path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _reject_symlink_components(parent, role="lifecycle lock parent", require_exists=True)
        parent_info = parent.stat()
        if parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) & 0o022:
            raise MatrixError(
                "consent_stage.lock_failed",
                "Lifecycle lock parent is not privately operator-owned",
                path=str(parent),
            )
        with runtime_control.global_lifecycle_lock(lock_path, (parent,)):
            yield
    except runtime_control.ControlError as error:
        raise MatrixError(
            "consent_stage.lock_failed",
            "Global lifecycle lock could not be acquired",
            path=str(lock_path),
            errorCode=error.code,
            error=error.message,
        ) from error


def _command_failure(result: runtime_control.CommandResult) -> dict[str, Any]:
    return {
        "argv": list(result.argv),
        "returncode": result.returncode,
        "error": result.error,
        "stderr": _bounded_output(result.stderr.strip()),
    }


def _run_baseline(
    stable: pathlib.Path,
    roots: Sequence[pathlib.Path],
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
    apply: bool,
) -> dict[str, Any]:
    preflight = _preflight(runner, commands)
    baseline, launch_services = _stable_candidate(stable, runner, commands)
    duplicates = _select_duplicates(launch_services["records"], baseline.path, roots)
    actions = [(str(runtime_control.LSREGISTER_PATH), "-u", str(path)) for path in duplicates]
    evidence: dict[str, Any] = {
        "preflight": preflight,
        "stable": baseline.to_dict(),
        "launchServices": launch_services,
        "duplicates": [str(path) for path in duplicates],
        "plannedActions": [list(argv) for argv in actions],
    }
    if not apply:
        return _result("baseline", "dry-run", evidence, commands + [{"argv": list(argv), "executed": False} for argv in actions])
    for argv in actions:
        result = _run(runner, commands, argv)
        if result.error is not None or result.returncode != 0:
            raise MatrixError("consent_stage.residue", "Launch Services duplicate cleanup command failed", command=_command_failure(result))
        current = _revalidate_stable(baseline, runner, commands, allow_duplicates=True)
        remaining = _select_duplicates(current["records"], baseline.path, roots)
        if pathlib.Path(argv[-1]) in remaining:
            raise MatrixError(
                "consent_stage.residue",
                "Launch Services duplicate cleanup left the targeted record",
                path=argv[-1],
            )
    final = _revalidate_stable(baseline, runner, commands)
    evidence["finalLaunchServices"] = final
    evidence["appliedActions"] = [list(argv) for argv in actions]
    return _result("baseline", "applied", evidence, commands)


def _validate_lanes(
    baseline: BundleEvidence,
    lane_paths: Sequence[pathlib.Path],
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> list[BundleEvidence]:
    lanes: list[BundleEvidence] = []
    seen_paths: set[pathlib.Path] = set()
    seen_ids: set[str] = set()
    seen_uuids = {baseline.uuid}
    for path in lane_paths:
        if path in seen_paths:
            raise MatrixError("consent_stage.identity_collision", "Lane app paths must be unique", path=str(path))
        seen_paths.add(path)
        lane = _inspect_bundle(path, runner, commands, private=True)
        if lane.bundle_id == PRODUCTION_BUNDLE_ID or lane.bundle_id in seen_ids:
            raise MatrixError(
                "consent_stage.identity_collision",
                "Lane bundle identifiers must be unique and never production",
                bundleId=lane.bundle_id,
                path=str(path),
            )
        if lane.team_id != baseline.team_id:
            raise MatrixError(
                "consent_stage.signature_invalid",
                "Lane signature Team ID does not match the stable app",
                path=str(path),
                stableTeamId=baseline.team_id,
                laneTeamId=lane.team_id,
            )
        if lane.uuid in seen_uuids:
            raise MatrixError(
                "consent_stage.uuid_collision",
                "Lane Mach-O UUID collides with the stable app or another lane",
                path=str(path),
                uuid=lane.uuid,
            )
        existing = _dump(lane.bundle_id, runner, commands)
        if existing["records"]:
            raise MatrixError(
                "consent_stage.identity_collision",
                "Lane bundle identifier already has Launch Services records",
                path=str(path),
                bundleId=lane.bundle_id,
                records=existing["records"],
            )
        lanes.append(lane)
        seen_ids.add(lane.bundle_id)
        seen_uuids.add(lane.uuid)
    return lanes


def _run_stage(
    stable: pathlib.Path,
    lane_paths: Sequence[pathlib.Path],
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
    apply: bool,
) -> dict[str, Any]:
    preflight = _preflight(runner, commands)
    baseline, launch_services = _stable_baseline(stable, runner, commands)
    lanes = _validate_lanes(baseline, lane_paths, runner, commands)
    actions = [(str(runtime_control.LSREGISTER_PATH), "-f", str(lane.path)) for lane in lanes]
    evidence: dict[str, Any] = {
        "preflight": preflight,
        "stable": baseline.to_dict(),
        "launchServices": launch_services,
        "lanes": [lane.to_dict() for lane in lanes],
        "plannedActions": [list(argv) for argv in actions],
    }
    if not apply:
        return _result("stage", "dry-run", evidence, commands + [{"argv": list(argv), "executed": False} for argv in actions])
    staged: list[str] = []
    for lane, argv in zip(lanes, actions):
        result = _run(runner, commands, argv)
        observed = _dump(lane.bundle_id, runner, commands)
        if result.error is not None or result.returncode != 0 or len(observed["records"]) != 1 or not _record_matches(observed["records"][0], lane):
            raise MatrixError(
                "consent_stage.register_failed",
                "Lane Launch Services registration was not exact after registration",
                lane=lane.identity(),
                command=_command_failure(result),
                records=observed["records"],
            )
        _revalidate_stable(baseline, runner, commands)
        staged.append(str(lane.path))
    evidence["staged"] = staged
    return _result("stage", "applied", evidence, commands)


def _run_cleanup(
    stable: pathlib.Path,
    lane_paths: Sequence[pathlib.Path],
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
    apply: bool,
) -> dict[str, Any]:
    preflight = _preflight(runner, commands)
    baseline, launch_services = _stable_baseline(stable, runner, commands)
    lanes = _validate_cleanup_lanes(lane_paths, runner, commands)
    ordered = tuple(reversed(lanes))
    actions = [(str(runtime_control.LSREGISTER_PATH), "-u", str(lane.path)) for lane in ordered]
    evidence: dict[str, Any] = {
        "preflight": preflight,
        "stable": baseline.to_dict(),
        "launchServices": launch_services,
        "lanes": [lane.to_dict() for lane in lanes],
        "plannedActions": [list(argv) for argv in actions],
        "filesystemRemoval": "separate explicit operator action",
    }
    if not apply:
        return _result("cleanup", "dry-run", evidence, commands + [{"argv": list(argv), "executed": False} for argv in actions])
    removed: list[str] = []
    for lane, argv in zip(ordered, actions):
        result = _run(runner, commands, argv)
        observed = _dump(lane.bundle_id, runner, commands)
        if result.error is not None or result.returncode != 0 or observed["records"]:
            raise MatrixError(
                "consent_stage.residue",
                "Lane Launch Services cleanup did not leave zero records",
                lane=lane.identity(),
                command=_command_failure(result),
                records=observed["records"],
            )
        _revalidate_stable(baseline, runner, commands)
        removed.append(str(lane.path))
    evidence["removed"] = removed
    return _result("cleanup", "applied", evidence, commands)


def _validate_cleanup_lanes(
    lane_paths: Sequence[pathlib.Path],
    runner: runtime_control.CommandRunner,
    commands: list[dict[str, Any]],
) -> list[BundleEvidence]:
    lanes: list[BundleEvidence] = []
    seen_ids: set[str] = set()
    for path in lane_paths:
        lane = _inspect_bundle(path, runner, commands, private=False)
        if lane.bundle_id == PRODUCTION_BUNDLE_ID or lane.bundle_id in seen_ids:
            raise MatrixError(
                "consent_stage.identity_collision",
                "Cleanup lane bundle identifiers must be unique and never production",
                bundleId=lane.bundle_id,
                path=str(path),
            )
        seen_ids.add(lane.bundle_id)
        lanes.append(lane)
    return lanes


def run_matrix(
    action: str,
    *,
    stable_app: str | pathlib.Path,
    lane_apps: Sequence[str | pathlib.Path] = (),
    allowed_roots: Sequence[str | pathlib.Path] = (),
    apply: bool = False,
    runner: runtime_control.CommandRunner | None = None,
    lifecycle_lock_path: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {"apply": apply}
    command_runner = runner or runtime_control.SubprocessRunner()
    try:
        stable = _path(stable_app, role="stable app")
        lanes = tuple(_path(item, role="lane app") for item in lane_apps)
        roots = tuple(_path(item, role="allowed root") for item in allowed_roots)
        lock = _path(lifecycle_lock_path, role="lifecycle lock", require_exists=False) if lifecycle_lock_path else None
        evidence["lifecycleLock"] = str(lock) if lock else None
        with _lifecycle_guard(apply and action != "inspect", lock):
            if action == "inspect":
                preflight = _preflight(command_runner, commands)
                bundle = _inspect_bundle(stable, command_runner, commands, private=False)
                launch_services = _dump(bundle.bundle_id, command_runner, commands)
                evidence.update({"preflight": preflight, "stable": bundle.to_dict(), "launchServices": launch_services})
                errors: list[dict[str, Any]] = []
                if bundle.bundle_id != PRODUCTION_BUNDLE_ID or bundle.executable != PRODUCTION_EXECUTABLE:
                    errors.append(
                        {
                            "code": "consent_stage.identity_collision",
                            "message": "Stable app is not the production bridge identity",
                            "details": {
                                "expectedBundleId": PRODUCTION_BUNDLE_ID,
                                "expectedExecutable": PRODUCTION_EXECUTABLE,
                                "actual": bundle.identity(),
                            },
                        }
                    )
                elif len(launch_services["records"]) != 1:
                    code = "consent_stage.duplicate_baseline" if len(launch_services["records"]) > 1 else "consent_stage.baseline_missing"
                    errors.append({"code": code, "message": "Stable app does not have exactly one Launch Services record", "details": {"records": launch_services["records"]}})
                elif not _record_matches(launch_services["records"][0], bundle):
                    errors.append({"code": "consent_stage.baseline_mismatch", "message": "Launch Services record does not match the stable app", "details": {"records": launch_services["records"]}})
                return _result(action, "dry-run", evidence, commands, errors)
            if action == "baseline":
                if not roots:
                    raise MatrixError("consent_stage.path_refused", "Baseline requires one or more explicit allowed duplicate roots")
                return _run_baseline(stable, roots, command_runner, commands, apply)
            if action == "stage":
                if not 1 <= len(lanes) <= 4:
                    raise MatrixError("consent_stage.path_refused", "Stage requires between one and four lane apps", count=len(lanes))
                return _run_stage(stable, lanes, command_runner, commands, apply)
            if action == "cleanup":
                if not lanes:
                    raise MatrixError("consent_stage.path_refused", "Cleanup requires at least one exact lane app path")
                return _run_cleanup(stable, lanes, command_runner, commands, apply)
            raise MatrixError("consent_stage.path_refused", "Unsupported consent matrix action", action=action)
    except MatrixError as error:
        return _error_result(action, error, commands, evidence)
    except (OSError, ValueError, TypeError) as error:
        return _error_result(
            action,
            MatrixError("consent_stage.internal_error", "Consent matrix helper failed safely", error=str(error)),
            commands,
            evidence,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "baseline", "stage", "cleanup"))
    parser.add_argument("--stable-app", required=True)
    parser.add_argument("--lane-app", action="append", default=[])
    parser.add_argument("--allowed-root", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_matrix(
        args.action,
        stable_app=args.stable_app,
        lane_apps=args.lane_app,
        allowed_roots=args.allowed_root,
        apply=args.apply,
        lifecycle_lock_path=runtime_control.DEFAULT_LIFECYCLE_LOCK,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
