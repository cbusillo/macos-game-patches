#!/usr/bin/env python3

import argparse
import json
import os
import signal
import subprocess
import time
from typing import Any

from steamvr_smoke import kill_smoke_processes, list_matching_processes, smoke_process_pattern


def run_best_effort(command: list[str], timeout_seconds: float = 8.0) -> None:
    try:
        subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return


def quit_gui_apps() -> None:
    for app_name in ("CrossOver", "Steam"):
        run_best_effort(["osascript", "-e", f'tell application "{app_name}" to quit'])


def force_kill_process_names() -> None:
    for process_name in ("CrossOver", "Steam", "wineserver"):
        run_best_effort(["killall", "-9", process_name])


def list_native_steam_helpers() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,args="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    matches: list[dict[str, Any]] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        pid_str, command = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue

        lowered = command.lower()
        if (
            "steam.appbundle/steam/contents/macos/ipcserver" in lowered
            or "steam.app/contents/macos/ipcserver" in lowered
        ):
            matches.append({"pid": pid, "command": command})

    return matches


def kill_pid(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        return


def kill_native_steam_helpers(grace_seconds: float = 0.3) -> dict[str, Any]:
    initial = list_native_steam_helpers()
    if not initial:
        return {"killed": [], "remaining": []}

    for item in initial:
        kill_pid(int(item["pid"]), signal.SIGTERM)

    time.sleep(max(grace_seconds, 0.0))

    for item in initial:
        kill_pid(int(item["pid"]), signal.SIGKILL)

    time.sleep(0.1)
    remaining_pids = {item["pid"] for item in list_native_steam_helpers()}

    killed = [item for item in initial if item["pid"] not in remaining_pids]
    remaining = [item for item in initial if item["pid"] in remaining_pids]
    return {"killed": killed, "remaining": remaining}


def run_cleanup(passes: int, grace_seconds: float, sterile_native_steam: bool) -> dict[str, Any]:
    quit_gui_apps()

    sterile_pre: dict[str, Any] = {"killed": [], "remaining": []}
    if sterile_native_steam:
        sterile_pre = kill_native_steam_helpers()

    pass_reports: list[dict[str, Any]] = []
    for _ in range(passes):
        pass_reports.append(kill_smoke_processes(grace_seconds))
        time.sleep(0.3)

    force_kill_process_names()
    tail_report = kill_smoke_processes(0.2)
    pass_reports.append(tail_report)

    sterile_post: dict[str, Any] = {"killed": [], "remaining": []}
    if sterile_native_steam:
        sterile_post = kill_native_steam_helpers()

    remaining = [{"pid": pid, "command": command} for pid, command in list_matching_processes(smoke_process_pattern())]
    if sterile_native_steam:
        remaining.extend(list_native_steam_helpers())

    return {
        "passes": pass_reports,
        "sterile_native_steam": {
            "enabled": sterile_native_steam,
            "pre": sterile_pre,
            "post": sterile_post,
        },
        "remaining": remaining,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean CrossOver/Wine/SteamVR stack processes")
    parser.add_argument("--passes", type=int, default=2, help="cleanup passes before final tail pass")
    parser.add_argument("--grace-seconds", type=float, default=2.0, help="SIGTERM grace period")
    parser.add_argument("--allow-remaining", action="store_true", help="exit 0 even when matches remain")
    parser.add_argument(
        "--sterile-native-steam",
        action="store_true",
        help="also kill native macOS Steam helper ipcserver for fully sterile runs",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args()

    report = run_cleanup(
        max(args.passes, 1),
        max(args.grace_seconds, 0.0),
        args.sterile_native_steam,
    )
    remaining: list[dict[str, Any]] = report["remaining"]

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        killed_total = sum(len(pass_report["killed"]) for pass_report in report["passes"])
        print(f"cleanup_passes={len(report['passes'])}")
        print(f"killed_total={killed_total}")
        if report["sterile_native_steam"]["enabled"]:
            sterile_killed = len(report["sterile_native_steam"]["pre"]["killed"]) + len(
                report["sterile_native_steam"]["post"]["killed"]
            )
            print(f"sterile_native_steam_killed={sterile_killed}")
        print(f"remaining={len(remaining)}")
        if remaining:
            for item in remaining[:20]:
                print(f"- pid={item['pid']} cmd={item['command']}")

    if remaining and not args.allow_remaining:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
