#!/usr/bin/env python3

"""One-command first-game attempt for AVP.

This favors a practical "show any game" path over strict probe-gated CI checks.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run(command: list[str]) -> int:
    print("+", " ".join(command))
    return subprocess.run(command, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attempt first in-headset game frame on AVP",
    )
    parser.add_argument("--steam-app-id", type=int, default=450390, help="Steam app id (default: The Lab)")
    parser.add_argument("--capture-seconds", type=int, default=420, help="capture window for the attempt")
    parser.add_argument(
        "--manual-client-host",
        default="5130.client.local..alvr",
        help="manual ALVR client host key",
    )
    parser.add_argument("--manual-client-ip", default="192.168.1.6", help="manual AVP IP")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = repo_root()

    cleanup = run([sys.executable, str(root / "tools/vr_stack_cleanup.py"), "--sterile-native-steam"])
    if cleanup != 0:
        print("ERROR: cleanup failed")
        return cleanup

    restart = run([sys.executable, str(root / "tools/avp_alvr_control.py"), "restart"])
    if restart != 0:
        print("ERROR: failed to restart ALVR app on AVP")
        return restart

    print("ACTION REQUIRED: Put on AVP, keep ALVR frontmost, tap Enter once if shown.")

    checkpoint_cmd = [
        sys.executable,
        str(root / "tools/live_avp_checkpoint.py"),
        "--sterile-native-steam",
        "--no-restart-avp-app",
        "--capture-seconds",
        str(args.capture_seconds),
        "--direct-mode",
        "off",
        "--graphics-backend",
        "dxvk",
        "--steamvr-home",
        "off",
        "--steamvr-tool",
        "none",
        "--steam-app-id",
        str(args.steam_app_id),
        "--steam-app-force-vr",
        "--steam-app-delay-seconds",
        "12",
        "--synthetic-fallback",
        "disable",
        "--host-idle-fallback",
        "disable",
        "--manual-client-host",
        args.manual_client_host,
        "--manual-client-ip",
        args.manual_client_ip,
    ]
    return run(checkpoint_cmd)


if __name__ == "__main__":
    raise SystemExit(main())

