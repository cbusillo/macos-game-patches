#!/usr/bin/env python3
"""Install and validate the per-user Aqua Every Code app-server LaunchAgent."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
import pathlib
import plistlib
import pwd
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any


SCHEMA_VERSION = 1
LABEL = "com.cbusillo.every-code-lab.app-server"
LISTEN_URL = "ws://127.0.0.1:8765"
LISTEN_PORT = 8765
READY_TIMEOUT_SECONDS = 20.0
EXPECTED_HOSTNAME = "chris-mbp"
EXPECTED_USER = "cbusillo"
EXPECTED_UID = 501


class AgentError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class Paths:
    home: pathlib.Path
    code: pathlib.Path
    plist: pathlib.Path
    log_root: pathlib.Path
    stdout_log: pathlib.Path
    stderr_log: pathlib.Path
    state_root: pathlib.Path
    contract: pathlib.Path
    lock: pathlib.Path


@dataclass(frozen=True)
class Listener:
    pid: int
    command_name: str
    addresses: tuple[str, ...]
    uid: int | None = None
    command: str | None = None
    start_token: int | None = None


class DarwinProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def paths_for(home: pathlib.Path) -> Paths:
    launch_agents = home / "Library" / "LaunchAgents"
    log_root = home / "Library" / "Logs" / "EveryCode"
    state_root = home / ".code" / "run"
    return Paths(
        home=home,
        code=home / ".local" / "bin" / "code",
        plist=launch_agents / f"{LABEL}.plist",
        log_root=log_root,
        stdout_log=log_root / "app-server.stdout.log",
        stderr_log=log_root / "app-server.stderr.log",
        state_root=state_root,
        contract=state_root / "m2-app-server-launch-agent.json",
        lock=state_root / "m2-app-server-launch-agent.lock",
    )


def expected_arguments(paths: Paths) -> list[str]:
    return [str(paths.code), "app-server", "--listen", LISTEN_URL]


def expected_command(paths: Paths) -> str:
    return " ".join(expected_arguments(paths))


def launch_domain(uid: int) -> str:
    return f"gui/{uid}"


def service_target(uid: int) -> str:
    return f"{launch_domain(uid)}/{LABEL}"


def build_plist(paths: Paths, working_directory: pathlib.Path) -> dict[str, Any]:
    return {
        "Label": LABEL,
        "ProgramArguments": expected_arguments(paths),
        "WorkingDirectory": str(working_directory),
        "LimitLoadToSessionType": "Aqua",
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "ExitTimeOut": 10,
        "Umask": 0o077,
        "StandardInPath": "/dev/null",
        "StandardOutPath": str(paths.stdout_log),
        "StandardErrorPath": str(paths.stderr_log),
        "EnvironmentVariables": {
            "HOME": str(paths.home),
            "PATH": f"{paths.home}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }


def run(
    command: list[str],
    *,
    check: bool = True,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        raise AgentError(
            "command.failed",
            "Required system command failed",
            command=command,
            status=completed.returncode,
            stderr=completed.stderr.strip(),
        )
    return completed


def reject_symlink_components(path: pathlib.Path) -> None:
    current = pathlib.Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.exists() and current.is_symlink():
            raise AgentError(
                "path.symlink",
                "A managed path contains a symlink component",
                path=str(current),
            )


def validate_regular_owner_file(path: pathlib.Path, uid: int, *, executable: bool = False) -> None:
    reject_symlink_components(path)
    try:
        metadata = path.stat()
    except FileNotFoundError as error:
        raise AgentError("path.missing", "Required file is missing", path=str(path)) from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != uid:
        raise AgentError("path.unsafe", "Required file has unsafe ownership or type", path=str(path))
    if metadata.st_mode & 0o022:
        raise AgentError("path.unsafe", "Required file is group/world writable", path=str(path))
    if executable and not os.access(path, os.X_OK):
        raise AgentError("path.not_executable", "Required executable is not executable", path=str(path))


def validate_working_directory(path: pathlib.Path, uid: int) -> pathlib.Path:
    resolved = path.expanduser().resolve(strict=True)
    reject_symlink_components(resolved)
    metadata = resolved.stat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid:
        raise AgentError(
            "working_directory.invalid",
            "Working directory has unsafe ownership or type",
            path=str(resolved),
        )
    if not (resolved / ".git").exists():
        raise AgentError(
            "working_directory.invalid",
            "Working directory is not a Git checkout",
            path=str(resolved),
        )
    return resolved


def validate_owned_directory_chain(home: pathlib.Path, target: pathlib.Path, uid: int) -> None:
    resolved_home = home.resolve(strict=True)
    resolved_target = target.resolve(strict=True)
    try:
        relative = resolved_target.relative_to(resolved_home)
    except ValueError as error:
        raise AgentError(
            "path.unsafe",
            "Managed path escapes the user home",
            path=str(resolved_target),
        ) from error
    current = resolved_home
    for component in (None, *relative.parts):
        if component is not None:
            current /= component
        metadata = current.stat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid or metadata.st_mode & 0o022:
            raise AgentError(
                "path.unsafe",
                "Managed directory has unsafe ownership or mode",
                path=str(current),
            )


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_lsof(output: str) -> list[Listener]:
    records: dict[int, dict[str, Any]] = {}
    current_pid: int | None = None
    for line in output.splitlines():
        if not line:
            continue
        prefix, value = line[0], line[1:]
        if prefix == "p" and value.isdigit():
            current_pid = int(value)
            records.setdefault(current_pid, {"command_name": "", "addresses": []})
        elif current_pid is not None and prefix == "c":
            records[current_pid]["command_name"] = value
        elif current_pid is not None and prefix == "n":
            records[current_pid]["addresses"].append(value)
    return [
        Listener(
            pid=pid,
            command_name=record["command_name"],
            addresses=tuple(record["addresses"]),
        )
        for pid, record in sorted(records.items())
    ]


def listeners() -> list[Listener]:
    completed = run(
        [
            "/usr/sbin/lsof",
            "-nP",
            f"-iTCP:{LISTEN_PORT}",
            "-sTCP:LISTEN",
            "-Fpcn",
        ],
        check=False,
    )
    if completed.returncode not in (0, 1):
        raise AgentError(
            "listener.query_failed",
            "Could not inspect the app-server listener",
            status=completed.returncode,
            stderr=completed.stderr.strip(),
        )
    enriched: list[Listener] = []
    for listener in parse_lsof(completed.stdout):
        uid, command, start_token = process_identity(listener.pid)
        enriched.append(
            Listener(
                pid=listener.pid,
                command_name=listener.command_name,
                addresses=listener.addresses,
                uid=uid,
                command=command,
                start_token=start_token,
            )
        )
    return enriched


def validate_listeners(found: list[Listener], paths: Paths, uid: int) -> list[int]:
    if len(found) > 1:
        raise AgentError(
            "listener.multiple",
            "More than one process is listening on the app-server port",
            pids=[listener.pid for listener in found],
        )
    accepted: list[int] = []
    for listener in found:
        if listener.uid != uid or listener.command != expected_command(paths):
            raise AgentError(
                "listener.foreign",
                "Port 8765 is owned by a foreign process",
                pid=listener.pid,
                uid=listener.uid,
                command=listener.command,
            )
        if not listener.addresses or any(
            address != f"127.0.0.1:{LISTEN_PORT}" for address in listener.addresses
        ):
            raise AgentError(
                "listener.foreign",
                "The app-server listener is not IPv4 loopback-only",
                pid=listener.pid,
                addresses=list(listener.addresses),
            )
        accepted.append(listener.pid)
    return accepted


def process_birth_token(pid: int) -> int | None:
    if pid <= 0:
        return None
    if sys.platform == "darwin":
        try:
            library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        except OSError:
            return None
        library.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        library.proc_pidinfo.restype = ctypes.c_int
        info = DarwinProcBsdInfo()
        size = library.proc_pidinfo(
            pid,
            3,
            0,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if size != ctypes.sizeof(info) or info.pbi_pid != pid:
            return None
        return int(info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec)
    if sys.platform.startswith("linux"):
        try:
            payload = pathlib.Path(f"/proc/{pid}/stat").read_text()
            fields = payload[payload.rfind(")") + 2 :].split()
            token = int(fields[19])
        except (FileNotFoundError, OSError, ValueError, IndexError):
            return None
        return token if token > 0 else None
    return None


def process_identity(pid: int) -> tuple[int | None, str | None, int | None]:
    process = run(
        ["/bin/ps", "-ww", "-p", str(pid), "-o", "uid=,command="],
        check=False,
    )
    if process.returncode != 0 or not process.stdout.strip():
        return None, None, None
    fields = process.stdout.strip().split(None, 1)
    if len(fields) != 2 or not fields[0].isdigit():
        return None, None, None
    return int(fields[0]), fields[1], process_birth_token(pid)


def parse_launchctl_print(output: str) -> dict[str, Any]:
    state_match = re.search(r"^\s*state = (.+)$", output, re.MULTILINE)
    pid_match = re.search(r"^\s*pid = (\d+)$", output, re.MULTILINE)
    path_match = re.search(r"^\s*path = (.+)$", output, re.MULTILINE)
    return {
        "state": state_match.group(1).strip() if state_match else None,
        "pid": int(pid_match.group(1)) if pid_match else None,
        "path": path_match.group(1).strip() if path_match else None,
    }


def launchd_status(uid: int) -> dict[str, Any]:
    completed = run(["/bin/launchctl", "print", service_target(uid)], check=False)
    if completed.returncode != 0:
        return {"loaded": False, "state": None, "pid": None, "path": None}
    return {"loaded": True, **parse_launchctl_print(completed.stdout)}


def service_ready(paths: Paths, service: dict[str, Any], listener_pids: list[int]) -> bool:
    return (
        service.get("loaded") is True
        and service.get("path") == str(paths.plist)
        and service.get("state") == "running"
        and isinstance(service.get("pid"), int)
        and service["pid"] in listener_pids
    )


def load_managed_installation(
    paths: Paths,
    uid: int,
    *,
    current_code_sha256: str | None,
) -> tuple[dict[str, Any], dict[str, Any], pathlib.Path] | None:
    plist_exists = paths.plist.exists() or paths.plist.is_symlink()
    contract_exists = paths.contract.exists() or paths.contract.is_symlink()
    if not plist_exists and not contract_exists:
        return None
    if not plist_exists or not contract_exists:
        raise AgentError(
            "installation.partial",
            "Managed plist and contract must either both exist or both be absent",
        )
    validate_regular_owner_file(paths.plist, uid)
    validate_regular_owner_file(paths.contract, uid)
    with paths.plist.open("rb") as stream:
        plist_payload = plistlib.load(stream)
    with paths.contract.open("r", encoding="utf-8") as stream:
        contract_payload = json.load(stream)
    if not isinstance(plist_payload, dict) or not isinstance(contract_payload, dict):
        raise AgentError("installation.identity_mismatch", "Managed files have invalid types")
    raw_working_directory = plist_payload.get("WorkingDirectory")
    if not isinstance(raw_working_directory, str):
        raise AgentError(
            "installation.identity_mismatch",
            "Managed plist has no valid working directory",
        )
    working_directory = validate_working_directory(pathlib.Path(raw_working_directory), uid)
    if plist_payload != build_plist(paths, working_directory):
        raise AgentError(
            "installation.identity_mismatch",
            "Managed plist does not match the exact LaunchAgent contract",
        )
    code_sha256 = contract_payload.get("codeSha256")
    expected_contract = {
        "codeSha256": code_sha256,
        "workingDirectory": str(working_directory),
    }
    if (
        not isinstance(code_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", code_sha256) is None
        or contract_payload != expected_contract
        or (current_code_sha256 is not None and code_sha256 != current_code_sha256)
    ):
        raise AgentError(
            "installation.identity_mismatch",
            "Managed contract does not match the exact LaunchAgent identity",
        )
    return plist_payload, contract_payload, working_directory


def validate_host(paths: Paths, working_directory: pathlib.Path | None) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise AgentError("host.unsupported", "LaunchAgent management requires macOS")
    uid = os.getuid()
    user = pwd.getpwuid(uid).pw_name
    hostname = socket.gethostname().split(".", 1)[0]
    if uid != EXPECTED_UID or user != EXPECTED_USER or hostname != EXPECTED_HOSTNAME:
        raise AgentError(
            "host.identity_mismatch",
            "LaunchAgent management is restricted to the configured M2 test host",
            hostname=hostname,
            user=user,
            uid=uid,
        )
    console_uid = os.stat("/dev/console").st_uid
    if console_uid != uid:
        raise AgentError(
            "session.aqua_unavailable",
            "The current user does not own the active console session",
            uid=uid,
            consoleUid=console_uid,
        )
    domain = run(["/bin/launchctl", "print", launch_domain(uid)], check=False)
    if domain.returncode != 0 or "session = Aqua" not in domain.stdout:
        raise AgentError(
            "session.aqua_unavailable",
            "The per-user Aqua launchd domain is unavailable",
            uid=uid,
        )
    validate_regular_owner_file(paths.code, uid, executable=True)
    code_version = run([str(paths.code), "--version"]).stdout.strip().splitlines()[0]
    resolved_working_directory = (
        validate_working_directory(working_directory, uid) if working_directory is not None else None
    )
    found_listeners = listeners()
    accepted_listener_pids = validate_listeners(found_listeners, paths, uid)
    return {
        "uid": uid,
        "user": user,
        "hostname": hostname,
        "code": str(paths.code),
        "codeVersion": code_version,
        "codeSha256": sha256(paths.code),
        "workingDirectory": (
            str(resolved_working_directory) if resolved_working_directory is not None else None
        ),
        "listenerPids": accepted_listener_pids,
        "launchd": launchd_status(uid),
    }


def prepare_private_file(path: pathlib.Path) -> None:
    reject_symlink_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    validate_owned_directory_chain(pathlib.Path.home(), path.parent, os.getuid())
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def write_plist_atomic(paths: Paths, payload: bytes) -> bytes | None:
    paths.plist.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink_components(paths.plist.parent)
    validate_owned_directory_chain(paths.home, paths.plist.parent, os.getuid())
    previous: bytes | None = None
    if paths.plist.exists() or paths.plist.is_symlink():
        validate_regular_owner_file(paths.plist, os.getuid())
        previous = paths.plist.read_bytes()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{LABEL}.",
        suffix=".plist",
        dir=paths.plist.parent,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        run(["/usr/bin/plutil", "-lint", str(temporary)])
        os.replace(temporary, paths.plist)
    finally:
        temporary.unlink(missing_ok=True)
    return previous


def restore_plist(paths: Paths, previous: bytes | None) -> None:
    if previous is None:
        return
    write_plist_atomic(paths, previous)


def write_json_atomic(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    reject_symlink_components(path.parent)
    validate_owned_directory_chain(pathlib.Path.home(), path.parent, os.getuid())
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def operation_lock(paths: Paths):
    paths.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(paths.state_root, 0o700)
    validate_owned_directory_chain(paths.home, paths.state_root, os.getuid())
    descriptor = os.open(
        paths.lock,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AgentError("operation.locked", "Another LaunchAgent operation is active") from error
        yield
    finally:
        os.close(descriptor)


def bootout(uid: int) -> None:
    completed = run(["/bin/launchctl", "bootout", service_target(uid)], check=False)
    if completed.returncode not in (0, 3, 113):
        raise AgentError(
            "launchd.bootout_failed",
            "Could not boot out the existing app-server service",
            status=completed.returncode,
            stderr=completed.stderr.strip(),
        )


def bootstrap(uid: int, paths: Paths) -> None:
    run(["/bin/launchctl", "enable", service_target(uid)], check=False)
    completed = run(
        ["/bin/launchctl", "bootstrap", launch_domain(uid), str(paths.plist)],
        check=False,
    )
    if completed.returncode != 0:
        raise AgentError(
            "launchd.bootstrap_failed",
            "Could not bootstrap the app-server LaunchAgent",
            status=completed.returncode,
            stderr=completed.stderr.strip(),
        )


def terminate_exact_processes(records: list[Listener], paths: Paths, uid: int) -> None:
    for baseline in records:
        pid = baseline.pid
        current_uid, current_command, current_start_token = process_identity(pid)
        if (
            baseline is None
            or baseline.uid != uid
            or baseline.command != expected_command(paths)
            or baseline.start_token is None
            or current_uid != uid
            or current_command != expected_command(paths)
            or current_start_token != baseline.start_token
        ):
            raise AgentError(
                "listener.identity_changed",
                "The listener identity changed before replacement",
                pid=pid,
            )
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 7.0
    while time.monotonic() < deadline:
        if not any(process_exists(record.pid) for record in records):
            return
        time.sleep(0.1)
    raise AgentError(
        "listener.stop_timeout",
        "The previous app-server process did not stop",
        pids=[record.pid for record in records],
    )


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_ready(paths: Paths, uid: int) -> dict[str, Any]:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        service = launchd_status(uid)
        found = listeners()
        try:
            accepted = validate_listeners(found, paths, uid)
        except AgentError:
            accepted = []
        last_status = {"launchd": service, "listenerPids": accepted}
        if service_ready(paths, service, accepted):
            return last_status
        time.sleep(0.25)
    raise AgentError(
        "launchd.service_not_ready",
        "The app-server LaunchAgent did not become ready",
        **last_status,
        stdoutLog=str(paths.stdout_log),
        stderrLog=str(paths.stderr_log),
    )


def install(paths: Paths, working_directory: pathlib.Path) -> dict[str, Any]:
    baseline = validate_host(paths, working_directory)
    uid = int(baseline["uid"])
    resolved_working_directory = pathlib.Path(str(baseline["workingDirectory"]))
    planned_plist = build_plist(paths, resolved_working_directory)
    previous_service = baseline["launchd"]
    if previous_service.get("loaded") is True and previous_service.get("path") != str(paths.plist):
        raise AgentError(
            "launchd.identity_mismatch",
            "Loaded service path does not match the managed plist",
            path=previous_service.get("path"),
        )
    previous_service_pid = previous_service.get("pid") if isinstance(previous_service, dict) else None
    preexisting_listeners = listeners()
    if validate_listeners(preexisting_listeners, paths, uid) != baseline["listenerPids"]:
        raise AgentError(
            "listener.identity_changed",
            "The app-server listener changed during admission",
        )
    preexisting_manual = [
        listener
        for listener in preexisting_listeners
        if not isinstance(previous_service_pid, int) or listener.pid != previous_service_pid
    ]

    current_hash = str(baseline["codeSha256"])
    expected_contract = {
        "codeSha256": current_hash,
        "workingDirectory": str(resolved_working_directory),
    }
    managed_installation = load_managed_installation(
        paths,
        uid,
        current_code_sha256=None,
    )
    current_plist = managed_installation[0] if managed_installation is not None else None
    current_contract = managed_installation[1] if managed_installation is not None else None
    if (
        current_plist == planned_plist
        and current_contract == expected_contract
        and service_ready(paths, previous_service, baseline["listenerPids"])
    ):
        return {
            "changed": False,
            "baseline": baseline,
            "plist": str(paths.plist),
            "service": previous_service,
            "listenerPids": baseline["listenerPids"],
        }

    prepare_private_file(paths.stdout_log)
    prepare_private_file(paths.stderr_log)
    payload = plistlib.dumps(
        planned_plist,
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )
    previous_plist = write_plist_atomic(paths, payload)
    try:
        if previous_service.get("loaded") is True:
            bootout(uid)
        if preexisting_manual:
            terminate_exact_processes(preexisting_manual, paths, uid)
        bootstrap(uid, paths)
        ready = wait_ready(paths, uid)
    except Exception as error:
        if previous_service.get("loaded") is True and previous_plist is not None:
            bootout(uid)
            restore_plist(paths, previous_plist)
            bootstrap(uid, paths)
        elif previous_plist is not None:
            bootout(uid)
            restore_plist(paths, previous_plist)
        else:
            bootout(uid)
            diagnostic: pathlib.Path | None = None
            if paths.plist.exists() and not paths.plist.is_symlink():
                diagnostic = paths.log_root / f"failed-app-server-{int(time.time())}.plist"
                os.replace(paths.plist, diagnostic)
                os.chmod(diagnostic, 0o600)
            original_code = error.code if isinstance(error, AgentError) else "internal.unexpected"
            raise AgentError(
                "migration.manual_recovery_required",
                "The first LaunchAgent migration failed after the manual server stopped",
                originalCode=original_code,
                diagnosticPlist=str(diagnostic) if diagnostic is not None else None,
                recoveryCommand=(
                    f"cd {resolved_working_directory} && exec {expected_command(paths)}"
                ),
            ) from error
        raise
    write_json_atomic(paths.contract, expected_contract)
    return {
        "changed": True,
        "baseline": baseline,
        "plist": str(paths.plist),
        "service": ready["launchd"],
        "listenerPids": ready["listenerPids"],
    }


def status(paths: Paths) -> dict[str, Any]:
    baseline = validate_host(paths, None)
    uid = int(baseline["uid"])
    managed_installation = load_managed_installation(
        paths,
        uid,
        current_code_sha256=str(baseline["codeSha256"]),
    )
    plist_payload = managed_installation[0] if managed_installation is not None else None
    contract_payload = managed_installation[1] if managed_installation is not None else None
    working_directory = managed_installation[2] if managed_installation is not None else None
    service = launchd_status(uid)
    accepted = validate_listeners(listeners(), paths, uid)
    exact_plist = (
        working_directory is not None and plist_payload == build_plist(paths, working_directory)
    )
    exact_contract = (
        working_directory is not None
        and contract_payload
        == {
            "codeSha256": baseline["codeSha256"],
            "workingDirectory": str(working_directory),
        }
    )
    ready = (
        exact_plist
        and exact_contract
        and service_ready(paths, service, accepted)
    )
    return {
        "ready": ready,
        "plist": str(paths.plist),
        "plistContract": plist_payload,
        "plistContractExact": exact_plist,
        "installedContract": contract_payload,
        "installedContractExact": exact_contract,
        "launchd": service,
        "listenerPids": accepted,
        "codeVersion": baseline["codeVersion"],
        "codeSha256": baseline["codeSha256"],
    }


def uninstall(paths: Paths) -> dict[str, Any]:
    baseline = validate_host(paths, None)
    uid = int(baseline["uid"])
    managed_installation = load_managed_installation(
        paths,
        uid,
        current_code_sha256=None,
    )
    service = launchd_status(uid)
    if service.get("loaded") is True:
        if managed_installation is None:
            raise AgentError(
                "launchd.identity_mismatch",
                "A loaded service has no exact managed plist and contract",
            )
        if service.get("path") != str(paths.plist):
            raise AgentError(
                "launchd.identity_mismatch",
                "Loaded service path does not match the managed plist",
                path=service.get("path"),
            )
        bootout(uid)
        service_pid = service.get("pid")
        if isinstance(service_pid, int):
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and process_exists(service_pid):
                time.sleep(0.1)
            if process_exists(service_pid):
                raise AgentError(
                    "launchd.bootout_failed",
                    "The managed service process remained after bootout",
                    pid=service_pid,
                )
    if managed_installation is not None:
        paths.plist.unlink()
        paths.contract.unlink()
    remaining = validate_listeners(listeners(), paths, uid)
    return {
        "removed": True,
        "plist": str(paths.plist),
        "remainingListenerPids": remaining,
    }


def response_ok(command: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "command": command, "ok": True, "result": result}


def response_error(command: str, error: AgentError) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "ok": False,
        "error": {"code": error.code, "message": error.message, "details": error.details},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--working-directory", type=pathlib.Path, required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--working-directory", type=pathlib.Path, required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("uninstall")
    arguments = parser.parse_args()

    paths = paths_for(pathlib.Path.home())
    try:
        if arguments.command == "check":
            working_directory = validate_working_directory(arguments.working_directory, os.getuid())
            result = validate_host(paths, working_directory)
            result["plannedPlist"] = build_plist(paths, working_directory)
        elif arguments.command == "install":
            with operation_lock(paths):
                result = install(paths, arguments.working_directory)
        elif arguments.command == "status":
            result = status(paths)
            if result["ready"] is not True:
                raise AgentError("launchd.service_not_ready", "The app-server LaunchAgent is not ready", **result)
        else:
            with operation_lock(paths):
                result = uninstall(paths)
    except AgentError as error:
        print(json.dumps(response_error(arguments.command, error), sort_keys=True))
        return 1
    print(json.dumps(response_ok(arguments.command, result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
