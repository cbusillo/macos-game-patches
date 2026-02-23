#!/usr/bin/env python3

import argparse
import getpass
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SmokePaths:
    repo_root: Path
    run_root: Path
    bottle_name: str
    bottle_root: Path
    steamvr_settings: Path
    steam_logs: Path
    cxstart: Path
    wineserver: Path


def resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def make_paths(bottle_name: str, run_root: Path, crossover_bin_dir: Path) -> SmokePaths:
    bottle_root = Path.home() / "Library/Application Support/CrossOver/Bottles" / bottle_name
    steamvr_settings = bottle_root / "drive_c/Program Files (x86)/Steam/config/steamvr.vrsettings"
    steam_logs = bottle_root / "drive_c/Program Files (x86)/Steam/logs"
    return SmokePaths(
        repo_root=resolve_repo_root(),
        run_root=run_root,
        bottle_name=bottle_name,
        bottle_root=bottle_root,
        steamvr_settings=steamvr_settings,
        steam_logs=steam_logs,
        cxstart=crossover_bin_dir / "cxstart",
        wineserver=crossover_bin_dir / "wineserver",
    )


def timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def run_dir(base: Path) -> Path:
    directory = base / f"{timestamp()}-steamvr-smoke"
    (directory / "logs").mkdir(parents=True, exist_ok=True)
    (directory / "config").mkdir(parents=True, exist_ok=True)
    return directory


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def apply_mode(settings: dict[str, Any], mode: str) -> None:
    steamvr = settings.setdefault("steamvr", {})
    dashboard = settings.setdefault("dashboard", {})
    driver_null = settings.setdefault("driver_null", {})
    driver_vrlink = settings.setdefault("driver_vrlink", {})
    driver_alvr_server = settings.setdefault("driver_alvr_server", {})

    if mode == "null":
        steamvr["forcedDriver"] = "null"
        steamvr["requireHmd"] = False
        steamvr["activateMultipleDrivers"] = False
        steamvr["enableHomeApp"] = False
        driver_null["enable"] = True
        driver_vrlink["enable"] = False
        driver_alvr_server["enable"] = False
        driver_alvr_server["blocked_by_safe_mode"] = False
        driver_null.setdefault("id", "Null Driver")
        driver_null.setdefault("serialNumber", "Null 4711")
        driver_null.setdefault("modelNumber", "Null Model Number")
        driver_null.setdefault("renderWidth", 2016)
        driver_null.setdefault("renderHeight", 2240)
        driver_null.setdefault("displayFrequency", 90)
        driver_null.setdefault("secondsFromVsyncToPhotons", 0.011)
        return

    if mode == "vrlink":
        steamvr["forcedDriver"] = "vrlink"
        steamvr["requireHmd"] = True
        steamvr["activateMultipleDrivers"] = False
        steamvr["enableHomeApp"] = False
        driver_null["enable"] = False
        driver_vrlink["enable"] = True
        driver_vrlink.setdefault("automaticBandwidth", True)
        driver_vrlink.setdefault("automaticStreamFormatWidth", True)
        driver_vrlink.setdefault("targetBandwidth", 200)
        driver_vrlink.setdefault("reqEncMode", "auto")
        return

    if mode == "alvr":
        steamvr["forcedDriver"] = "alvr_server"
        steamvr["requireHmd"] = True
        steamvr["activateMultipleDrivers"] = False
        steamvr["enableHomeApp"] = False
        steamvr["enableSafeMode"] = False
        steamvr["startMonitorFromAppLaunch"] = False
        steamvr["startDashboardFromAppLaunch"] = False
        steamvr["startOverlayAppsFromDashboard"] = False
        steamvr["enableappcontainers"] = False
        dashboard["enableDashboard"] = False
        dashboard["webUI"] = False
        driver_null["enable"] = False
        driver_vrlink["enable"] = False
        driver_alvr_server["enable"] = True
        driver_alvr_server["blocked_by_safe_mode"] = False
        return

    if mode == "alvr_nodirect":
        steamvr["forcedDriver"] = "alvr_server"
        steamvr["requireHmd"] = True
        steamvr["activateMultipleDrivers"] = False
        steamvr["enableHomeApp"] = False
        steamvr["enableSafeMode"] = False
        # Explicitly disable direct mode to avoid driver direct-mode swapchain paths.
        steamvr["directMode"] = False
        steamvr["directModeEdidVid"] = 0
        steamvr["directModeEdidPid"] = 0
        steamvr["showMirrorView"] = False
        steamvr["showLegacyMirrorView"] = False
        steamvr["startMonitorFromAppLaunch"] = False
        steamvr["startDashboardFromAppLaunch"] = False
        steamvr["startOverlayAppsFromDashboard"] = False
        steamvr["enableappcontainers"] = False
        dashboard["enableDashboard"] = False
        dashboard["webUI"] = False
        driver_null["enable"] = False
        driver_vrlink["enable"] = False
        driver_alvr_server["enable"] = True
        driver_alvr_server["blocked_by_safe_mode"] = False
        return

    raise ValueError(f"Unsupported mode: {mode}")


