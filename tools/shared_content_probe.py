#!/usr/bin/env python3
"""Build and run the Win32 shared-content probe across CrossOver backends.

This is a focused low-level diagnostic:
- API-level sharing can report success (GetSharedHandle/OpenSharedResource)
- but cross-process texture content can still be empty.

The script compiles `tools/win_shared_content_probe.c`, runs it under the
requested backend(s), and emits a JSON report suitable for debugging and CI
gates.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROBE_SOURCE = REPO_ROOT / "tools" / "win_shared_content_probe.c"
DEFAULT_PROBE_EXE = REPO_ROOT / "temp" / "probes" / "win_shared_content_probe.exe"
DEFAULT_PROBES_DIR = REPO_ROOT / "temp" / "probes"
DEFAULT_CXSTART = Path("/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart")


@dataclass
class BackendResult:
    backend: str
    scenario: str
    output_file: str
    exit_code: int
    create_texture_hr: str | None
    get_shared_handle_hr: str | None
    open_shared_hr: str | None
    open_shared1_hr: str | None
    same_process_pixel: str | None
    child_expected_pixel: str | None
    child_pixels: list[str]
    child_openread_exit: int | None
    child_openread1_exit: int | None
    child_open_shared_hr: str | None
    child_open_shared1_hr: str | None
    keyed_parent_qi_hr: str | None
    keyed_child_qi_hr: str | None
    keyed_parent_acquire_hr: str | None
    keyed_parent_release_hr: str | None
    keyed_child_acquire_hr: str | None
    keyed_child_release_hr: str | None
    api_surface_success: bool
    cross_process_content_ok: bool
    diagnosis: str


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None, stdout: Path | None = None) -> int:
    if stdout is None:
        return subprocess.run(cmd, cwd=cwd, env=env, check=False).returncode
    stdout.parent.mkdir(parents=True, exist_ok=True)
    with stdout.open("w", encoding="utf-8") as fh:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            check=False,
            stdout=fh,
            stderr=subprocess.STDOUT,
            text=True,
        ).returncode


def parse_hr(text: str, label: str) -> str | None:
    match = re.search(rf"^{re.escape(label)} hr=(0x[0-9a-fA-F]+)", text, flags=re.MULTILINE)
    return match.group(1).lower() if match else None


def parse_value(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}=([^\r\n]+)", text, flags=re.MULTILINE)
    return match.group(1).strip().lower() if match else None


def parse_exit(text: str, key: str) -> int | None:
    match = re.search(rf"^{re.escape(key)}=(\d+)", text, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def classify_result(backend: str, scenario: str, output_file: Path, exit_code: int) -> BackendResult:
    text = output_file.read_text(encoding="utf-8", errors="replace")
    child_pixels = [m.group(1).lower() for m in re.finditer(r"^\[child\] first_pixel_bgra=(0x[0-9a-fA-F]+)", text, flags=re.MULTILINE)]

    create_texture_hr = parse_hr(text, "CreateTexture2D")
    get_shared_handle_hr = parse_hr(text, "IDXGIResource::GetSharedHandle")
    open_shared_hr = parse_hr(text, "ID3D11Device::OpenSharedResource")
    open_shared1_hr = parse_hr(text, "ID3D11Device1::OpenSharedResource1")
    same_process_pixel = parse_value(text, "same_process_first_pixel_bgra")
    child_expected_pixel = parse_value(text, "[child] expected_bgra")
    child_openread_exit = parse_exit(text, "child_openread_exit")
    child_openread1_exit = parse_exit(text, "child_openread1_exit")
    child_open_shared_hr = parse_hr(text, "[child] ID3D11Device::OpenSharedResource")
    child_open_shared1_hr = parse_hr(text, "[child] ID3D11Device1::OpenSharedResource1")
    keyed_parent_qi_hr = parse_hr(text, "QI(IDXGIKeyedMutex,parent_source)")
    keyed_child_qi_hr = parse_hr(text, "[child] QI(IDXGIKeyedMutex)")
    keyed_parent_acquire_hr = parse_hr(text, "IDXGIKeyedMutex::AcquireSync(parent_source,0)")
    keyed_parent_release_hr = parse_hr(text, "IDXGIKeyedMutex::ReleaseSync(parent_source,1)")
    keyed_child_acquire_hr = parse_hr(text, "[child] IDXGIKeyedMutex::AcquireSync")
    keyed_child_release_hr = parse_hr(text, "[child] IDXGIKeyedMutex::ReleaseSync")

    api_surface_success = (
        get_shared_handle_hr == "0x00000000"
        and open_shared_hr == "0x00000000"
        and (open_shared1_hr in (None, "0x00000000"))
    )

    child_match = bool(child_pixels) and child_expected_pixel is not None and all(px == child_expected_pixel for px in child_pixels)
    child_exit_ok = (
        child_openread_exit == 0
        and (child_openread1_exit in (None, 0))
    )
    child_open_api_ok = (
        child_open_shared_hr in (None, "0x00000000")
        and child_open_shared1_hr in (None, "0x00000000")
    )
    cross_process_content_ok = child_match and child_exit_ok

    keyed_requested = scenario in {"shared_keyed", "shared_keyed_nthandle"}

    if not api_surface_success:
        diagnosis = "api_share_path_unavailable"
    elif not child_open_api_ok:
        diagnosis = "child_open_shared_failed"
    elif keyed_requested and keyed_parent_qi_hr not in (None, "0x00000000"):
        diagnosis = "keyed_mutex_interface_missing"
    elif keyed_requested and keyed_child_qi_hr not in (None, "0x00000000"):
        diagnosis = "keyed_mutex_interface_missing"
    elif keyed_requested and any(
        hr not in (None, "0x00000000")
        for hr in (
            keyed_parent_acquire_hr,
            keyed_parent_release_hr,
            keyed_child_acquire_hr,
            keyed_child_release_hr,
        )
    ):
        diagnosis = "keyed_mutex_sync_failed"
    elif not cross_process_content_ok:
        diagnosis = "api_success_content_not_shared"
    elif exit_code != 0:
        diagnosis = "probe_process_failed"
    else:
        diagnosis = "content_shared"

    return BackendResult(
        backend=backend,
        scenario=scenario,
        output_file=str(output_file),
        exit_code=exit_code,
        create_texture_hr=create_texture_hr,
        get_shared_handle_hr=get_shared_handle_hr,
        open_shared_hr=open_shared_hr,
        open_shared1_hr=open_shared1_hr,
        same_process_pixel=same_process_pixel,
        child_expected_pixel=child_expected_pixel,
        child_pixels=child_pixels,
        child_openread_exit=child_openread_exit,
        child_openread1_exit=child_openread1_exit,
        child_open_shared_hr=child_open_shared_hr,
        child_open_shared1_hr=child_open_shared1_hr,
        keyed_parent_qi_hr=keyed_parent_qi_hr,
        keyed_child_qi_hr=keyed_child_qi_hr,
        keyed_parent_acquire_hr=keyed_parent_acquire_hr,
        keyed_parent_release_hr=keyed_parent_release_hr,
        keyed_child_acquire_hr=keyed_child_acquire_hr,
        keyed_child_release_hr=keyed_child_release_hr,
        api_surface_success=api_surface_success,
        cross_process_content_ok=cross_process_content_ok,
        diagnosis=diagnosis,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-source", type=Path, default=DEFAULT_PROBE_SOURCE)
    parser.add_argument("--probe-exe", type=Path, default=DEFAULT_PROBE_EXE)
    parser.add_argument("--probes-dir", type=Path, default=DEFAULT_PROBES_DIR)
    parser.add_argument("--cxstart", type=Path, default=DEFAULT_CXSTART)
    parser.add_argument("--bottle", default="Steam")
    parser.add_argument("--compiler", default="x86_64-w64-mingw32-gcc")
    parser.add_argument(
        "--backends",
        nargs="+",
        choices=("d3dmetal", "dxvk"),
        default=("d3dmetal", "dxvk"),
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=("shared", "shared_keyed", "shared_keyed_nthandle", "shared_nthandle"),
        default=("shared", "shared_keyed", "shared_keyed_nthandle", "shared_nthandle"),
    )
    parser.add_argument(
        "--sterile-native-steam",
        action="store_true",
        help="run cleanup with --sterile-native-steam before each backend run",
    )
    parser.add_argument(
        "--skip-cleanup",
        action="store_true",
        help="skip required preflight cleanup (debug only)",
    )
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if not args.probe_source.exists():
        print(f"error: probe source not found: {args.probe_source}", file=sys.stderr)
        return 2
    if not args.cxstart.exists():
        print(f"error: cxstart not found: {args.cxstart}", file=sys.stderr)
        return 2
    if shutil.which(args.compiler) is None:
        print(f"error: compiler not found in PATH: {args.compiler}", file=sys.stderr)
        return 2

    args.probe_exe.parent.mkdir(parents=True, exist_ok=True)
    compile_cmd = [
        args.compiler,
        str(args.probe_source),
        "-O2",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-o",
        str(args.probe_exe),
        "-ld3d11",
        "-ldxgi",
        "-luuid",
    ]
    compile_rc = run(compile_cmd, cwd=REPO_ROOT)
    if compile_rc != 0:
        print("error: probe build failed", file=sys.stderr)
        return compile_rc

    cleanup_script = REPO_ROOT / "tools" / "vr_stack_cleanup.py"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results: list[BackendResult] = []

    for backend in args.backends:
        for scenario in args.scenarios:
            if not args.skip_cleanup:
                cleanup_cmd = [sys.executable, str(cleanup_script)]
                if args.sterile_native_steam:
                    cleanup_cmd.append("--sterile-native-steam")
                cleanup_rc = run(cleanup_cmd, cwd=REPO_ROOT)
                if cleanup_rc != 0:
                    print(
                        f"error: cleanup failed before backend={backend} scenario={scenario} rc={cleanup_rc}",
                        file=sys.stderr,
                    )
                    return cleanup_rc

            output_file = args.probes_dir / f"probe-content-{backend}-{scenario}-{stamp}.stdout"
            env = dict(os.environ)
            env["CX_GRAPHICS_BACKEND"] = backend
            run_rc = run(
                [str(args.cxstart), "--bottle", args.bottle, "--no-gui", str(args.probe_exe), "scenario", scenario],
                cwd=REPO_ROOT,
                env=env,
                stdout=output_file,
            )
            results.append(classify_result(backend, scenario, output_file, run_rc))

    payload = {
        "probe_source": str(args.probe_source),
        "probe_exe": str(args.probe_exe),
        "timestamp": stamp,
        "results": [asdict(r) for r in results],
    }

    report_text = json.dumps(payload, indent=2, sort_keys=True)
    print(report_text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(report_text + "\n", encoding="utf-8")

    return 0 if all(r.cross_process_content_ok for r in results if r.api_surface_success) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
