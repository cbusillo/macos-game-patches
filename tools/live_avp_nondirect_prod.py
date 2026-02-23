#!/usr/bin/env python3

"""Run the known-good strict non-direct AVP validation profile.

This wrapper codifies the current production fallback path so operators can run
the same strict contract every time without rebuilding long command lines.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run strict non-direct production profile for AVP validation",
    )
    parser.add_argument(
        "--graphics-backend",
        choices=["dxvk", "d3dmetal"],
        default="dxvk",
        help="CrossOver backend (dxvk is the currently locked-good profile)",
    )
    parser.add_argument(
        "--capture-seconds",
        type=int,
        default=200,
        help="capture window passed through to live_avp_checkpoint.py",
    )
    parser.add_argument(
        "--wine-debug-channels",
        default="",
        help="optional WINEDEBUG channels forwarded to live_avp_checkpoint.py",
    )
    parser.add_argument(
        "--steamvr-home",
        choices=["off", "on"],
        default="on",
        help="SteamVR Home policy used to produce non-static source motion",
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
        default="steamvr_overlay_viewer",
        help="tool app to launch for deterministic source motion (default avoids narrated Room Setup prompts)",
    )
    parser.add_argument(
        "--confirm-twice",
        action="store_true",
        help="run the strict profile twice back-to-back and require both runs to pass",
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="extra args forwarded to live_avp_checkpoint.py after '--'",
    )
    return parser


def read_last_run_outcome() -> tuple[str | None, bool | None]:
    marker_path = Path("/tmp/current_live_run.txt")
    if not marker_path.exists():
        return None, None

    run_dir = marker_path.read_text(encoding="utf-8", errors="replace").strip()
    if not run_dir:
        return None, None

    outcome_path = Path(run_dir) / "config" / "outcome.json"
    if not outcome_path.exists():
        return run_dir, None

    try:
        outcome = json.loads(outcome_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return run_dir, None

    pass_value = outcome.get("pass")
    return run_dir, bool(pass_value) if isinstance(pass_value, bool) else None


def main() -> int:
    args = build_parser().parse_args()

    checkpoint_script = repo_root() / "tools" / "live_avp_checkpoint.py"
    command = [
        sys.executable,
        str(checkpoint_script),
        "--sterile-native-steam",
        "--direct-mode",
        "off",
        "--graphics-backend",
        args.graphics_backend,
        "--wine-debug-channels",
        args.wine_debug_channels,
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
        "--require-client-ready",
        "--require-client-video-present",
        "--forbid-synthetic-fallback",
        "--forbid-host-idle-fallback",
        "--require-real-decode",
        "--require-source-motion",
        "--require-host-frame-signals",
        "--forbid-static-source",
        "--forbid-known-synthetic-source",
        "--require-pass",
    ]

    forwarded = list(args.extra_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    command.extend(forwarded)

    run_count = 2 if args.confirm_twice else 1
    print("PROFILE: strict non-direct production")
    print("COMMAND:", " ".join(command))

    run_dirs: list[str] = []
    for run_index in range(1, run_count + 1):
        print(f"RUN_ATTEMPT={run_index}/{run_count}")
        completed = subprocess.run(command, check=False)
        run_dir, pass_value = read_last_run_outcome()
        if run_dir is not None:
            run_dirs.append(run_dir)
            print(f"RUN_DIR[{run_index}]={run_dir}")
        if pass_value is not None:
            print(f"RUN_PASS[{run_index}]={str(pass_value).lower()}")

        if completed.returncode != 0:
            return completed.returncode
        if pass_value is False:
            return 2

    if run_count > 1 and run_dirs:
        print("CONFIRMATION_RUNS=", " ".join(run_dirs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
