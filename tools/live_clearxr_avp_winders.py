#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from live_clearxr_avp import (
    DEFAULT_AVP_DEVICE,
    DEFAULT_SERVER_BUNDLE_ID,
    copy_app_preferences,
    launch_avp_app,
    query_app_processes,
    reveal_local_artifact,
    run_devc_json,
    seed_auto_connect_preferences,
    terminate_app_processes,
    write_json,
    write_qr_png,
)
from probe_clearxr_winders import (
    append_new_log_text,
    collect_interactive_task_output,
    launch_interactive_headless_task,
    read_remote_text,
    wait_for_interactive_task_exit,
    wait_for_startup_from_interactive_task,
)
from smoke_clearxr_winders import detect_host_address as detect_windows_host_address
from stage_clearxr_winders import stage_to_winders


DEFAULT_ARCHIVE_ROOT = Path("temp/clearxr_live_runs")
DEFAULT_WINDOWS_SSH_TARGET = "gaming@winders"
DEFAULT_WINDOWS_ROOT = r"C:\dev\clearxr-server"
STARTUP_TIMEOUT_SECONDS = 45.0
LAUNCHER_MODE_SESSION0 = "session0"
LAUNCHER_MODE_INTERACTIVE_TASK = "interactive-task"


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def windows_powershell_command(script: str) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live Apple Vision Pro validation against the Windows ClearXR backend on winders",
    )
    parser.add_argument("--windows-ssh-target", default=DEFAULT_WINDOWS_SSH_TARGET)
    parser.add_argument("--windows-root", default=DEFAULT_WINDOWS_ROOT)
    parser.add_argument("--host", default="auto", help="advertised host for the Windows ClearXR backend")
    parser.add_argument("--port", type=int, default=55000)
    parser.add_argument("--server-bundle-id", default=DEFAULT_SERVER_BUNDLE_ID)
    parser.add_argument("--avp-device", default=DEFAULT_AVP_DEVICE)
    parser.add_argument("--avp-bundle-id", default=DEFAULT_SERVER_BUNDLE_ID)
    parser.add_argument("--run-seconds", type=int, default=240)
    parser.add_argument("--snapshot-interval-seconds", type=int, default=2)
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE_ROOT))
    parser.add_argument(
        "--skip-device",
        action="store_true",
        help="do not query or launch the Vision Pro app; backend-only harness",
    )
    parser.add_argument(
        "--no-restart-app",
        action="store_true",
        help="skip the best-effort devicectl launch of the Vision Pro ClearXR app",
    )
    parser.add_argument(
        "--skip-stage",
        action="store_true",
        help="skip refreshing the staged Windows bundle before launch",
    )
    parser.add_argument(
        "--launcher-mode",
        choices=[LAUNCHER_MODE_SESSION0, LAUNCHER_MODE_INTERACTIVE_TASK],
        default=LAUNCHER_MODE_SESSION0,
        help="how to launch the Windows ClearXR backend",
    )
    return parser


