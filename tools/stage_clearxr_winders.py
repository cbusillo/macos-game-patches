#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_WINDOWS_SSH_TARGET = "gaming@winders"
DEFAULT_WINDOWS_ROOT = r"C:\dev\clearxr-server"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def local_debug_root() -> Path:
    return (
        repo_root()
        / "temp"
        / "external"
        / "clearxr-server"
        / "clearxr-streamer"
        / "target"
        / "x86_64-pc-windows-gnu"
        / "debug"
    )


def run_capture(command: list[str], timeout_seconds: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def ssh_capture(ssh_target: str, remote_command: list[str], timeout_seconds: int = 60) -> subprocess.CompletedProcess[str]:
    return run_capture(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            ssh_target,
            shlex.join(remote_command),
        ],
        timeout_seconds=timeout_seconds,
    )


def windows_powershell_capture(ssh_target: str, script: str, timeout_seconds: int = 60) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return ssh_capture(
        ssh_target,
        ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
        timeout_seconds=timeout_seconds,
    )


def scp_copy(local_path: Path, ssh_target: str, remote_destination: str, recursive: bool = False) -> subprocess.CompletedProcess[str]:
    command = ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    if recursive:
        command.append("-r")
    command.extend([str(local_path), f"{ssh_target}:{remote_destination}"])
    return run_capture(command, timeout_seconds=600)


def required_local_paths() -> dict[str, Path]:
    debug_root = local_debug_root()
    return {
        "clearxr_streamer_exe": debug_root / "clearxr-streamer.exe",
        "openxr_loader_dll": debug_root / "openxr_loader.dll",
        "nvstream_manager_client_dll": debug_root / "NvStreamManagerClient.dll",
        "webview2_loader_dll": debug_root / "WebView2Loader.dll",
        "server_dir": debug_root / "Server",
    }


def ensure_local_inputs(paths: dict[str, Path]) -> None:
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing local ClearXR Windows artifacts: {missing}")


def build_remote_paths(windows_root: str) -> dict[str, str]:
    root = windows_root.rstrip("\\/")
    debug_root = f"{root}\\clearxr-streamer\\target\\x86_64-pc-windows-gnu\\debug"
    return {
        "windows_root": root,
        "windows_debug_root": debug_root,
        "scp_debug_root": debug_root.replace("\\", "/"),
        "windows_upload_dirname": "clearxr-stage-upload",
        "scp_upload_root": "clearxr-stage-upload",
    }


def prepare_remote_root(ssh_target: str, remote_paths: dict[str, str]) -> subprocess.CompletedProcess[str]:
    debug_root = remote_paths["windows_debug_root"]
    upload_dirname = remote_paths["windows_upload_dirname"]
    script = (
        f"$debugRoot = '{debug_root}';"
        f"$uploadRoot = Join-Path $env:USERPROFILE '{upload_dirname}';"
        "New-Item -ItemType Directory -Force -Path $debugRoot | Out-Null;"
        "New-Item -ItemType Directory -Force -Path $uploadRoot | Out-Null;"
        "$managedPaths = @("
        "(Join-Path $debugRoot 'clearxr-streamer.exe'),"
        "(Join-Path $debugRoot 'openxr_loader.dll'),"
        "(Join-Path $debugRoot 'NvStreamManagerClient.dll'),"
        "(Join-Path $debugRoot 'WebView2Loader.dll'),"
        "(Join-Path $debugRoot 'Server')"
        ");"
        "foreach ($path in $managedPaths) {"
        "  if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force }"
        "}"
        "Get-ChildItem -LiteralPath $uploadRoot -Force -ErrorAction SilentlyContinue | "
        "  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue;"
        "New-Item -ItemType Directory -Force -Path $debugRoot | Out-Null"
    )
    return windows_powershell_capture(ssh_target, script, timeout_seconds=120)


