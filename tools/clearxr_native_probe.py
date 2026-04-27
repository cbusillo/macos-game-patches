#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any


DEFAULT_ARCHIVE_ROOT = Path("temp/clearxr_native_probe_runs")
STARTUP_TIMEOUT_SECONDS = 30.0


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def crate_dir() -> Path:
    return repo_root() / "temp" / "external" / "clearxr-server" / "clearxr-streamer"


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch the native ClearXR headless server and probe the control-plane handshake",
    )
    parser.add_argument("--host", default="127.0.0.1", help="headless server host address")
    parser.add_argument("--port", type=int, default=55000, help="session-management port")
    parser.add_argument(
        "--bundle-id",
        default="com.shinycomputers.clearxrclient",
        help="bundle identifier advertised by the headless server",
    )
    parser.add_argument(
        "--run-seconds",
        type=int,
        default=15,
        help="auto-shutdown window passed to clearxr-streamer headless mode",
    )
    parser.add_argument(
        "--archive-root",
        default=str(DEFAULT_ARCHIVE_ROOT),
        help="directory where probe bundles will be written",
    )
    parser.add_argument(
        "--cargo-command",
        nargs="+",
        default=["cargo", "run", "--"],
        help="command prefix used to launch clearxr-streamer",
    )
    return parser.parse_args()


def wait_for_startup(process: subprocess.Popen[str], log_path: Path) -> tuple[Path, dict[str, Any]]:
    archive_dir: Path | None = None
    startup_snapshot: dict[str, Any] | None = None
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    stdout = process.stdout
    if stdout is None:
        raise RuntimeError("clearxr headless was launched without a stdout pipe")

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
                payload = stripped.split(" ", 2)[2]
                startup_snapshot = json.loads(payload)
                break

    if archive_dir is None:
        raise RuntimeError("clearxr headless did not print CLEARXR_HEADLESS_ARCHIVE")
    if startup_snapshot is None:
        raise RuntimeError("clearxr headless did not reach startup snapshot")
    return archive_dir, startup_snapshot


def main() -> int:
    args = parse_args()
    probe_root = Path(args.archive_root).resolve()
    run_dir = probe_root / f"{utc_stamp()}-clearxr-native-probe"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    server_archive_root = (run_dir / "server").resolve()
    command = [
        *args.cargo_command,
        "--clearxr-headless",
        "--clearxr-headless-bundle-id",
        args.bundle_id,
        "--clearxr-headless-host",
        args.host,
        "--clearxr-headless-port",
        str(args.port),
        "--clearxr-headless-run-seconds",
        str(args.run_seconds),
        "--clearxr-headless-snapshot-interval-seconds",
        "1",
        "--clearxr-headless-archive-root",
        str(server_archive_root),
    ]

    process = subprocess.Popen(
        command,
        cwd=crate_dir(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "RUST_LOG": os.environ.get("RUST_LOG", "info")},
    )

    local_summary: dict[str, Any] = {
        "command": command,
        "host": args.host,
        "requested_port": args.port,
        "probe_run_dir": str(run_dir),
        "probe_started_at_utc": datetime.now(UTC).isoformat(),
    }

    error_message: str | None = None

    try:
        archive_dir, startup_snapshot = wait_for_startup(process, logs_dir / "clearxr-headless.log")
        local_summary["server_archive_dir"] = str(archive_dir.resolve())
        local_summary["startup_snapshot"] = startup_snapshot
        local_summary["actual_port"] = startup_snapshot["config"]["port"]

        session_id = f"probe-session-{utc_stamp()}"
        client_id = "local-clearxr-probe"
        apply_configuration = {
            "Event": "ApplyConfiguration",
            "RenderedResolution": 3840,
            "EncodedResolution": 2880,
            "FoveationInsetRatio": 0.15,
            "DefaultAppEnabled": False,
            "AlphaTransparencyEnabled": True,
        }
        request_connection = {
            "Event": "RequestConnection",
            "SessionID": session_id,
            "ProtocolVersion": "1",
            "StreamingProvider": "CloudXR",
            "StreamingProviderVersion": "6.x",
            "UserInterfaceIdiom": "visionOS",
            "ClientID": client_id,
        }
        waiting_status = {
            "Event": "SessionStatusDidChange",
            "SessionID": session_id,
            "Status": "WAITING",
        }
        disconnect_status = {
            "Event": "SessionStatusDidChange",
            "SessionID": session_id,
            "Status": "DISCONNECTED",
        }

        with socket.create_connection((args.host, int(local_summary["actual_port"])), timeout=5) as sock:
            sock.settimeout(5)
            write_frame(sock, apply_configuration)
            write_frame(sock, request_connection)
            acknowledge = read_frame(sock)
            write_frame(sock, waiting_status)
            ready = read_frame(sock)
            write_frame(sock, disconnect_status)

        local_summary["probe_messages"] = {
            "apply_configuration": apply_configuration,
            "request_connection": request_connection,
            "acknowledge_connection": acknowledge,
            "waiting_status": waiting_status,
            "media_stream_ready": ready,
            "disconnect_status": disconnect_status,
        }

        if acknowledge.get("Event") != "AcknowledgeConnection":
            raise RuntimeError(f"unexpected acknowledge response: {acknowledge}")
        if ready.get("Event") != "MediaStreamIsReady":
            raise RuntimeError(f"unexpected ready response: {ready}")

        local_summary["probe_passed"] = True
        local_summary["probe_finished_at_utc"] = datetime.now(UTC).isoformat()
    except Exception as error:  # pragma: no cover - exercised in live runs.
        error_message = str(error)
        local_summary["probe_passed"] = False
        local_summary["probe_error"] = error_message
    finally:
        try:
            process.wait(timeout=max(args.run_seconds, 1) + 5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        local_summary["process_returncode"] = process.returncode

    summary_path = run_dir / "probe_summary.json"
    summary_path.write_text(json.dumps(local_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(local_summary, indent=2, sort_keys=True))
    if error_message is not None:
        return 1
    return 0 if local_summary.get("probe_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
