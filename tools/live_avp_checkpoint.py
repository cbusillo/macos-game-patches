#!/usr/bin/env python3

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
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
) -> None:
    before_path = run_dir / "config/steamvr.vrsettings.before.json"
    after_path = run_dir / "config/steamvr.vrsettings.after.json"

    if steamvr_settings.exists():
        before_path.write_text(steamvr_settings.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        settings = read_json(steamvr_settings)
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
    steamvr["showMirrorView"] = False
    steamvr["showLegacyMirrorView"] = False

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
    *,
    manual_client_host: str | None,
    manual_client_ip: str | None,
) -> None:
    before_path = run_dir / "config/session.before.json"
    after_path = run_dir / "config/session.after.json"
    if not session_path.exists():
        return

    before_path.write_text(session_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    session = read_json(session_path)

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

    write_json(session_path, session)
    after_path.write_text(session_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")


def parse_key_outcome(
    alvr_text: str,
    daemon_log: str,
    dashboard_text: str,
    avp_probe_text: str,
    steam_runtime_text: str,
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
    ) -> tuple[str, dict[str, bool]]:
        combined = f"{alvr_log_text}\n{steam_runtime_log_text}".lower()
        details = {
            "extension_missing": extension_missing,
            "get_shared_handle_failed": "getsharedhandle failed" in combined,
            "create_shared_handle_failed": "createsharedhandle" in combined and "failed" in combined,
            "create_shared_handle_e_invalidarg": "createsharedhandle" in combined
            and "0x80070057" in combined,
            "create_shared_handle_e_notimpl": "createsharedhandle" in combined and "0x80004001" in combined,
            "open_shared_resource_failed": "opensharedresource" in combined and "failed" in combined,
            "vrcompositor_access_violation": "exception c0000005" in steam_runtime_log_text.lower(),
            "virtual_display_component_seen": virtual_display_component_seen,
            "virtual_display_present_seen": virtual_display_present_seen,
            "direct_mode_swap_failed": direct_mode_swap_failed,
            "source_known_synthetic_pattern": source_known_synthetic_pattern,
            "source_static_suspected": source_static_suspected,
            "direct_mode_recovery_used": direct_mode_recovery_used,
        }

        if details["extension_missing"]:
            return "vk_win32_external_memory_missing", details
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

    combined_probe_text = f"{alvr_text}\n{avp_probe_text}"
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
        re.finditer(r"PROBE host_idle_fallback_enabled=(\d)", alvr_text)
    )
    host_idle_fallback_enabled: bool | None = None
    if host_idle_fallback_enabled_matches:
        host_idle_fallback_enabled = host_idle_fallback_enabled_matches[-1].group(1) == "1"

    copy_to_staging_matches = [
        int(match.group(1))
        for match in re.finditer(r"CEncoder: copy_to_staging calls=(\d+)", alvr_text)
    ]
    new_frame_ready_matches = [
        int(match.group(1))
        for match in re.finditer(r"CEncoder: new_frame_ready calls=(\d+)", alvr_text)
    ]
    non_direct_frame_produced_matches = [
        int(match.group(1))
        for match in re.finditer(r"PROBE host_non_direct_frame_produced count=(\d+)", alvr_text)
    ]
    non_direct_frame_submitted_matches = [
        int(match.group(1))
        for match in re.finditer(r"PROBE host_non_direct_frame_submitted count=(\d+)", alvr_text)
    ]
    direct_mode_recovery_frame_produced_matches = [
        int(match.group(1))
        for match in re.finditer(r"PROBE host_direct_mode_recovery_frame_produced count=(\d+)", alvr_text)
    ]

    streaming_state_seen = (
        '"connection_state":"Streaming"' in dashboard_text
        or "handshake finished; unlocking streams" in alvr_text
    )
    timeout_observed = (
        "Could not initiate connection" in alvr_text
        or "timed out" in alvr_text
        or "OS Error 10065" in alvr_text
    )

    fresh_encode_matches = list(
        re.finditer(
            r"fresh_encode sequence=(\d+) encoded_bytes=(\d+) sample_crc=0x([0-9a-fA-F]+)",
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
    source_known_synthetic_pattern = (
        fresh_encode_count >= 8
        and len(unique_fresh_sample_crcs) >= 4
        and unique_fresh_sample_crcs.issubset(known_synthetic_fresh_crcs)
    )
    source_static_suspected = (
        fresh_encode_count >= 20
        and len(unique_fresh_sample_crcs) == 1
        and len(unique_fresh_encoded_sizes) == 1
        and next(iter(unique_fresh_encoded_sizes), 0) <= 3000
    )

    non_direct_source_matches = [
        match.group(1)
        for match in re.finditer(
            r"PROBE host_non_direct_frame_rendered tick=\d+ source=([a-z_]+)",
            alvr_text,
        )
    ]
    synthetic_phase_match_count = len(
        re.findall(r"PROBE host_non_direct_frame_rendered tick=\d+ phase=", alvr_text)
    )
    source_path_counts: dict[str, int] = {}
    for source in non_direct_source_matches:
        source_path_counts[source] = source_path_counts.get(source, 0) + 1
    if synthetic_phase_match_count > 0:
        source_path_counts["synthetic_pattern"] = (
            source_path_counts.get("synthetic_pattern", 0) + synthetic_phase_match_count
        )

    source_path_selected = "unknown"
    if source_path_counts:
        source_path_selected = max(
            sorted(source_path_counts.keys()),
            key=lambda path_name: source_path_counts[path_name],
        )

    source_quality_grade = "unknown"
    if source_static_suspected:
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

    stretch_error_values = sorted(
        {
            int(match.group(1))
            for match in re.finditer(
                r"PROBE host_non_direct_desktop_capture_failed.*stretch_error=(\d+)",
                alvr_text,
            )
        }
    )

    bridge_connected_explicit = "VideoEncoderVtBridge: connected" in alvr_text
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

    explicit_non_direct_source_enabled = "PROBE host_non_direct_source_enabled=1" in alvr_text
    inferred_non_direct_source_enabled = (
        len(non_direct_frame_produced_matches) > 0
        or len(non_direct_frame_submitted_matches) > 0
        or "source=non_direct" in alvr_text
    )
    host_non_direct_source_enabled = (
        explicit_non_direct_source_enabled or inferred_non_direct_source_enabled
    )

    explicit_direct_mode_disabled = (
        "ALVR MGP direct-mode guard" in alvr_text and "disabled=1" in alvr_text
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
        "host_new_frame_ready_seen": len(new_frame_ready_matches) > 0,
        "host_new_frame_ready_max_calls": max(new_frame_ready_matches) if new_frame_ready_matches else -1,
        "host_non_direct_source_enabled": host_non_direct_source_enabled,
        "host_non_direct_source_enabled_inferred": inferred_non_direct_source_enabled,
        "host_direct_mode_recovery_source_enabled": "PROBE host_direct_mode_recovery_source_enabled=1"
        in alvr_text,
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
        or "source=direct_mode_recovery" in alvr_text,
        "host_direct_mode_recovery_max_count": max(direct_mode_recovery_frame_produced_matches)
        if direct_mode_recovery_frame_produced_matches
        else -1,
        "source_fresh_encode_count": fresh_encode_count,
        "source_unique_fresh_encoded_sizes": sorted(unique_fresh_encoded_sizes),
        "source_unique_fresh_sample_crcs": sorted(unique_fresh_sample_crcs),
        "source_known_synthetic_pattern": source_known_synthetic_pattern,
        "source_path_selected": source_path_selected,
        "source_path_counts": source_path_counts,
        "source_quality_grade": source_quality_grade,
        "source_bootstrap_refresh_count": len(bootstrap_refresh_matches),
        "source_bootstrap_colors": sorted(bootstrap_refresh_colors),
        "source_static_suspected": source_static_suspected,
        "host_non_direct_capture_stretch_errors": stretch_error_values,
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
        "PROBE host_idle_fallback_injected" in alvr_text
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
        "CreateSwapTextureSet failed" in alvr_text
        or "CreateSwapTextureSet failed" in steam_runtime_text
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
        "PROBE display_redirect_component_virtual_display" in steam_runtime_text
        or "PROBE display_redirect_component_virtual_display" in alvr_text
        or "VDR GetComponent request=IVRVirtualDisplay_002" in steam_runtime_text
    )
    virtual_display_present_seen = (
        "PROBE display_redirect_present" in steam_runtime_text
        or "PROBE display_redirect_present" in alvr_text
        or "PROBE virtual_display_present_api" in steam_runtime_text
        or "PROBE virtual_display_present_api" in alvr_text
        or "PROBE display_redirect_wait_for_present" in steam_runtime_text
        or "PROBE display_redirect_wait_for_present" in alvr_text
        or "PROBE display_redirect_get_time_since_last_vsync" in steam_runtime_text
        or "PROBE display_redirect_get_time_since_last_vsync" in alvr_text
    )
    direct_mode_disabled_log_seen = (
        "Hmd::GetComponent virtual_display requested direct_mode_disabled=1"
        in steam_runtime_text
        or "Hmd::GetComponent virtual_display requested direct_mode_disabled=1" in alvr_text
        or "ALVR MGP direct-mode guard" in steam_runtime_text and "disabled=1" in steam_runtime_text
    )

    outcome["host_virtual_display_component_seen"] = virtual_display_component_seen
    outcome["host_virtual_display_present_seen"] = virtual_display_present_seen
    if direct_mode_disabled_log_seen:
        outcome["host_direct_mode_disabled"] = True

    interop_signature, interop_signature_details = detect_interop_signature(
        alvr_log_text=alvr_text,
        steam_runtime_log_text=steam_runtime_text,
        extension_missing=outcome["steamvr_external_memory_extensions_missing"],
        direct_mode_swap_failed=outcome["host_direct_mode_swap_failed"],
        virtual_display_component_seen=outcome["host_virtual_display_component_seen"],
        virtual_display_present_seen=outcome["host_virtual_display_present_seen"],
        direct_mode_disabled=outcome["host_direct_mode_disabled"],
        source_known_synthetic_pattern=outcome["source_known_synthetic_pattern"],
        source_static_suspected=outcome["source_static_suspected"],
        direct_mode_recovery_used=outcome["host_direct_mode_recovery_used"],
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
    run_dir = run_bundle_dir(Path(args.run_root))
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
        "require_client_ready": args.require_client_ready,
        "wine_debug_channels": args.wine_debug_channels,
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
            run_dir / "logs/avp-app-restart.log",
            timeout_seconds=30,
        )
        meta["avp_restart_exit_code"] = avp_restart_rc
        write_json(run_dir / "config/meta.json", meta)

    patch_steamvr_settings(
        paths["steamvr_settings"],
        run_dir,
        enable_home_app=(args.steamvr_home == "on"),
        direct_mode=args.direct_mode,
    )
    patch_session_contract(
        paths["alvr_session"],
        run_dir,
        args.codec,
        args.stream_protocol,
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
    current_debugger = query_aedebug_debugger(
        cxstart,
        run_dir / "logs/aedebug-query-after.log",
        env=launch_env,
    )
    meta["aedebug_previous_debugger"] = previous_debugger
    meta["aedebug_set_exit_code"] = aedebug_set_rc
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

    deadline = time.monotonic() + args.capture_seconds
    prompted = False
    while time.monotonic() < deadline:
        elapsed = args.capture_seconds - (deadline - time.monotonic())
        minimize_crossover_windows()
        if not args.host_only and not prompted and elapsed >= args.prompt_at_seconds:
            print(
                "ACTION: Keep ALVR frontmost on AVP for ~20 seconds. "
                "If an Enter button appears, tap it once."
            )
            prompted = True
        time.sleep(1)

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
        if args.direct_mode == "on" and outcome["host_direct_mode_disabled"]:
            fail_gate("host_direct_mode_disabled")
        if args.direct_mode == "on" and outcome["host_non_direct_source_enabled"]:
            fail_gate("host_non_direct_source_enabled")
        if args.direct_mode == "on" and outcome["host_direct_mode_recovery_used"]:
            fail_gate("host_direct_mode_recovery_used")

    if args.require_source_motion:
        has_fresh_motion = (
            outcome["source_fresh_encode_count"] >= 2
            and len(outcome["source_unique_fresh_sample_crcs"]) >= 2
        )
        has_bootstrap_motion = (
            outcome["source_bootstrap_refresh_count"] >= 2
            and len(outcome["source_bootstrap_colors"]) >= 2
        )
        if not has_fresh_motion and not has_bootstrap_motion:
            if outcome["source_fresh_encode_count"] < 2 and outcome["source_bootstrap_refresh_count"] < 2:
                fail_gate("source_fresh_encode_missing")
            fail_gate("source_motion_missing")

    if args.forbid_known_synthetic_source and outcome["source_known_synthetic_pattern"]:
        fail_gate("source_known_synthetic_pattern")

    if (
        args.forbid_static_source
        and outcome["source_bootstrap_refresh_count"] == 0
        and outcome["source_static_suspected"]
    ):
        fail_gate("source_static_suspected")

    if outcome["client_ui_block_summary"]:
        print(f"CLIENT_UI_DIAG: {outcome['client_ui_block_summary']}")

    write_json(run_dir / "config/outcome.json", outcome)
    print(json.dumps(outcome, indent=2))

    terminate_process(vtbridge_proc)
    if dashboard_started_here and dashboard_proc is not None:
        terminate_process(dashboard_proc)

    postflight_rc = run_cleanup_script(
        sys.executable,
        cleanup_script,
        run_dir / "logs/postflight-cleanup.log",
        args.sterile_native_steam,
    )
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
        default="udp",
        help="ALVR stream transport protocol to set in session contract",
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
        "--steamvr-tool",
        choices=[
            "none",
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