def promote_uploaded_bundle(ssh_target: str, remote_paths: dict[str, str]) -> subprocess.CompletedProcess[str]:
    debug_root = remote_paths["windows_debug_root"]
    upload_dirname = remote_paths["windows_upload_dirname"]
    script = (
        "$ErrorActionPreference = 'Stop';"
        f"$debugRoot = '{debug_root}';"
        f"$uploadRoot = Join-Path $env:USERPROFILE '{upload_dirname}';"
        "$required = @(" 
        "(Join-Path $uploadRoot 'clearxr-streamer.exe'),"
        "(Join-Path $uploadRoot 'openxr_loader.dll'),"
        "(Join-Path $uploadRoot 'NvStreamManagerClient.dll'),"
        "(Join-Path $uploadRoot 'WebView2Loader.dll'),"
        "(Join-Path $uploadRoot 'Server')"
        ");"
        "foreach ($path in $required) { if (-not (Test-Path $path)) { throw \"missing uploaded artifact: $path\" } }"
        "Copy-Item -LiteralPath (Join-Path $uploadRoot 'clearxr-streamer.exe') -Destination $debugRoot -Force;"
        "Copy-Item -LiteralPath (Join-Path $uploadRoot 'openxr_loader.dll') -Destination $debugRoot -Force;"
        "Copy-Item -LiteralPath (Join-Path $uploadRoot 'NvStreamManagerClient.dll') -Destination $debugRoot -Force;"
        "Copy-Item -LiteralPath (Join-Path $uploadRoot 'WebView2Loader.dll') -Destination $debugRoot -Force;"
        "Copy-Item -LiteralPath (Join-Path $uploadRoot 'Server') -Destination $debugRoot -Recurse -Force;"
        "[pscustomobject]@{ promoted = $true } | ConvertTo-Json -Compress"
    )
    return windows_powershell_capture(ssh_target, script, timeout_seconds=300)


def ensure_remote_firewall_rules(ssh_target: str, remote_paths: dict[str, str]) -> dict[str, Any]:
    debug_root = remote_paths["windows_debug_root"]
    script = (
        "$ErrorActionPreference = 'Stop';"
        "$ProgressPreference = 'SilentlyContinue';"
        "$rules = @(" 
        "@{ DisplayName='ClearXR Session TCP 55000'; Direction='Inbound'; Action='Allow'; Protocol='TCP'; LocalPort=55000 },"
        "@{ DisplayName='ClearXR Session UDP 55000'; Direction='Inbound'; Action='Allow'; Protocol='UDP'; LocalPort=55000 },"
        "@{ DisplayName='ClearXR Media TCP 48322'; Direction='Inbound'; Action='Allow'; Protocol='TCP'; LocalPort=48322 },"
        "@{ DisplayName='ClearXR Media UDP 48322'; Direction='Inbound'; Action='Allow'; Protocol='UDP'; LocalPort=48322 },"
        f"@{{ DisplayName='ClearXR Streamer Program'; Direction='Inbound'; Action='Allow'; Program='{debug_root}\\clearxr-streamer.exe' }},"
        f"@{{ DisplayName='ClearXR CloudXR Service Program'; Direction='Inbound'; Action='Allow'; Program='{debug_root}\\Server\\CloudXrService.exe' }}"
        ");"
        "foreach ($params in $rules) {"
        "  if (-not (Get-NetFirewallRule -DisplayName $params.DisplayName -ErrorAction SilentlyContinue)) {"
        "    New-NetFirewallRule @params | Out-Null"
        "  }"
        "}"
        "Get-NetFirewallRule -DisplayName 'ClearXR*' | "
        "Select-Object DisplayName,Enabled,Direction,Action | ConvertTo-Json -Compress"
    )
    result = windows_powershell_capture(ssh_target, script, timeout_seconds=120)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    parsed: Any = None
    for line in output.splitlines():
        candidate = line.strip()
        if not (candidate.startswith("{") or candidate.startswith("[")):
            continue
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    return {
        "returncode": result.returncode,
        "raw_output": output,
        "parsed": parsed,
    }


