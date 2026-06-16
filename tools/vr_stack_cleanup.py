#!/usr/bin/env python3
"""Clean stale VR, Steam, CrossOver, and Wine processes before live probes."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass


PROCESS_NAMES = {
    "ALVR Dashboard",
    "ALVR Launcher",
    "alvr_dashboard",
    "alvr_launcher",
    "alvr_server",
    "alvr_streamer",
    "SteamVR",
    "vrmonitor",
    "vrserver",
    "vrcompositor",
    "vrwebhelper",
    "vrdashboard",
    "vrstartup",
}

WINE_CROSSOVER_PROCESS_NAMES = {
    "CrossOver",
    "cxstart",
    "wine64-preloader",
    "wine-preloader",
    "wineserver",
}

WINE_VR_COMMAND_PATTERNS = (
    "C:\\ALVR\\",
    "\\ALVR Dashboard.exe",
    "\\ALVR Launcher.exe",
    "\\alvr_dashboard.exe",
    "\\alvr_launcher.exe",
    "\\driver_alvr_server.dll",
    "\\SteamVR\\bin\\win64\\vrserver.exe",
    "\\SteamVR\\bin\\win64\\vrstartup.exe",
    "\\SteamVR\\bin\\win64\\vrmonitor.exe",
    "\\SteamVR\\bin\\win64\\vrcompositor.exe",
    "\\SteamVR\\bin\\vrwebhelper\\win64\\vrwebhelper.exe",
)

NATIVE_STEAM_PATTERNS = (
    "Steam.app/Contents/MacOS",
    "Steam.AppBundle/Steam/Contents/MacOS",
)

NATIVE_STEAM_LAUNCHCTL_LABELS = (
    "com.valvesoftware.steam.ipctool",
)


@dataclass(frozen=True)
class ProcessMatch:
    pid: int
    name: str
    command: str


@dataclass(frozen=True)
class CleanupReport:
    matched: list[ProcessMatch]
    terminated: list[ProcessMatch]
    remaining: list[ProcessMatch]
    ps_error: str | None = None
    launchctl_actions: list[str] | None = None


def list_processes() -> tuple[list[ProcessMatch], str | None]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,comm=,args="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return [], result.stderr.strip() or "ps failed"

    current_pid = os.getpid()
    matches: list[ProcessMatch] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_text, command_name, command_args = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        command = f"{command_name} {command_args}".strip()
        matches.append(ProcessMatch(pid=pid, name=os.path.basename(command_name), command=command))
    return matches, None


def command_matches(process: ProcessMatch, include_wine_crossover: bool, sterile_native_steam: bool) -> bool:
    name = process.name
    lowered_command = process.command.lower()
    if name in PROCESS_NAMES:
        return True
    if any(pattern.lower() in lowered_command for pattern in WINE_VR_COMMAND_PATTERNS):
        return True
    if include_wine_crossover and name in WINE_CROSSOVER_PROCESS_NAMES:
        return True
    if sterile_native_steam:
        return any(pattern.lower() in lowered_command for pattern in NATIVE_STEAM_PATTERNS)
    return False


def list_matching_processes(include_wine_crossover: bool, sterile_native_steam: bool) -> tuple[list[ProcessMatch], str | None]:
    processes, ps_error = list_processes()
    if ps_error is not None:
        return [], ps_error
    return [
        process
        for process in processes
        if command_matches(process, include_wine_crossover, sterile_native_steam)
    ], None


def signal_process(process: ProcessMatch, sig: signal.Signals) -> None:
    try:
        os.kill(process.pid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        return


def bootout_native_steam_services() -> list[str]:
    actions: list[str] = []
    uid = os.getuid()
    domains = (f"gui/{uid}", f"user/{uid}")

    for label in NATIVE_STEAM_LAUNCHCTL_LABELS:
        for domain in domains:
            service = f"{domain}/{label}"
            result = subprocess.run(
                ["launchctl", "bootout", service],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                actions.append(f"bootout {service}: ok")
            else:
                stderr = result.stderr.strip()
                actions.append(f"bootout {service}: exit {result.returncode}: {stderr}")

    return actions


def cleanup(
    grace_seconds: float,
    include_wine_crossover: bool,
    sterile_native_steam: bool,
    dry_run: bool,
) -> CleanupReport:
    launchctl_actions: list[str] | None = None
    if sterile_native_steam and not dry_run:
        launchctl_actions = bootout_native_steam_services()

    matched, ps_error = list_matching_processes(include_wine_crossover, sterile_native_steam)
    if ps_error is not None:
        return CleanupReport(
            matched=[],
            terminated=[],
            remaining=[],
            ps_error=ps_error,
            launchctl_actions=launchctl_actions,
        )
    if dry_run:
        return CleanupReport(matched=matched, terminated=[], remaining=matched)

    for process in matched:
        signal_process(process, signal.SIGTERM)

    time.sleep(max(grace_seconds, 0.0))

    current_matches, ps_error = list_matching_processes(include_wine_crossover, sterile_native_steam)
    if ps_error is not None:
        return CleanupReport(
            matched=matched,
            terminated=[],
            remaining=[],
            ps_error=ps_error,
            launchctl_actions=launchctl_actions,
        )
    still_running = {process.pid: process for process in current_matches}
    for process in matched:
        current_process = still_running.get(process.pid)
        if current_process is not None and current_process.command == process.command:
            signal_process(process, signal.SIGKILL)

    remaining: list[ProcessMatch] = []
    for _ in range(5):
        time.sleep(0.1)
        remaining, ps_error = list_matching_processes(include_wine_crossover, sterile_native_steam)
        if ps_error is not None or not remaining:
            break
    if ps_error is not None:
        return CleanupReport(
            matched=matched,
            terminated=[],
            remaining=[],
            ps_error=ps_error,
            launchctl_actions=launchctl_actions,
        )

    remaining_pids = {process.pid for process in remaining}
    terminated = [process for process in matched if process.pid not in remaining_pids]

    return CleanupReport(
        matched=matched,
        terminated=terminated,
        remaining=remaining,
        launchctl_actions=launchctl_actions,
    )


def print_text_report(report: CleanupReport) -> None:
    print(f"matched={len(report.matched)}")
    print(f"terminated={len(report.terminated)}")
    print(f"remaining={len(report.remaining)}")
    if report.ps_error is not None:
        print(f"ps_error={report.ps_error}")
    if report.launchctl_actions:
        for action in report.launchctl_actions:
            print(f"launchctl={action}")
    for process in report.remaining[:20]:
        print(f"- pid={process.pid} name={process.name} cmd={process.command}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean stale VR stack processes")
    parser.add_argument("--grace-seconds", type=float, default=2.0, help="seconds to wait after SIGTERM")
    parser.add_argument("--allow-remaining", action="store_true", help="exit 0 even when matching processes remain")
    parser.add_argument("--dry-run", action="store_true", help="show matching processes without signaling them")
    parser.add_argument(
        "--include-wine-crossover",
        action="store_true",
        help="also terminate generic Wine and CrossOver processes",
    )
    parser.add_argument(
        "--sterile-native-steam",
        action="store_true",
        help="also terminate native Steam helper processes",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    report = cleanup(
        args.grace_seconds,
        args.include_wine_crossover,
        args.sterile_native_steam,
        args.dry_run,
    )
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print_text_report(report)

    if report.ps_error is not None:
        return 1
    if report.remaining and not args.allow_remaining:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
