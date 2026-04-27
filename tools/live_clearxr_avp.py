#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from typing import Any


DEFAULT_ARCHIVE_ROOT = Path("temp/clearxr_live_runs")
DEFAULT_SERVER_BUNDLE_ID = "com.shinycomputers.clearxrclient"
DEFAULT_AVP_DEVICE = "Apple Vision Pro"
STARTUP_TIMEOUT_SECONDS = 30.0


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def clearxr_crate_dir() -> Path:
    return repo_root() / "temp" / "external" / "clearxr-server" / "clearxr-streamer"


def clearxr_probe_script() -> Path:
    return repo_root() / "tools" / "clearxr_native_probe.py"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def detect_host_address() -> str:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
            if address and not address.startswith("127."):
                return address
        except OSError:
            pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if isinstance(address, str) and address and not address.startswith("127."):
                return address
    except OSError:
        pass

    return "127.0.0.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live native ClearXR Apple Vision Pro validation bundle",
    )
    parser.add_argument(
        "--host",
        default="auto",
        help="advertised/bind host for the ClearXR headless server (default: auto-detect LAN IP)",
    )
    parser.add_argument("--port", type=int, default=55000, help="session-management port")
    parser.add_argument(
        "--server-bundle-id",
        default=DEFAULT_SERVER_BUNDLE_ID,
        help="bundle id advertised by the ClearXR server over Bonjour",
    )
    parser.add_argument(
        "--avp-device",
        default=DEFAULT_AVP_DEVICE,
        help="devicectl device name or identifier for Apple Vision Pro",
    )
    parser.add_argument(
        "--avp-bundle-id",
        default=DEFAULT_SERVER_BUNDLE_ID,
        help="Vision Pro ClearXR app bundle identifier to launch via devicectl",
    )
    parser.add_argument(
        "--run-seconds",
        type=int,
        default=180,
        help="how long to keep the native ClearXR server running",
    )
    parser.add_argument(
        "--snapshot-interval-seconds",
        type=int,
        default=1,
        help="snapshot cadence passed to clearxr-streamer headless mode",
    )
    parser.add_argument(
        "--archive-root",
        default=str(DEFAULT_ARCHIVE_ROOT),
        help="directory where live ClearXR run bundles will be written",
    )
    parser.add_argument(
        "--cargo-command",
        nargs="+",
        default=["cargo", "run", "--"],
        help="command prefix used to launch clearxr-streamer",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="skip the local native control-plane preflight probe",
    )
    parser.add_argument(
        "--skip-device",
        action="store_true",
        help="do not query or launch the Vision Pro app; host-side harness only",
    )
    parser.add_argument(
        "--no-restart-app",
        action="store_true",
        help="skip the best-effort devicectl launch of the Vision Pro ClearXR app",
    )
    return parser