def ensure_cloudxr_ports_available(ssh_target: str) -> dict[str, Any]:
    script = (
        "$ErrorActionPreference = 'Stop';"
        "$ProgressPreference = 'SilentlyContinue';"
        "$service = Get-Service -Name 'SunshineService' -ErrorAction SilentlyContinue;"
        "if ($null -ne $service -and $service.Status -ne 'Stopped') {"
        "  Stop-Service -Name 'SunshineService' -Force -ErrorAction SilentlyContinue;"
        "  $service.WaitForStatus('Stopped', '00:00:10');"
        "}"
        "$sunshineProcs = @(Get-Process -Name 'sunshine' -ErrorAction SilentlyContinue);"
        "foreach ($proc in $sunshineProcs) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }"
        "$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
        "  Where-Object { $_.LocalPort -in @(47984, 47989, 47990, 48010, 48322) } | "
        "  Select-Object LocalAddress,LocalPort,OwningProcess;"
        "$udpListeners = Get-NetUDPEndpoint -ErrorAction SilentlyContinue | "
        "  Where-Object { $_.LocalPort -in @(47998, 47999, 48000, 48322) } | "
        "  Select-Object LocalAddress,LocalPort,OwningProcess;"
        "$state = [ordered]@{"
        "  sunshineService = if ($null -eq $service) { $null } else { [ordered]@{ Name=$service.Name; Status=$service.Status; StartType=$service.StartType.ToString() } };"
        "  sunshineProcesses = @($sunshineProcs | Select-Object Id,ProcessName);"
        "  tcpListeners = @($listeners);"
        "  udpListeners = @($udpListeners);"
        "};"
        "$state | ConvertTo-Json -Compress -Depth 4"
    )
    result = windows_powershell_capture(ssh_target, script, timeout_seconds=120)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    parsed: Any = None
    for line in output.splitlines():
        candidate = line.strip()
        if not (candidate.startswith("{") or candidate.startswith("[")):
            continue
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    return {
        "returncode": result.returncode,
        "raw_output": output,
        "parsed": parsed,
    }


def ensure_openxr_runtime_registration(ssh_target: str, remote_paths: dict[str, str]) -> dict[str, Any]:
    debug_root = remote_paths["windows_debug_root"]
    runtime_manifest = f"{debug_root}\\Server\\releases\\6.0.4\\openxr_cloudxr.json"
    script = (
        "$ErrorActionPreference = 'Stop';"
        "$ProgressPreference = 'SilentlyContinue';"
        f"$runtimeManifest = '{runtime_manifest}';"
        "if (-not (Test-Path $runtimeManifest)) { throw \"missing runtime manifest\" };"
        "New-Item -Path 'HKLM:\\SOFTWARE\\Khronos\\OpenXR\\1' -Force | Out-Null;"
        "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Khronos\\OpenXR\\1' -Name 'ActiveRuntime' -Value $runtimeManifest;"
        "$state = [ordered]@{"
        "  activeRuntimeHklm = (Get-ItemProperty -Path 'HKLM:\\SOFTWARE\\Khronos\\OpenXR\\1' -Name ActiveRuntime).ActiveRuntime;"
        "  activeRuntimeHkcu = (Get-ItemProperty -Path 'HKCU:\\SOFTWARE\\Khronos\\OpenXR\\1' -Name ActiveRuntime -ErrorAction SilentlyContinue).ActiveRuntime;"
        f"  expectedRuntime = '{runtime_manifest}';"
        "}; $state | ConvertTo-Json -Compress"
    )
    result = windows_powershell_capture(ssh_target, script, timeout_seconds=120)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    parsed: Any = None
    for line in output.splitlines():
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    return {
        "returncode": result.returncode,
        "raw_output": output,
        "parsed": parsed,
    }