def collect_basic_system_info() -> dict[str, Any]:
    sw_vers = subprocess.run(["sw_vers"], check=False, capture_output=True, text=True)
    uname = subprocess.run(["uname", "-a"], check=False, capture_output=True, text=True)
    return {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "sw_vers": sw_vers.stdout,
        "uname": uname.stdout.strip(),
    }


def snapshot_log_sizes(log_dir: Path) -> dict[str, int]:
    sizes: dict[str, int] = {}
    if not log_dir.exists():
        return sizes
    for path in log_dir.glob("*.txt"):
        sizes[path.name] = path.stat().st_size
    return sizes


def copy_log_with_delta(source: Path, target_root: Path, previous_size: int) -> None:
    target_full = target_root / source.name
    shutil.copy2(source, target_full)

    current_size = source.stat().st_size
    target_delta = target_root / f"{source.stem}.delta.txt"
    if current_size == previous_size:
        target_delta.write_text("", encoding="utf-8")
        return

    # If SteamVR rotates or truncates logs between runs, the whole file belongs
    # to this run and should be treated as the delta payload.
    read_from = 0 if current_size < previous_size else previous_size

    with source.open("rb") as source_file:
        source_file.seek(read_from)
        delta_bytes = source_file.read()
    target_delta.write_bytes(delta_bytes)


def run_best_effort(command: list[str], timeout_seconds: float = 2.0) -> None:
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


def minimize_crossover_windows() -> None:
    # SteamVR desktop compositor can open a full-screen green task window when
    # direct mode is disabled. Repeated best-effort minimization keeps the
    # display usable during live runs.
    script_lines = [
        'tell application "System Events"',
        'repeat with p in processes',
        'set processName to name of p',
        'if processName contains "CrossOver" or processName contains "Wine" then',
        'tell p',
        'repeat with w in windows',
        'try',
        'set value of attribute "AXMinimized" of w to true',
        'end try',
        'end repeat',
        'end tell',
        'end if',
        'end repeat',
        'end tell',
    ]
    command = ["osascript"]
    for line in script_lines:
        command.extend(["-e", line])
    run_best_effort(command)

    run_best_effort(["osascript", "-e", 'tell application "CrossOver" to hide'])


def smoke_process_pattern() -> re.Pattern[str]:
    return re.compile(
        r"(winedbg|wineserver|winedevice\.exe|winesync\.exe|"
        r"wineboot\.exe|conhost\.exe|services\.exe|explorer\.exe|"
        r"vrserver\.exe|vrcompositor\.exe|vrmonitor\.exe|vrdashboard\.exe|"
        r"vrwebhelper\.exe|vrstartup\.exe|steam\.exe|steamservice\.exe|"
        r"steamtours\.exe|steamtourscfg\.exe|steamvr_room_setup\.exe|"
        r"steamvr_tutorial\.exe|overlay_viewer\.exe|"
        r"cxmanip\.exe)",
        re.IGNORECASE,
    )


def process_token_basename(token: str) -> str:
    return token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def is_smoke_process(args: str, pattern: re.Pattern[str]) -> bool:
    args = args.strip()
    if not args:
        return False

    lowered = args.lower()
    first_token = args.split(None, 1)[0].strip('"')
    first_lower = first_token.lower()
    first_basename = process_token_basename(first_lower)

    if first_basename in {
        "crossover",
        "steam",
        "cxstart",
        "cxbottle",
        "winedbg",
        "wineserver",
        "wine64-preloader",
        "wine-preloader",
    }:
        return True

    if first_lower.startswith("/applications/crossover.app/"):
        return True

    if first_lower.startswith("/applications/steam.app/"):
        return True

    if first_lower.startswith("c:\\"):
        return bool(pattern.search(lowered))

    if "/winetemp-" in first_lower and bool(pattern.search(lowered)):
        return True

    # Some CrossOver helper processes are launched through perl wrappers.
    if first_basename == "perl" and "/applications/crossover.app/" in lowered and " bin/wine " in f" {lowered} ":
        return bool(pattern.search(lowered))

    return False


