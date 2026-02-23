#!/usr/bin/env python3

"""One-shot release gate for the currently supported AVP pipeline.

This runner executes the strict non-direct production gate, writes machine-
readable artifacts for CI, and optionally captures direct-mode blocker matrix
evidence without turning that R&D path into a release requirement.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def parse_run_dirs(text: str) -> list[str]:
    run_dirs: list[str] = []
    for match in re.finditer(r"^RUN_DIR\[\d+\]=(.*)$", text, re.MULTILINE):
        run_dir = match.group(1).strip()
        if run_dir:
            run_dirs.append(run_dir)
    return run_dirs


def parse_report_path(text: str) -> str | None:
    match = re.search(r"^REPORT=(.*)$", text, re.MULTILINE)
    if match is None:
        return None
    report_path = match.group(1).strip()
    return report_path or None


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    # Preserve subprocess output in parent logs for interactive debugging.
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    return {
        "command": command,
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def write_markdown_summary(path: Path, summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# AVP Release Gate Summary")
    lines.append("")
    lines.append(f"- Captured at: `{summary['captured_at_utc']}`")
    lines.append(f"- Gate pass: `{str(summary['gate_pass']).lower()}`")
    lines.append(f"- Non-direct return code: `{summary['non_direct']['return_code']}`")

    run_dirs = summary["non_direct"].get("run_dirs", [])
    if run_dirs:
        lines.append("- Non-direct runs:")
        for run_dir in run_dirs:
            lines.append(f"  - `{run_dir}`")

    for run in summary["non_direct"].get("runs", []):
        run_dir = run.get("run_dir", "<unknown>")
        pass_value = run.get("pass")
        ui_summary = run.get("client_ui_block_summary")
        lines.append("")
        lines.append(f"## Run `{run_dir}`")
        lines.append(f"- pass: `{str(pass_value).lower()}`")
        lines.append(f"- gate_failures: `{run.get('gate_failures', [])}`")
        lines.append(
            "- client_ready: "
            f"`{str(run.get('client_ready')).lower()}`; "
            "delay_s: "
            f"`{run.get('client_streaming_start_delay_seconds')}`"
        )
        lines.append(
            "- host_idle_fallback_enabled: "
            f"`{run.get('host_idle_fallback_enabled')}` "
            "(inferred: "
            f"`{str(run.get('host_idle_fallback_enabled_inferred')).lower()}`)"
        )
        if ui_summary:
            lines.append(f"- client_ui_block_summary: `{ui_summary}`")

    matrix = summary.get("direct_mode_matrix")
    if matrix is not None:
        lines.append("")
        lines.append("## Direct-Mode Matrix")
        lines.append(f"- return_code: `{matrix.get('return_code')}`")
        report_path = matrix.get("report_path")
        if report_path:
            lines.append(f"- report: `{report_path}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one-shot AVP release gate and emit CI artifacts",
    )
    parser.add_argument(
        "--capture-seconds",
        type=int,
        default=60,
        help="capture window for each strict non-direct run",
    )
    parser.add_argument(
        "--graphics-backend",
        choices=["dxvk", "d3dmetal"],
        default="dxvk",
        help="graphics backend for non-direct production gate",
    )
    parser.add_argument(
        "--steamvr-home",
        choices=["off", "on"],
        default="on",
        help="SteamVR Home policy for non-direct gate",
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
        help="SteamVR tool app for source-motion generation",
    )
    parser.add_argument(
        "--include-directmode-matrix",
        action="store_true",
        help="also run strict direct-mode matrix and attach its report",
    )
    parser.add_argument(
        "--matrix-capture-seconds",
        type=int,
        default=60,
        help="capture window for direct-mode matrix runs",
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(repo_root() / "temp" / "pipeline_reports"),
        help="artifact output directory",
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="extra args forwarded to underlying strict commands after '--'",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = repo_root()

    forwarded = list(args.extra_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    nondirect_cmd = [
        sys.executable,
        str(root / "tools" / "live_avp_nondirect_prod.py"),
        "--confirm-twice",
        "--capture-seconds",
        str(args.capture_seconds),
        "--graphics-backend",
        args.graphics_backend,
        "--steamvr-home",
        args.steamvr_home,
        "--steamvr-tool",
        args.steamvr_tool,
    ]
    nondirect_cmd.extend(forwarded)

    non_direct_exec = run_command(nondirect_cmd)
    non_direct_output = (non_direct_exec["stdout"] or "") + "\n" + (non_direct_exec["stderr"] or "")
    run_dirs = parse_run_dirs(non_direct_output)

    run_results: list[dict[str, Any]] = []
    for run_dir_text in run_dirs:
        run_dir = Path(run_dir_text)
        outcome = read_json(run_dir / "config" / "outcome.json") or {}
        run_results.append(
            {
                "run_dir": run_dir_text,
                "pass": outcome.get("pass"),
                "gate_failures": outcome.get("gate_failures", []),
                "client_ready": outcome.get("client_ready"),
                "client_streaming_start_delay_seconds": outcome.get(
                    "client_streaming_start_delay_seconds"
                ),
                "client_ui_block_summary": outcome.get("client_ui_block_summary"),
                "host_idle_fallback_enabled": outcome.get("host_idle_fallback_enabled"),
                "host_idle_fallback_enabled_inferred": outcome.get(
                    "host_idle_fallback_enabled_inferred"
                ),
            }
        )

    gate_pass = (
        non_direct_exec["return_code"] == 0
        and len(run_results) >= 2
        and all(run.get("pass") is True for run in run_results)
    )

    summary: dict[str, Any] = {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "gate_pass": gate_pass,
        "non_direct": {
            "command": non_direct_exec["command"],
            "return_code": non_direct_exec["return_code"],
            "run_dirs": run_dirs,
            "runs": run_results,
        },
    }

    if args.include_directmode_matrix:
        matrix_cmd = [
            sys.executable,
            str(root / "tools" / "live_avp_directmode_matrix.py"),
            "--capture-seconds",
            str(args.matrix_capture_seconds),
        ]
        matrix_cmd.extend(forwarded)
        matrix_exec = run_command(matrix_cmd)
        matrix_output = (matrix_exec["stdout"] or "") + "\n" + (matrix_exec["stderr"] or "")
        report_path = parse_report_path(matrix_output)
        matrix_report = read_json(Path(report_path)) if report_path else None
        summary["direct_mode_matrix"] = {
            "command": matrix_exec["command"],
            "return_code": matrix_exec["return_code"],
            "report_path": report_path,
            "report": matrix_report,
        }

    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()

    json_path = artifact_dir / f"{stamp}-release-gate.json"
    md_path = artifact_dir / f"{stamp}-release-gate.md"
    latest_json_path = artifact_dir / "latest-release-gate.json"
    latest_md_path = artifact_dir / "latest-release-gate.md"

    json_text = json.dumps(summary, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json_path.write_text(json_text, encoding="utf-8")

    write_markdown_summary(md_path, summary)
    write_markdown_summary(latest_md_path, summary)

    print(f"RELEASE_GATE_JSON={json_path}")
    print(f"RELEASE_GATE_MD={md_path}")
    print(f"RELEASE_GATE_PASS={str(gate_pass).lower()}")

    return 0 if gate_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())