def verify_remote_stage(ssh_target: str, remote_paths: dict[str, str]) -> dict[str, Any]:
    debug_root = remote_paths["windows_debug_root"]
    script = (
        "$ProgressPreference = 'SilentlyContinue';"
        "$state = [ordered]@{"
        f"streamerExe = (Test-Path '{debug_root}\\clearxr-streamer.exe');"
        f"openxrLoader = (Test-Path '{debug_root}\\openxr_loader.dll');"
        f"nvstreamManagerClient = (Test-Path '{debug_root}\\NvStreamManagerClient.dll');"
        f"webview2Loader = (Test-Path '{debug_root}\\WebView2Loader.dll');"
        f"serverDir = (Test-Path '{debug_root}\\Server');"
        f"runtimeManifest = (Test-Path '{debug_root}\\Server\\releases\\6.0.4\\openxr_cloudxr.json');"
        f"runtimeDll = (Test-Path '{debug_root}\\Server\\releases\\6.0.4\\cloudxr.dll');"
        "}; $state | ConvertTo-Json -Compress"
    )
    result = windows_powershell_capture(ssh_target, script, timeout_seconds=30)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    parsed: dict[str, Any] = {}
    for line in output.splitlines():
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    return {
        "returncode": result.returncode,
        "raw_output": output,
        "parsed": parsed,
    }


def stage_to_winders(ssh_target: str, windows_root: str) -> dict[str, Any]:
    local_paths = required_local_paths()
    ensure_local_inputs(local_paths)
    remote_paths = build_remote_paths(windows_root)

    prepare_result = prepare_remote_root(ssh_target, remote_paths)
    if prepare_result.returncode != 0:
        raise RuntimeError(((prepare_result.stdout or "") + (prepare_result.stderr or "")).strip())

    copied: list[dict[str, Any]] = []
    for key in (
        "clearxr_streamer_exe",
        "openxr_loader_dll",
        "nvstream_manager_client_dll",
        "webview2_loader_dll",
    ):
        local_path = local_paths[key]
        result = scp_copy(local_path, ssh_target, remote_paths["scp_upload_root"])
        copied.append(
            {
                "name": key,
                "local_path": str(local_path),
                "returncode": result.returncode,
                "output": ((result.stdout or "") + (result.stderr or "")).strip(),
            }
        )
        if result.returncode != 0:
            raise RuntimeError(f"failed to copy {local_path}: {copied[-1]['output']}")

    server_result = scp_copy(local_paths["server_dir"], ssh_target, remote_paths["scp_upload_root"], recursive=True)
    copied.append(
        {
            "name": "server_dir",
            "local_path": str(local_paths["server_dir"]),
            "returncode": server_result.returncode,
            "output": ((server_result.stdout or "") + (server_result.stderr or "")).strip(),
        }
    )
    if server_result.returncode != 0:
        raise RuntimeError(f"failed to copy Server dir: {copied[-1]['output']}")

    promote_result = promote_uploaded_bundle(ssh_target, remote_paths)
    if promote_result.returncode != 0:
        raise RuntimeError(((promote_result.stdout or "") + (promote_result.stderr or "")).strip())

    firewall_rules = ensure_remote_firewall_rules(ssh_target, remote_paths)
    if firewall_rules["returncode"] != 0:
        raise RuntimeError(f"failed to ensure firewall rules: {firewall_rules['raw_output']}")

    cloudxr_ports = ensure_cloudxr_ports_available(ssh_target)
    if cloudxr_ports["returncode"] != 0:
        raise RuntimeError(f"failed to clear CloudXR port conflicts: {cloudxr_ports['raw_output']}")

    openxr_runtime = ensure_openxr_runtime_registration(ssh_target, remote_paths)
    if openxr_runtime["returncode"] != 0:
        raise RuntimeError(f"failed to register OpenXR runtime: {openxr_runtime['raw_output']}")

    verification = verify_remote_stage(ssh_target, remote_paths)
    return {
        "ssh_target": ssh_target,
        "windows_root": windows_root,
        "remote_paths": remote_paths,
        "copied": copied,
        "firewall_rules": firewall_rules,
        "cloudxr_ports": cloudxr_ports,
        "openxr_runtime": openxr_runtime,
        "verification": verification,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage the minimal runnable ClearXR Windows bundle to winders")
    parser.add_argument("--windows-ssh-target", default=DEFAULT_WINDOWS_SSH_TARGET)
    parser.add_argument("--windows-root", default=DEFAULT_WINDOWS_ROOT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = stage_to_winders(args.windows_ssh_target, args.windows_root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
