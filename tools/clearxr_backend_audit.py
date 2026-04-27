#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_PROXMOX_HOST = "prox-main.shiny"
DEFAULT_VM_ID = "201"
DEFAULT_WINDOWS_SSH_TARGET = "gaming@winders"
DEFAULT_WINDOWS_ROOT = r"C:\dev\clearxr-server"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_capture(command: list[str], timeout_seconds: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def local_clearxr_state() -> dict[str, Any]:
    clearxr_root = repo_root() / "temp" / "external" / "clearxr-server"
    vendor_root = clearxr_root / "vendor" / "Server" / "releases"
    windows_build_root = (
        clearxr_root
        / "clearxr-streamer"
        / "target"
        / "x86_64-pc-windows-gnu"
        / "debug"
        / "Server"
        / "releases"
    )

    local_vendor_manifests = sorted(vendor_root.glob("**/openxr_cloudxr.json"))
    local_vendor_dlls = sorted(vendor_root.glob("**/cloudxr.dll"))
    windows_build_manifests = sorted(windows_build_root.glob("**/openxr_cloudxr.json"))
    windows_build_dlls = sorted(windows_build_root.glob("**/cloudxr.dll"))

    return {
        "clearxr_root": str(clearxr_root),
        "local_vendor_runtime_ready": bool(local_vendor_manifests and local_vendor_dlls),
        "local_vendor_manifest_paths": [str(path) for path in local_vendor_manifests],
        "local_vendor_dll_paths": [str(path) for path in local_vendor_dlls],
        "windows_build_runtime_available": bool(windows_build_manifests and windows_build_dlls),
        "windows_build_manifest_paths": [str(path) for path in windows_build_manifests],
        "windows_build_dll_paths": [str(path) for path in windows_build_dlls],
    }


def ssh_capture(host: str, remote_command: list[str], timeout_seconds: int = 30) -> subprocess.CompletedProcess[str]:
    return run_capture(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            host,
            shlex.join(remote_command),
        ],
        timeout_seconds=timeout_seconds,
    )


def windows_powershell_capture(ssh_target: str, script: str, timeout_seconds: int = 30) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return ssh_capture(
        ssh_target,
        ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
        timeout_seconds=timeout_seconds,
    )


def windows_ssh_state(ssh_target: str) -> dict[str, Any]:
    result = ssh_capture(ssh_target, ["hostname"], timeout_seconds=10)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return {
        "ssh_reachable": result.returncode == 0,
        "returncode": result.returncode,
        "output": output,
    }


def proxmox_vm_status(host: str, vm_id: str) -> dict[str, Any]:
    status_result = ssh_capture(host, ["qm", "status", vm_id])
    list_result = ssh_capture(host, ["qm", "list"])
    status_text = (status_result.stdout or "") + (status_result.stderr or "")
    status = "unknown"
    for line in status_text.splitlines():
        if line.startswith("status:"):
            status = line.split(":", 1)[1].strip()
            break

    return {
        "ssh_reachable": status_result.returncode == 0 or list_result.returncode == 0,
        "qm_status_returncode": status_result.returncode,
        "qm_status_output": status_text.strip(),
        "qm_list_returncode": list_result.returncode,
        "qm_list_excerpt": ((list_result.stdout or "") + (list_result.stderr or "")).strip()[-4000:],
        "status": status,
    }


def guest_exec_powershell(host: str, vm_id: str, script: str, timeout_seconds: int = 30) -> dict[str, Any]:
    result = ssh_capture(
        host,
        ["qm", "guest", "exec", vm_id, "--", "powershell.exe", "-NoProfile", "-Command", script],
        timeout_seconds=timeout_seconds,
    )
    text = (result.stdout or "") + (result.stderr or "")
    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    return {
        "returncode": result.returncode,
        "raw_output": text.strip(),
        "parsed": parsed,
    }


def guest_agent_state(host: str, vm_id: str) -> dict[str, Any]:
    ping_result = ssh_capture(host, ["qm", "guest", "cmd", vm_id, "ping"], timeout_seconds=15)
    output = ((ping_result.stdout or "") + (ping_result.stderr or "")).strip()
    return {
        "guest_agent_ready": ping_result.returncode == 0,
        "returncode": ping_result.returncode,
        "output": output,
    }


def build_windows_path_probe_script(windows_root: str) -> str:
    root = windows_root.rstrip("\\/")
    release_root = f"{root}\\clearxr-streamer\\target\\x86_64-pc-windows-gnu\\debug\\Server\\releases\\6.0.4"
    streamer_root = f"{root}\\clearxr-streamer\\target\\x86_64-pc-windows-gnu\\debug"
    return (
        "$ProgressPreference = 'SilentlyContinue';"
        "$state = [ordered]@{"
        f"clearxrServer = (Test-Path '{root}');"
        f"clearxr = (Test-Path '{root.replace('clearxr-server', 'clearxr')}');"
        "alvr = (Test-Path 'C:\\dev\\ALVR');"
        f"runtimeManifest = (Test-Path '{root}\\vendor\\Server\\releases\\6.0.4\\openxr_cloudxr.json');"
        f"runtimeDll = (Test-Path '{root}\\vendor\\Server\\releases\\6.0.4\\cloudxr.dll');"
        f"windowsBuildManifest = (Test-Path '{release_root}\\openxr_cloudxr.json');"
        f"windowsBuildDll = (Test-Path '{release_root}\\cloudxr.dll');"
        f"streamerExe = (Test-Path '{streamer_root}\\clearxr-streamer.exe');"
        f"openXrLoaderDll = (Test-Path '{streamer_root}\\openxr_loader.dll');"
        "}; $state | ConvertTo-Json -Compress"
    )