def start_remote_headless_process(
    ssh_target: str,
    windows_root: str,
    host: str,
    port: int,
    bundle_id: str,
    run_seconds: int,
    snapshot_interval_seconds: int,
) -> subprocess.Popen[str]:
    debug_root = windows_root.rstrip("\\/") + r"\clearxr-streamer\target\x86_64-pc-windows-gnu\debug"
    release_root = debug_root + r"\Server\releases\6.0.4"
    exe_path = debug_root + r"\clearxr-streamer.exe"
    script = (
        "$ProgressPreference = 'SilentlyContinue';"
        f"$env:PATH = '{release_root};' + $env:PATH;"
        "$env:RUST_LOG = 'info';"
        f"& '{exe_path}' "
        "--clearxr-headless "
        f"--clearxr-headless-bundle-id {bundle_id} "
        f"--clearxr-headless-host {host} "
        f"--clearxr-headless-port {port} "
        f"--clearxr-headless-run-seconds {run_seconds} "
        f"--clearxr-headless-snapshot-interval-seconds {snapshot_interval_seconds} "
    )
    remote_command = " ".join(windows_powershell_command(script))
    return subprocess.Popen(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            ssh_target,
            remote_command,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def extract_snapshot_payload(line: str, stage: str) -> dict[str, Any] | None:
    prefix = f"CLEARXR_HEADLESS_SNAPSHOT {stage} "
    if not line.startswith(prefix):
        return None
    with contextlib.suppress(json.JSONDecodeError):
        payload = json.loads(line[len(prefix) :])
        if isinstance(payload, dict):
            return payload
    return None


def maybe_write_qr(snapshot: dict[str, Any], destination: Path) -> Path | None:
    qr_data_url = snapshot.get("qrDataUrl")
    if not isinstance(qr_data_url, str) or not qr_data_url.startswith("data:image/png;base64,"):
        return None
    return write_qr_png(qr_data_url, destination)


def wait_for_startup(process: subprocess.Popen[str], log_path: Path) -> dict[str, Any]:
    stdout = process.stdout
    if stdout is None:
        raise RuntimeError("remote ClearXR process has no stdout pipe")

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    startup_snapshot: dict[str, Any] | None = None
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

            startup_snapshot = extract_snapshot_payload(line.strip(), "startup")
            if startup_snapshot is not None:
                return startup_snapshot

    raise RuntimeError("remote ClearXR backend did not emit a startup snapshot in time")


def drain_remote_output(process: subprocess.Popen[str], log_path: Path, qr_path: Path) -> dict[str, Any]:
    stdout = process.stdout
    qr_written = False
    snapshots: list[dict[str, Any]] = []
    if stdout is None:
        return {"qr_path": None, "snapshots": snapshots}

    with log_path.open("a", encoding="utf-8") as handle:
        for line in stdout:
            handle.write(line)
            handle.flush()
            print(line.rstrip(), flush=True)

            stripped = line.strip()
            for stage in ("startup", "update", "shutdown"):
                snapshot = extract_snapshot_payload(stripped, stage)
                if snapshot is None:
                    continue
                snapshots.append({"stage": stage, "snapshot": snapshot})
                if not qr_written:
                    materialized = maybe_write_qr(snapshot, qr_path)
                    if materialized is not None:
                        reveal_local_artifact(materialized)
                        print(f"PAIRING_QR={materialized}", flush=True)
                        print("READY_FOR_QR_SCAN=1", flush=True)
                        qr_written = True
                break

    return {"qr_path": str(qr_path) if qr_written else None, "snapshots": snapshots}


def materialize_qr_from_text(stdout_text: str, qr_path: Path) -> str | None:
    for line in stdout_text.splitlines():
        stripped = line.strip()
        for stage in ("startup", "update", "shutdown"):
            snapshot = extract_snapshot_payload(stripped, stage)
            if snapshot is None:
                continue
            materialized = maybe_write_qr(snapshot, qr_path)
            if materialized is not None:
                return str(materialized)
    return None


def monitor_interactive_task_for_qr(
    ssh_target: str,
    task: dict[str, str],
    log_path: Path,
    qr_path: Path,
    timeout_seconds: int,
) -> str | None:
    deadline = time.monotonic() + max(timeout_seconds, 1)
    previous_text = ""
    qr_materialized: str | None = None
    while time.monotonic() < deadline:
        current_text = read_remote_text(ssh_target, task["stdout_log"])
        append_new_log_text(log_path, previous_text, current_text)
        previous_text = current_text
        if qr_materialized is None:
            qr_materialized = materialize_qr_from_text(current_text, qr_path)
            if qr_materialized is not None:
                reveal_local_artifact(Path(qr_materialized))
                print(f"PAIRING_QR={qr_materialized}", flush=True)
                print("READY_FOR_QR_SCAN=1", flush=True)
                return qr_materialized
        time.sleep(1.0)
    return qr_materialized


def main() -> int:
    args = build_parser().parse_args()
    host = detect_windows_host_address(args.windows_ssh_target) if args.host == "auto" else args.host

    run_root = Path(args.archive_root).resolve()
    run_dir = run_root / f"{utc_stamp()}-live-clearxr-avp-winders"
    logs_dir = run_dir / "logs"
    config_dir = run_dir / "config"
    logs_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    print(f"RUN={run_dir}", flush=True)
    meta: dict[str, Any] = {
        "windows_ssh_target": args.windows_ssh_target,
        "windows_root": args.windows_root,
        "launcher_mode": args.launcher_mode,
        "host": host,
        "port": args.port,
        "server_bundle_id": args.server_bundle_id,
        "avp_device": args.avp_device,
        "avp_bundle_id": args.avp_bundle_id,
        "run_seconds": args.run_seconds,
        "snapshot_interval_seconds": args.snapshot_interval_seconds,
        "skip_device": args.skip_device,
        "skip_stage": args.skip_stage,
    }
    write_json(config_dir / "meta.json", meta)

    if not args.skip_stage:
        stage_report = stage_to_winders(args.windows_ssh_target, args.windows_root)
        meta["stage_report"] = stage_report
        write_json(config_dir / "meta.json", meta)
        print("WINDOWS_STAGE_REFRESHED=1", flush=True)

    device_state: dict[str, Any] = {}
    launch_app_after_startup = not args.skip_device and not args.no_restart_app
    if not args.skip_device:
        lock_payload = run_devc_json(
            ["device", "info", "lockState", "--device", args.avp_device],
            logs_dir / "avp-lock-state.log",
        )
        apps_payload = run_devc_json(
            [
                "device",
                "info",
                "apps",
                "--device",
                args.avp_device,
                "--include-all-apps",
            ],
            logs_dir / "avp-apps.log",
        )
        device_state = {
            "lock_state": lock_payload.get("result", {}),
            "lock_state_returncode": lock_payload.get("_returncode"),
            "apps_returncode": apps_payload.get("_returncode"),
            "bundle_present": any(
                app.get("bundleIdentifier") == args.avp_bundle_id
                for app in apps_payload.get("result", {}).get("apps", [])
                if isinstance(app, dict)
            ),
            "processes_before_launch": query_app_processes(
                args.avp_device,
                args.avp_bundle_id,
                logs_dir / "avp-processes-before.log",
            ),
        }
        device_state["terminated_before_seed"] = terminate_app_processes(
            args.avp_device,
            device_state["processes_before_launch"],
            logs_dir,
        )
        device_state["auto_connect_seed"] = seed_auto_connect_preferences(
            args.avp_device,
            args.avp_bundle_id,
            host,
            args.port,
            config_dir,
            logs_dir,
        )
        meta["device_state"] = device_state
        write_json(config_dir / "meta.json", meta)

    headless_log = logs_dir / "clearxr-headless-winders.log"
    qr_path = config_dir / "pairing-qr.png"
    startup_snapshot: dict[str, Any] | None = None
    startup_error: str | None = None
    qr_result: dict[str, Any] | None = None
    launcher_info: dict[str, Any] | None = None
    process: subprocess.Popen[str] | None = None
    interactive_task: dict[str, str] | None = None
    try:
        if args.launcher_mode == LAUNCHER_MODE_INTERACTIVE_TASK:
            interactive_task = launch_interactive_headless_task(
                args.windows_ssh_target,
                args.windows_root,
                host,
                args.port,
                args.server_bundle_id,
                args.run_seconds,
                args.snapshot_interval_seconds,
                run_dir.name,
                force_qr_code=False,
            )
            startup_snapshot, launcher_info = wait_for_startup_from_interactive_task(
                args.windows_ssh_target,
                interactive_task,
                headless_log,
            )
        else:
            process = start_remote_headless_process(
                args.windows_ssh_target,
                args.windows_root,
                host,
                args.port,
                args.server_bundle_id,
                args.run_seconds,
                args.snapshot_interval_seconds,
            )
            startup_snapshot = wait_for_startup(process, headless_log)
        meta["startup_snapshot"] = startup_snapshot
        if launcher_info is not None:
            meta["launcher_info"] = launcher_info
        if launch_app_after_startup:
            try:
                restart_rc, restart_text = launch_avp_app(
                    args.avp_device,
                    args.avp_bundle_id,
                    logs_dir / "avp-app-launch.log",
                )
                device_state["launch_exit_code"] = restart_rc
                device_state["launch_output_tail"] = restart_text[-2000:]
            except subprocess.TimeoutExpired as error:
                device_state["launch_exit_code"] = None
                device_state["launch_timed_out"] = True
                device_state["launch_output_tail"] = (
                    f"devicectl launch timed out after {error.timeout} seconds; continuing with the existing headset app state"
                )
            device_state["processes_after_launch"] = query_app_processes(
                args.avp_device,
                args.avp_bundle_id,
                logs_dir / "avp-processes-after-launch.log",
            )
            meta["device_state"] = device_state
        pairing_qr = maybe_write_qr(startup_snapshot, qr_path)
        if pairing_qr is not None:
            reveal_local_artifact(pairing_qr)
            print(f"PAIRING_QR={pairing_qr}", flush=True)
            print("READY_FOR_QR_SCAN=1", flush=True)
        write_json(config_dir / "meta.json", meta)
        print(f"READY_FOR_USER_ACTION host={host} port={args.port}", flush=True)
        print(
            f"ACTION: Put the headset on, open Clear XR, keep it frontmost, and approve any visionOS streaming authorization prompt for {host}:{args.port}.",
            flush=True,
        )
        if interactive_task is not None:
            qr_materialized = monitor_interactive_task_for_qr(
                args.windows_ssh_target,
                interactive_task,
                headless_log,
                qr_path,
                args.run_seconds,
            )
            wait_for_interactive_task_exit(
                args.windows_ssh_target,
                interactive_task,
                launcher_info.get("ChildPid") if launcher_info else None,
                args.run_seconds + 30,
            )
            qr_result = collect_interactive_task_output(
                args.windows_ssh_target,
                interactive_task,
                headless_log,
            )
            stdout_log_text = qr_result.get("stdout_log_text") if isinstance(qr_result, dict) else None
            if isinstance(stdout_log_text, str):
                qr_materialized = materialize_qr_from_text(stdout_log_text, qr_path)
                if qr_materialized is not None:
                    reveal_local_artifact(Path(qr_materialized))
                    qr_result["qr_path"] = qr_materialized
            if launcher_info is None:
                launcher_info = qr_result.get("launcher_info")
        else:
            assert process is not None
            qr_result = drain_remote_output(process, headless_log, qr_path)
    except Exception as error:  # noqa: BLE001
        startup_error = str(error)
        print(f"STARTUP_ERROR={startup_error}", flush=True)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    outcome: dict[str, Any] = {
        "pass": False,
        "server_process_returncode": process.returncode if process is not None else None,
        "startup_error": startup_error,
        "startup_snapshot": startup_snapshot,
        "qr_path": qr_result.get("qr_path") if isinstance(qr_result, dict) else None,
        "launcher_info": launcher_info,
    }
    if not args.skip_device:
        preferences_snapshot = copy_app_preferences(
            args.avp_device,
            args.avp_bundle_id,
            config_dir / "avp-preferences.plist",
            logs_dir / "avp-preferences-copy.log",
        )
        device_state["processes_after_run"] = query_app_processes(
            args.avp_device,
            args.avp_bundle_id,
            logs_dir / "avp-processes-after-run.log",
        )
        device_state["preferences_snapshot"] = preferences_snapshot
        meta["device_state"] = device_state
        write_json(config_dir / "meta.json", meta)
        outcome["device_state"] = device_state

    outcome["pass"] = startup_error is None and (process is None or process.returncode == 0)
    write_json(config_dir / "outcome.json", outcome)
    print(json.dumps(outcome, indent=2, sort_keys=True), flush=True)
    return 0 if outcome["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
