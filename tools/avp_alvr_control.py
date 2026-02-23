#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_DEVICE = "Apple Vision Pro"
DEFAULT_BUNDLE_ID = "com.shinycomputers.alvrclient"
FALLBACK_BUNDLE_IDS = ["com.shinycomputers.alvrclient", "alvr.client"]
ALVR_PROCESS_FILTER = "name CONTAINS[c] 'ALVR' OR executablePath CONTAINS[c] 'ALVR'"
ALVR_APP_FILTER = "name CONTAINS[c] 'ALVR' OR bundleIdentifier CONTAINS[c] 'alvr'"


def run_devc_json(args: list[str]) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(prefix="devicectl-json-", suffix=".json", delete=False) as tmp:
        json_path = Path(tmp.name)

    command = ["xcrun", "devicectl", *args, "--json-output", str(json_path)]
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    if not json_path.exists():
        return {
            "info": {
                "outcome": "error",
                "error": "devicectl did not produce JSON output",
            },
            "result": {},
        }

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - we want a robust CLI wrapper
        payload = {
            "info": {
                "outcome": "error",
                "error": f"failed to parse devicectl JSON: {exc}",
            },
            "result": {},
        }
    finally:
        json_path.unlink(missing_ok=True)

    payload.setdefault("_stdout", result.stdout)
    payload.setdefault("_stderr", result.stderr)
    payload.setdefault("_returncode", result.returncode)
    return payload


def run_devc(args: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["xcrun", "devicectl", *args]
    return subprocess.run(command, check=False, capture_output=True, text=True)


def list_alvr_apps(device: str) -> list[dict[str, Any]]:
    payload = run_devc_json(
        [
            "device",
            "info",
            "apps",
            "--device",
            device,
            "--include-all-apps",
        ]
    )

    apps = payload.get("result", {}).get("apps", [])
    filtered: list[dict[str, Any]] = []
    for app in apps:
        bundle = str(app.get("bundleIdentifier", "")).lower()
        name = str(app.get("name", "")).lower()
        if "alvr" in bundle or "alvr" in name:
            filtered.append(app)

    return filtered


def get_lock_state(device: str) -> dict[str, Any]:
    payload = run_devc_json(["device", "info", "lockState", "--device", device])
    return payload.get("result", {})


def list_alvr_pids(device: str) -> list[int]:
    payload = run_devc_json(
        [
            "device",
            "info",
            "processes",
            "--device",
            device,
            "--filter",
            ALVR_PROCESS_FILTER,
        ]
    )

    pids: list[int] = []
    for process in payload.get("result", {}).get("runningProcesses", []):
        pid = process.get("processIdentifier")
        if isinstance(pid, int):
            pids.append(pid)
    return pids


def resolve_bundle_id(device: str, preferred: str) -> str:
    apps = list_alvr_apps(device)
    bundle_ids = [app.get("bundleIdentifier", "") for app in apps]

    if preferred in bundle_ids:
        return preferred

    for bundle_id in FALLBACK_BUNDLE_IDS:
        if bundle_id in bundle_ids:
            return bundle_id

    if bundle_ids:
        return bundle_ids[0]

    return preferred


def terminate_alvr(device: str) -> int:
    pids = list_alvr_pids(device)
    if not pids:
        print("No ALVR process found on device")
        return 0

    failures = 0
    for pid in pids:
        result = run_devc(["device", "process", "terminate", "--device", device, "--pid", str(pid)])
        if result.returncode == 0:
            print(f"Terminated ALVR pid={pid}")
        else:
            failures += 1
            print(f"Failed to terminate pid={pid}")
            if result.stderr:
                print(result.stderr.strip())

    return 1 if failures else 0


def launch_alvr(device: str, bundle_id: str, activate: bool, terminate_existing: bool) -> int:
    lock_state = get_lock_state(device)
    if lock_state.get("passcodeRequired") is True:
        print("Device is locked (passcode required). Unlock/wear AVP, then retry launch.")
        return 2

    command = ["device", "process", "launch", "--device", device]
    if terminate_existing:
        command.append("--terminate-existing")
    command.append("--activate" if activate else "--no-activate")
    command.append(bundle_id)

    last_error = ""
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, 4):
        result = run_devc(command)
        if result.returncode == 0:
            print(f"Launched {bundle_id}")
            return 0

        last_error = (result.stderr or "").strip()
        if attempt < 3:
            time.sleep(1.5)

    print(f"Failed to launch {bundle_id}")
    if last_error:
        print(last_error)
    return result.returncode if result is not None else 1


def status(device: str) -> int:
    lock = get_lock_state(device)

    print(f"Device: {device}")
    if lock:
        print(
            "Lock state: "
            f"passcode_required={lock.get('passcodeRequired')} "
            f"unlocked_since_boot={lock.get('unlockedSinceBoot')}"
        )

    apps = list_alvr_apps(device)
    if apps:
        print("ALVR apps:")
        for app in apps:
            name = app.get("name", "<unknown>")
            bundle = app.get("bundleIdentifier", "<unknown>")
            version = app.get("version", "<unknown>")
            print(f"- {name} ({bundle}) v{version}")
    else:
        print("ALVR apps: none detected")

    pids = list_alvr_pids(device)
    if pids:
        print("ALVR running pids: " + ", ".join(str(pid) for pid in pids))
    else:
        print("ALVR running pids: none")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control ALVR on Apple Vision Pro via devicectl")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="device name or identifier")
    parser.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID, help="ALVR app bundle identifier")

    subparsers = parser.add_subparsers(dest="command", required=True)

    launch_parser = subparsers.add_parser("launch", help="launch ALVR")
    launch_parser.add_argument("--no-activate", action="store_true", help="launch in background")
    launch_parser.add_argument(
        "--no-terminate-existing",
        action="store_true",
        help="do not terminate existing app instances before launch",
    )

    subparsers.add_parser("terminate", help="terminate running ALVR process(es)")

    restart_parser = subparsers.add_parser("restart", help="terminate then relaunch ALVR")
    restart_parser.add_argument("--no-activate", action="store_true", help="relaunch in background")

    subparsers.add_parser("status", help="show lock state, installed ALVR apps, and ALVR process status")

    return parser


def main() -> int:
    args = build_parser().parse_args()

    bundle_id = resolve_bundle_id(args.device, args.bundle_id)

    if args.command == "status":
        return status(args.device)

    if args.command == "terminate":
        return terminate_alvr(args.device)

    if args.command == "launch":
        return launch_alvr(
            args.device,
            bundle_id,
            activate=not args.no_activate,
            terminate_existing=not args.no_terminate_existing,
        )

    if args.command == "restart":
        terminate_rc = terminate_alvr(args.device)
        launch_rc = launch_alvr(
            args.device,
            bundle_id,
            activate=not args.no_activate,
            terminate_existing=True,
        )
        return launch_rc if launch_rc != 0 else terminate_rc

    print(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
