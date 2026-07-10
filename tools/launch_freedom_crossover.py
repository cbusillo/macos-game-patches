#!/usr/bin/env python3
"""Launch Freedom Locomotion VR through CrossOver and hide its windows."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import threading
import time
from typing import Any


DEFAULT_EXE = (
    r"C:\Program Files (x86)\Steam\steamapps\common\Freedom Locomotion VR"
    r"\FreedomLocomotion\Binaries\Win64\FreedomLocomotion-Win64-Shipping.exe"
)

DEFAULT_WINE = (
    "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/"
    "CrossOver-Hosted Application/wine"
)

PROCESS_NAMES = (
    "FreedomLocomotion-Win64-Shipping",
    "FreedomLocomotion-Win64-Shipping.exe",
)

WINDOW_TITLE_PATTERNS = (
    "Freedom Locomotion VR",
    "FreedomLocomotion",
)

HIDE_TIMEOUT_SECONDS = 2.0
SCRIPT_MARKER = "alvr-freedom-hide-helper"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bottle", default="Steam", help="CrossOver bottle name")
    parser.add_argument("--wine", default=DEFAULT_WINE, help="CrossOver-hosted wine executable")
    parser.add_argument("--exe", default=DEFAULT_EXE, help="Windows executable to launch")
    parser.add_argument("--inner-crop-px", type=int, default=224, help="ALVR_SHIM_INNER_CROP_PX value")
    parser.add_argument(
        "--hide-seconds",
        type=float,
        default=None,
        help="How long to keep hiding launch windows; defaults to the launched process lifetime",
    )
    parser.add_argument("--hide-interval", type=float, default=0.5, help="Seconds between hide attempts")
    parser.add_argument("--verbose-hide", action="store_true", help="Print hide/minimize attempts")
    parser.add_argument("--no-hide", action="store_true", help="Launch without hiding/minimizing windows")
    return parser.parse_args()


def applescript_list(values: tuple[str, ...]) -> str:
    return "{" + ", ".join(f'"{value}"' for value in values) + "}"


def terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        process.kill()


def hide_windows(
    process_names: tuple[str, ...],
    window_title_patterns: tuple[str, ...],
    verbose: bool,
) -> None:
    script = f'''
-- {SCRIPT_MARKER}
set targetProcessNames to {applescript_list(process_names)}
set targetWindowPatterns to {applescript_list(window_title_patterns)}

on textMatchesAnyExactly(valueText, candidates)
    repeat with candidateText in candidates
        if valueText is (candidateText as text) then return true
    end repeat
    return false
end textMatchesAnyExactly

on textMatchesAny(valueText, patterns)
    repeat with patternText in patterns
        if valueText contains (patternText as text) then return true
    end repeat
    return false
end textMatchesAny

tell application "System Events"
    repeat with proc in application processes
        try
            set processName to name of proc as text
            if my textMatchesAnyExactly(processName, targetProcessNames) then
                set visible of proc to false
                repeat with win in windows of proc
                    try
                        set miniaturized of win to true
                    end try
                end repeat
                log "hid process " & processName
            else
                repeat with win in windows of proc
                    set windowName to name of win as text
                    if my textMatchesAny(windowName, targetWindowPatterns) then
                        try
                            set miniaturized of win to true
                        end try
                        try
                            set visible of proc to false
                        end try
                        log "hid window " & windowName & " in process " & processName
                    end if
                end repeat
            end if
        end try
    end repeat
end tell
'''
    process = subprocess.Popen(
        ["osascript", "-e", script],
        stdout=None if verbose else subprocess.DEVNULL,
        stderr=None if verbose else subprocess.DEVNULL,
        text=True,
    )
    try:
        process.wait(timeout=HIDE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        terminate_process(process)
        return


def supervise_hidden_windows(
    process: subprocess.Popen[bytes],
    process_names: tuple[str, ...],
    window_title_patterns: tuple[str, ...],
    interval: float,
    hide_seconds: float | None,
    verbose: bool,
    stop_event: threading.Event,
) -> None:
    deadline = None if hide_seconds is None else time.monotonic() + hide_seconds
    while process.poll() is None and not stop_event.is_set():
        if deadline is not None and time.monotonic() >= deadline:
            break
        hide_windows(process_names, window_title_patterns, verbose)
        stop_event.wait(max(interval, 0.1))


def main() -> int:
    args = parse_args()
    env = os.environ.copy()
    env["CX_BOTTLE"] = args.bottle
    env["ALVR_SHIM_INNER_CROP_PX"] = str(args.inner_crop_px)

    signal.signal(signal.SIGTERM, lambda _signum, _frame: raise_keyboard_interrupt())
    process = subprocess.Popen([args.wine, args.exe], env=env)
    stop_event = threading.Event()
    supervisor = None
    if not args.no_hide and args.hide_seconds != 0:
        supervisor = threading.Thread(
            target=supervise_hidden_windows,
            args=(
                process,
                PROCESS_NAMES,
                WINDOW_TITLE_PATTERNS,
                args.hide_interval,
                args.hide_seconds,
                args.verbose_hide,
                stop_event,
            ),
            daemon=True,
        )
        supervisor.start()

    try:
        return process.wait()
    except KeyboardInterrupt:
        terminate_process(process)
        return 130
    finally:
        stop_event.set()
        if supervisor is not None:
            supervisor.join(timeout=max(args.hide_interval, 0.1) + HIDE_TIMEOUT_SECONDS)


def raise_keyboard_interrupt() -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    raise SystemExit(main())