def parse_guest_path_probe(path_probe: dict[str, Any]) -> dict[str, Any] | None:
    parsed = path_probe.get("parsed") or {}
    out_data = parsed.get("out-data") if isinstance(parsed, dict) else None
    guest_paths: dict[str, Any] | None = None
    if isinstance(out_data, str):
        try:
            guest_paths = json.loads(out_data.strip())
        except json.JSONDecodeError:
            guest_paths = None

    if guest_paths is None and isinstance(parsed, dict):
        guest_paths = parsed if parsed else None

    if guest_paths is None:
        raw_output = path_probe.get("raw_output")
        if isinstance(raw_output, str):
            for line in raw_output.splitlines():
                candidate = line.strip()
                if not candidate.startswith("{"):
                    continue
                try:
                    guest_paths = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue

    return guest_paths


def guest_clearxr_state(host: str, vm_id: str, windows_root: str) -> dict[str, Any]:
    path_probe = guest_exec_powershell(host, vm_id, build_windows_path_probe_script(windows_root))
    guest_paths = parse_guest_path_probe(path_probe)

    return {
        "probe_method": "qga",
        "path_probe": path_probe,
        "paths": guest_paths,
    }


def windows_clearxr_state_via_ssh(ssh_target: str, windows_root: str) -> dict[str, Any]:
    result = windows_powershell_capture(ssh_target, build_windows_path_probe_script(windows_root), timeout_seconds=20)
    raw_output = ((result.stdout or "") + (result.stderr or "")).strip()
    parsed: dict[str, Any] | None = None
    if raw_output:
        lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
        candidate = lines[-1] if lines else raw_output
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
    path_probe = {
        "returncode": result.returncode,
        "raw_output": raw_output,
        "parsed": parsed,
    }
    guest_paths = parse_guest_path_probe(path_probe)

    return {
        "probe_method": "windows_ssh",
        "path_probe": path_probe,
        "paths": guest_paths,
    }


def build_report(host: str, vm_id: str, windows_ssh_target: str, windows_root: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "local": local_clearxr_state(),
        "proxmox": proxmox_vm_status(host, vm_id),
        "windows_ssh": windows_ssh_state(windows_ssh_target),
    }

    if report["windows_ssh"].get("ssh_reachable"):
        report["guest"] = windows_clearxr_state_via_ssh(windows_ssh_target, windows_root)
    elif report["proxmox"].get("status") == "running":
        guest_agent = guest_agent_state(host, vm_id)
        report["guest_agent"] = guest_agent
        if guest_agent.get("guest_agent_ready"):
            report["guest"] = guest_clearxr_state(host, vm_id, windows_root)

    local = report["local"]
    proxmox = report["proxmox"]
    windows_ssh = report.get("windows_ssh", {})
    guest = report.get("guest", {})
    guest_paths = guest.get("paths") if isinstance(guest, dict) else None
    guest_paths = guest_paths if isinstance(guest_paths, dict) else {}

    report["summary"] = {
        "macos_control_plane_only": not bool(local.get("local_vendor_runtime_ready")),
        "local_windows_runtime_artifacts_available": bool(local.get("windows_build_runtime_available")),
        "windows_vm_running": bool(proxmox.get("status") == "running" or windows_ssh.get("ssh_reachable")),
        "windows_direct_ssh_reachable": bool(windows_ssh.get("ssh_reachable")),
        "windows_guest_clearxr_repo_present": bool(guest_paths.get("clearxrServer") or guest_paths.get("clearxr")),
        "windows_guest_runtime_ready": bool(
            (guest_paths.get("runtimeManifest") and guest_paths.get("runtimeDll"))
            or (
                guest_paths.get("windowsBuildManifest")
                and guest_paths.get("windowsBuildDll")
                and guest_paths.get("streamerExe")
                and guest_paths.get("openXrLoaderDll")
            )
        ),
        "windows_guest_build_runtime_available": bool(
            guest_paths.get("windowsBuildManifest") and guest_paths.get("windowsBuildDll")
        ),
        "windows_guest_streamer_executable_present": bool(
            guest_paths.get("streamerExe") and guest_paths.get("openXrLoaderDll")
        ),
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit local and Windows-lane ClearXR backend readiness")
    parser.add_argument("--proxmox-host", default=DEFAULT_PROXMOX_HOST)
    parser.add_argument("--vm-id", default=DEFAULT_VM_ID)
    parser.add_argument("--windows-ssh-target", default=DEFAULT_WINDOWS_SSH_TARGET)
    parser.add_argument("--windows-root", default=DEFAULT_WINDOWS_ROOT)
    parser.add_argument("--json", action="store_true", help="emit compact JSON only")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(args.proxmox_host, args.vm_id, args.windows_ssh_target, args.windows_root)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
