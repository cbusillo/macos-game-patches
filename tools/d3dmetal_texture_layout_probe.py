#!/usr/bin/env python3
"""Attach LLDB to a running Wine/CrossOver process and dump DXGIResource layout.

This is a reconnaissance helper for D3DMetal direct-mode work. It waits for
`DXGIResource::GetSharedHandle()` to be hit, then prints object-field pointers
and best-effort Objective-C class names for each field.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


DEFAULT_SYMBOL = "__ZN12DXGIResource15GetSharedHandleEPPv"
DEFAULT_PATTERN = "vrcompositor.exe"


def find_pid(pattern: str) -> int:
    proc = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    regex = re.compile(pattern)
    self_pid = os.getpid()
    candidates: list[int] = []
    for line in proc.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        try:
            pid_str, cmd = line.split(maxsplit=1)
        except ValueError:
            continue
        if regex.search(cmd):
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            if pid == self_pid:
                continue
            if "d3dmetal_texture_layout_probe.py" in cmd:
                continue
            candidates.append(pid)
    if not candidates:
        raise RuntimeError(f"no process matched pattern: {pattern!r}")
    return max(candidates)


def build_lldb_script(pid: int, symbol: str, slots: int, probe_iosurface: bool) -> str:
    lines = [
        "settings set target.process.stop-on-sharedlibrary-events false",
        f"process attach --pid {pid}",
        f"breakpoint set --name {symbol}",
        "continue",
        "thread backtrace",
        "register read rdi rsi rdx rcx",
        "expr -l c -- (void*)$rdi",
        "expr -l c -- (void*)$rsi",
    ]
    if probe_iosurface:
        lines.extend(
            [
                "expr -l objc++ -- @import Foundation",
                "expr -l objc++ -- @import IOSurface",
            ]
        )
    for idx in range(slots):
        offset = idx * 8
        ptr_expr = f"(void*)*(void**)((uintptr_t)$rdi + {offset})"
        class_expr = (
            "(const char *)object_getClassName((id)*(void**)((uintptr_t)$rdi + "
            f"{offset}))"
        )
        lines.append(f"expr -l c -- {ptr_expr}")
        lines.append(f"expr -l objc++ -O -- {class_expr}")
        if probe_iosurface:
            iosurface_expr = (
                "({ "
                f"id obj = (id)*(void**)((uintptr_t)$rdi + {offset}); "
                "unsigned int sid = 0; "
                "if (obj && [obj respondsToSelector:@selector(iosurface)]) { "
                "id surf = [obj iosurface]; "
                "if (surf) sid = IOSurfaceGetID((IOSurfaceRef)surf); "
                "} sid; })"
            )
            lines.append(f"expr -l objc++ -O -- {iosurface_expr}")
    lines.append("quit")
    return "\n".join(lines) + "\n"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def default_output_path() -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    return Path("temp/probes") / f"d3dmetal_texture_layout_probe-{stamp}.log"


def _as_text(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pid", type=int, help="PID to attach to")
    p.add_argument(
        "--process-pattern",
        default=DEFAULT_PATTERN,
        help="Regex used to find PID when --pid is omitted",
    )
    p.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Breakpoint symbol")
    p.add_argument(
        "--slots",
        type=int,
        default=20,
        help="Number of 8-byte fields from `this` pointer to inspect",
    )
    p.add_argument(
        "--lldb-bin",
        default="lldb",
        help="LLDB executable path/name",
    )
    p.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="Timeout for the LLDB session",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="Output log path",
    )
    p.add_argument(
        "--probe-iosurface",
        action="store_true",
        help="attempt to resolve IOSurface IDs for candidate Objective-C fields",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    try:
        pid = args.pid if args.pid is not None else find_pid(args.process_pattern)
    except Exception as exc:
        print(f"ERROR: failed to resolve target PID: {exc}")
        return 2

    lldb_script = build_lldb_script(
        pid=pid,
        symbol=args.symbol,
        slots=args.slots,
        probe_iosurface=args.probe_iosurface,
    )

    ensure_parent(args.output)

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".lldb") as tf:
        tf.write(lldb_script)
        script_path = tf.name

    cmd = [args.lldb_bin, "-b", "-s", script_path]
    print(f"Target PID: {pid}")
    print(f"LLDB cmd : {' '.join(shlex.quote(c) for c in cmd)}")
    print(f"Output   : {args.output}")

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_out = (
            f"TIMEOUT after {args.timeout_seconds}s\n"
            f"{_as_text(exc.stdout)}\n{_as_text(exc.stderr)}"
        )
        args.output.write_text(timeout_out)
        os.unlink(script_path)
        print(f"ERROR: LLDB timed out; partial output written to {args.output}")
        return 3

    combined = (proc.stdout or "") + (proc.stderr or "")
    args.output.write_text(combined)
    os.unlink(script_path)

    print(f"LLDB exit : {proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