def run_capture(command: list[str], log_path: Path, timeout_seconds: int) -> tuple[int, str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    text = (result.stdout or "") + (result.stderr or "")
    log_path.write_text(text, encoding="utf-8")
    return result.returncode, text


def run_devc_json(args: list[str], log_path: Path) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(prefix="devicectl-json-", suffix=".json", delete=False) as tmp:
        json_path = Path(tmp.name)

    command = ["xcrun", "devicectl", *args, "--json-output", str(json_path)]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    log_path.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")

    payload: dict[str, Any]
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            payload = {"info": {"outcome": "error", "error": str(error)}, "result": {}}
    else:
        payload = {
            "info": {"outcome": "error", "error": "devicectl did not emit JSON output"},
            "result": {},
        }

    json_path.unlink(missing_ok=True)
    payload["_returncode"] = result.returncode
    return payload


def launch_avp_app(device: str, bundle_id: str, log_path: Path) -> tuple[int, str]:
    command = [
        "xcrun",
        "devicectl",
        "device",
        "process",
        "launch",
        "--device",
        device,
        "--terminate-existing",
        "--activate",
        bundle_id,
    ]
    return run_capture(command, log_path, timeout_seconds=30)


def query_app_processes(device: str, bundle_id: str, log_path: Path) -> dict[str, Any]:
    payload = run_devc_json(
        [
            "device",
            "info",
            "processes",
            "--device",
            device,
            "--filter",
            (
                f"name CONTAINS[c] 'Clear XR' OR executablePath CONTAINS[c] '{bundle_id}' "
                "OR executablePath CONTAINS[c] 'ClearXR'"
            ),
        ],
        log_path,
    )
    running = payload.get("result", {}).get("runningProcesses", [])
    if not isinstance(running, list):
        running = []
    return {
        "returncode": payload.get("_returncode"),
        "processes": running,
    }


def terminate_app_processes(
    device: str,
    process_snapshot: dict[str, Any],
    logs_dir: Path,
) -> dict[str, Any]:
    terminations: list[dict[str, Any]] = []
    for process in process_snapshot.get("processes", []):
        if not isinstance(process, dict):
            continue
        pid = process.get("processIdentifier")
        if not isinstance(pid, int):
            continue
        rc, text = run_capture(
            [
                "xcrun",
                "devicectl",
                "device",
                "process",
                "terminate",
                "--device",
                device,
                "--pid",
                str(pid),
                "--kill",
            ],
            logs_dir / f"avp-terminate-{pid}.log",
            timeout_seconds=30,
        )
        terminations.append(
            {
                "pid": pid,
                "returncode": rc,
                "output_tail": text[-2000:],
            }
        )
    return {
        "terminations": terminations,
    }


def copy_app_preferences(
    device: str,
    bundle_id: str,
    destination: Path,
    log_path: Path,
) -> dict[str, Any]:
    command = [
        "xcrun",
        "devicectl",
        "device",
        "copy",
        "from",
        "--device",
        device,
        "--domain-type",
        "appDataContainer",
        "--domain-identifier",
        bundle_id,
        "--source",
        f"Library/Preferences/{bundle_id}.plist",
        "--destination",
        str(destination),
    ]
    rc, text = run_capture(command, log_path, timeout_seconds=30)
    parsed: dict[str, Any] | None = None
    if rc == 0 and destination.exists():
        with destination.open("rb") as handle:
            with contextlib.suppress(Exception):
                value = plistlib.load(handle)
                if isinstance(value, dict):
                    parsed = value
    debug_markers = extract_debug_markers(parsed)
    return {
        "returncode": rc,
        "path": str(destination),
        "parsed": parsed,
        "debug_markers": debug_markers,
        "output_tail": text[-2000:],
    }


def push_app_preferences(
    device: str,
    bundle_id: str,
    source: Path,
    log_path: Path,
) -> dict[str, Any]:
    command = [
        "xcrun",
        "devicectl",
        "device",
        "copy",
        "to",
        "--device",
        device,
        "--source",
        str(source),
        "--destination",
        f"Library/Preferences/{bundle_id}.plist",
        "--domain-type",
        "appDataContainer",
        "--domain-identifier",
        bundle_id,
    ]
    rc, text = run_capture(command, log_path, timeout_seconds=30)
    return {
        "returncode": rc,
        "path": str(source),
        "output_tail": text[-2000:],
    }


def seed_auto_connect_preferences(
    device: str,
    bundle_id: str,
    host: str,
    port: int,
    config_dir: Path,
    logs_dir: Path,
) -> dict[str, Any]:
    existing = copy_app_preferences(
        device,
        bundle_id,
        config_dir / "avp-preferences-before-seed.plist",
        logs_dir / "avp-preferences-before-seed.log",
    )
    parsed = existing.get("parsed")
    prefs = dict(parsed) if isinstance(parsed, dict) else {}
    prefs.update(
        {
            "ipAddress": host,
            "port": port,
            "lastConnectionMode": "manual",
            "selectedEndpointHost": host,
            "selectedEndpointPort": port,
            "autoConnectRequested": True,
        }
    )
    seeded_path = config_dir / "avp-preferences-seeded.plist"
    with seeded_path.open("wb") as handle:
        plistlib.dump(prefs, handle)
    push_result = push_app_preferences(
        device,
        bundle_id,
        seeded_path,
        logs_dir / "avp-preferences-seeded-push.log",
    )
    return {
        "existing": existing,
        "push": push_result,
        "seeded_path": str(seeded_path),
        "seeded_values": {
            "ipAddress": host,
            "port": port,
            "lastConnectionMode": "manual",
            "selectedEndpointHost": host,
            "selectedEndpointPort": port,
            "autoConnectRequested": True,
        },
    }


def extract_debug_markers(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None
    markers = {
        key.removeprefix("debug."): value
        for key, value in parsed.items()
        if isinstance(key, str) and key.startswith("debug.")
    }
    return markers or None


def wait_for_startup(process: subprocess.Popen[str], log_path: Path) -> tuple[Path, dict[str, Any]]:
    archive_dir: Path | None = None
    startup_snapshot: dict[str, Any] | None = None
    stdout = process.stdout
    if stdout is None:
        raise RuntimeError("clearxr headless process has no stdout pipe")

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    with log_path.open("w", encoding="utf-8") as handle:
        while time.monotonic() < deadline:
            line = stdout.readline()
            if not line:
                if process.poll() is not None:
                    raise RuntimeError("clearxr headless exited before startup completed")
                time.sleep(0.1)
                continue

            handle.write(line)
            handle.flush()

            stripped = line.strip()
            if stripped.startswith("CLEARXR_HEADLESS_ARCHIVE "):
                archive_dir = Path(stripped.split(" ", 1)[1])
            elif stripped.startswith("CLEARXR_HEADLESS_SNAPSHOT startup "):
                startup_snapshot = json.loads(stripped.split(" ", 2)[2])
                break

    if archive_dir is None or startup_snapshot is None:
        raise RuntimeError("clearxr headless startup markers were incomplete")
    return archive_dir.resolve(), startup_snapshot


def build_runtime_backend_warning(startup_snapshot: dict[str, Any] | None) -> str | None:
    if not isinstance(startup_snapshot, dict):
        return None

    cloudxr = startup_snapshot.get("cloudxr")
    cloudxr_detail = cloudxr.get("detail") if isinstance(cloudxr, dict) else None
    if isinstance(cloudxr_detail, str) and "Native macOS control-plane backend is ready" in cloudxr_detail:
        return (
            "WARNING: This host is using the macOS placeholder backend because the vendored "
            "CloudXR runtime is unavailable. Pairing and control-plane checks may work, but "
            "real media streaming is not expected to succeed on this host."
        )

    notes = startup_snapshot.get("notes")
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, str) and "CloudXR runtime loading failed on this macOS host" in note:
                return (
                    "WARNING: This host is using the macOS placeholder backend because the "
                    "vendored CloudXR runtime is unavailable. Pairing and control-plane checks "
                    "may work, but real media streaming is not expected to succeed on this host."
                )

    return None


def drain_process_output(process: subprocess.Popen[str], log_path: Path) -> None:
    stdout = process.stdout
    if stdout is None:
        return
    with log_path.open("a", encoding="utf-8") as handle:
        for line in stdout:
            handle.write(line)


def summarize_server_archive(server_archive_dir: Path) -> dict[str, Any]:
    events_path = server_archive_dir / "events.jsonl"
    summary_path = server_archive_dir / "summary.json"
    summary = read_json(summary_path) or {}

    event_records: list[dict[str, Any]] = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            with contextlib.suppress(json.JSONDecodeError):
                event_records.append(json.loads(line))

    def saw(category: str, event: str) -> bool:
        return any(
            record.get("category") == category and record.get("event") == event
            for record in event_records
        )

    session_statuses = [
        record.get("fields", {}).get("status")
        for record in event_records
        if record.get("category") == "session_management"
        and record.get("event") == "session_status_changed"
    ]
    session_statuses = [status for status in session_statuses if isinstance(status, str)]

    return {
        "event_count": summary.get("event_count"),
        "snapshot_count": summary.get("snapshot_count"),
        "last_session_id": summary.get("last_session_id"),
        "observed_client_connected": saw("session_management", "client_connected"),
        "observed_acknowledge_connection": saw(
            "session_management", "sent_acknowledge_connection"
        ),
        "observed_media_stream_ready": saw("session_management", "sent_media_stream_ready"),
        "observed_native_presentation_started": saw("cloudxr", "native_presentation_started"),
        "observed_runtime_backend_unavailable": saw("cloudxr", "runtime_backend_unavailable"),
        "observed_native_backend_ready": saw("cloudxr", "native_backend_ready"),
        "observed_apply_configuration": saw("session_management", "apply_configuration"),
        "observed_native_configuration_applied": saw("cloudxr", "native_configuration_applied"),
        "observed_status_connected": "CONNECTED" in session_statuses,
        "observed_status_waiting": "WAITING" in session_statuses,
        "observed_status_disconnected": "DISCONNECTED" in session_statuses,
        "observed_barcode_presentation_requested": saw(
            "session_management", "sent_acknowledge_barcode_presentation"
        ),
        "session_statuses": session_statuses,
    }


def extract_qr_data_url(server_archive_dir: Path) -> str | None:
    snapshots_path = server_archive_dir / "snapshots.jsonl"
    if not snapshots_path.exists():
        return None

    last_qr_data_url: str | None = None
    for line in snapshots_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        with contextlib.suppress(json.JSONDecodeError):
            record = json.loads(line)
            snapshot = record.get("snapshot", {})
            qr_data_url = snapshot.get("qrDataUrl")
            if isinstance(qr_data_url, str) and qr_data_url.startswith("data:image/png;base64,"):
                last_qr_data_url = qr_data_url
    return last_qr_data_url


def write_qr_png(data_url: str, destination: Path) -> Path:
    encoded = data_url.split(",", 1)[1]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(base64.b64decode(encoded))
    return destination


def reveal_local_artifact(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["open", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def materialize_pairing_qr(server_archive_dir: Path, destination: Path) -> Path | None:
    qr_data_url = extract_qr_data_url(server_archive_dir)
    if not qr_data_url:
        return None
    return write_qr_png(qr_data_url, destination)


def find_single_summary(root: Path) -> Path | None:
    summaries = sorted(root.glob("**/probe_summary.json"))
    return summaries[-1] if summaries else None


def run_preflight(run_dir: Path, host: str, port: int) -> dict[str, Any]:
    preflight_root = run_dir / "preflight"
    preflight_root.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "logs" / "preflight-native-probe.log"
    command = [
        sys.executable,
        str(clearxr_probe_script()),
        "--host",
        host,
        "--port",
        str(port),
        "--run-seconds",
        "12",
        "--archive-root",
        str(preflight_root),
    ]
    rc, text = run_capture(command, log_path, timeout_seconds=40)
    summary_path = find_single_summary(preflight_root)
    summary = read_json(summary_path) if summary_path is not None else None
    return {
        "command": command,
        "exit_code": rc,
        "summary_path": str(summary_path) if summary_path is not None else None,
        "summary": summary,
        "stdout_excerpt": text[-4000:],
    }


def main() -> int:
    args = build_parser().parse_args()
    host = detect_host_address() if args.host == "auto" else args.host

    run_root = Path(args.archive_root).resolve()
    run_dir = run_root / f"{utc_stamp()}-live-clearxr-avp"
    logs_dir = run_dir / "logs"
    config_dir = run_dir / "config"
    logs_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    print(f"RUN={run_dir}")
    Path("/tmp/current_live_run.txt").write_text(str(run_dir), encoding="utf-8")

    meta: dict[str, Any] = {
        "host": host,
        "port": args.port,
        "server_bundle_id": args.server_bundle_id,
        "avp_device": args.avp_device,
        "avp_bundle_id": args.avp_bundle_id,
        "run_seconds": args.run_seconds,
        "snapshot_interval_seconds": args.snapshot_interval_seconds,
        "skip_device": args.skip_device,
        "skip_preflight": args.skip_preflight,
    }
    write_json(config_dir / "meta.json", meta)

    if not args.skip_preflight:
        preflight = run_preflight(run_dir, host, args.port)
        meta["preflight"] = preflight
        write_json(config_dir / "meta.json", meta)
        summary = preflight.get("summary") or {}
        if preflight["exit_code"] != 0 or summary.get("probe_passed") is not True:
            preflight_outcome = {
                "pass": False,
                "failure_stage": "preflight",
                "preflight_exit_code": preflight["exit_code"],
                "preflight_summary_path": preflight["summary_path"],
            }
            write_json(config_dir / "outcome.json", preflight_outcome)
            print("preflight failed; see logs/preflight-native-probe.log")
            return 1

    device_state: dict[str, Any] = {}
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
        if not args.no_restart_app:
            restart_rc, restart_text = launch_avp_app(
                args.avp_device,
                args.avp_bundle_id,
                logs_dir / "avp-app-launch.log",
            )
            device_state["launch_exit_code"] = restart_rc
            device_state["launch_output_tail"] = restart_text[-2000:]
            device_state["processes_after_launch"] = query_app_processes(
                args.avp_device,
                args.avp_bundle_id,
                logs_dir / "avp-processes-after-launch.log",
            )
        meta["device_state"] = device_state
        write_json(config_dir / "meta.json", meta)

    server_archive_root = (run_dir / "server").resolve()
    command = [
        *args.cargo_command,
        "--clearxr-headless",
        "--clearxr-headless-bundle-id",
        args.server_bundle_id,
        "--clearxr-headless-host",
        host,
        "--clearxr-headless-port",
        str(args.port),
        "--clearxr-headless-run-seconds",
        str(args.run_seconds),
        "--clearxr-headless-snapshot-interval-seconds",
        str(args.snapshot_interval_seconds),
        "--clearxr-headless-archive-root",
        str(server_archive_root),
    ]

    process = subprocess.Popen(
        command,
        cwd=clearxr_crate_dir(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "RUST_LOG": os.environ.get("RUST_LOG", "info")},
    )

    headless_log = logs_dir / "clearxr-headless.log"
    server_archive_dir: Path | None = None
    startup_snapshot: dict[str, Any] | None = None
    startup_error: str | None = None
    pairing_qr_path: Path | None = None
    pairing_qr_opened = False
    runtime_backend_warning: str | None = None
    try:
        server_archive_dir, startup_snapshot = wait_for_startup(process, headless_log)
        runtime_backend_warning = build_runtime_backend_warning(startup_snapshot)
        meta["server_command"] = command
        meta["server_archive_dir"] = str(server_archive_dir)
        meta["startup_snapshot"] = startup_snapshot
        meta["runtime_backend_warning"] = runtime_backend_warning
        write_json(config_dir / "meta.json", meta)
        print(f"SERVER_ARCHIVE={server_archive_dir}")
        if runtime_backend_warning:
            print(runtime_backend_warning)
        print(
            f"ACTION: On Apple Vision Pro, use Local IP and connect to {host}:{args.port} before the run window ends."
        )
        deadline = time.monotonic() + max(args.run_seconds, 1) + 10
        while time.monotonic() < deadline:
            if server_archive_dir.exists() and not pairing_qr_opened:
                pairing_qr_path = materialize_pairing_qr(
                    server_archive_dir,
                    config_dir / "pairing-qr.png",
                )
                if pairing_qr_path is not None:
                    reveal_local_artifact(pairing_qr_path)
                    print(f"PAIRING_QR={pairing_qr_path}")
                    print(
                        "ACTION: The pairing QR is open on this Mac. In the headset, look at the Mac screen, scan the QR, then approve the authorization prompt."
                    )
                    pairing_qr_opened = True

            if process.poll() is not None:
                break
            time.sleep(1)

        if process.poll() is None:
            process.wait(timeout=5)
    except Exception as error:  # noqa: BLE001
        startup_error = str(error)
    finally:
        if process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        drain_process_output(process, headless_log)

    outcome: dict[str, Any] = {
        "pass": False,
        "server_process_returncode": process.returncode,
        "server_archive_dir": str(server_archive_dir) if server_archive_dir is not None else None,
        "startup_error": startup_error,
    }
    if startup_snapshot is not None:
        outcome["startup_snapshot"] = startup_snapshot
    if runtime_backend_warning is not None:
        outcome["runtime_backend_warning"] = runtime_backend_warning
    if server_archive_dir is not None and server_archive_dir.exists():
        outcome["server_summary"] = summarize_server_archive(server_archive_dir)
        if pairing_qr_path is None:
            pairing_qr_path = materialize_pairing_qr(
                server_archive_dir,
                config_dir / "pairing-qr.png",
            )
        if pairing_qr_path is not None:
            outcome["pairing_qr_png"] = str(pairing_qr_path)
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

    server_summary = outcome.get("server_summary", {})
    harness_pass = bool(startup_error is None and process.returncode == 0)
    live_connection_observed = bool(
        server_summary.get("observed_client_connected")
        and server_summary.get("observed_acknowledge_connection")
        and server_summary.get("observed_media_stream_ready")
    )
    placeholder_backend_active = bool(
        server_summary.get("observed_runtime_backend_unavailable")
        and server_summary.get("observed_native_backend_ready")
    )
    debug_markers = device_state.get("preferences_snapshot", {}).get("debug_markers") or {}
    outcome["harness_pass"] = harness_pass
    outcome["live_connection_observed"] = live_connection_observed
    outcome["placeholder_backend_active"] = placeholder_backend_active
    outcome["app_debug_evidence"] = {
        "app_initialized": bool(debug_markers.get("appInitAt")),
        "main_window_appeared": bool(debug_markers.get("mainWindowAppearAt")),
        "connection_view_task_started": bool(debug_markers.get("connectionViewTaskAt")),
        "connection_attempt_started": bool(debug_markers.get("connectionAttemptAt")),
        "tcp_probe_started": bool(debug_markers.get("tcpProbeStartedAt")),
        "tcp_probe_succeeded": bool(debug_markers.get("tcpProbeSucceededAt")),
        "tcp_probe_failed": bool(debug_markers.get("tcpProbeFailedAt")),
        "tcp_probe_error": debug_markers.get("tcpProbeError"),
        "connection_call_returned": bool(debug_markers.get("connectionCallReturnedAt")),
        "connection_call_failed": bool(debug_markers.get("connectionCallFailedAt")),
        "connection_call_error": debug_markers.get("connectionCallError"),
        "last_scene_phase": debug_markers.get("mainWindowScenePhase"),
        "last_session_status": debug_markers.get("lastSessionStatus"),
        "last_disconnect_reason": debug_markers.get("lastDisconnectReason"),
        "connection_attempt_trigger": debug_markers.get("connectionAttemptTrigger"),
        "connection_attempt_mode": debug_markers.get("connectionAttemptMode"),
        "connection_attempt_host": debug_markers.get("connectionAttemptHost"),
        "connection_attempt_port": debug_markers.get("connectionAttemptPort"),
    }
    process_alive = bool(device_state.get("processes_after_run", {}).get("processes"))
    attempted_connection = bool(debug_markers.get("connectionAttemptAt"))
    connection_error = debug_markers.get("connectionCallError") or debug_markers.get(
        "lastDisconnectReason"
    )
    unauthorized = isinstance(connection_error, str) and "unauthorized" in connection_error.lower()
    outcome["ui_action_required_suspected"] = bool(
        not args.skip_device
        and device_state.get("launch_exit_code") == 0
        and process_alive
        and not attempted_connection
        and not live_connection_observed
    )
    outcome["main_window_not_foreground_suspected"] = bool(
        not args.skip_device
        and device_state.get("launch_exit_code") == 0
        and process_alive
        and not debug_markers.get("mainWindowAppearAt")
        and not live_connection_observed
    )
    outcome["client_session_management_failure_suspected"] = bool(
        not args.skip_device
        and device_state.get("launch_exit_code") == 0
        and process_alive
        and attempted_connection
        and connection_error
        and not unauthorized
        and not live_connection_observed
    )
    outcome["authorization_required_suspected"] = bool(
        not args.skip_device
        and device_state.get("launch_exit_code") == 0
        and process_alive
        and attempted_connection
        and unauthorized
        and not live_connection_observed
    )
    outcome["post_pairing_stream_start_failure_suspected"] = bool(
        not args.skip_device
        and device_state.get("launch_exit_code") == 0
        and process_alive
        and attempted_connection
        and connection_error
        and live_connection_observed
    )
    outcome["real_media_backend_unavailable_suspected"] = bool(
        placeholder_backend_active
    )
    outcome["pairing_qr_presented"] = bool(outcome.get("pairing_qr_png"))
    outcome["pass"] = harness_pass if args.skip_device else (
        harness_pass
        and live_connection_observed
        and not outcome["real_media_backend_unavailable_suspected"]
        and not outcome["post_pairing_stream_start_failure_suspected"]
    )
    write_json(config_dir / "outcome.json", outcome)
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0 if outcome["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
