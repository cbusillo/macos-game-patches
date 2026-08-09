#!/usr/bin/env python3
"""Invoke MacOSUIHelper through Launch Services and return its JSON result."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path


DEFAULT_APP = (
    Path.home()
    / "Library"
    / "Application Support"
    / "MacOSGamePatches"
    / "UIHelper"
    / "MacOSUIHelper.app"
)
RESULT_ROOT = DEFAULT_APP.parent / "Results"
ALLOWED_COMMANDS = {
    "status",
    "request-accessibility",
    "request-screen-recording",
    "windows",
    "buttons",
    "screenshot",
    "press-button",
}


def fail(code: str, detail: str) -> int:
    print(json.dumps({"schema": 1, "ok": False, "error": {"code": code, "details": {"message": detail}}}, sort_keys=True))
    return 1


def read_result(path: Path) -> dict[str, object]:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("result is not an owner-controlled regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > 1_048_576:
            raise RuntimeError("result permissions or size violate the transport contract")
        chunks: list[bytes] = []
        remaining = 1_048_577
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(payload) > 1_048_576:
        raise RuntimeError("result exceeds the transport limit")
    parsed = json.loads(payload)
    if not isinstance(parsed, dict) or parsed.get("schema") != 1 or not isinstance(parsed.get("ok"), bool):
        raise RuntimeError("result does not match schema v1")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("command")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command not in ALLOWED_COMMANDS:
        return fail("client.command_not_allowed", args.command)
    app = args.app.expanduser().resolve()
    if app != DEFAULT_APP.resolve() or app.is_symlink() or not app.is_dir():
        return fail("client.app_invalid", str(app))
    token = uuid.uuid4().hex
    result_path = RESULT_ROOT / f"{token}.json"
    if result_path.exists() or result_path.is_symlink():
        return fail("client.result_collision", token)

    command = [
        "open", "-n", str(app), "--args", args.command,
        *args.arguments, "--result-token", token,
    ]
    timeout = max(1.0, min(args.timeout, 30.0))
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return fail("client.launch_timeout", f"Launch Services did not return after {timeout:.1f}s")
    if completed.returncode != 0:
        return fail("client.launch_failed", completed.stderr.strip())

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result_path.exists() or result_path.is_symlink():
            try:
                result = read_result(result_path)
            except (OSError, ValueError, RuntimeError) as error:
                return fail("client.result_invalid", str(error))
            finally:
                result_path.unlink(missing_ok=True)
            print(json.dumps(result, sort_keys=True))
            return 0 if result.get("ok") is True else 1
        time.sleep(0.05)
    return fail("client.timeout", f"no result after {args.timeout:.1f}s")


if __name__ == "__main__":
    raise SystemExit(main())