def list_matching_processes(pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    current_user = getpass.getuser()
    this_pid = os.getpid()
    parent_pid = os.getppid()

    result = subprocess.run(
        ["ps", "-axo", "pid=,user=,args="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    matches: list[tuple[int, str]] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_str, user, args = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid in {this_pid, parent_pid}:
            continue
        if user != current_user:
            continue
        if is_smoke_process(args, pattern):
            matches.append((pid, args))

    return matches


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_smoke_processes(grace_seconds: float = 2.0) -> dict[str, Any]:
    pattern = smoke_process_pattern()
    initial = list_matching_processes(pattern)
    if not initial:
        return {"killed": [], "remaining": []}

    for pid, _ in initial:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass

    time.sleep(grace_seconds)

    for pid, _ in initial:
        if process_exists(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass

    time.sleep(0.5)

    remaining_now = {pid for pid, _ in list_matching_processes(pattern)}
    killed = [args for pid, args in initial if pid not in remaining_now]
    remaining = [args for pid, args in initial if pid in remaining_now]
    return {"killed": killed, "remaining": remaining}


def run_smoke(
    paths: SmokePaths,
    mode: str,
    wait_seconds: int,
    kill_first: bool,
    kill_after: bool,
    graphics_backend: str,
) -> int:
    if not paths.bottle_root.exists():
        print(f"Bottle not found: {paths.bottle_root}")
        return 1
    if not paths.cxstart.exists() or not paths.wineserver.exists():
        print("CrossOver binaries missing. Expected cxstart and wineserver in:")
        print(paths.cxstart.parent)
        return 1
    if not paths.steam_logs.exists():
        print(f"Steam log path not found: {paths.steam_logs}")
        return 1

    bundle_dir = run_dir(paths.run_root)
    meta_path = bundle_dir / "config" / "meta.json"
    meta = collect_basic_system_info()
    meta["bottle_name"] = paths.bottle_name
    meta["bottle_root"] = str(paths.bottle_root)
    meta["mode"] = mode
    meta["graphics_backend"] = graphics_backend
    meta["wait_seconds"] = wait_seconds
    write_json(meta_path, meta)

    cleanup_before: dict[str, Any] = {"killed": [], "remaining": []}
    if kill_first:
        cleanup_before = kill_smoke_processes()
    meta["cleanup_before"] = cleanup_before
    write_json(meta_path, meta)

    if mode != "unchanged":
        if not paths.steamvr_settings.exists():
            print(f"SteamVR settings file not found: {paths.steamvr_settings}")
            return 1
        before = bundle_dir / "config" / "steamvr.vrsettings.before.json"
        after = bundle_dir / "config" / "steamvr.vrsettings.after.json"
        shutil.copy2(paths.steamvr_settings, before)
        settings = read_json(paths.steamvr_settings)
        apply_mode(settings, mode)
        write_json(paths.steamvr_settings, settings)
        shutil.copy2(paths.steamvr_settings, after)

    pre_launch_log_sizes = snapshot_log_sizes(paths.steam_logs)

    env = dict(os.environ)
    env["WINEPREFIX"] = str(paths.bottle_root)
    if graphics_backend == "d3dmetal":
        env["CX_GRAPHICS_BACKEND"] = "d3dmetal"
        env["WINED3DMETAL"] = "1"
    elif graphics_backend == "dxvk":
        env["CX_GRAPHICS_BACKEND"] = "dxvk"
        env["WINED3DMETAL"] = "0"

    startup_exe = r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrstartup.exe"
    cx_log = bundle_dir / "logs" / "cxstart.log"
    launch = subprocess.run(
        [
            str(paths.cxstart),
            "--bottle",
            paths.bottle_name,
            "--no-gui",
            "--no-wait",
            "--cx-log",
            str(cx_log),
            startup_exe,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    (bundle_dir / "logs" / "cxstart.stdout.log").write_text(
        (launch.stdout or "") + (launch.stderr or ""),
        encoding="utf-8",
    )
    if launch.returncode != 0:
        if kill_after:
            cleanup_after_fail = kill_smoke_processes()
            meta["cleanup_after"] = cleanup_after_fail
            write_json(meta_path, meta)
        print(f"SteamVR launch command failed with exit code {launch.returncode}")
        print(f"Run bundle: {bundle_dir}")
        return 1

    for elapsed in range(wait_seconds):
        if elapsed < 90:
            minimize_crossover_windows()
        time.sleep(1)

    copied_logs: list[str] = []
    candidate_logs = [
        "vrserver.txt",
        "vrmonitor.txt",
        "vrcompositor.txt",
        "vrstartup.txt",
        "vrclient_vrstartup.txt",
        "driver_vrlink.txt",
        "driver_alvr_server.txt",
        "vrclient_steam.txt",
    ]

    dynamic_logs = sorted(
        {
            path.name
            for path in paths.steam_logs.glob("*.txt")
            if path.name.startswith("vr") or path.name.startswith("driver_")
        }
    )
    all_logs = sorted(set(candidate_logs + dynamic_logs))
    for log_name in all_logs:
        source = paths.steam_logs / log_name
        if source.exists():
            copy_log_with_delta(
                source,
                bundle_dir / "logs",
                pre_launch_log_sizes.get(log_name, 0),
            )
            copied_logs.append(log_name)

    ps_out = subprocess.run(["ps", "aux"], check=False, capture_output=True, text=True)
    (bundle_dir / "logs" / "processes.txt").write_text(ps_out.stdout, encoding="utf-8")

    lsof_out = subprocess.run(
        ["lsof", "-nP", "-iUDP:10400", "-iTCP:10440"],
        check=False,
        capture_output=True,
        text=True,
    )
    (bundle_dir / "logs" / "ports.txt").write_text(lsof_out.stdout + lsof_out.stderr, encoding="utf-8")

    summary_patterns = [
        "Loaded server driver",
        "Unable to load driver",
        "No connected devices",
        "VRInitError_Init_HmdNotFound",
        "Using existing HMD",
        "Startup Complete",
        "Exception c0000005",
        "Failed to init graphics device",
        "VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer",
        "VRInitError_Compositor_CreateMirrorTextures",
        "VRInitError_Compositor_CreateDriverDirectModeResolveTextures",
        "Headset Error",
        "SteamVR Fail",
        "No links? Server-as-client Mode?",
        "Listening for incoming control connections",
    ]
    summary_lines: list[str] = []
    for name in copied_logs:
        delta_path = bundle_dir / "logs" / f"{Path(name).stem}.delta.txt"
        if delta_path.exists():
            text = delta_path.read_text(encoding="utf-8", errors="replace")
        else:
            text = (bundle_dir / "logs" / name).read_text(encoding="utf-8", errors="replace")
        for pattern in summary_patterns:
            if pattern in text:
                summary_lines.append(f"{name}: {pattern}")

    summary_path = bundle_dir / "logs" / "smoke-summary.txt"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    cleanup_after: dict[str, Any] = {"killed": [], "remaining": []}
    if kill_after:
        cleanup_after = kill_smoke_processes()
        meta["cleanup_after"] = cleanup_after
        write_json(meta_path, meta)

    print(f"SteamVR smoke complete. Run bundle: {bundle_dir}")
    for line in summary_lines:
        print(f"- {line}")
    if kill_after:
        print(f"- cleanup_after.killed: {len(cleanup_after['killed'])}")
        print(f"- cleanup_after.remaining: {len(cleanup_after['remaining'])}")
    return 0


def parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser(description="Minimal SteamVR smoke harness with deterministic run bundles")
    arg_parser.add_argument("--bottle", default="Steam", help="CrossOver bottle name")
    arg_parser.add_argument(
        "--mode",
        choices=["unchanged", "null", "vrlink", "alvr", "alvr_nodirect"],
        default="unchanged",
        help="SteamVR driver mode applied before launch",
    )
    arg_parser.add_argument("--wait", type=int, default=30, help="seconds to wait after launch before capturing logs")
    arg_parser.add_argument("--no-kill-first", action="store_true", help="skip wineserver -k before launch")
    arg_parser.add_argument("--no-kill-after", action="store_true", help="leave Wine/SteamVR processes running after capture")
    arg_parser.add_argument(
        "--graphics-backend",
        choices=["default", "d3dmetal", "dxvk"],
        default="dxvk",
        help="override CrossOver graphics backend for this smoke run",
    )
    arg_parser.add_argument(
        "--run-root",
        default=str(resolve_repo_root() / "temp" / "vr_runs"),
        help="directory where run bundles are stored",
    )
    arg_parser.add_argument(
        "--crossover-bin-dir",
        default="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin",
        help="CrossOver bin directory containing cxstart and wineserver",
    )
    return arg_parser


def main() -> int:
    args = parser().parse_args()
    paths = make_paths(args.bottle, Path(args.run_root), Path(args.crossover_bin_dir))
    return run_smoke(
        paths,
        args.mode,
        args.wait,
        not args.no_kill_first,
        not args.no_kill_after,
        args.graphics_backend,
    )


if __name__ == "__main__":
    sys.exit(main())
