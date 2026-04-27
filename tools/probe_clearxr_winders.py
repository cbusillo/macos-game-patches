#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import socket
import struct
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smoke_clearxr_winders import detect_host_address as detect_windows_host_address
from stage_clearxr_winders import scp_copy, stage_to_winders, windows_powershell_capture


DEFAULT_ARCHIVE_ROOT = Path("temp/clearxr_probe_runs")
DEFAULT_WINDOWS_SSH_TARGET = "gaming@winders"
DEFAULT_WINDOWS_ROOT = r"C:\dev\clearxr-server"
STARTUP_TIMEOUT_SECONDS = 45.0
READY_TIMEOUT_SECONDS = 10.0
INTERACTIVE_REMOTE_ROOT = r"C:\Users\gaming\codex-clearxr-interactive"


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_frame(sock: socket.socket, message: dict[str, Any]) -> None:
    payload = json.dumps(message).encode("utf-8")
    sock.sendall(struct.pack("<I", len(payload)))
    sock.sendall(payload)


def read_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("connection closed while reading frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket) -> dict[str, Any]:
    header = read_exact(sock, 4)
    length = struct.unpack("<I", header)[0]
    payload = read_exact(sock, length)
    return json.loads(payload)


def build_probe_messages(session_id: str, client_id: str) -> dict[str, dict[str, Any]]:
    return {
        "apply_configuration": {
            "Event": "ApplyConfiguration",
            "RenderedResolution": 3840,
            "EncodedResolution": 2880,
            "FoveationInsetRatio": 0.15,
            "DefaultAppEnabled": False,
            "AlphaTransparencyEnabled": True,
        },
        "request_connection": {
            "Event": "RequestConnection",
            "SessionID": session_id,
            "ProtocolVersion": "1",
            "StreamingProvider": "CloudXR",
            "StreamingProviderVersion": "6.x",
            "UserInterfaceIdiom": "visionOS",
            "ClientID": client_id,
        },
        "waiting_status": {
            "Event": "SessionStatusDidChange",
            "SessionID": session_id,
            "Status": "WAITING",
        },
        "disconnect_status": {
            "Event": "SessionStatusDidChange",
            "SessionID": session_id,
            "Status": "DISCONNECTED",
        },
    }


def extract_snapshot_payload(line: str, stage: str) -> dict[str, Any] | None:
    prefix = f"CLEARXR_HEADLESS_SNAPSHOT {stage} "
    if not line.startswith(prefix):
        return None
    try:
        payload = json.loads(line[len(prefix) :])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def extract_remote_log_path(line: str) -> str | None:
    prefix = "Further logging is now being redirected to the file:"
    if prefix not in line:
        return None
    candidate = line.split(prefix, 1)[1].strip()
    if candidate.startswith("`") and candidate.endswith("`"):
        return candidate[1:-1]
    return candidate or None


def build_remote_headless_script(
    *,
    windows_root: str,
    host: str,
    port: int,
    bundle_id: str,
    run_seconds: int,
    snapshot_interval_seconds: int,
    force_qr_code: bool = False,
) -> str:
    debug_root = windows_root.rstrip("\\/") + r"\clearxr-streamer\target\x86_64-pc-windows-gnu\debug"
    release_root = debug_root + r"\Server\releases\6.0.4"
    exe_path = debug_root + r"\clearxr-streamer.exe"
    force_qr_arg = "--clearxr-headless-force-qr-code " if force_qr_code else ""
    return "".join(
        [
            "$ProgressPreference = 'SilentlyContinue';",
            f"$stalePids = @(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique);",
            "foreach ($stalePid in $stalePids) { if ($stalePid) { Stop-Process -Id $stalePid -Force -ErrorAction SilentlyContinue } }",
            "$staleProcs = @(Get-Process -Name 'clearxr-streamer','NvStreamManager' -ErrorAction SilentlyContinue);",
            "foreach ($staleProc in $staleProcs) { Stop-Process -Id $staleProc.Id -Force -ErrorAction SilentlyContinue }",
            "Start-Sleep -Milliseconds 500;",
            f"$env:PATH = '{release_root};' + $env:PATH;",
            "$env:RUST_LOG = 'info';",
            f"& '{exe_path}' ",
            "--clearxr-headless ",
            f"--clearxr-headless-bundle-id {bundle_id} ",
            f"--clearxr-headless-host {host} ",
            f"--clearxr-headless-port {port} ",
            force_qr_arg,
            f"--clearxr-headless-run-seconds {run_seconds} ",
            f"--clearxr-headless-snapshot-interval-seconds {snapshot_interval_seconds}",
        ]
    )


def build_interactive_headless_wrapper(
    *,
    windows_root: str,
    host: str,
    port: int,
    bundle_id: str,
    run_seconds: int,
    snapshot_interval_seconds: int,
    stdout_log: str,
    stderr_log: str,
    info_json: str,
    force_qr_code: bool = False,
) -> str:
    debug_root = windows_root.rstrip("\\/") + r"\clearxr-streamer\target\x86_64-pc-windows-gnu\debug"
    release_root = debug_root + r"\Server\releases\6.0.4"
    exe_path = debug_root + r"\clearxr-streamer.exe"
    manager_exe = debug_root + r"\Server\NvStreamManager.exe"
    force_qr_entry = "'--clearxr-headless-force-qr-code'," if force_qr_code else ""
    return "".join(
        [
            "$ErrorActionPreference = 'Stop';",
            "$ProgressPreference = 'SilentlyContinue';",
            f"$debugRoot = '{debug_root}';",
            f"$releaseRoot = '{release_root}';",
            f"$exePath = '{exe_path}';",
            f"$managerExe = '{manager_exe}';",
            f"$stdoutLog = '{stdout_log}';",
            f"$stderrLog = '{stderr_log}';",
            f"$infoJson = '{info_json}';",
            "$managerStdout = Join-Path (Split-Path -Parent $stdoutLog) 'manager-stdout.log';",
            "$managerStderr = Join-Path (Split-Path -Parent $stdoutLog) 'manager-stderr.log';",
            "Remove-Item -LiteralPath $stdoutLog,$stderrLog,$infoJson -Force -ErrorAction SilentlyContinue;",
            "Remove-Item -LiteralPath $managerStdout,$managerStderr -Force -ErrorAction SilentlyContinue;",
            f"$stalePids = @(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique);",
            "foreach ($stalePid in $stalePids) { if ($stalePid) { Stop-Process -Id $stalePid -Force -ErrorAction SilentlyContinue } }",
            "$staleProcs = @(Get-Process -Name 'clearxr-streamer','NvStreamManager' -ErrorAction SilentlyContinue);",
            "foreach ($staleProc in $staleProcs) { Stop-Process -Id $staleProc.Id -Force -ErrorAction SilentlyContinue }",
            "Start-Sleep -Milliseconds 500;",
            "$env:PATH = \"$releaseRoot;$env:PATH\";",
            "$env:RUST_LOG = 'info';",
            "$manager = Start-Process -FilePath $managerExe -WorkingDirectory (Split-Path -Parent $managerExe) -PassThru -WindowStyle Hidden -RedirectStandardOutput $managerStdout -RedirectStandardError $managerStderr;",
            "Start-Sleep -Milliseconds 750;",
            "$argList = @(",
            "'--clearxr-headless',",
            f"'--clearxr-headless-bundle-id', '{bundle_id}',",
            f"'--clearxr-headless-host', '{host}',",
            f"'--clearxr-headless-port', '{port}',",
            force_qr_entry,
            f"'--clearxr-headless-run-seconds', '{run_seconds}',",
            f"'--clearxr-headless-snapshot-interval-seconds', '{snapshot_interval_seconds}'",
            ");",
            "$proc = Start-Process -FilePath $exePath -WorkingDirectory $debugRoot -ArgumentList $argList -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog;",
            "[pscustomobject]@{",
            "  PowerShellPid = $PID;",
            "  PowerShellSessionId = (Get-Process -Id $PID).SessionId;",
            "  UserInteractive = [Environment]::UserInteractive;",
            "  ManagerPid = $manager.Id;",
            "  ManagerSessionId = (Get-Process -Id $manager.Id).SessionId;",
            "  ChildPid = $proc.Id;",
            "  ChildSessionId = (Get-Process -Id $proc.Id).SessionId;",
            "  StartedAt = (Get-Date).ToString('o');",
            "} | ConvertTo-Json -Compress | Set-Content -LiteralPath $infoJson -Encoding UTF8;",
            "$proc.WaitForExit();",
            "if (-not $manager.HasExited) { Stop-Process -Id $manager.Id -Force -ErrorAction SilentlyContinue }",
        ]
    )


def build_interactive_task_metadata(run_id: str) -> dict[str, str]:
    remote_root = INTERACTIVE_REMOTE_ROOT + "\\" + run_id
    return {
        "task_name": f"CodexClearXR-{run_id}",
        "remote_root": remote_root,
        "script_path": remote_root + r"\launch-headless.ps1",
        "stdout_log": remote_root + r"\headless-stdout.log",
        "stderr_log": remote_root + r"\headless-stderr.log",
        "info_json": remote_root + r"\launcher-info.json",
    }


def launch_interactive_headless_task(
    ssh_target: str,
    windows_root: str,
    host: str,
    port: int,
    bundle_id: str,
    run_seconds: int,
    snapshot_interval_seconds: int,
    run_id: str,
    force_qr_code: bool = False,
) -> dict[str, str]:
    task = build_interactive_task_metadata(run_id)
    wrapper_script = build_interactive_headless_wrapper(
        windows_root=windows_root,
        host=host,
        port=port,
        bundle_id=bundle_id,
        run_seconds=run_seconds,
        snapshot_interval_seconds=snapshot_interval_seconds,
        stdout_log=task["stdout_log"],
        stderr_log=task["stderr_log"],
        info_json=task["info_json"],
        force_qr_code=force_qr_code,
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".ps1", delete=False) as handle:
        handle.write(wrapper_script)
        local_wrapper_path = Path(handle.name)

    remote_upload_path = f"codex-clearxr-interactive/{run_id}/launch-headless.ps1"
    script = (
        "$ErrorActionPreference = 'Stop';"
        f"New-Item -ItemType Directory -Force -Path '{task['remote_root']}' | Out-Null;"
        f"cmd /c schtasks /Create /TN {task['task_name']} /SC ONCE /ST 23:59 /TR \"powershell.exe -NoProfile -ExecutionPolicy Bypass -File {task['script_path']}\" /RU gaming /IT /F | Out-Null;"
        f"cmd /c schtasks /Run /TN {task['task_name']} | Out-Null"
    )
    try:
        prepare_result = windows_powershell_capture(
            ssh_target,
            "$ErrorActionPreference = 'Stop';"
            f"New-Item -ItemType Directory -Force -Path '{task['remote_root']}' | Out-Null;",
            timeout_seconds=60,
        )
        if prepare_result.returncode != 0:
            raise RuntimeError(
                f"failed to prepare interactive ClearXR task root: {(prepare_result.stdout or '') + (prepare_result.stderr or '')}"
            )

        copy_result = scp_copy(local_wrapper_path, ssh_target, remote_upload_path)
        if copy_result.returncode != 0:
            raise RuntimeError(
                f"failed to upload interactive ClearXR launcher: {(copy_result.stdout or '') + (copy_result.stderr or '')}"
            )

        result = windows_powershell_capture(ssh_target, script, timeout_seconds=120)
    finally:
        local_wrapper_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch interactive ClearXR task: {(result.stdout or '') + (result.stderr or '')}")
    return task


def read_remote_text(ssh_target: str, remote_path: str) -> str:
    result = windows_powershell_capture(
        ssh_target,
        (
            "$ErrorActionPreference = 'Stop';"
            f"$path = '{remote_path}';"
            "if (Test-Path -LiteralPath $path) { Get-Content -LiteralPath $path -Raw }"
        ),
        timeout_seconds=30,
    )
    return result.stdout or ""


def read_remote_json(ssh_target: str, remote_path: str) -> dict[str, Any] | None:
    text = read_remote_text(ssh_target, remote_path).strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def append_new_log_text(log_path: Path, previous_text: str, current_text: str) -> None:
    if current_text == previous_text:
        return
    if current_text.startswith(previous_text):
        delta = current_text[len(previous_text) :]
    else:
        delta = current_text
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(delta)


def wait_for_startup_from_interactive_task(
    ssh_target: str,
    task: dict[str, str],
    log_path: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    previous_text = ""
    while time.monotonic() < deadline:
        current_text = read_remote_text(ssh_target, task["stdout_log"])
        append_new_log_text(log_path, previous_text, current_text)
        previous_text = current_text
        for line in current_text.splitlines():
            snapshot = extract_snapshot_payload(line.strip(), "startup")
            if snapshot is not None:
                return snapshot, read_remote_json(ssh_target, task["info_json"])
        time.sleep(0.5)
    raise RuntimeError("interactive ClearXR task did not emit a startup snapshot in time")


def wait_for_interactive_task_exit(
    ssh_target: str,
    task: dict[str, str],
    child_pid: int | None,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    if child_pid is None:
        time.sleep(min(timeout_seconds, 3))
        return
    while time.monotonic() < deadline:
        result = windows_powershell_capture(
            ssh_target,
            (
                "$ErrorActionPreference = 'Stop';"
                f"$proc = Get-Process -Id {child_pid} -ErrorAction SilentlyContinue;"
                "if ($null -eq $proc) { 'stopped' } else { 'running' }"
            ),
            timeout_seconds=30,
        )
        if "stopped" in (result.stdout or ""):
            return
        time.sleep(1.0)


def collect_interactive_task_output(
    ssh_target: str,
    task: dict[str, str],
    log_path: Path,
) -> dict[str, Any]:
    stdout_text = read_remote_text(ssh_target, task["stdout_log"])
    log_path.write_text(stdout_text, encoding="utf-8")
    remote_log_paths: list[str] = []
    tail: list[str] = []
    for line in stdout_text.splitlines():
        tail.append(line.rstrip())
        if len(tail) > 40:
            tail = tail[-40:]
        remote_log_path = extract_remote_log_path(line.rstrip())
        if remote_log_path is not None and remote_log_path not in remote_log_paths:
            remote_log_paths.append(remote_log_path)
    stderr_log_text = None
    return {
        "remote_log_paths": remote_log_paths,
        "tail": tail,
        "launcher_info": read_remote_json(ssh_target, task["info_json"]),
        "stderr_log_path": task["stderr_log"],
        "stdout_log_text": stdout_text,
        "stderr_log_text": stderr_log_text,
    }


def start_remote_headless_process(
    ssh_target: str,
    windows_root: str,
    host: str,
    port: int,
    bundle_id: str,
    run_seconds: int,
    snapshot_interval_seconds: int,
) -> subprocess.Popen[str]:
    script = build_remote_headless_script(
        windows_root=windows_root,
        host=host,
        port=port,
        bundle_id=bundle_id,
        run_seconds=run_seconds,
        snapshot_interval_seconds=snapshot_interval_seconds,
    )
    return subprocess.Popen(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            ssh_target,
            "powershell.exe",
            "-NoProfile",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16le")).decode("ascii"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_ssh_tunnel(
    ssh_target: str,
    remote_host: str,
    remote_port: int,
    local_port: int,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-N",
            "-L",
            f"127.0.0.1:{local_port}:{remote_host}:{remote_port}",
            ssh_target,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def wait_for_startup(process: subprocess.Popen[str], log_path: Path) -> dict[str, Any]:
    stdout = process.stdout
    if stdout is None:
        raise RuntimeError("remote ClearXR process has no stdout pipe")

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    with log_path.open("w", encoding="utf-8") as handle:
        while time.monotonic() < deadline:
            line = stdout.readline()
            if not line:
                if process.poll() is not None:
                    raise RuntimeError("remote ClearXR backend exited before startup completed")
                time.sleep(0.1)
                continue

            handle.write(line)
            handle.flush()
            print(line.rstrip(), flush=True)

            snapshot = extract_snapshot_payload(line.strip(), "startup")
            if snapshot is not None:
                return snapshot

    raise RuntimeError("remote ClearXR backend did not emit a startup snapshot in time")


def drain_remote_output(process: subprocess.Popen[str], log_path: Path) -> dict[str, Any]:
    stdout = process.stdout
    if stdout is None:
        return {"remote_log_paths": [], "tail": []}

    remote_log_paths: list[str] = []
    tail: list[str] = []
    with log_path.open("a", encoding="utf-8") as handle:
        for line in stdout:
            handle.write(line)
            handle.flush()
            print(line.rstrip(), flush=True)

            stripped = line.rstrip()
            tail.append(stripped)
            if len(tail) > 40:
                tail = tail[-40:]

            remote_log_path = extract_remote_log_path(stripped)
            if remote_log_path is not None and remote_log_path not in remote_log_paths:
                remote_log_paths.append(remote_log_path)

    return {
        "remote_log_paths": remote_log_paths,
        "tail": tail,
    }


def fetch_remote_file(ssh_target: str, remote_path: str, destination: Path) -> dict[str, Any]:
    escaped_path = remote_path.replace("'", "''")
    result = windows_powershell_capture(
        ssh_target,
        (
            "$ErrorActionPreference = 'Stop';"
            f"$path = '{escaped_path}';"
            "if (-not (Test-Path -LiteralPath $path)) { throw \"missing remote file: $path\" };"
            "$bytes = [System.IO.File]::ReadAllBytes($path);"
            "[Convert]::ToBase64String($bytes)"
        ),
        timeout_seconds=120,
    )
    if result.returncode == 0:
        payload = base64.b64decode((result.stdout or "").strip())
        destination.write_bytes(payload)
    return {
        "returncode": result.returncode,
        "stdout_path": str(destination) if result.returncode == 0 else None,
        "stderr_tail": (result.stderr or "")[-2000:],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an unattended ClearXR control-plane probe against the Windows backend on winders",
    )
    parser.add_argument("--windows-ssh-target", default=DEFAULT_WINDOWS_SSH_TARGET)
    parser.add_argument("--windows-root", default=DEFAULT_WINDOWS_ROOT)
    parser.add_argument("--host", default="auto", help="advertised host for the Windows ClearXR backend")
    parser.add_argument("--port", type=int, default=55000)
    parser.add_argument("--bundle-id", default="com.shinycomputers.clearxrclient")
    parser.add_argument("--run-seconds", type=int, default=45)
    parser.add_argument("--snapshot-interval-seconds", type=int, default=1)
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE_ROOT))
    parser.add_argument(
        "--launcher-mode",
        choices=("session0", "interactive-task"),
        default="session0",
        help="launch headless ClearXR directly over SSH or through an interactive scheduled task",
    )
    parser.add_argument(
        "--skip-stage",
        action="store_true",
        help="skip refreshing the staged Windows bundle before launch",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    host = detect_windows_host_address(args.windows_ssh_target) if args.host == "auto" else args.host

    run_root = Path(args.archive_root).resolve()
    run_dir = run_root / f"{utc_stamp()}-probe-clearxr-winders"
    logs_dir = run_dir / "logs"
    config_dir = run_dir / "config"
    logs_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    print(f"RUN={run_dir}", flush=True)
    meta: dict[str, Any] = {
        "windows_ssh_target": args.windows_ssh_target,
        "windows_root": args.windows_root,
        "host": host,
        "port": args.port,
        "bundle_id": args.bundle_id,
        "run_seconds": args.run_seconds,
        "snapshot_interval_seconds": args.snapshot_interval_seconds,
        "skip_stage": args.skip_stage,
        "launcher_mode": args.launcher_mode,
    }
    write_json(config_dir / "meta.json", meta)

    if not args.skip_stage:
        meta["stage_report"] = stage_to_winders(args.windows_ssh_target, args.windows_root)
        write_json(config_dir / "meta.json", meta)
        print("WINDOWS_STAGE_REFRESHED=1", flush=True)

    headless_log = logs_dir / "clearxr-headless-winders.log"
    startup_snapshot: dict[str, Any] | None = None
    startup_error: str | None = None
    probe_messages: dict[str, Any] | None = None
    remote_output: dict[str, Any] | None = None
    launcher_info: dict[str, Any] | None = None
    tunnel_process: subprocess.Popen[str] | None = None
    tunnel_log_path = logs_dir / "ssh-tunnel.log"
    process: subprocess.Popen[str] | None = None
    interactive_task: dict[str, str] | None = None
    try:
        if args.launcher_mode == "session0":
            process = start_remote_headless_process(
                args.windows_ssh_target,
                args.windows_root,
                host,
                args.port,
                args.bundle_id,
                args.run_seconds,
                args.snapshot_interval_seconds,
            )
            startup_snapshot = wait_for_startup(process, headless_log)
        else:
            interactive_task = launch_interactive_headless_task(
                args.windows_ssh_target,
                args.windows_root,
                host,
                args.port,
                args.bundle_id,
                args.run_seconds,
                args.snapshot_interval_seconds,
                utc_stamp(),
            )
            startup_snapshot, launcher_info = wait_for_startup_from_interactive_task(
                args.windows_ssh_target,
                interactive_task,
                headless_log,
            )
        meta["startup_snapshot"] = startup_snapshot
        meta["launcher_info"] = launcher_info
        write_json(config_dir / "meta.json", meta)

        session_id = f"probe-session-{utc_stamp()}"
        client_id = "winders-control-probe"
        messages = build_probe_messages(session_id, client_id)
        probe_messages = {
            "apply_configuration": messages["apply_configuration"],
            "request_connection": messages["request_connection"],
            "waiting_status": messages["waiting_status"],
            "disconnect_status": messages["disconnect_status"],
        }

        actual_port = int(startup_snapshot.get("config", {}).get("port", args.port))
        local_port = find_free_local_port()
        tunnel_process = start_ssh_tunnel(
            args.windows_ssh_target,
            host,
            actual_port,
            local_port,
        )
        time.sleep(1.0)
        if tunnel_process.poll() is not None:
            raise RuntimeError("ssh tunnel exited before the probe client connected")

        with socket.create_connection(("127.0.0.1", local_port), timeout=5) as sock:
            sock.settimeout(READY_TIMEOUT_SECONDS)
            write_frame(sock, messages["apply_configuration"])
            write_frame(sock, messages["request_connection"])
            acknowledge = read_frame(sock)
            probe_messages["acknowledge_connection"] = acknowledge
            write_frame(sock, messages["waiting_status"])
            try:
                ready = read_frame(sock)
            except (ConnectionError, TimeoutError, socket.timeout, OSError) as error:
                ready = None
                probe_messages["media_stream_ready_error"] = str(error)
            else:
                probe_messages["media_stream_ready"] = ready

            try:
                write_frame(sock, messages["disconnect_status"])
            except OSError as error:
                probe_messages["disconnect_error"] = str(error)

        meta["probe_messages"] = probe_messages
        write_json(config_dir / "meta.json", meta)
        if args.launcher_mode == "session0":
            assert process is not None
            remote_output = drain_remote_output(process, headless_log)
        else:
            child_pid = None
            if isinstance(launcher_info, dict):
                raw_child_pid = launcher_info.get("ChildPid")
                if isinstance(raw_child_pid, (int, str)) and str(raw_child_pid):
                    child_pid = int(raw_child_pid)
            if interactive_task is not None:
                wait_for_interactive_task_exit(
                    args.windows_ssh_target,
                    interactive_task,
                    child_pid,
                    max(args.run_seconds, 1) + 5,
                )
                remote_output = collect_interactive_task_output(
                    args.windows_ssh_target,
                    interactive_task,
                    headless_log,
                )
    except Exception as error:  # noqa: BLE001
        startup_error = str(error)
        print(f"PROBE_ERROR={startup_error}", flush=True)
        if process is not None and process.poll() is None:
            process.terminate()
    finally:
        if tunnel_process is not None:
            if tunnel_process.poll() is None:
                tunnel_process.terminate()
            tunnel_output, _ = tunnel_process.communicate(timeout=5)
            tunnel_log_path.write_text(tunnel_output or "", encoding="utf-8")
        if process is not None:
            try:
                process.wait(timeout=max(args.run_seconds, 1) + 5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    fetched_logs: list[dict[str, Any]] = []
    if remote_output is not None:
        for index, remote_path in enumerate(remote_output.get("remote_log_paths", []), start=1):
            destination = logs_dir / f"cloudxr-streamsdk-{index:02d}.log"
            fetched_logs.append(fetch_remote_file(args.windows_ssh_target, remote_path, destination))
        stderr_log_path = remote_output.get("stderr_log_path")
        if isinstance(stderr_log_path, str):
            destination = logs_dir / "clearxr-headless-winders.stderr.log"
            fetched_logs.append(fetch_remote_file(args.windows_ssh_target, stderr_log_path, destination))

    outcome = {
        "pass": False,
        "server_process_returncode": process.returncode if process is not None else None,
        "startup_error": startup_error,
        "startup_snapshot": startup_snapshot,
        "probe_messages": probe_messages,
        "remote_output": remote_output,
        "fetched_logs": fetched_logs,
        "launcher_info": launcher_info,
    }

    acknowledge = (probe_messages or {}).get("acknowledge_connection", {})
    ready_message = (probe_messages or {}).get("media_stream_ready", {})
    outcome["pass"] = (
        startup_error is None
        and acknowledge.get("Event") == "AcknowledgeConnection"
        and bool(acknowledge.get("CertificateFingerprint"))
        and ready_message.get("Event") == "MediaStreamIsReady"
    )

    write_json(run_dir / "probe_summary.json", outcome)
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0 if outcome["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
