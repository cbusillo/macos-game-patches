#!/usr/bin/env python3

import argparse
from collections import Counter
import json
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import time
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from steamvr_smoke import (
    copy_log_with_delta,
    minimize_crossover_windows,
    read_json,
    snapshot_log_sizes,
    write_json,
)


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def run_bundle_dir(base_dir: Path) -> Path:
    run_dir = base_dir / f"{utc_stamp()}-live-avp-checkpoint"
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "config").mkdir(parents=True, exist_ok=True)
    return run_dir


def prune_old_run_bundles(
    run_root: Path,
    *,
    keep_last: int,
    older_than_days: int,
) -> list[Path]:
    if keep_last < 0:
        raise ValueError("keep_last must be >= 0")
    if older_than_days < 0:
        raise ValueError("older_than_days must be >= 0")

    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    candidates = sorted(
        [
            path
            for path in run_root.glob("*-live-avp-checkpoint")
            if path.is_dir()
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    pruned: list[Path] = []
    for index, path in enumerate(candidates):
        if index < keep_last:
            continue

        modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if modified_at > cutoff:
            continue

        shutil.rmtree(path)
        pruned.append(path)

    return pruned


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _parse_png_flat_rgba(path: Path) -> tuple[bool, tuple[int, int, int, int] | None] | None:
    """Return whether a PNG is a single flat RGBA color.

    Returns:
        None: Unsupported/invalid PNG.
        (False, first_pixel): Parsed PNG with at least two different pixels.
        (True, color): Parsed PNG where every pixel matches one RGBA color.
    """

    try:
        content = path.read_bytes()
    except OSError:
        return None

    png_magic = b"\x89PNG\r\n\x1a\n"
    if len(content) < 8 or content[:8] != png_magic:
        return None

    width = height = 0
    bit_depth = color_type = -1
    idat_parts: list[bytes] = []

    cursor = 8
    while cursor + 8 <= len(content):
        chunk_len = struct.unpack(">I", content[cursor : cursor + 4])[0]
        cursor += 4
        chunk_type = content[cursor : cursor + 4]
        cursor += 4
        if cursor + chunk_len + 4 > len(content):
            return None
        chunk_data = content[cursor : cursor + chunk_len]
        cursor += chunk_len + 4  # skip payload + CRC

        if chunk_type == b"IHDR":
            if len(chunk_data) != 13:
                return None
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(
                ">IIBBBBB", chunk_data
            )
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width <= 0 or height <= 0 or not idat_parts:
        return None
    if bit_depth != 8:
        return None

    if color_type == 6:
        bytes_per_pixel = 4
    elif color_type == 2:
        bytes_per_pixel = 3
    else:
        return None

    try:
        raw = zlib.decompress(b"".join(idat_parts))
    except zlib.error:
        return None

    stride = width * bytes_per_pixel
    expected_min_len = (stride + 1) * height
    if len(raw) < expected_min_len:
        return None

    prev_row = bytearray(stride)
    offset = 0
    reference: tuple[int, int, int, int] | None = None

    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset : offset + stride])
        offset += stride
        if len(row) != stride:
            return None

        if filter_type == 1:
            for i in range(stride):
                left = row[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                row[i] = (row[i] + left) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                row[i] = (row[i] + prev_row[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = row[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                up = prev_row[i]
                row[i] = (row[i] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = row[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                up = prev_row[i]
                up_left = prev_row[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                row[i] = (row[i] + _paeth_predictor(left, up, up_left)) & 0xFF
        elif filter_type != 0:
            return None

        for i in range(0, stride, bytes_per_pixel):
            if bytes_per_pixel == 4:
                pixel = (row[i], row[i + 1], row[i + 2], row[i + 3])
            else:
                pixel = (row[i], row[i + 1], row[i + 2], 255)

            if reference is None:
                reference = pixel
            elif pixel != reference:
                return (False, reference)

        prev_row = row

    return (True, reference)


def analyze_vtbridge_debug_frames(frames_dir: Path | None) -> dict[str, Any]:
    analysis: dict[str, Any] = {
        "source_debug_frame_count": 0,
        "source_debug_flat_frame_count": 0,
        "source_debug_nonflat_frame_count": 0,
        "source_debug_unknown_frame_count": 0,
        "source_debug_flat_ratio": 0.0,
        "source_debug_all_flat": False,
        "source_debug_flat_colors": [],
    }

    if frames_dir is None or not frames_dir.exists() or not frames_dir.is_dir():
        return analysis

    png_paths = sorted(frames_dir.glob("*.png"))
    if not png_paths:
        return analysis

    flat_colors: set[str] = set()
    flat_count = 0
    nonflat_count = 0
    unknown_count = 0

    for path in png_paths:
        parsed = _parse_png_flat_rgba(path)
        if parsed is None:
            unknown_count += 1
            continue
        is_flat, color = parsed
        if is_flat:
            flat_count += 1
            if color is not None:
                flat_colors.add(
                    f"{color[0]},{color[1]},{color[2]},{color[3]}"
                )
        else:
            nonflat_count += 1

    total = len(png_paths)
    analysis["source_debug_frame_count"] = total
    analysis["source_debug_flat_frame_count"] = flat_count
    analysis["source_debug_nonflat_frame_count"] = nonflat_count
    analysis["source_debug_unknown_frame_count"] = unknown_count
    analysis["source_debug_flat_ratio"] = (flat_count / total) if total > 0 else 0.0
    analysis["source_debug_all_flat"] = total > 0 and flat_count == total
    analysis["source_debug_flat_colors"] = sorted(flat_colors)
    return analysis


def run_capture(command: list[str], output_path: Path, env: dict[str, str] | None = None, cwd: Path | None = None) -> int:
    return run_capture_timeout(command, output_path, env=env, cwd=cwd)


def run_capture_timeout(
    command: list[str],
    output_path: Path,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
    timeout_is_success: bool = False,
) -> int:
    def normalize_output(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value).decode("utf-8", errors="replace")
        return str(value)

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_text = normalize_output(exc.stdout)
        stderr_text = normalize_output(exc.stderr)
        output_path.write_text(
            stdout_text + stderr_text + f"\n[timeout after {timeout_seconds}s]\n",
            encoding="utf-8",
        )
        if timeout_is_success:
            return 0
        return 124

    output_path.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
    return result.returncode


def read_json_retry(
    path: Path,
    *,
    retries: int = 20,
    sleep_seconds: float = 0.2,
    allow_empty_fallback: bool = False,
) -> dict[str, Any]:
    """Read a JSON file that may be transiently empty while another process writes it."""
    last_error: Exception | None = None
    for _ in range(retries):
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        if not raw_text.strip():
            time.sleep(sleep_seconds)
            continue
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as error:
            last_error = error
            time.sleep(sleep_seconds)
            continue
        if isinstance(parsed, dict):
            return parsed
        raise ValueError(f"Expected JSON object in {path}, got {type(parsed).__name__}")

    if allow_empty_fallback:
        return {}

    if last_error is not None:
        raise ValueError(f"Failed to parse JSON from {path} after retries: {last_error}") from last_error
    raise ValueError(f"JSON file remained empty after retries: {path}")


def query_aedebug_debugger(cxstart: Path, output_path: Path, env: dict[str, str] | None = None) -> str | None:
    reg_exe = r"C:\windows\system32\reg.exe"
    key = r"HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug"
    rc = run_capture_timeout(
        [str(cxstart), "--bottle", "Steam", "--no-gui", reg_exe, "query", key, "/v", "Debugger"],
        output_path,
        env=env,
        timeout_seconds=15,
    )
    if rc != 0:
        return None

    text = output_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\bDebugger\s+REG_SZ\s+(.+)$", text, re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def set_aedebug_debugger(
    cxstart: Path,
    debugger_value: str,
    output_path: Path,
    env: dict[str, str] | None = None,
) -> int:
    reg_exe = r"C:\windows\system32\reg.exe"
    key = r"HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug"
    return run_capture_timeout(
        [
            str(cxstart),
            "--bottle",
            "Steam",
            "--no-gui",
            reg_exe,
            "add",
            key,
            "/v",
            "Debugger",
            "/t",
            "REG_SZ",
            "/d",
            debugger_value,
            "/f",
        ],
        output_path,
        env=env,
        timeout_seconds=15,
    )


def set_aedebug_auto(
    cxstart: Path,
    auto_value: str,
    output_path: Path,
    env: dict[str, str] | None = None,
) -> int:
    reg_exe = r"C:\windows\system32\reg.exe"
    key = r"HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug"
    return run_capture_timeout(
        [
            str(cxstart),
            "--bottle",
            "Steam",
            "--no-gui",
            reg_exe,
            "add",
            key,
            "/v",
            "Auto",
            "/t",
            "REG_SZ",
            "/d",
            auto_value,
            "/f",
        ],
        output_path,
        env=env,
        timeout_seconds=15,
    )


def set_winedbg_show_crash_dialog(
    cxstart: Path,
    enabled: bool,
    output_path: Path,
    env: dict[str, str] | None = None,
) -> int:
    reg_exe = r"C:\windows\system32\reg.exe"
    key = r"HKCU\Software\Wine\WineDbg"
    value = "1" if enabled else "0"
    return run_capture_timeout(
        [
            str(cxstart),
            "--bottle",
            "Steam",
            "--no-gui",
            reg_exe,
            "add",
            key,
            "/v",
            "ShowCrashDialog",
            "/t",
            "REG_DWORD",
            "/d",
            value,
            "/f",
        ],
        output_path,
        env=env,
        timeout_seconds=15,
    )


def start_logged_process(
    command: list[str],
    output_path: Path,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.Popen[str]:
    log_file = output_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    return process


def terminate_process(process: subprocess.Popen[str], timeout_seconds: float = 3.0) -> None:
    if process.poll() is not None:
        return

    try:
        process.terminate()
    except ProcessLookupError:
        return

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.1)

    try:
        process.kill()
    except ProcessLookupError:
        return


def start_window_capture_loop(
    output_dir: Path,
    log_path: Path,
    interval_seconds: float,
    duration_seconds: float,
    stop_file: Path,
    title_filters: str = "",
) -> subprocess.Popen[str]:
    loop_script = Path(__file__).parent / "macos_window_capture_loop.swift"
    command = [
        "xcrun",
        "swift",
        str(loop_script),
        "--output-dir",
        str(output_dir),
        "--interval-seconds",
        str(interval_seconds),
        "--duration-seconds",
        str(duration_seconds),
        "--stop-file",
        str(stop_file),
    ]
    if title_filters:
        command.extend(["--title-contains", title_filters])
    return start_logged_process(command, log_path)


def analyze_window_capture_manifest(manifest_path: Path) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "window_capture_frame_count": 0,
        "window_capture_succeeded_count": 0,
        "window_capture_nonflat_count": 0,
        "window_capture_nonflat_ratio": 0.0,
        "window_capture_window_titles": [],
    }
    if not manifest_path.exists():
        return empty

    total = 0
    succeeded = 0
    nonflat = 0
    titles: set[str] = set()

    for raw in manifest_path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        total += 1
        if entry.get("captureSucceeded"):
            succeeded += 1
            if not entry.get("flat", True):
                nonflat += 1
        title = entry.get("title", "")
        if title:
            titles.add(title)

    return {
        "window_capture_frame_count": total,
        "window_capture_succeeded_count": succeeded,
        "window_capture_nonflat_count": nonflat,
        "window_capture_nonflat_ratio": nonflat / succeeded if succeeded > 0 else 0.0,
        "window_capture_window_titles": sorted(titles),
    }


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def steam_paths() -> dict[str, Path]:
    bottle_root = Path.home() / "Library/Application Support/CrossOver/Bottles/Steam"
    drive_c = bottle_root / "drive_c"
    steam_root = drive_c / "Program Files (x86)/Steam"
    windows_temp = drive_c / "windows/temp"
    return {
        "bottle_root": bottle_root,
        "steam_logs": steam_root / "logs",
        "steamvr_settings": steam_root / "config/steamvr.vrsettings",
        "alvr_session": steam_root / "steamapps/common/SteamVR/drivers/alvr_server/session.json",
        "alvr_log": steam_root / "steamapps/common/SteamVR/drivers/alvr_server/session_log.txt",
        "windows_temp": windows_temp,
        "winedbg_log": windows_temp / "winedbg-auto.log",
    }


def patch_steamvr_settings(
    steamvr_settings: Path,
    run_dir: Path,
    *,
    enable_home_app: bool,
    direct_mode: str,
    mirror_view: str,
) -> None:
    before_path = run_dir / "config/steamvr.vrsettings.before.json"
    after_path = run_dir / "config/steamvr.vrsettings.after.json"

    if steamvr_settings.exists():
        before_path.write_text(steamvr_settings.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        settings = read_json_retry(steamvr_settings)
    else:
        settings = {}

    steamvr = settings.setdefault("steamvr", {})
    dashboard = settings.setdefault("dashboard", {})
    driver_alvr = settings.setdefault("driver_alvr_server", {})
    driver_null = settings.setdefault("driver_null", {})
    driver_vrlink = settings.setdefault("driver_vrlink", {})

    steamvr["forcedDriver"] = "alvr_server"
    steamvr["requireHmd"] = True
    steamvr["activateMultipleDrivers"] = False
    steamvr["enableHomeApp"] = enable_home_app
    steamvr["enableSafeMode"] = False
    steamvr["blocked_by_safe_mode"] = False
    steamvr["startMonitorFromAppLaunch"] = False
    steamvr["startDashboardFromAppLaunch"] = False
    steamvr["startOverlayAppsFromDashboard"] = False
    if mirror_view == "legacy":
        steamvr["showMirrorView"] = False
        steamvr["showLegacyMirrorView"] = True
    else:
        mirror_enabled = mirror_view == "on"
        steamvr["showMirrorView"] = mirror_enabled
        steamvr["showLegacyMirrorView"] = mirror_enabled

    # Keep SteamVR UI popups from stealing focus during unattended loops.
    # A very high notice version suppresses changelog re-prompts after updates.
    steamvr["lastVersionNotice"] = "99.99.99"
    steamvr["lastVersionNoticeDate"] = str(int(time.time()))

    if direct_mode == "on":
        steamvr["directMode"] = True
    else:
        steamvr["directMode"] = False
        steamvr["directModeEdidVid"] = 0
        steamvr["directModeEdidPid"] = 0

    # Keep dashboard off by default, but allow Home-enabled runs to present
    # visible scene content instead of an idle black compositor feed.
    dashboard["enableDashboard"] = enable_home_app
    dashboard["webUI"] = False

    driver_null["enable"] = False
    driver_vrlink["enable"] = False
    driver_alvr["enable"] = True
    driver_alvr["blocked_by_safe_mode"] = False

    write_json(steamvr_settings, settings)
    after_path.write_text(steamvr_settings.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")


def patch_session_contract(
    session_path: Path,
    run_dir: Path,
    codec: str,
    stream_protocol: str,
    foveated_encoding: str,
    *,
    manual_client_host: str | None,
    manual_client_ip: str | None,
) -> None:
    before_path = run_dir / "config/session.before.json"
    after_path = run_dir / "config/session.after.json"
    if not session_path.exists():
        return

    before_path.write_text(session_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    session = read_json_retry(session_path, allow_empty_fallback=True)

    # Keep trusted client records by default so reconnect runs can reuse the
    # last-known endpoint. Optionally override with a manual route when mDNS
    # discovery is flaky.
    existing_connections = session.get("client_connections", {})
    trusted_connections: dict[str, Any] = {}
    if isinstance(existing_connections, dict):
        for key, value in existing_connections.items():
            if not isinstance(value, dict):
                continue
            if value.get("trusted") is True:
                trusted_connections[str(key)] = value

    if manual_client_host and manual_client_ip:
        session["client_connections"] = {
            manual_client_host: {
                "display_name": "Apple Vision Pro",
                "current_ip": manual_client_ip,
                "manual_ips": [manual_client_ip],
                "trusted": True,
                "connection_state": "Disconnected",
            }
        }
    else:
        session["client_connections"] = trusted_connections

    session_settings = session.setdefault("session_settings", {})
    connection = session_settings.setdefault("connection", {})
    video = session_settings.setdefault("video", {})
    protocol_variant = "Udp" if stream_protocol == "udp" else "Tcp"
    connection["stream_protocol"] = {"variant": protocol_variant}
    connection["stream_port"] = 9943
    connection["client_discovery"] = {
        "enabled": True,
        "content": {"auto_trust_clients": True},
    }
    # VT bridge emits a static test-pattern IDR payload for decode validation.
    # Keep packet gating permissive so packets are still submitted even if the
    # runtime initially classifies the stream as corrupted.
    connection["avoid_video_glitching"] = False

    extra = session_settings.setdefault("extra", {})
    logging = extra.setdefault("logging", {})
    # Surface client-side probe logs in server events so harnesses can
    # distinguish "streaming but wireframe" from "video actively presented".
    logging["client_log_report_level"] = {
        "enabled": True,
        "content": {"variant": "Info"},
    }
    # Persist host-side probe lines so source-path classification does not rely
    # solely on dashboard event wrappers.
    logging["log_to_disk"] = True

    # Keep the runtime codec aligned with the VT bridge protocol contract.
    if codec == "h264":
        video["preferred_codec"] = {"variant": "H264"}
        openvr_codec = 0
    else:
        video["preferred_codec"] = {"variant": "Hevc"}
        openvr_codec = 1
    video["preferred_fps"] = 90.0
    openvr = session.setdefault("openvr_config", {})
    openvr["codec"] = openvr_codec
    openvr["refresh_rate"] = 90
    openvr["force_sw_encoding"] = True

    if foveated_encoding == "on":
        video.setdefault("foveated_encoding", {})["enabled"] = True
        openvr["enable_foveated_encoding"] = True
    elif foveated_encoding == "off":
        video.setdefault("foveated_encoding", {})["enabled"] = False
        openvr["enable_foveated_encoding"] = False

    write_json(session_path, session)
    after_path.write_text(session_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")


def parse_key_outcome(
    alvr_text: str,
    daemon_log: str,
    dashboard_text: str,
    avp_probe_text: str,
    steam_runtime_text: str,
    debug_frames_dir: Path | None = None,
) -> dict[str, Any]:
    known_synthetic_fresh_crcs = {
        "07aa4390",
        "3d20cd83",
        "3f882ae0",
        "68cf1f8a",
        "9492a69c",
        "dcf41d0c",
        "e187baba",
        "eabc7a17",
    }

    def extract_dashboard_server_log_content(text: str) -> str:
        """Extract server-side ALVR log payloads emitted in dashboard event wrappers."""
        extracted_lines: list[str] = []
        for line in text.splitlines():
            if "Server event:" not in line:
                continue
            payload = line.split("Server event:", 1)[1].strip()
            if not payload:
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            event_type = event.get("event_type")
            if not isinstance(event_type, dict):
                continue
            event_data = event_type.get("data")
            if not isinstance(event_data, dict):
                continue
            content = event_data.get("content")
            if isinstance(content, str) and content:
                extracted_lines.append(content)

        return "\n".join(extracted_lines)

    def parse_probe_timestamps(text: str) -> dict[str, list[float]]:
        events: dict[str, list[float]] = {}
        for match in re.finditer(r"^(\d+(?:\.\d+)?)\s+PROBE\s+([a-zA-Z0-9_]+)", text, re.MULTILINE):
            ts = float(match.group(1))
            event = match.group(2)
            events.setdefault(event, []).append(ts)
        return events

    def detect_interop_signature(
        *,
        alvr_log_text: str,
        steam_runtime_log_text: str,
        extension_missing: bool,
        direct_mode_swap_failed: bool,
        virtual_display_component_seen: bool,
        virtual_display_present_seen: bool,
        direct_mode_disabled: bool,
        source_known_synthetic_pattern: bool,
        source_static_suspected: bool,
        direct_mode_recovery_used: bool,
        swap_texture_set_fallback_loop: bool,
    ) -> tuple[str, dict[str, bool]]:
        combined = f"{alvr_log_text}\n{steam_runtime_log_text}".lower()
        combined_lines = combined.splitlines()

        create_shared_handle_failed_lines = [
            line
            for line in combined_lines
            if "createsharedhandle" in line and "failed" in line
        ]

        get_shared_handle_failed = any(
            "getsharedhandle" in line and "failed" in line
            for line in combined_lines
        )
        open_shared_resource_failed = any(
            "opensharedresource" in line and "failed" in line
            for line in combined_lines
        )

        details = {
            "extension_missing": extension_missing,
            "get_shared_handle_failed": get_shared_handle_failed,
            "direct_mode_local_handle_fallback_get_shared": (
                "direct_mode_local_handle_fallback" in combined
                and "reason=getsharedhandle" in combined
            ),
            "create_shared_handle_failed": len(create_shared_handle_failed_lines) > 0,
            "create_shared_handle_e_invalidarg": any(
                "0x80070057" in line for line in create_shared_handle_failed_lines
            ),
            "create_shared_handle_e_notimpl": any(
                "0x80004001" in line for line in create_shared_handle_failed_lines
            ),
            "open_shared_resource_failed": open_shared_resource_failed,
            "vrcompositor_access_violation": "exception c0000005" in steam_runtime_log_text.lower(),
            "virtual_display_component_seen": virtual_display_component_seen,
            "virtual_display_present_seen": virtual_display_present_seen,
            "direct_mode_swap_failed": direct_mode_swap_failed,
            "source_known_synthetic_pattern": source_known_synthetic_pattern,
            "source_static_suspected": source_static_suspected,
            "direct_mode_recovery_used": direct_mode_recovery_used,
            "swap_texture_set_fallback_loop": swap_texture_set_fallback_loop,
        }

        if details["extension_missing"]:
            return "vk_win32_external_memory_missing", details
        if details["direct_mode_local_handle_fallback_get_shared"]:
            return "direct_mode_local_handle_fallback_get_shared", details
        if details["get_shared_handle_failed"]:
            return "get_shared_handle_failed", details
        if details["create_shared_handle_e_notimpl"]:
            return "create_shared_handle_not_implemented", details
        if details["create_shared_handle_e_invalidarg"]:
            return "create_shared_handle_invalid_argument", details
        if details["create_shared_handle_failed"]:
            return "create_shared_handle_failed", details
        if details["open_shared_resource_failed"]:
            return "open_shared_resource_failed", details
        if details["vrcompositor_access_violation"]:
            return "vrcompositor_access_violation", details
        if details["swap_texture_set_fallback_loop"] and (
            source_static_suspected or direct_mode_recovery_used
        ):
            return "create_swap_texture_set_fallback_loop", details
        if (
            virtual_display_component_seen
            and not virtual_display_present_seen
            and direct_mode_disabled
        ):
            return "virtual_display_callbacks_missing", details
        if direct_mode_recovery_used:
            return "direct_mode_recovery_source_active", details
        if source_known_synthetic_pattern:
            return "non_direct_synthetic_source", details
        if source_static_suspected:
            return "source_static_black", details
        if direct_mode_swap_failed:
            return "direct_mode_swap_failed", details

        return "none", details

    dashboard_server_log_text = extract_dashboard_server_log_content(dashboard_text)
    host_log_text = "\n".join(
        part for part in (alvr_text, dashboard_server_log_text) if part
    )
    telemetry_log_text = "\n".join(
        part for part in (host_log_text, steam_runtime_text) if part
    )

    combined_probe_text = f"{host_log_text}\n{avp_probe_text}"
    probe_timestamps = parse_probe_timestamps(combined_probe_text)
    all_probe_timestamps = [ts for timestamps in probe_timestamps.values() for ts in timestamps]
    app_initialized_seen = "PROBE app_initialized" in combined_probe_text
    streaming_started_seen = "PROBE streaming_started" in combined_probe_text
    decode_success_seen = "PROBE decode_success" in combined_probe_text
    video_presenting_seen = "PROBE video_presenting" in combined_probe_text
    frame_sequences = [int(match.group(1)) for match in re.finditer(r"frame_ready sequence=(\d+)", daemon_log)]
    frame_progress = len(frame_sequences) > 0
    frame_max = max(frame_sequences) if frame_sequences else -1

    fallback_enabled_matches = list(
        re.finditer(r"PROBE synthetic_fallback_enabled=(\d)", combined_probe_text)
    )
    fallback_enabled: bool | None = None
    if fallback_enabled_matches:
        fallback_enabled = fallback_enabled_matches[-1].group(1) == "1"

    host_idle_fallback_enabled_matches = list(
        re.finditer(r"PROBE host_idle_fallback_enabled=(\d)", telemetry_log_text)
    )
    host_idle_fallback_enabled: bool | None = None
    if host_idle_fallback_enabled_matches:
        host_idle_fallback_enabled = host_idle_fallback_enabled_matches[-1].group(1) == "1"

    copy_to_staging_matches = [
        int(match.group(1))
        for match in re.finditer(r"CEncoder: copy_to_staging calls=(\d+)", telemetry_log_text)
    ]
    copy_composed_to_staging_matches = [
        int(match.group(1))
        for match in re.finditer(
            r"CEncoder: copy_composed_to_staging calls=(\d+)", telemetry_log_text
        )
    ]
    new_frame_ready_matches = [
        int(match.group(1))
        for match in re.finditer(r"CEncoder: new_frame_ready calls=(\d+)", telemetry_log_text)
    ]
    non_direct_frame_produced_matches = [
        int(match.group(1))
        for match in re.finditer(r"PROBE host_non_direct_frame_produced count=(\d+)", telemetry_log_text)
    ]
    non_direct_frame_submitted_matches = [
        int(match.group(1))
        for match in re.finditer(r"PROBE host_non_direct_frame_submitted count=(\d+)", telemetry_log_text)
    ]
    direct_mode_recovery_frame_produced_matches = [
        int(match.group(1))
        for match in re.finditer(
            r"PROBE host_direct_mode_recovery_frame_produced count=(\d+)", telemetry_log_text
        )
    ]

    streaming_state_seen = (
        '"connection_state":"Streaming"' in dashboard_text
        or "handshake finished; unlocking streams" in telemetry_log_text
    )
    timeout_patterns = (
        r"Could not initiate connection",
        r"OS Error 10065",
        r"connection[^\n]*timed out",
        r"timed out[^\n]*connection",
    )
    # Limit timeout detection to bridge/session logs. Steam runtime text can
    # contain unrelated timeout strings that would otherwise cause false
    # connection-timeout classifications.
    timeout_observed = any(
        re.search(pattern, host_log_text, re.IGNORECASE) is not None
        for pattern in timeout_patterns
    )

    fresh_encode_matches = list(
        re.finditer(
            r"fresh_encode sequence=(\d+) encoded_bytes=(\d+) sample_crc=0x([0-9a-fA-F]+)"
            r"(?: spread_crc=0x([0-9a-fA-F]+))?"
            r"(?: sample_nonzero=(\d+))?"
            r"(?: sample_len=\d+)?"
            r"(?: sample_min=(\d+) sample_max=(\d+))?",
            daemon_log,
        )
    )
    fresh_encode_count = len(fresh_encode_matches)
    unique_fresh_encoded_sizes = {
        int(match.group(2)) for match in fresh_encode_matches
    }
    unique_fresh_sample_crcs = {
        match.group(3).lower() for match in fresh_encode_matches
    }
    unique_fresh_spread_crcs = {
        match.group(4).lower() for match in fresh_encode_matches if match.group(4)
    }
    fresh_sample_nonzero_values = [
        int(match.group(5)) for match in fresh_encode_matches if match.group(5)
    ]
    fresh_sample_nonzero_max = max(fresh_sample_nonzero_values) if fresh_sample_nonzero_values else 0
    fresh_sample_min_values = [
        int(match.group(6)) for match in fresh_encode_matches if match.group(6)
    ]
    fresh_sample_max_values = [
        int(match.group(7)) for match in fresh_encode_matches if match.group(7)
    ]
    fresh_crc_counts = Counter(match.group(3).lower() for match in fresh_encode_matches)
    fresh_crc_top_counts = fresh_crc_counts.most_common(3)
    fresh_crc_total = sum(fresh_crc_counts.values())
    dominant_crc_share = (
        fresh_crc_top_counts[0][1] / fresh_crc_total if fresh_crc_total > 0 else 0.0
    )
    dominant_two_crc_share = (
        sum(count for _, count in fresh_crc_top_counts[:2]) / fresh_crc_total
        if fresh_crc_total > 0
        else 0.0
    )
    dominant_crc_pair = {crc for crc, _ in fresh_crc_top_counts[:2]}
    # Empirically, AVP black/red flashing runs are dominated by these two
    # sample CRCs with tiny encoded payloads and near-constant sampled bytes.
    source_black_red_oscillation_suspected = (
        fresh_encode_count >= 120
        and dominant_crc_pair == {"c71c0011", "5c2f9ff2"}
        and dominant_two_crc_share >= 0.95
        and max(unique_fresh_encoded_sizes, default=0) <= 4000
        and fresh_sample_nonzero_max >= 4096
    )
    source_low_entropy_suspected = (
        fresh_encode_count >= 120
        and len(unique_fresh_sample_crcs) <= 8
        and dominant_two_crc_share >= 0.95
        and max(unique_fresh_encoded_sizes, default=0) <= 4000
    )

    source_sample_matches = list(
        re.finditer(
            r"VideoEncoderVtBridge: source_sample calls=(\d+) row_pitch=\d+ payload=\d+ "
            r"first_bgra=(\d+),(\d+),(\d+),(\d+) sample_hash=0x([0-9a-fA-F]+)",
            telemetry_log_text,
        )
    )
    source_sample_count = len(source_sample_matches)
    source_sample_hashes = [match.group(6).lower() for match in source_sample_matches]
    unique_source_sample_hashes = set(source_sample_hashes)
    source_sample_nonblack_count = sum(
        1
        for match in source_sample_matches
        if int(match.group(2)) != 0 or int(match.group(3)) != 0 or int(match.group(4)) != 0
    )
    stable_source_sample_matches = source_sample_matches[3:] if source_sample_count > 3 else source_sample_matches
    stable_source_hashes = [match.group(6).lower() for match in stable_source_sample_matches]
    unique_stable_source_hashes = set(stable_source_hashes)
    stable_source_nonblack_count = sum(
        1
        for match in stable_source_sample_matches
        if int(match.group(2)) != 0 or int(match.group(3)) != 0 or int(match.group(4)) != 0
    )
    source_known_synthetic_pattern = (
        fresh_encode_count >= 8
        and len(unique_fresh_sample_crcs) >= 4
        and unique_fresh_sample_crcs.issubset(known_synthetic_fresh_crcs)
    )
    source_static_from_fresh_crc = (
        fresh_encode_count >= 20
        and len(unique_fresh_sample_crcs) == 1
        and len(unique_fresh_encoded_sizes) == 1
        and next(iter(unique_fresh_encoded_sizes), 0) <= 3000
    )
    # Keep this conservative: a single stable source_sample hash can miss real
    # scene motion elsewhere in the frame. Only treat it as static evidence when
    # we have a longer observation window and the sampled pixels settle to black
    # after the initial startup churn.
    source_static_from_source_sample = (
        source_sample_count >= 8
        and len(stable_source_sample_matches) >= 5
        and len(unique_stable_source_hashes) == 1
        and stable_source_nonblack_count == 0
    )
    source_static_suspected = source_static_from_fresh_crc or source_static_from_source_sample

    source_motion_seen = (
        (fresh_encode_count >= 2 and len(unique_fresh_sample_crcs) >= 2)
        or (len(unique_fresh_spread_crcs) >= 2)
        or (
            len(stable_source_sample_matches) >= 5
            and len(unique_stable_source_hashes) >= 2
            and stable_source_nonblack_count >= 2
        )
    )

    non_direct_source_matches = [
        match.group(1)
        for match in re.finditer(
            r"PROBE host_non_direct_frame_rendered tick=\d+ source=([a-z_]+)",
            telemetry_log_text,
        )
    ]
    non_direct_window_titles = [
        match.group(1).strip()
        for match in re.finditer(
            r"PROBE host_non_direct_frame_rendered tick=\d+ source=window_capture hwnd=[^\s]+ title=(.*)",
            telemetry_log_text,
        )
    ]
    unique_non_direct_window_titles = sorted(
        {title for title in non_direct_window_titles if title}
    )
    lowered_window_titles = [title.lower() for title in unique_non_direct_window_titles]
    steam_login_ui_detected = any("sign in to steam" in title for title in lowered_window_titles)
    steam_client_ui_detected = any(
        title in {"steam", "steam login", "sign in to steam"}
        or "sign in to steam" in title
        for title in lowered_window_titles
    )
    synthetic_phase_match_count = len(
        re.findall(r"PROBE host_non_direct_frame_rendered tick=\d+ phase=", telemetry_log_text)
    )
    source_path_counts: dict[str, int] = {}
    for source in non_direct_source_matches:
        source_path_counts[source] = source_path_counts.get(source, 0) + 1

    # Non-direct runs can flow through virtual-display callbacks without
    # host_non_direct_frame_rendered markers. Count those callbacks as a
    # real-source candidate path for source quality classification.
    virtual_display_present_count = len(
        re.findall(r"PROBE virtual_display_present calls=\d+", telemetry_log_text)
    )
    display_redirect_present_count = len(
        re.findall(r"PROBE display_redirect_present calls=\d+", telemetry_log_text)
    )
    if virtual_display_present_count > 0:
        source_path_counts["virtual_display"] = source_path_counts.get(
            "virtual_display", 0
        ) + virtual_display_present_count
    elif display_redirect_present_count > 0:
        source_path_counts["virtual_display"] = source_path_counts.get(
            "virtual_display", 0
        ) + display_redirect_present_count

    if synthetic_phase_match_count > 0:
        source_path_counts["synthetic_pattern"] = (
            source_path_counts.get("synthetic_pattern", 0) + synthetic_phase_match_count
        )

    source_path_selected = "unknown"
    if source_path_counts:
        # Prefer higher counts, but in tie cases prefer real capture paths over
        # synthetic fallbacks so sparse sampling does not misclassify runs.
        source_path_priority = {
            "window_capture": 4,
            "desktop_capture": 3,
            "desktop_duplication": 3,
            "virtual_display": 2,
            "direct_mode": 2,
            "direct_mode_recovery": 1,
            "synthetic_pattern": 0,
        }
        source_path_selected = max(
            sorted(source_path_counts.keys()),
            key=lambda path_name: (
                source_path_counts[path_name],
                source_path_priority.get(path_name, 0),
            ),
        )

    source_quality_grade = "unknown"
    if source_black_red_oscillation_suspected:
        source_quality_grade = "synthetic_like"
    elif source_low_entropy_suspected:
        source_quality_grade = "synthetic_like"
    elif source_static_suspected:
        source_quality_grade = "static"
    elif source_known_synthetic_pattern or source_path_selected == "synthetic_pattern":
        source_quality_grade = "synthetic_like"
    elif source_path_selected in {
        "window_capture",
        "desktop_capture",
        "desktop_duplication",
        "virtual_display",
        "direct_mode",
        "direct_mode_recovery",
    }:
        source_quality_grade = "real_candidate"
    elif (
        fresh_encode_count >= 20
        and len(unique_fresh_sample_crcs) >= 10
        and fresh_sample_nonzero_max > 0
    ):
        # Fallback classification for runs where upstream source-path markers are
        # absent but payload telemetry shows sustained non-static frame diversity.
        source_quality_grade = "real_candidate"

    debug_frame_analysis = analyze_vtbridge_debug_frames(debug_frames_dir)
    source_debug_all_flat = bool(debug_frame_analysis["source_debug_all_flat"])
    source_debug_flat_ratio = float(debug_frame_analysis["source_debug_flat_ratio"])
    source_debug_flat_colors = set(debug_frame_analysis["source_debug_flat_colors"])

    source_virtual_display_placeholder_suspected = (
        source_path_selected == "virtual_display"
        and source_sample_count >= 20
        and len(unique_stable_source_hashes) == 1
        and max(unique_fresh_encoded_sizes, default=0) <= 5000
        and len(unique_fresh_sample_crcs) <= 4
        and debug_frame_analysis["source_debug_frame_count"] >= 8
        and source_debug_flat_ratio >= 0.30
        and source_debug_flat_colors.issubset({"0,0,0,0"})
    )

    if source_virtual_display_placeholder_suspected:
        source_quality_grade = "synthetic_like"

    if source_debug_all_flat:
        # If every dumped VT bridge source frame is a single flat color,
        # treat this as synthetic-like output even if other heuristics look live.
        source_quality_grade = "synthetic_like"

    stretch_error_values = sorted(
        {
            int(match.group(1))
            for match in re.finditer(
                r"PROBE host_non_direct_desktop_capture_failed.*stretch_error=(\d+)",
                telemetry_log_text,
            )
        }
    )

    bridge_connected_explicit = "VideoEncoderVtBridge: connected" in telemetry_log_text
    bridge_connected_inferred = (not bridge_connected_explicit) and fresh_encode_count > 0

    bootstrap_refresh_matches = list(
        re.finditer(
            r"bootstrap_test_pattern_refresh sequence=(\d+) color=([a-zA-Z0-9_]+) bytes=(\d+)",
            daemon_log,
        )
    )
    bootstrap_refresh_colors = {
        match.group(2).lower() for match in bootstrap_refresh_matches
    }

    app_init_ts = probe_timestamps.get("app_initialized", [None])[0]
    streaming_started_ts = probe_timestamps.get("streaming_started", [None])[0]
    decode_success_ts = probe_timestamps.get("decode_success", [None])[0]
    video_presenting_ts = probe_timestamps.get("video_presenting", [None])[0]
    ready_ts_candidates = [ts for ts in [decode_success_ts, video_presenting_ts] if ts is not None]
    ready_ts = min(ready_ts_candidates) if ready_ts_candidates else None
    probe_last_ts = max(all_probe_timestamps) if all_probe_timestamps else None

    streaming_start_delay_seconds: float | None = None
    if app_init_ts is not None and streaming_started_ts is not None:
        streaming_start_delay_seconds = max(0.0, streaming_started_ts - app_init_ts)

    ready_delay_seconds: float | None = None
    if app_init_ts is not None and ready_ts is not None:
        ready_delay_seconds = max(0.0, ready_ts - app_init_ts)

    ui_block_elapsed_seconds: float | None = None
    if app_init_ts is not None and streaming_started_ts is None and probe_last_ts is not None:
        ui_block_elapsed_seconds = max(0.0, probe_last_ts - app_init_ts)

    streaming_start_delayed = (
        streaming_start_delay_seconds is not None and streaming_start_delay_seconds >= 10.0
    )

    client_ui_block_summary: str | None = None
    if app_initialized_seen and not streaming_started_seen:
        if ui_block_elapsed_seconds is not None:
            client_ui_block_summary = (
                "app_initialized seen but streaming_started missing for "
                f"{ui_block_elapsed_seconds:.1f}s; probable AVP UI/frontmost blocker"
            )
        else:
            client_ui_block_summary = (
                "app_initialized seen but streaming_started missing; probable AVP UI/frontmost blocker"
            )
    elif streaming_start_delayed:
        client_ui_block_summary = (
            "streaming_started delayed "
            f"{streaming_start_delay_seconds:.1f}s after app_initialized; check AVP popup/frontmost state"
        )

    explicit_non_direct_source_enabled = "PROBE host_non_direct_source_enabled=1" in telemetry_log_text
    inferred_non_direct_source_enabled = (
        len(non_direct_frame_produced_matches) > 0
        or len(non_direct_frame_submitted_matches) > 0
        or "source=non_direct" in telemetry_log_text
    )
    host_non_direct_source_enabled = (
        explicit_non_direct_source_enabled or inferred_non_direct_source_enabled
    )

    explicit_direct_mode_disabled = (
        "ALVR MGP direct-mode guard" in telemetry_log_text
        and "disabled=1" in telemetry_log_text
    )
    inferred_direct_mode_disabled = (
        not explicit_direct_mode_disabled and host_non_direct_source_enabled
    )

    outcome: dict[str, Any] = {
        "bridge_connected": bridge_connected_explicit or bridge_connected_inferred,
        "bridge_connected_inferred": bridge_connected_inferred,
        "decoder_fatal": "Fatal decoder error" in alvr_text or "Gimme frames" in alvr_text,
        "connection_timeout": timeout_observed and not streaming_state_seen,
        "client_reset_10054": "os error 10054" in alvr_text.lower(),
        "frame_ready_seen": frame_progress,
        "frame_ready_max_sequence": frame_max,
        "streaming_state_seen": streaming_state_seen,
        "client_app_initialized": False,
        "client_streaming_started": False,
        "client_streaming_start_delay_seconds": streaming_start_delay_seconds,
        "client_streaming_start_delayed": streaming_start_delayed,
        "client_ready": False,
        "client_ready_delay_seconds": ready_delay_seconds,
        "client_ui_block_suspected": False,
        "client_ui_block_elapsed_seconds": ui_block_elapsed_seconds,
        "client_ui_block_summary": client_ui_block_summary,
        "client_video_presenting": False,
        "client_decoder_config_seen": False,
        "client_decode_success": False,
        "client_decode_nil_seen": False,
        "client_synthetic_fallback_enabled": fallback_enabled,
        "client_synthetic_fallback_used": False,
        "host_idle_fallback_enabled": host_idle_fallback_enabled,
        "host_idle_fallback_enabled_inferred": False,
        "host_idle_fallback_used": False,
        "host_copy_to_staging_seen": len(copy_to_staging_matches) > 0,
        "host_copy_to_staging_max_calls": max(copy_to_staging_matches) if copy_to_staging_matches else -1,
        "host_copy_composed_to_staging_seen": len(copy_composed_to_staging_matches) > 0,
        "host_copy_composed_to_staging_max_calls": max(copy_composed_to_staging_matches)
        if copy_composed_to_staging_matches
        else -1,
        "host_new_frame_ready_seen": len(new_frame_ready_matches) > 0,
        "host_new_frame_ready_max_calls": max(new_frame_ready_matches) if new_frame_ready_matches else -1,
        "host_non_direct_source_enabled": host_non_direct_source_enabled,
        "host_non_direct_source_enabled_inferred": inferred_non_direct_source_enabled,
        "host_direct_mode_recovery_source_enabled": "PROBE host_direct_mode_recovery_source_enabled=1"
        in telemetry_log_text,
        "host_direct_mode_disabled": explicit_direct_mode_disabled or inferred_direct_mode_disabled,
        "host_direct_mode_disabled_inferred": inferred_direct_mode_disabled,
        "host_non_direct_frame_produced_seen": len(non_direct_frame_produced_matches) > 0,
        "host_non_direct_frame_produced_max_count": max(non_direct_frame_produced_matches)
        if non_direct_frame_produced_matches
        else -1,
        "host_non_direct_frame_submitted_seen": len(non_direct_frame_submitted_matches) > 0,
        "host_non_direct_frame_submitted_max_count": max(non_direct_frame_submitted_matches)
        if non_direct_frame_submitted_matches
        else -1,
        "host_direct_mode_recovery_used": len(direct_mode_recovery_frame_produced_matches) > 0
        or "source=direct_mode_recovery" in telemetry_log_text,
        "host_direct_mode_recovery_max_count": max(direct_mode_recovery_frame_produced_matches)
        if direct_mode_recovery_frame_produced_matches
        else -1,
        "source_fresh_encode_count": fresh_encode_count,
        "source_unique_fresh_encoded_sizes": sorted(unique_fresh_encoded_sizes),
        "source_unique_fresh_sample_crcs": sorted(unique_fresh_sample_crcs),
        "source_unique_fresh_spread_crcs": sorted(unique_fresh_spread_crcs),
        "source_fresh_sample_nonzero_max": fresh_sample_nonzero_max,
        "source_fresh_sample_min_values": sorted(set(fresh_sample_min_values)),
        "source_fresh_sample_max_values": sorted(set(fresh_sample_max_values)),
        "source_crc_top_counts": [
            {"crc": crc, "count": count}
            for crc, count in fresh_crc_top_counts
        ],
        "source_dominant_crc_share": dominant_crc_share,
        "source_dominant_two_crc_share": dominant_two_crc_share,
        "source_black_red_oscillation_suspected": source_black_red_oscillation_suspected,
        "source_low_entropy_suspected": source_low_entropy_suspected,
        "source_sample_observation_count": source_sample_count,
        "source_unique_source_sample_hashes": sorted(unique_source_sample_hashes),
        "source_sample_nonblack_count": source_sample_nonblack_count,
        "source_stable_source_sample_count": len(stable_source_sample_matches),
        "source_unique_stable_source_sample_hashes": sorted(unique_stable_source_hashes),
        "source_stable_source_sample_nonblack_count": stable_source_nonblack_count,
        "source_motion_seen": source_motion_seen,
        "source_known_synthetic_pattern": source_known_synthetic_pattern,
        "source_path_selected": source_path_selected,
        "source_path_counts": source_path_counts,
        "source_window_capture_titles": unique_non_direct_window_titles,
        "source_steam_login_ui_detected": steam_login_ui_detected,
        "source_steam_client_ui_detected": steam_client_ui_detected,
        "source_quality_grade": source_quality_grade,
        "source_debug_frame_count": debug_frame_analysis["source_debug_frame_count"],
        "source_debug_flat_frame_count": debug_frame_analysis["source_debug_flat_frame_count"],
        "source_debug_nonflat_frame_count": debug_frame_analysis[
            "source_debug_nonflat_frame_count"
        ],
        "source_debug_unknown_frame_count": debug_frame_analysis[
            "source_debug_unknown_frame_count"
        ],
        "source_debug_flat_ratio": source_debug_flat_ratio,
        "source_debug_all_flat": source_debug_all_flat,
        "source_debug_flat_colors": debug_frame_analysis["source_debug_flat_colors"],
        "source_bootstrap_refresh_count": len(bootstrap_refresh_matches),
        "source_bootstrap_colors": sorted(bootstrap_refresh_colors),
        "source_static_from_fresh_crc": source_static_from_fresh_crc,
        "source_static_from_source_sample": source_static_from_source_sample,
        "source_static_suspected": source_static_suspected,
        "source_virtual_display_placeholder_suspected": source_virtual_display_placeholder_suspected,
        "host_non_direct_capture_stretch_errors": stretch_error_values,
        "host_create_swap_texture_set_fallback_count": 0,
        "host_create_swap_texture_set_fallback_loop": False,
        "host_direct_mode_swap_failed": False,
        "steamvr_external_memory_extensions_missing": False,
        "interop_signature": "none",
        "interop_signature_details": {},
        "gate_failures": [],
    }
    outcome["client_app_initialized"] = (
        app_initialized_seen
    )
    outcome["client_streaming_started"] = (
        streaming_started_seen
    )
    if outcome["client_streaming_started"]:
        # AVP probe confirms stream start even when dashboard/alvr text markers
        # are delayed or missing from captured deltas.
        outcome["streaming_state_seen"] = True
    outcome["client_video_presenting"] = (
        video_presenting_seen
    )
    outcome["client_decoder_config_seen"] = (
        "PROBE decoder_config" in combined_probe_text
    )
    outcome["client_decode_success"] = (
        decode_success_seen
    )
    outcome["client_decode_nil_seen"] = (
        "PROBE decode_nil" in combined_probe_text
    )
    outcome["client_synthetic_fallback_used"] = (
        "PROBE synthetic_fallback_injected" in combined_probe_text
    )
    outcome["host_idle_fallback_used"] = (
        "PROBE host_idle_fallback_injected" in telemetry_log_text
    )
    outcome["client_ready"] = (
        outcome["client_app_initialized"]
        and outcome["client_streaming_started"]
        and (outcome["client_decode_success"] or outcome["client_video_presenting"])
    )
    outcome["client_ui_block_suspected"] = (
        outcome["client_app_initialized"] and not outcome["client_streaming_started"]
    )
    outcome["host_direct_mode_swap_failed"] = (
        "CreateSwapTextureSet failed" in telemetry_log_text
    )
    create_swap_texture_set_fallback_count = telemetry_log_text.count(
        "CreateSwapTextureSet: trying format fallback"
    )
    outcome["host_create_swap_texture_set_fallback_count"] = create_swap_texture_set_fallback_count
    # A small fallback burst (typically 2-4 attempts) can happen during
    # startup format negotiation and does not necessarily indicate a looping
    # interop failure. Treat only sustained fallback churn as a loop.
    outcome["host_create_swap_texture_set_fallback_loop"] = (
        create_swap_texture_set_fallback_count >= 8
    )

    extension_missing_keywords = (
        "missing",
        "unsupported",
        "not supported",
        "not available",
        "not found",
        "unavailable",
    )
    extension_missing_matches = any(
        any(
            ext in line
            for ext in ("vk_khr_external_memory_win32", "vk_khr_win32_keyed_mutex")
        )
        and any(keyword in line for keyword in extension_missing_keywords)
        for line in steam_runtime_text.lower().splitlines()
    )
    outcome["steamvr_external_memory_extensions_missing"] = extension_missing_matches

    virtual_display_component_seen = (
        "PROBE display_redirect_component_virtual_display" in telemetry_log_text
        or "VDR GetComponent request=IVRVirtualDisplay_002" in telemetry_log_text
    )
    virtual_display_present_seen = (
        "PROBE display_redirect_present" in telemetry_log_text
        or "PROBE virtual_display_present_api" in telemetry_log_text
        or "PROBE display_redirect_wait_for_present" in telemetry_log_text
        or "PROBE display_redirect_get_time_since_last_vsync" in telemetry_log_text
    )
    direct_mode_disabled_log_seen = (
        "Hmd::GetComponent virtual_display requested direct_mode_disabled=1"
        in telemetry_log_text
        or "ALVR MGP direct-mode guard" in telemetry_log_text
        and "disabled=1" in telemetry_log_text
    )

    outcome["host_virtual_display_component_seen"] = virtual_display_component_seen
    outcome["host_virtual_display_present_seen"] = virtual_display_present_seen
    if direct_mode_disabled_log_seen:
        outcome["host_direct_mode_disabled"] = True

    # Some non-direct runs exercise the virtual-display callback path without
    # host_non_direct_frame_rendered markers. Promote these runs to a
    # real-candidate source path when payload motion is present and synthetic
    # signatures are absent.
    if (
        outcome["source_path_selected"] == "unknown"
        and outcome["host_virtual_display_present_seen"]
        and outcome["source_motion_seen"]
        and not outcome["source_known_synthetic_pattern"]
    ):
        source_path_counts = dict(outcome["source_path_counts"])
        source_path_counts["virtual_display"] = max(
            1, int(source_path_counts.get("virtual_display", 0))
        )
        outcome["source_path_counts"] = source_path_counts
        outcome["source_path_selected"] = "virtual_display"
        if not outcome["source_static_suspected"]:
            outcome["source_quality_grade"] = "real_candidate"

    interop_signature, interop_signature_details = detect_interop_signature(
        alvr_log_text=host_log_text,
        steam_runtime_log_text=steam_runtime_text,
        extension_missing=outcome["steamvr_external_memory_extensions_missing"],
        direct_mode_swap_failed=outcome["host_direct_mode_swap_failed"],
        virtual_display_component_seen=outcome["host_virtual_display_component_seen"],
        virtual_display_present_seen=outcome["host_virtual_display_present_seen"],
        direct_mode_disabled=outcome["host_direct_mode_disabled"],
        source_known_synthetic_pattern=outcome["source_known_synthetic_pattern"],
        source_static_suspected=outcome["source_static_suspected"],
        direct_mode_recovery_used=outcome["host_direct_mode_recovery_used"],
        swap_texture_set_fallback_loop=outcome["host_create_swap_texture_set_fallback_loop"],
    )
    outcome["interop_signature"] = interop_signature
    outcome["interop_signature_details"] = interop_signature_details

    outcome["pass"] = (
        outcome["bridge_connected"]
        and outcome["frame_ready_seen"]
        and outcome["streaming_state_seen"]
        and not outcome["decoder_fatal"]
        and not outcome["connection_timeout"]
    )
    return outcome


def build_steam_runtime_text(logs_dir: Path) -> str:
    def read_with_delta_preference(log_name: str) -> str:
        base_path = logs_dir / log_name
        delta_path = base_path.with_suffix(".delta.txt")

        if delta_path.exists():
            return delta_path.read_text(encoding="utf-8", errors="replace")
        if base_path.exists():
            return base_path.read_text(encoding="utf-8", errors="replace")
        return ""

    runtime_log_names = [
        "vrserver.txt",
        "vrcompositor.txt",
        "vrmonitor.txt",
    ]

    runtime_text_parts: list[str] = []
    for runtime_log_name in runtime_log_names:
        runtime_text = read_with_delta_preference(runtime_log_name)
        if runtime_text:
            runtime_text_parts.append(runtime_text)

    for vrclient_log in sorted(logs_dir.glob("vrclient_*.txt")):
        if ".previous." in vrclient_log.name:
            continue

        runtime_text = read_with_delta_preference(vrclient_log.name)
        if runtime_text:
            runtime_text_parts.append(runtime_text)

    return "\n".join(runtime_text_parts)


def read_alvr_session_text(logs_dir: Path) -> tuple[str, bool]:
    delta_path = logs_dir / "session_log.delta.txt"
    full_path = logs_dir / "session_log.txt"

    delta_text = ""
    full_text = ""
    delta_exists = delta_path.exists()
    if delta_path.exists():
        delta_text = delta_path.read_text(encoding="utf-8", errors="replace")
    if full_path.exists():
        full_text = full_path.read_text(encoding="utf-8", errors="replace")

    if delta_text:
        anchors = (
            "ALVR MGP direct-mode guard",
            "PROBE host_idle_fallback_enabled=",
            "PROBE host_non_direct_source_enabled=",
            "CEncoder: new_frame_ready",
            "CEncoder: copy_to_staging",
            "CEncoder: copy_composed_to_staging",
            "CreateSwapTextureSet",
            "PROBE direct_mode_",
        )
        if any(anchor in delta_text for anchor in anchors):
            return delta_text, False
        if full_text:
            # Some runs capture a malformed/truncated session delta that misses
            # early run probes. Fall back to a bounded tail of the full log.
            full_tail = "\n".join(full_text.splitlines()[-8000:])
            return full_tail, True
        return delta_text, False

    if delta_exists:
        # A present-but-empty delta means no new ALVR data was emitted for this
        # capture window. Do not fall back to the full log, which can contain
        # stale probe lines from previous runs and poison outcome inference.
        return "", False

    if full_text:
        return full_text, False

    return "", False


def copy_avp_probe_log(run_dir: Path, device: str, bundle_id: str) -> tuple[int, str]:
    probe_copy_log = run_dir / "logs/avp-probe-copy.log"
    probe_output_path = run_dir / "logs/avp-probe.log"

    command = [
        "xcrun",
        "devicectl",
        "device",
        "copy",
        "from",
        "--device",
        device,
        "--source",
        "Documents/alvr_probe.log",
        "--destination",
        str(probe_output_path),
        "--domain-type",
        "appDataContainer",
        "--domain-identifier",
        bundle_id,
    ]
    copy_rc = run_capture_timeout(command, probe_copy_log, timeout_seconds=20)

    probe_text = ""
    if probe_output_path.exists() and probe_output_path.is_file():
        probe_text = probe_output_path.read_text(encoding="utf-8", errors="replace")

    return copy_rc, probe_text


def clear_avp_probe_log(run_dir: Path, device: str, bundle_id: str) -> int:
    empty_probe_path = run_dir / "config/avp-probe-empty.log"
    empty_probe_path.write_text("", encoding="utf-8")

    command = [
        "xcrun",
        "devicectl",
        "device",
        "copy",
        "to",
        "--device",
        device,
        "--source",
        str(empty_probe_path),
        "--destination",
        "Documents/alvr_probe.log",
        "--domain-type",
        "appDataContainer",
        "--domain-identifier",
        bundle_id,
    ]
    return run_capture_timeout(command, run_dir / "logs/avp-probe-clear.log", timeout_seconds=25)


def write_avp_probe_config(
    run_dir: Path,
    device: str,
    bundle_id: str,
    synthetic_fallback_enabled: bool,
) -> int:
    config_path = run_dir / "config/avp-probe-config.json"
    write_json(config_path, {"syntheticFallbackEnabled": synthetic_fallback_enabled})

    command = [
        "xcrun",
        "devicectl",
        "device",
        "copy",
        "to",
        "--device",
        device,
        "--source",
        str(config_path),
        "--destination",
        "Documents/alvr_probe_config.json",
        "--domain-type",
        "appDataContainer",
        "--domain-identifier",
        bundle_id,
    ]
    return run_capture_timeout(command, run_dir / "logs/avp-probe-config-copy.log", timeout_seconds=25)


def write_avp_global_settings(run_dir: Path, device: str, bundle_id: str) -> int:
    # Force unattended-friendly defaults on every run so app behavior does not
    # depend on stale client-side toggles from manual testing.
    settings_path = run_dir / "config/avp-globalsettings.data"
    write_json(
        settings_path,
        {
            "autoEnterOnConnect": True,
            "keepAwakeWhileActive": True,
            "dismissWindowOnEnter": True,
            # Suppress repeat networking prompts during unattended loops.
            "dontShowAWDLAlertAgain": True,
            # Keep system overlays from stealing focus while entering streams.
            "disablePersistentSystemOverlays": True,
        },
    )

    command = [
        "xcrun",
        "devicectl",
        "device",
        "copy",
        "to",
        "--device",
        device,
        "--source",
        str(settings_path),
        "--destination",
        "Documents/globalsettings.data",
        "--domain-type",
        "appDataContainer",
        "--domain-identifier",
        bundle_id,
    ]
    return run_capture_timeout(command, run_dir / "logs/avp-globalsettings-copy.log", timeout_seconds=25)


def run_cleanup_script(python_exe: str, cleanup_script: Path, log_path: Path, sterile_native_steam: bool) -> int:
    command = [python_exe, str(cleanup_script)]
    if sterile_native_steam:
        command.append("--sterile-native-steam")
    return run_capture(command, log_path)


def run_safe_mode_recovery_smoke(
    python_exe: str,
    smoke_script: Path,
    log_path: Path,
    graphics_backend: str,
    wait_seconds: int,
) -> int:
    command = [
        python_exe,
        str(smoke_script),
        "--mode",
        "null",
        "--graphics-backend",
        graphics_backend,
        "--wait",
        str(wait_seconds),
    ]
    return run_capture(command, log_path)


def run_once(args: argparse.Namespace) -> int:
    if args.codec != "hevc":
        print("codec mismatch: vtbridge path is currently HEVC-only; rerun with --codec hevc")
        return 1

    if args.direct_mode == "on" and args.graphics_backend == "dxvk":
        print(
            "unsupported configuration: direct-mode on + dxvk is blocked on this stack "
            "(missing VK_KHR_external_memory_win32 / VK_KHR_win32_keyed_mutex). "
            "Use --graphics-backend d3dmetal for direct-mode runs."
        )
        return 2

    root = repo_root()
    paths = steam_paths()
    run_root = Path(args.run_root)
    if args.prune_old_runs:
        pruned_runs = prune_old_run_bundles(
            run_root,
            keep_last=args.prune_keep_last,
            older_than_days=args.prune_older_than_days,
        )
        if pruned_runs:
            print(
                "PRUNED_RUNS="
                + json.dumps([str(path) for path in pruned_runs], ensure_ascii=True)
            )
    run_dir = run_bundle_dir(run_root)
    print(f"RUN={run_dir}")
    Path("/tmp/current_live_run.txt").write_text(str(run_dir), encoding="utf-8")

    meta: dict[str, Any] = {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "capture_seconds": args.capture_seconds,
        "prompt_at_seconds": args.prompt_at_seconds,
        "host_only": args.host_only,
        "sterile_native_steam": args.sterile_native_steam,
        "graphics_backend": args.graphics_backend,
        "restart_avp_app": not args.no_restart_avp_app,
        "force_test_pattern_hevc": args.force_test_pattern_hevc,
        "codec": args.codec,
        "stream_protocol": args.stream_protocol,
        "direct_mode": args.direct_mode,
        "display_redirect": args.display_redirect,
        "steamvr_home": args.steamvr_home,
        "steamvr_tool": args.steamvr_tool,
        "steam_app_id": args.steam_app_id,
        "steam_app_args": args.steam_app_args,
        "steam_app_force_vr": args.steam_app_force_vr,
        "steam_app_delay_seconds": args.steam_app_delay_seconds,
        "avp_device": args.avp_device,
        "avp_bundle_id": args.avp_bundle_id,
        "synthetic_fallback": args.synthetic_fallback,
        "host_idle_fallback": args.host_idle_fallback,
        "native_window_capture_title_contains": args.native_window_capture_title_contains,
        "native_window_capture_owner_contains": args.native_window_capture_owner_contains,
        "native_window_capture_fps": args.native_window_capture_fps,
        "require_client_ready": args.require_client_ready,
        "wine_debug_channels": args.wine_debug_channels,
        "vtbridge_debug_dump_limit": args.vtbridge_debug_dump_limit,
    }
    write_json(run_dir / "config/meta.json", meta)

    if args.host_only:
        meta["avp_probe_config_skipped_host_only"] = True
        meta["avp_globalsettings_skipped_host_only"] = True
        meta["avp_probe_clear_skipped_host_only"] = True
        write_json(run_dir / "config/meta.json", meta)
    else:
        if args.synthetic_fallback != "unchanged":
            probe_config_rc = write_avp_probe_config(
                run_dir=run_dir,
                device=args.avp_device,
                bundle_id=args.avp_bundle_id,
                synthetic_fallback_enabled=(args.synthetic_fallback == "enable"),
            )
            meta["avp_probe_config_copy_exit_code"] = probe_config_rc
            write_json(run_dir / "config/meta.json", meta)
            if probe_config_rc != 0:
                meta["avp_probe_config_copy_failed"] = True
                write_json(run_dir / "config/meta.json", meta)
                print(
                    "warning: failed to push AVP probe config; device may be locked/untrusted; continuing"
                )

        avp_settings_rc = write_avp_global_settings(run_dir, args.avp_device, args.avp_bundle_id)
        meta["avp_globalsettings_copy_exit_code"] = avp_settings_rc
        write_json(run_dir / "config/meta.json", meta)
        if avp_settings_rc != 0:
            meta["avp_globalsettings_copy_failed"] = True
            write_json(run_dir / "config/meta.json", meta)
            print(
                "warning: failed to push AVP global settings; device may be locked/untrusted; continuing"
            )

        clear_probe_rc = clear_avp_probe_log(run_dir, args.avp_device, args.avp_bundle_id)
        meta["avp_probe_clear_exit_code"] = clear_probe_rc
        write_json(run_dir / "config/meta.json", meta)
        if clear_probe_rc != 0:
            meta["avp_probe_clear_failed"] = True
            write_json(run_dir / "config/meta.json", meta)
            print(
                "warning: failed to clear AVP probe log; device may be locked/untrusted; continuing"
            )

    cleanup_script = root / "tools/vr_stack_cleanup.py"
    smoke_script = root / "tools/steamvr_smoke.py"
    preflight_cleanup_log = run_dir / "logs/preflight-cleanup.log"
    preflight_rc = run_cleanup_script(sys.executable, cleanup_script, preflight_cleanup_log, args.sterile_native_steam)
    meta["preflight_cleanup_exit_code"] = preflight_rc
    write_json(run_dir / "config/meta.json", meta)
    if preflight_rc != 0:
        print("preflight cleanup failed; aborting run")
        return 1

    if args.safe_mode_recovery:
        recovery_log = run_dir / "logs/safe-mode-recovery-smoke.log"
        recovery_rc = run_safe_mode_recovery_smoke(
            python_exe=sys.executable,
            smoke_script=smoke_script,
            log_path=recovery_log,
            graphics_backend=args.graphics_backend,
            wait_seconds=args.safe_mode_recovery_wait_seconds,
        )
        meta["safe_mode_recovery_enabled"] = True
        meta["safe_mode_recovery_exit_code"] = recovery_rc
        meta["safe_mode_recovery_wait_seconds"] = args.safe_mode_recovery_wait_seconds
        write_json(run_dir / "config/meta.json", meta)
        if recovery_rc != 0:
            print("safe-mode recovery smoke failed; aborting run")
            return 1

        recovery_cleanup_log = run_dir / "logs/post-recovery-cleanup.log"
        recovery_cleanup_rc = run_cleanup_script(
            sys.executable,
            cleanup_script,
            recovery_cleanup_log,
            args.sterile_native_steam,
        )
        meta["post_recovery_cleanup_exit_code"] = recovery_cleanup_rc
        write_json(run_dir / "config/meta.json", meta)
        if recovery_cleanup_rc != 0:
            print("post-recovery cleanup failed; aborting run")
            return 1
    else:
        meta["safe_mode_recovery_enabled"] = False
        write_json(run_dir / "config/meta.json", meta)

    if not args.host_only and not args.no_restart_avp_app:
        avp_control_script = root / "tools/avp_alvr_control.py"
        avp_restart_log_path = run_dir / "logs/avp-app-restart.log"
        avp_restart_rc = run_capture_timeout(
            [
                sys.executable,
                str(avp_control_script),
                "--device",
                args.avp_device,
                "--bundle-id",
                args.avp_bundle_id,
                "restart",
            ],
            avp_restart_log_path,
            timeout_seconds=30,
        )
        meta["avp_restart_exit_code"] = avp_restart_rc
        avp_restart_log_text = avp_restart_log_path.read_text(encoding="utf-8", errors="replace")
        if "Device is locked" in avp_restart_log_text:
            meta["avp_restart_device_locked"] = True
            write_json(run_dir / "config/meta.json", meta)
            print(
                "AVP app restart blocked: device is locked (passcode required). "
                "Unlock/wear AVP, then rerun checkpoint."
            )
            return 3
        write_json(run_dir / "config/meta.json", meta)

    patch_steamvr_settings(
        paths["steamvr_settings"],
        run_dir,
        enable_home_app=(args.steamvr_home == "on"),
        direct_mode=args.direct_mode,
        mirror_view=args.mirror_view,
    )
    patch_session_contract(
        paths["alvr_session"],
        run_dir,
        args.codec,
        args.stream_protocol,
        args.foveated_encoding,
        manual_client_host=args.manual_client_host.strip() or None,
        manual_client_ip=args.manual_client_ip.strip() or None,
    )

    # Enforce ALVR driver registration each run.
    run_capture(
        [sys.executable, str(root / "tools/alvr_driver_register.py")],
        run_dir / "logs/driver-register.log",
    )

    subprocess.run(["pkill", "-f", "tools/vtbridge_daemon.py"], check=False)
    time.sleep(0.3)

    vtbridge_command = [
        sys.executable,
        str(root / "tools/vtbridge_daemon.py"),
        "--port",
        "37317",
        "--accept-configure",
        "--report-hardware-active",
        "--ring-path",
        "/tmp/alvr-vtbridge-ring.bin",
        "--force-codec",
        args.codec,
    ]
    if args.vtbridge_debug_dump_limit > 0:
        vtbridge_command.extend(
            [
                "--debug-dump-dir",
                str(run_dir / "logs/vtbridge-debug-frames"),
                "--debug-dump-limit",
                str(args.vtbridge_debug_dump_limit),
            ]
        )
    if args.native_window_capture_title_contains.strip():
        vtbridge_command.extend(
            [
                "--native-window-capture-title-contains",
                args.native_window_capture_title_contains.strip(),
            ]
        )
    if args.native_window_capture_owner_contains.strip():
        vtbridge_command.extend(
            [
                "--native-window-capture-owner-contains",
                args.native_window_capture_owner_contains.strip(),
            ]
        )
    if args.native_window_capture_title_contains.strip() or args.native_window_capture_owner_contains.strip():
        vtbridge_command.extend(
            [
                "--native-window-capture-fps",
                str(max(1, args.native_window_capture_fps)),
            ]
        )
    if args.force_test_pattern_hevc and args.codec == "hevc":
        vtbridge_command.append("--force-test-pattern-hevc")

    vtbridge_proc = start_logged_process(
        vtbridge_command,
        run_dir / "logs/vtbridge-daemon.log",
    )

    dashboard_started_here = False
    dashboard_proc: subprocess.Popen[str] | None = None
    dashboard_pid_check = subprocess.run(
        ["pgrep", "-f", "target/debug/alvr_dashboard"],
        check=False,
        capture_output=True,
        text=True,
    )
    if dashboard_pid_check.returncode != 0:
        dashboard_bin = Path.home() / "Developer/ALVR/target/debug/alvr_dashboard"
        if dashboard_bin.exists():
            dashboard_started_here = True
            dashboard_proc = start_logged_process(
                [str(dashboard_bin)],
                run_dir / "logs/alvr-dashboard.log",
                env={
                    **os.environ,
                    "RUST_LOG": "alvr_dashboard::data_sources=debug,alvr_dashboard=info",
                },
                cwd=dashboard_bin.parent.parent.parent,
            )

    baseline_steam_sizes = snapshot_log_sizes(paths["steam_logs"])
    baseline_alvr_size = paths["alvr_log"].stat().st_size if paths["alvr_log"].exists() else 0

    cxstart = Path("/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart")
    launch_env = {
        **os.environ,
        "WINEPREFIX": str(paths["bottle_root"]),
        "ALVR_VTBRIDGE_REQUIRED": "1",
        # Keep host-side VTBridge pattern injection disabled by default so
        # stale shell/session env cannot contaminate real-source runs.
        "ALVR_VTBRIDGE_TEST_PATTERN": (
            "1" if args.force_test_pattern_hevc and args.codec == "hevc" else "0"
        ),
    }
    if args.wine_debug_channels:
        launch_env["WINEDEBUG"] = args.wine_debug_channels
        (run_dir / "logs/wine-debug-env.txt").write_text(
            f"WINEDEBUG={args.wine_debug_channels}\n",
            encoding="utf-8",
        )
    if args.graphics_backend == "d3dmetal":
        launch_env["CX_GRAPHICS_BACKEND"] = "d3dmetal"
        launch_env["WINED3DMETAL"] = "1"
    else:
        launch_env["CX_GRAPHICS_BACKEND"] = "dxvk"
        launch_env["WINED3DMETAL"] = "0"

    if args.direct_mode == "off":
        launch_env["ALVR_DISABLE_DIRECT_MODE"] = "1"
    else:
        # Avoid inheriting stale shell/session env that can accidentally keep
        # direct mode disabled during strict direct-mode runs.
        launch_env["ALVR_DISABLE_DIRECT_MODE"] = "0"

    if args.display_redirect == "auto":
        launch_env["ALVR_ENABLE_DISPLAY_REDIRECT"] = "1" if args.direct_mode == "off" else "0"
    elif args.display_redirect == "on":
        launch_env["ALVR_ENABLE_DISPLAY_REDIRECT"] = "1"
    else:
        launch_env["ALVR_ENABLE_DISPLAY_REDIRECT"] = "0"

    if args.non_direct_source == "auto":
        launch_env["ALVR_DISABLE_NON_DIRECT_FRAME_SOURCE"] = (
            "0" if args.direct_mode == "off" else "1"
        )
    elif args.non_direct_source == "disable":
        launch_env["ALVR_DISABLE_NON_DIRECT_FRAME_SOURCE"] = "1"
    else:
        launch_env["ALVR_DISABLE_NON_DIRECT_FRAME_SOURCE"] = "0"
    if args.host_idle_fallback == "enable":
        launch_env["ALVR_DISABLE_VTBRIDGE_IDLE_FALLBACK"] = "0"
    elif args.host_idle_fallback == "disable":
        launch_env["ALVR_DISABLE_VTBRIDGE_IDLE_FALLBACK"] = "1"

    # Configure AeDebug for this run. Default mode suppresses debugger popups
    # (cmd /c exit 0). Capture mode redirects winedbg output to a file.
    paths["windows_temp"].mkdir(parents=True, exist_ok=True)
    if paths["winedbg_log"].exists():
        paths["winedbg_log"].unlink()
    previous_debugger = query_aedebug_debugger(
        cxstart,
        run_dir / "logs/aedebug-query-before.log",
        env=launch_env,
    )
    if args.winedbg_mode == "capture":
        debugger_value = r"cmd /c winedbg --auto %ld %ld >> C:\windows\temp\winedbg-auto.log 2>&1"
    else:
        debugger_value = r"cmd /c exit 0"
    aedebug_set_rc = set_aedebug_debugger(
        cxstart,
        debugger_value,
        run_dir / "logs/aedebug-set.log",
        env=launch_env,
    )
    # Force automatic debugger prompts off so unhandled exceptions do not block
    # headless runs with modal Wine crash dialogs.
    aedebug_auto_set_rc = set_aedebug_auto(
        cxstart,
        "0",
        run_dir / "logs/aedebug-auto-set.log",
        env=launch_env,
    )
    winedbg_show_crash_dialog_set_rc = set_winedbg_show_crash_dialog(
        cxstart,
        enabled=False,
        output_path=run_dir / "logs/winedbg-show-crash-dialog-set.log",
        env=launch_env,
    )
    current_debugger = query_aedebug_debugger(
        cxstart,
        run_dir / "logs/aedebug-query-after.log",
        env=launch_env,
    )
    meta["aedebug_previous_debugger"] = previous_debugger
    meta["aedebug_set_exit_code"] = aedebug_set_rc
    meta["aedebug_auto_set_exit_code"] = aedebug_auto_set_rc
    meta["winedbg_show_crash_dialog_set_exit_code"] = winedbg_show_crash_dialog_set_rc
    meta["aedebug_current_debugger"] = current_debugger
    meta["winedbg_mode"] = args.winedbg_mode
    meta["winedbg_capture_log"] = str(paths["winedbg_log"])
    write_json(run_dir / "config/meta.json", meta)

    startup_exe = r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrstartup.exe"
    launch_rc = run_capture_timeout(
        [str(cxstart), "--bottle", "Steam", "--no-gui", "--no-wait", startup_exe],
        run_dir / "logs/cxstart.log",
        env=launch_env,
        timeout_seconds=20,
        timeout_is_success=True,
    )
    meta["cxstart_exit_code"] = launch_rc
    write_json(run_dir / "config/meta.json", meta)
    if launch_rc != 0:
        print("SteamVR launch failed; tearing down")
        terminate_process(vtbridge_proc)
        if dashboard_started_here and dashboard_proc is not None:
            terminate_process(dashboard_proc)
        run_cleanup_script(
            sys.executable,
            cleanup_script,
            run_dir / "logs/postflight-cleanup.log",
            args.sterile_native_steam,
        )
        return 1

    steamvr_tool_exe: str | None = None
    if args.steamvr_tool == "steamvr_tutorial":
        steamvr_tool_exe = (
            "C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\tools\\"
            "steamvr_tutorial\\win64\\steamvr_tutorial.exe"
        )
    elif args.steamvr_tool == "steamvr_room_setup":
        steamvr_tool_exe = (
            "C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\tools\\"
            "steamvr_room_setup\\win64\\steamvr_room_setup.exe"
        )
    elif args.steamvr_tool == "steamvr_overlay_viewer":
        steamvr_tool_exe = (
            "C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\"
            "win64\\overlay_viewer.exe"
        )
    elif args.steamvr_tool == "steamvr_monitor":
        steamvr_tool_exe = (
            "C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\"
            "win64\\vrmonitor.exe"
        )
    elif args.steamvr_tool == "steamvr_steamtours":
        steamvr_tool_exe = (
            "C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\tools\\"
            "steamvr_environments\\game\\bin\\win64\\steamtours.exe"
        )

    if steamvr_tool_exe is not None:
        tool_rc = run_capture_timeout(
            [str(cxstart), "--bottle", "Steam", "--no-gui", "--no-wait", steamvr_tool_exe],
            run_dir / "logs/steamvr-tool-launch.log",
            env=launch_env,
            timeout_seconds=20,
            timeout_is_success=True,
        )
        meta["steamvr_tool_launch_exit_code"] = tool_rc
        write_json(run_dir / "config/meta.json", meta)

    if args.steam_app_id is not None:
        if args.steam_app_delay_seconds > 0:
            time.sleep(args.steam_app_delay_seconds)

        if args.steam_app_args or args.steam_app_force_vr:
            steam_exe = r"C:\Program Files (x86)\Steam\steam.exe"
            steam_app_command = [
                str(cxstart),
                "--bottle",
                "Steam",
                "--no-gui",
                "--no-wait",
                steam_exe,
                "-applaunch",
                str(args.steam_app_id),
            ]
            if args.steam_app_force_vr:
                steam_app_command.append("-vr")
            if args.steam_app_args:
                steam_app_command.extend(shlex.split(args.steam_app_args))
            meta["steam_app_launch_mode"] = "applaunch"
        else:
            steam_url = f"steam://rungameid/{args.steam_app_id}"
            steam_app_command = [
                str(cxstart),
                "--bottle",
                "Steam",
                "--no-gui",
                "--no-wait",
                steam_url,
            ]
            meta["steam_app_launch_mode"] = "steam_url"

        steam_app_rc = run_capture_timeout(
            steam_app_command,
            run_dir / "logs/steam-app-launch.log",
            env=launch_env,
            timeout_seconds=20,
            timeout_is_success=True,
        )
        meta["steam_app_launch_exit_code"] = steam_app_rc
        write_json(run_dir / "config/meta.json", meta)

    window_capture_proc: subprocess.Popen[str] | None = None
    window_capture_stop_file = run_dir / "window-capture-stop"
    window_capture_dir = run_dir / "window-capture"
    if args.window_capture == "on":
        window_capture_dir.mkdir(parents=True, exist_ok=True)
        window_capture_proc = start_window_capture_loop(
            output_dir=window_capture_dir,
            log_path=run_dir / "logs/window-capture-loop.log",
            interval_seconds=args.window_capture_interval,
            duration_seconds=args.capture_seconds + 10,
            stop_file=window_capture_stop_file,
        )
        meta["window_capture_enabled"] = True
        meta["window_capture_pid"] = window_capture_proc.pid
        write_json(run_dir / "config/meta.json", meta)
    else:
        meta["window_capture_enabled"] = False

    deadline = time.monotonic() + args.capture_seconds
    prompted = False
    while time.monotonic() < deadline:
        elapsed = args.capture_seconds - (deadline - time.monotonic())
        if args.minimize_crossover_windows == "on":
            minimize_crossover_windows()
        if not args.host_only and not prompted and elapsed >= args.prompt_at_seconds:
            print(
                "ACTION: Keep ALVR frontmost on AVP for ~20 seconds. "
                "If an Enter button appears, tap it once."
            )
            prompted = True
        time.sleep(1)

    if window_capture_proc is not None:
        window_capture_stop_file.touch()
        terminate_process(window_capture_proc, timeout_seconds=6.0)

    logs_dir = run_dir / "logs"
    candidate_logs = [
        "vrserver.txt",
        "vrmonitor.txt",
        "vrcompositor.txt",
        "vrstartup.txt",
        "driver_alvr_server.txt",
    ]

    for vrclient_log in sorted(paths["steam_logs"].glob("vrclient_*.txt")):
        candidate_logs.append(vrclient_log.name)

    for name in candidate_logs:
        source = paths["steam_logs"] / name
        if source.exists():
            copy_log_with_delta(source, logs_dir, baseline_steam_sizes.get(name, 0))

    if paths["alvr_log"].exists():
        copy_log_with_delta(paths["alvr_log"], logs_dir, baseline_alvr_size)

    if paths["winedbg_log"].exists():
        copy_log_with_delta(paths["winedbg_log"], logs_dir, 0)

    alvr_text, alvr_delta_fallback_used = read_alvr_session_text(logs_dir)
    meta["alvr_delta_fallback_used"] = alvr_delta_fallback_used
    write_json(run_dir / "config/meta.json", meta)
    daemon_log_path = logs_dir / "vtbridge-daemon.log"
    daemon_log = daemon_log_path.read_text(encoding="utf-8", errors="replace") if daemon_log_path.exists() else ""
    dashboard_log_path = logs_dir / "alvr-dashboard.log"
    dashboard_text = (
        dashboard_log_path.read_text(encoding="utf-8", errors="replace")
        if dashboard_log_path.exists()
        else ""
    )

    if args.host_only:
        probe_copy_rc, avp_probe_text = 0, ""
        meta["avp_probe_copy_skipped_host_only"] = True
    else:
        probe_copy_rc, avp_probe_text = copy_avp_probe_log(run_dir, args.avp_device, args.avp_bundle_id)
        meta["avp_probe_copy_exit_code"] = probe_copy_rc
    write_json(run_dir / "config/meta.json", meta)

    steam_runtime_text = build_steam_runtime_text(logs_dir)
    if args.wine_debug_channels:
        cxstart_log_text = (logs_dir / "cxstart.log").read_text(encoding="utf-8", errors="replace")
        trace_patterns = ("d3d11", "dxgi", "vulkan", "wined3d", "err:")
        trace_lines: list[str] = []
        for line in f"{cxstart_log_text}\n{steam_runtime_text}".splitlines():
            lower_line = line.lower()
            if any(pattern in lower_line for pattern in trace_patterns):
                trace_lines.append(line)
        deduped_trace_lines: list[str] = []
        seen_trace_lines: set[str] = set()
        for line in trace_lines:
            if line in seen_trace_lines:
                continue
            seen_trace_lines.add(line)
            deduped_trace_lines.append(line)
        wine_trace_path = logs_dir / "wine-d3d-trace.log"
        wine_trace_path.write_text("\n".join(deduped_trace_lines), encoding="utf-8")
        meta["wine_trace_excerpt_lines"] = len(deduped_trace_lines)
        write_json(run_dir / "config/meta.json", meta)

    outcome = parse_key_outcome(
        alvr_text,
        daemon_log,
        dashboard_text,
        avp_probe_text,
        steam_runtime_text,
        logs_dir / "vtbridge-debug-frames",
    )

    winedbg_text = ""
    winedbg_run_log = logs_dir / "winedbg-auto.log"
    if winedbg_run_log.exists():
        winedbg_text = winedbg_run_log.read_text(encoding="utf-8", errors="replace")
    outcome["winedbg_unhandled_exception"] = "Unhandled exception:" in winedbg_text
    outcome["winedbg_page_fault_execute"] = "page fault on execute access" in winedbg_text
    outcome["winedbg_no_code_accessible"] = "-- no code accessible --" in winedbg_text
    outcome["winedbg_log_bytes"] = len(winedbg_text.encode("utf-8"))
    outcome["client_probe_log_collected"] = probe_copy_rc == 0
    outcome["client_probe_log_bytes"] = len(avp_probe_text.encode("utf-8"))

    def fail_gate(reason: str) -> None:
        gate_failures = outcome["gate_failures"]
        if reason not in gate_failures:
            gate_failures.append(reason)
        outcome["pass"] = False

    if args.require_client_video_present and not outcome["client_video_presenting"]:
        fail_gate("client_video_presenting_missing")

    if (
        (
            args.require_client_ready
            or args.require_client_video_present
            or args.forbid_synthetic_fallback
            or args.require_real_decode
        )
        and probe_copy_rc != 0
    ):
        fail_gate("avp_probe_unavailable")

    if args.require_client_ready:
        if not outcome["client_app_initialized"]:
            fail_gate("client_app_not_initialized")
        if not outcome["client_streaming_started"]:
            fail_gate("client_streaming_not_started")
            if outcome["client_ui_block_suspected"]:
                fail_gate("client_ui_block_suspected")
        if not (outcome["client_decode_success"] or outcome["client_video_presenting"]):
            fail_gate("client_ready_frame_missing")

    if args.forbid_synthetic_fallback or args.require_real_decode:
        if outcome["client_synthetic_fallback_enabled"] is not False:
            fail_gate("synthetic_fallback_not_disabled")
        if outcome["client_synthetic_fallback_used"]:
            fail_gate("synthetic_fallback_used")

    if args.require_real_decode:
        if not outcome["client_decoder_config_seen"]:
            fail_gate("decoder_config_missing")
        if not outcome["client_decode_success"]:
            fail_gate("decode_success_missing")
        if not outcome["client_video_presenting"]:
            fail_gate("client_video_presenting_missing")

    if args.forbid_host_idle_fallback:
        if outcome["host_idle_fallback_enabled"] is None and args.host_idle_fallback == "disable":
            # When session delta capture starts after the initial probe burst,
            # infer disabled state from the explicit launch contract.
            outcome["host_idle_fallback_enabled"] = False
            outcome["host_idle_fallback_enabled_inferred"] = True
        if outcome["host_idle_fallback_enabled"] is not False:
            fail_gate("host_idle_fallback_not_disabled")
        if outcome["host_idle_fallback_used"]:
            fail_gate("host_idle_fallback_used")

    if args.require_host_frame_signals:
        host_frame_produced = (
            outcome["host_new_frame_ready_seen"]
            or outcome["host_non_direct_frame_produced_seen"]
        )
        host_frame_submitted = (
            outcome["host_copy_to_staging_seen"]
            or outcome["host_copy_composed_to_staging_seen"]
            or outcome["host_non_direct_frame_submitted_seen"]
        )
        if not host_frame_produced:
            fail_gate("host_new_frame_ready_missing")
        if not host_frame_submitted:
            fail_gate("host_copy_to_staging_missing")

    if (
        outcome.get("host_virtual_display_component_seen")
        and not outcome.get("host_virtual_display_present_seen")
        and not outcome["host_non_direct_source_enabled"]
    ):
        fail_gate("host_virtual_display_present_missing")

    if args.require_direct_mode_healthy:
        if outcome["host_direct_mode_swap_failed"]:
            fail_gate("host_direct_mode_swap_failed")
        if outcome["steamvr_external_memory_extensions_missing"]:
            fail_gate("steamvr_external_memory_extensions_missing")
        if outcome["interop_signature_details"].get(
            "direct_mode_local_handle_fallback_get_shared", False
        ):
            fail_gate("direct_mode_local_handle_fallback_get_shared")
        if outcome["interop_signature"] == "create_swap_texture_set_fallback_loop":
            fail_gate("create_swap_texture_set_fallback_loop")
        if args.direct_mode == "on" and outcome["host_direct_mode_disabled"]:
            fail_gate("host_direct_mode_disabled")
        if args.direct_mode == "on" and outcome["host_non_direct_source_enabled"]:
            fail_gate("host_non_direct_source_enabled")
        if args.direct_mode == "on" and outcome["host_direct_mode_recovery_used"]:
            fail_gate("host_direct_mode_recovery_used")

    if args.require_source_motion:
        has_fresh_motion = outcome["source_motion_seen"]
        has_bootstrap_motion = (
            outcome["source_bootstrap_refresh_count"] >= 2
            and len(outcome["source_bootstrap_colors"]) >= 2
        )
        if not has_fresh_motion and not has_bootstrap_motion:
            if outcome["source_fresh_encode_count"] < 2 and outcome["source_bootstrap_refresh_count"] < 2:
                fail_gate("source_fresh_encode_missing")
            fail_gate("source_motion_missing")

    if args.require_source_motion and outcome.get("source_steam_login_ui_detected"):
        fail_gate("steam_login_ui_detected")
    if args.require_source_motion and outcome.get("source_steam_client_ui_detected"):
        fail_gate("steam_client_ui_detected")

    if args.forbid_known_synthetic_source:
        if outcome["source_known_synthetic_pattern"]:
            fail_gate("source_known_synthetic_pattern")
        if outcome.get("source_black_red_oscillation_suspected"):
            fail_gate("source_black_red_oscillation_suspected")

    if args.require_real_source:
        source_quality = outcome.get("source_quality_grade")
        if source_quality != "real_candidate":
            fail_gate(f"source_not_real_candidate:{source_quality}")
        if outcome.get("source_debug_all_flat"):
            fail_gate("source_debug_frames_all_flat")
        if outcome.get("source_path_selected") == "synthetic_pattern":
            fail_gate("source_synthetic_pattern_selected")
        if outcome.get("source_known_synthetic_pattern"):
            fail_gate("source_known_synthetic_pattern")
        if outcome.get("source_black_red_oscillation_suspected"):
            fail_gate("source_black_red_oscillation_suspected")

    if (
        args.forbid_static_source
        and outcome["source_bootstrap_refresh_count"] == 0
        and outcome["source_static_suspected"]
    ):
        fail_gate("source_static_suspected")

    if outcome["client_ui_block_summary"]:
        print(f"CLIENT_UI_DIAG: {outcome['client_ui_block_summary']}")

    if args.window_capture == "on":
        capture_stats = analyze_window_capture_manifest(
            window_capture_dir / "manifest.jsonl"
        )
        meta.update(capture_stats)
        write_json(run_dir / "config/meta.json", meta)
        print(
            f"WINDOW_CAPTURE: frames={capture_stats['window_capture_frame_count']} "
            f"succeeded={capture_stats['window_capture_succeeded_count']} "
            f"nonflat={capture_stats['window_capture_nonflat_count']} "
            f"titles={capture_stats['window_capture_window_titles']}"
        )

    write_json(run_dir / "config/outcome.json", outcome)
    print(json.dumps(outcome, indent=2))

    if args.keep_session_alive:
        meta["postflight_cleanup_skipped"] = True
        meta["postflight_cleanup_exit_code"] = None
        write_json(run_dir / "config/meta.json", meta)
        print(
            "POSTFLIGHT: keep-session-alive enabled; skipped postflight cleanup and process teardown."
        )
        print(
            "When finished, run: python3 tools/vr_stack_cleanup.py"
            + (" --sterile-native-steam" if args.sterile_native_steam else "")
        )
    else:
        terminate_process(vtbridge_proc)
        if dashboard_started_here and dashboard_proc is not None:
            terminate_process(dashboard_proc)

        postflight_rc = run_cleanup_script(
            sys.executable,
            cleanup_script,
            run_dir / "logs/postflight-cleanup.log",
            args.sterile_native_steam,
        )
        meta["postflight_cleanup_skipped"] = False
        meta["postflight_cleanup_exit_code"] = postflight_rc
        write_json(run_dir / "config/meta.json", meta)

        if postflight_rc != 0:
            print("postflight cleanup reported remaining processes")
            return 1
    if args.require_pass and not outcome["pass"]:
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strict one-shot live AVP checkpoint harness")
    parser.add_argument(
        "--run-root",
        default=str(repo_root() / "temp/vr_runs"),
        help="directory for run bundles",
    )
    parser.add_argument(
        "--prune-old-runs",
        action="store_true",
        help="prune old live checkpoint bundles under --run-root before creating a new run",
    )
    parser.add_argument(
        "--prune-keep-last",
        type=int,
        default=25,
        help="when pruning, always keep at least this many newest live checkpoint bundles",
    )
    parser.add_argument(
        "--prune-older-than-days",
        type=int,
        default=14,
        help="when pruning, only delete live checkpoint bundles older than this many days",
    )
    parser.add_argument(
        "--capture-seconds",
        type=int,
        default=45,
        help="capture window length after launch",
    )
    parser.add_argument(
        "--prompt-at-seconds",
        type=int,
        default=8,
        help="when to print AVP Enter prompt in non-host-only mode",
    )
    parser.add_argument(
        "--host-only",
        action="store_true",
        help="skip AVP Enter prompt and run unattended host-only checkpoint",
    )
    parser.add_argument(
        "--sterile-native-steam",
        action="store_true",
        help="also kill native Steam ipcserver during cleanup pre/postflight",
    )
    parser.add_argument(
        "--no-safe-mode-recovery",
        action="store_false",
        dest="safe_mode_recovery",
        help="skip pre-launch SteamVR null-smoke recovery used to clear crash safe-mode state",
    )
    parser.add_argument(
        "--safe-mode-recovery-wait-seconds",
        type=int,
        default=20,
        help="wait time used by the pre-launch null-smoke safe-mode recovery",
    )
    parser.set_defaults(safe_mode_recovery=True)
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="exit nonzero when bridge checkpoint does not meet pass criteria",
    )
    parser.add_argument(
        "--keep-session-alive",
        action="store_true",
        help=(
            "skip postflight teardown so SteamVR/ALVR stay running after capture; "
            "run tools/vr_stack_cleanup.py manually when finished"
        ),
    )
    parser.add_argument(
        "--no-restart-avp-app",
        action="store_true",
        help="skip best-effort restart of the AVP ALVR app before launch",
    )
    parser.add_argument(
        "--force-test-pattern-hevc",
        action="store_true",
        help="force vtbridge to stream a static HEVC test pattern frame",
    )
    parser.add_argument(
        "--vtbridge-debug-dump-limit",
        type=int,
        default=0,
        help=(
            "dump up to N unique sampled VT bridge source payload frames as PNG in "
            "logs/vtbridge-debug-frames (0 disables)"
        ),
    )
    parser.add_argument(
        "--native-window-capture-title-contains",
        default="",
        help="comma-separated macOS window title filters for the native ScreenCaptureKit fallback source",
    )
    parser.add_argument(
        "--native-window-capture-owner-contains",
        default="",
        help="comma-separated macOS window owner filters for the native ScreenCaptureKit fallback source",
    )
    parser.add_argument(
        "--native-window-capture-fps",
        type=int,
        default=15,
        help="target fps for the native ScreenCaptureKit fallback source",
    )
    parser.add_argument(
        "--synthetic-fallback",
        choices=["disable", "enable", "unchanged"],
        default="disable",
        help="set AVP probe config for synthetic fallback before run",
    )
    parser.add_argument(
        "--host-idle-fallback",
        choices=["disable", "enable", "unchanged"],
        default="disable",
        help="control host VT bridge idle-frame fallback injection policy",
    )
    parser.add_argument(
        "--codec",
        choices=["hevc"],
        default="hevc",
        help="stream codec contract for ALVR and vtbridge",
    )
    parser.add_argument(
        "--stream-protocol",
        choices=["udp", "tcp"],
        default="tcp",
        help="ALVR stream transport protocol to set in session contract",
    )
    parser.add_argument(
        "--foveated-encoding",
        choices=["auto", "off", "on"],
        default="auto",
        help="force ALVR foveated encoding policy in session contract",
    )
    parser.add_argument(
        "--graphics-backend",
        choices=["dxvk", "d3dmetal"],
        default="dxvk",
        help="CrossOver graphics backend for this run",
    )
    parser.add_argument(
        "--wine-debug-channels",
        default="",
        help="optional WINEDEBUG channels (for example: +d3d11,+dxgi,+vulkan)",
    )
    parser.add_argument(
        "--winedbg-mode",
        choices=["off", "capture"],
        default="off",
        help=(
            "AeDebug mode: off suppresses debugger popups (cmd /c exit 0); "
            "capture routes winedbg output to logs/winedbg-auto.log"
        ),
    )
    parser.add_argument(
        "--minimize-crossover-windows",
        choices=["on", "off"],
        default="on",
        help=(
            "minimize CrossOver/Wine windows during capture loop. "
            "Disable when validating non-direct window capture paths."
        ),
    )
    parser.add_argument(
        "--direct-mode",
        choices=["off", "on"],
        default="off",
        help="SteamVR direct mode policy for ALVR launch",
    )
    parser.add_argument(
        "--non-direct-source",
        choices=["auto", "disable", "enable"],
        default="auto",
        help=(
            "control host non-direct synthetic frame source override; "
            "auto keeps existing policy (enabled when direct-mode off, disabled when direct-mode on)"
        ),
    )
    parser.add_argument(
        "--display-redirect",
        choices=["auto", "on", "off"],
        default="auto",
        help=(
            "control ALVR display-redirect virtual display path; "
            "auto enables redirect when direct mode is off"
        ),
    )
    parser.add_argument(
        "--steamvr-home",
        choices=["off", "on"],
        default="off",
        help="toggle SteamVR Home scene generation for non-black source validation",
    )
    parser.add_argument(
        "--mirror-view",
        choices=["off", "on", "legacy"],
        default="off",
        help=(
            "toggle SteamVR mirror window generation for non-direct window capture experiments; "
            "legacy enables only Legacy Mirror"
        ),
    )
    parser.add_argument(
        "--steamvr-tool",
        choices=[
            "none",
            "steamvr_monitor",
            "steamvr_tutorial",
            "steamvr_room_setup",
            "steamvr_overlay_viewer",
            "steamvr_steamtours",
        ],
        default="none",
        help="launch a SteamVR tool app after startup to generate non-black source content",
    )
    parser.add_argument(
        "--steam-app-id",
        type=int,
        default=None,
        help="optional Steam app id to launch via steam.exe -applaunch",
    )
    parser.add_argument(
        "--steam-app-args",
        default="",
        help="optional extra args appended after -applaunch <appid>",
    )
    parser.add_argument(
        "--steam-app-force-vr",
        action="store_true",
        help="append -vr to app launch args for hybrid 2D/VR titles",
    )
    parser.add_argument(
        "--steam-app-delay-seconds",
        type=int,
        default=8,
        help="delay before launching --steam-app-id after SteamVR startup",
    )
    parser.add_argument(
        "--require-client-ready",
        action="store_true",
        help=(
            "require client app init, streaming start, and first decoded/presented frame; "
            "helps fail fast when AVP UI blockers prevent entry"
        ),
    )
    parser.add_argument(
        "--require-client-video-present",
        action="store_true",
        help="require client probe marker that video frames were actively presented",
    )
    parser.add_argument(
        "--forbid-synthetic-fallback",
        action="store_true",
        help="fail validation if synthetic fallback is enabled or injected",
    )
    parser.add_argument(
        "--forbid-host-idle-fallback",
        action="store_true",
        help="fail validation if host idle-frame fallback is enabled or injected",
    )
    parser.add_argument(
        "--require-real-decode",
        action="store_true",
        help=(
            "require decoder_config, decode_success, and client video presenting "
            "while synthetic fallback remains disabled and unused"
        ),
    )
    parser.add_argument(
        "--require-source-motion",
        action="store_true",
        help="require source fresh-encode sample CRCs to vary across the run",
    )
    parser.add_argument(
        "--require-host-frame-signals",
        action="store_true",
        help="require host CEncoder new_frame_ready and copy_to_staging telemetry",
    )
    parser.add_argument(
        "--require-direct-mode-healthy",
        action="store_true",
        help="require no direct-mode swap failures and no missing SteamVR external-memory extensions",
    )
    parser.add_argument(
        "--forbid-static-source",
        action="store_true",
        help="fail when source appears static/blank based on fresh-encode telemetry",
    )
    parser.add_argument(
        "--forbid-known-synthetic-source",
        action="store_true",
        help="fail when source matches the known deterministic non-direct color pattern set",
    )
    parser.add_argument(
        "--require-real-source",
        action="store_true",
        help=(
            "require a non-synthetic source path candidate (window/desktop/virtual/direct-mode) "
            "and fail when source quality remains static/synthetic/unknown"
        ),
    )
    parser.add_argument(
        "--avp-device",
        default="Apple Vision Pro",
        help="devicectl target device name/UDID",
    )
    parser.add_argument(
        "--avp-bundle-id",
        default="com.shinycomputers.alvrclient",
        help="visionOS ALVR bundle identifier",
    )
    parser.add_argument(
        "--manual-client-host",
        default="",
        help="optional manual ALVR client host key (for example: 5130.client.local..alvr)",
    )
    parser.add_argument(
        "--manual-client-ip",
        default="",
        help="optional manual ALVR client IP used to seed client_connections",
    )
    parser.add_argument(
        "--window-capture",
        choices=["off", "on"],
        default="off",
        help=(
            "continuously capture the best-matching SteamVR/CrossOver app window via "
            "ScreenCaptureKit during the capture window; frames and manifest saved under "
            "window-capture/ in the run bundle (requires macOS Screen Recording permission)"
        ),
    )
    parser.add_argument(
        "--window-capture-interval",
        type=float,
        default=3.0,
        help="seconds between window captures when --window-capture on (default: 3.0)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
