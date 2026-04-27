#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PROBES_DIR = REPO / "temp/probes"
PROBES_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO / "tools"))
import shared_content_probe  # type: ignore  # noqa: E402


WINE = Path("/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine")
GPTK_DLL_DIR = Path("/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/apple_gptk/wine/x86_64-windows")


def ensure_probe_exe() -> Path:
    source_exe = REPO / "temp/probes/win_shared_content_probe.exe"
    isolated_dir = PROBES_DIR / "phase1-gptk-controlled-probe"
    isolated_dir.mkdir(parents=True, exist_ok=True)
    probe_exe = isolated_dir / "win_shared_content_probe.exe"
    shutil.copy2(source_exe, probe_exe)
    for shadow_name in ("d3d11.dll", "dxgi.dll"):
        shadow = isolated_dir / shadow_name
        if shadow.exists():
            shadow.unlink()
    return probe_exe


def classify_with_runtime_evidence(output_file: Path, run_rc: int, scenario: str) -> dict[str, object]:
    result = shared_content_probe.classify_result("d3dmetal", scenario, output_file, run_rc)
    payload: dict[str, object] = asdict(result)
    text = output_file.read_text(encoding="utf-8", errors="replace")

    loaded_modules: dict[str, dict[str, str]] = {}
    for module in ("d3d11.dll", "dxgi.dll"):
        match = re.search(
            rf'Loaded L"(?P<path>[^\"]*{re.escape(module).replace("\\.", ".")})".*?: (?P<kind>native|builtin)',
            text,
            flags=re.IGNORECASE,
        )
        if match:
            loaded_modules[module] = {
                "path": match.group("path"),
                "kind": match.group("kind").lower(),
            }

    map_match = re.search(r"WineDxgiSharedHandleMapV(\d+)_64", text)
    payload["map_version"] = int(map_match.group(1)) if map_match else None
    payload["saw_surrogate_warning"] = "Returning surrogate cross-process shared texture" in text
    payload["saw_reject_warning"] = "Cross-process shared open for token" in text
    payload["loaded_modules"] = loaded_modules
    return payload


def run_variant(variant: str, probe_exe: Path, stamp: str, scenarios: list[str]) -> list[dict[str, object]]:
    cleanup_cmd = [sys.executable, str(REPO / "tools/vr_stack_cleanup.py"), "--sterile-native-steam"]
    variant_results: list[dict[str, object]] = []

    for scenario in scenarios:
        cleanup_rc = subprocess.run(cleanup_cmd, cwd=REPO, check=False).returncode
        if cleanup_rc != 0:
            raise RuntimeError(f"cleanup failed before {variant}/{scenario}: rc={cleanup_rc}")

        output_file = PROBES_DIR / f"probe-content-phase1-gptk-{variant}-{scenario}-{stamp}.stdout"
        cmd = [
            str(WINE),
            "--bottle",
            "Steam",
            "--debugmsg",
            "+loaddll,+d3d11,+dxgi",
            "--dll",
            "d3d11,dxgi=n,b",
            "--env",
            "CX_GRAPHICS_BACKEND=d3dmetal",
            str(probe_exe),
            "scenario",
            scenario,
        ]
        with output_file.open("w", encoding="utf-8") as fh:
            run_rc = subprocess.run(cmd, cwd=REPO, check=False, stdout=fh, stderr=subprocess.STDOUT).returncode

        payload = classify_with_runtime_evidence(output_file, run_rc, scenario)
        payload["variant"] = variant
        variant_results.append(payload)

    return variant_results


def run_matrix() -> tuple[Path, Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    scenarios = ["shared", "shared_keyed", "shared_keyed_nthandle", "shared_nthandle"]
    probe_exe = ensure_probe_exe()

    backup_dir = PROBES_DIR / f"gptk-runtime-backup-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    gptk_d3d11 = GPTK_DLL_DIR / "d3d11.dll"
    gptk_dxgi = GPTK_DLL_DIR / "dxgi.dll"
    patched_d3d11 = REPO / "temp/probes/patched-wine-dlls/d3d11.dll"
    patched_dxgi = REPO / "temp/probes/patched-wine-dlls/dxgi.dll"

    shutil.copy2(gptk_d3d11, backup_dir / "d3d11.dll.stock")
    shutil.copy2(gptk_dxgi, backup_dir / "dxgi.dll.stock")

    all_results: list[dict[str, object]] = []
    try:
        all_results.extend(run_variant("stock_gptk", probe_exe, stamp, scenarios))

        shutil.copy2(patched_d3d11, gptk_d3d11)
        shutil.copy2(patched_dxgi, gptk_dxgi)

        all_results.extend(run_variant("patched_gptk", probe_exe, stamp, scenarios))
    finally:
        shutil.copy2(backup_dir / "d3d11.dll.stock", gptk_d3d11)
        shutil.copy2(backup_dir / "dxgi.dll.stock", gptk_dxgi)

    matrix_payload = {
        "timestamp": stamp,
        "gptk_dll_dir": str(GPTK_DLL_DIR),
        "backup_dir": str(backup_dir),
        "results": all_results,
    }
    matrix_path = PROBES_DIR / "probe-content-phase1-gptk-matrix.json"
    matrix_path.write_text(json.dumps(matrix_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    delta: dict[str, dict[str, object | None]] = {}
    for scenario in scenarios:
        before = next(r for r in all_results if r["variant"] == "stock_gptk" and r["scenario"] == scenario)
        after = next(r for r in all_results if r["variant"] == "patched_gptk" and r["scenario"] == scenario)
        delta[scenario] = {
            "before_diagnosis": before["diagnosis"],
            "after_diagnosis": after["diagnosis"],
            "before_map_version": before["map_version"],
            "after_map_version": after["map_version"],
            "before_open_shared_hr": before["open_shared_hr"],
            "after_open_shared_hr": after["open_shared_hr"],
            "before_child_exit": before["child_openread_exit"],
            "after_child_exit": after["child_openread_exit"],
            "before_saw_surrogate_warning": before["saw_surrogate_warning"],
            "after_saw_surrogate_warning": after["saw_surrogate_warning"],
            "before_saw_reject_warning": before["saw_reject_warning"],
            "after_saw_reject_warning": after["saw_reject_warning"],
            "before_loaded_d3d11": before["loaded_modules"],
            "after_loaded_d3d11": after["loaded_modules"],
        }

    delta_path = PROBES_DIR / "probe-content-phase1-gptk-delta.json"
    delta_path.write_text(json.dumps(delta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return matrix_path, delta_path, backup_dir


def main() -> int:
    matrix_path, delta_path, backup_dir = run_matrix()
    print(matrix_path)
    print(delta_path)
    print(backup_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

