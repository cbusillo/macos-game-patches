#!/usr/bin/env python3

import argparse
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_dir(base_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    result = base_dir / f"{stamp}-h2-vtbridge-hw-stream"
    (result / "logs").mkdir(parents=True, exist_ok=True)
    return result


def require_python() -> str:
    path = shutil.which("python3")
    if path is None:
        raise RuntimeError("Missing required executable: python3")
    return path


def run_gate(run_root: Path, port: int) -> int:
    python = require_python()
    bundle = run_dir(run_root)
    daemon_log = bundle / "logs" / "daemon.log"
    ring_path = bundle / "logs" / "ring.bin"

    daemon_cmd = [
        python,
        str(repo_root() / "tools" / "vtbridge_daemon.py"),
        "--port",
        str(port),
        "--accept-configure",
        "--report-hardware-active",
        "--enforce-hw-hevc",
        "--ring-path",
        str(ring_path),
    ]

    daemon_file = daemon_log.open("w", encoding="utf-8")
    daemon_proc = subprocess.Popen(
        daemon_cmd,
        stdout=daemon_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        subprocess.run(["sleep", "0.5"], check=True)

        conformance_cmd = [
            python,
            str(repo_root() / "tools" / "vtbridge_ring_conformance.py"),
            "--external-daemon",
            "--port",
            str(port),
            "--ring-path",
            str(ring_path),
        ]
        conformance = subprocess.run(
            conformance_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        (bundle / "logs" / "conformance.log").write_text(
            (conformance.stdout or "") + (conformance.stderr or ""),
            encoding="utf-8",
        )
    finally:
        daemon_proc.terminate()
        try:
            daemon_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()
            daemon_proc.wait(timeout=2)
        daemon_file.close()

    daemon_text = daemon_log.read_text(encoding="utf-8", errors="replace")
    failed_markers = [
        "hevc_probe passed=0",
        "frame encode failed",
        "hardware encoder required",
        "configure rejected",
    ]

    passed = conformance.returncode == 0
    passed = passed and "hevc_probe passed=1" in daemon_text
    for marker in failed_markers:
        if marker in daemon_text.lower():
            passed = False

    if not passed:
        print("FAIL: vtbridge hardware stream gate")
        print(f"Run bundle: {bundle}")
        return 1

    print("PASS: vtbridge hardware stream gate")
    print(f"Run bundle: {bundle}")
    return 0


def parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser(description="Run vtbridge hardware encode stream gate")
    arg_parser.add_argument(
        "--run-root",
        default=str(repo_root() / "temp" / "vr_runs"),
        help="directory where run bundles are created",
    )
    arg_parser.add_argument("--port", type=int, default=37342, help="daemon/conformance test port")
    return arg_parser


def main() -> int:
    args = parser().parse_args()
    return run_gate(Path(args.run_root), args.port)


if __name__ == "__main__":
    sys.exit(main())

