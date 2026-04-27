#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import subprocess
from typing import Any


DEFAULT_WINDOWS_SSH_TARGET = "gaming@winders"
DEFAULT_WINDOWS_ROOT = r"C:\dev\clearxr-server"


def run_capture(command: list[str], timeout_seconds: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def windows_powershell_capture(ssh_target: str, script: str, timeout_seconds: int = 180) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return run_capture(
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
            encoded,
        ],
        timeout_seconds=timeout_seconds,
    )


def detect_host_address(ssh_target: str) -> str:
    script = (
        "$ProgressPreference = 'SilentlyContinue';"
        "$ip = Get-NetIPAddress -AddressFamily IPv4 | "
        "Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254*' } | "
        "Sort-Object -Property InterfaceMetric, SkipAsSource | "
        "Select-Object -First 1 -ExpandProperty IPAddress;"
        "if (-not $ip) { throw 'no usable IPv4 address found' };"
        "Write-Output $ip"
    )
    result = windows_powershell_capture(ssh_target, script, timeout_seconds=30)
    if result.returncode != 0:
        raise RuntimeError(((result.stdout or "") + (result.stderr or "")).strip())
    for line in (result.stdout or "").splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#<"):
            return candidate
    raise RuntimeError("failed to detect a usable Windows IPv4 address")


def build_smoke_script(windows_root: str, host_address: str, run_seconds: int) -> str:
    debug_root = windows_root.rstrip("\\/") + r"\clearxr-streamer\target\x86_64-pc-windows-gnu\debug"
    release_root = debug_root + r"\Server\releases\6.0.4"
    exe_path = debug_root + r"\clearxr-streamer.exe"
    return (
        "$ProgressPreference = 'SilentlyContinue';"
        f"$env:PATH = '{release_root};' + $env:PATH;"
        "$env:RUST_LOG = 'info';"
        f"& '{exe_path}' "
        "--clearxr-headless "
        f"--clearxr-headless-host {host_address} "
        f"--clearxr-headless-run-seconds {run_seconds} "
        "--clearxr-headless-force-qr-code "
        "--clearxr-headless-snapshot-interval-seconds 2"
    )


def extract_snapshot_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("CLEARXR_HEADLESS_SNAPSHOT ")]


def run_smoke(ssh_target: str, windows_root: str, run_seconds: int, host_address: str | None) -> dict[str, Any]:
    resolved_host_address = host_address or detect_host_address(ssh_target)
    result = windows_powershell_capture(
        ssh_target,
        build_smoke_script(windows_root, resolved_host_address, run_seconds),
        timeout_seconds=max(180, run_seconds + 60),
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    return {
        "ssh_target": ssh_target,
        "windows_root": windows_root,
        "host_address": resolved_host_address,
        "run_seconds": run_seconds,
        "returncode": result.returncode,
        "snapshot_lines": extract_snapshot_lines(stdout),
        "stdout": stdout,
        "stderr": stderr,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a PATH-prepped ClearXR headless smoke on winders")
    parser.add_argument("--windows-ssh-target", default=DEFAULT_WINDOWS_SSH_TARGET)
    parser.add_argument("--windows-root", default=DEFAULT_WINDOWS_ROOT)
    parser.add_argument("--run-seconds", type=int, default=8)
    parser.add_argument("--host-address", help="override the advertised Windows IPv4 address")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke(args.windows_ssh_target, args.windows_root, args.run_seconds, args.host_address)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
