#!/usr/bin/env python3

"""Run strict direct-mode validation across multiple graphics backends.

This keeps direct-mode work evidence-driven by executing a fixed strict command
for each backend and emitting a compact blocker report.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def read_last_run_dir() -> Path | None:
    marker_path = Path("/tmp/current_live_run.txt")
    if not marker_path.exists():
        return None
    run_dir_text = marker_path.read_text(encoding="utf-8", errors="replace").strip()
    if not run_dir_text:
        return None
    return Path(run_dir_text)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def collect_signatures(run_dir: Path, outcome: dict[str, Any] | None) -> dict[str, bool]:
    logs_dir = run_dir / "logs"
    session_text = read_text_if_exists(logs_dir / "session_log.delta.txt")
    if not session_text:
        session_text = read_text_if_exists(logs_dir / "session_log.txt")
    compositor_text = read_text_if_exists(logs_dir / "vrclient_vrcompositor.delta.txt")
    if not compositor_text:
        compositor_text = read_text_if_exists(logs_dir / "vrclient_vrcompositor.txt")

    gate_failures: list[str] = []
    interop_signature = ""
    if outcome is not None:
        gate_failures = [str(item) for item in outcome.get("gate_failures", [])]
        interop_signature = str(outcome.get("interop_signature", ""))

    return {
        "steamvr_external_memory_extensions_missing": (
            "steamvr_external_memory_extensions_missing" in gate_failures
            or "VK_KHR_external_memory_win32" in compositor_text
            or "VK_KHR_win32_keyed_mutex" in compositor_text
        ),
        "get_shared_handle_failed": "GetSharedHandle failed" in session_text,
        "idxgi_resource1_query_failed": "QueryInterface IDXGIResource1 failed" in session_text,
        "create_swap_texture_set_failed": "CreateSwapTextureSet failed" in session_text,
        "direct_mode_recovery_source_active": "host_direct_mode_recovery_used" in gate_failures
        or interop_signature == "direct_mode_recovery_source_active",
    }


def collect_signatures_from_output(text: str) -> dict[str, bool]:
    return {
        "steamvr_external_memory_extensions_missing": (
            "VK_KHR_external_memory_win32" in text
            or "VK_KHR_win32_keyed_mutex" in text
        ),
        "get_shared_handle_failed": "GetSharedHandle failed" in text,
        "idxgi_resource1_query_failed": "QueryInterface IDXGIResource1 failed" in text,
        "create_swap_texture_set_failed": "CreateSwapTextureSet failed" in text,
        "direct_mode_recovery_source_active": "host_direct_mode_recovery_used" in text,
    }


def rank_next_patches(results: list[dict[str, Any]]) -> list[str]:
    has_vk_ext_blocker = any(
        result["signatures"].get("steamvr_external_memory_extensions_missing", False)
        for result in results
    )
    has_get_shared_handle_blocker = any(
        result["signatures"].get("get_shared_handle_failed", False)
        for result in results
    )
    has_idxgi_resource1_blocker = any(
        result["signatures"].get("idxgi_resource1_query_failed", False)
        for result in results
    )
    has_recovery_source_blocker = any(
        result["signatures"].get("direct_mode_recovery_source_active", False)
        for result in results
    )

    ranked: list[str] = []
    if has_vk_ext_blocker:
        ranked.append(
            "Highest impact: validate direct-mode on a runtime/backend exposing "
            "VK_KHR_external_memory_win32 + VK_KHR_win32_keyed_mutex to separate "
            "platform capability gaps from ALVR driver behavior."
        )
    if has_get_shared_handle_blocker or has_idxgi_resource1_blocker:
        ranked.append(
            "Next ALVR patch: add an explicit shared-resource bridge path that does not "
            "depend on IDXGIResource1/GetSharedHandle success for every swap texture "
            "(log opened handle type and fallback branch per layer)."
        )
    if has_recovery_source_blocker:
        ranked.append(
            "Direct-mode path is still recovering from missing host frames; prioritize "
            "restoring real direct-mode compositor submissions before any fallback source "
            "is allowed."
        )
    if not ranked:
        ranked.append(
            "No dominant blocker signature found; capture longer runs and collect "
            "per-backend direct-mode submission telemetry before the next patch."
        )
    return ranked


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run strict direct-mode validation matrix and emit blocker report",
    )
    parser.add_argument(
        "--graphics-backends",
        nargs="+",
        choices=["dxvk", "d3dmetal"],
        default=["d3dmetal"],
        help="backends to evaluate in order (d3dmetal is the supported direct-mode path)",
    )
    parser.add_argument(
        "--capture-seconds",
        type=int,
        default=120,
        help="capture window passed through to live_avp_checkpoint.py",
    )
    parser.add_argument(
        "--steamvr-home",
        choices=["off", "on"],
        default="on",
        help="SteamVR Home policy used while generating source motion",
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
        default="steamvr_room_setup",
        help="tool app to launch for deterministic source activity",
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="extra args forwarded to live_avp_checkpoint.py after '--'",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    checkpoint_script = repo_root() / "tools" / "live_avp_checkpoint.py"

    forwarded = list(args.extra_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    matrix_dir = repo_root() / "temp" / "vr_runs" / f"{utc_stamp()}-directmode-matrix"
    matrix_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    overall_exit_code = 0

    for backend in args.graphics_backends:
        command = [
            sys.executable,
            str(checkpoint_script),
            "--sterile-native-steam",
            "--host-only",
            "--direct-mode",
            "on",
            "--graphics-backend",
            backend,
            "--capture-seconds",
            str(args.capture_seconds),
            "--steamvr-home",
            args.steamvr_home,
            "--steamvr-tool",
            args.steamvr_tool,
            "--synthetic-fallback",
            "disable",
            "--host-idle-fallback",
            "disable",
            "--forbid-host-idle-fallback",
            "--require-source-motion",
            "--require-host-frame-signals",
            "--require-direct-mode-healthy",
            "--forbid-static-source",
            "--forbid-known-synthetic-source",
            "--require-pass",
        ]
        command.extend(forwarded)

        print(f"MATRIX_BACKEND={backend}")
        print("COMMAND:", " ".join(command))
        before_run_dir = read_last_run_dir()
        completed = subprocess.run(command, check=False, capture_output=True, text=True)

        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)

        run_dir = read_last_run_dir()
        no_new_run = (
            completed.returncode != 0
            and run_dir is not None
            and before_run_dir is not None
            and run_dir == before_run_dir
        )
        if no_new_run:
            run_dir = None

        outcome = read_json(run_dir / "config" / "outcome.json") if run_dir is not None else None
        if run_dir is not None:
            signatures = collect_signatures(run_dir, outcome)
        else:
            combined_output = (completed.stdout or "") + "\n" + (completed.stderr or "")
            signatures = collect_signatures_from_output(combined_output)

        gate_failures: list[str] = []
        pass_value: bool | None = None
        if outcome is not None:
            gate_failures = [str(item) for item in outcome.get("gate_failures", [])]
            pass_field = outcome.get("pass")
            pass_value = bool(pass_field) if isinstance(pass_field, bool) else None
            interop_signature = str(outcome.get("interop_signature", ""))
        else:
            interop_signature = ""

        result = {
            "backend": backend,
            "return_code": completed.returncode,
            "run_dir": str(run_dir) if run_dir is not None else None,
            "pass": pass_value,
            "gate_failures": gate_failures,
            "interop_signature": interop_signature,
            "signatures": signatures,
        }
        print(json.dumps(result, indent=2))
        results.append(result)

        if completed.returncode != 0 and overall_exit_code == 0:
            overall_exit_code = completed.returncode

    report = {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "profile": "strict_direct_mode_matrix",
        "results": results,
        "ranked_next_patches": rank_next_patches(results),
    }

    report_path = matrix_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"REPORT={report_path}")
    print(json.dumps(report, indent=2))
    return overall_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
