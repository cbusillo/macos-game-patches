#!/usr/bin/env python3

from __future__ import annotations

import hashlib
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

WINE = Path("/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine")
GPTK_DLL_DIR = Path("/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/apple_gptk/wine/x86_64-windows")
BOTTLE_SYSTEM32 = Path.home() / "Library/Application Support/CrossOver/Bottles/Steam/drive_c/windows/system32"

SCENARIOS = ["shared", "shared_keyed", "shared_keyed_nthandle", "shared_nthandle"]
VARIANTS = ["stock_gptk", "patched_gptk"]

sys.path.insert(0, str(REPO / "tools"))
import shared_content_probe  # type: ignore  # noqa: E402


def sha1sum(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def ensure_probe_exe() -> Path:
    source_probe_exe = REPO / "temp/probes/win_shared_content_probe.exe"
    if not source_probe_exe.exists():
        raise RuntimeError(f"probe exe missing: {source_probe_exe}")

    isolated_dir = PROBES_DIR / "phase1-controlled-probe-exe"
    isolated_dir.mkdir(parents=True, exist_ok=True)
    probe_exe_path = isolated_dir / "win_shared_content_probe.exe"
    shutil.copy2(source_probe_exe, probe_exe_path)

    # Avoid accidental app-local DLL shadowing from probe directory.
    for shadow_name in ("d3d11.dll", "dxgi.dll"):
        shadow_path = isolated_dir / shadow_name
        if shadow_path.exists():
            shadow_path.unlink()

    return probe_exe_path


def verify_probe_execution(log_text: str, scenario: str, output_file: Path) -> None:
    required_markers = [
        f"scenario={scenario}",
        "CreateTexture2D hr=",
        "IDXGIResource::GetSharedHandle hr=",
    ]
    missing = [marker for marker in required_markers if marker not in log_text]
    if missing:
        raise RuntimeError(
            f"probe execution markers missing for {scenario} in {output_file}: {', '.join(missing)}"
        )


def parse_loaded_modules(log_text: str) -> dict[str, dict[str, object]]:
    load_re = re.compile(
        r'Loaded L"(?P<path>[^\"]*?(?P<name>d3d11\.dll|dxgi\.dll))" at .*?: (?P<kind>native|builtin)',
        re.IGNORECASE,
    )

    modules: dict[str, dict[str, object]] = {}
    for match in load_re.finditer(log_text):
        name = match.group("name").lower()
        path = match.group("path")
        kind = match.group("kind").lower()
        if name not in modules:
            modules[name] = {
                "first_path": path,
                "kind": kind,
                "unique_paths": [path],
                "load_events": 1,
            }
            continue

        module = modules[name]
        load_events_obj = module.get("load_events")
        load_events = load_events_obj if isinstance(load_events_obj, int) else 0
        module["load_events"] = load_events + 1
        unique_paths = module["unique_paths"]
        if isinstance(unique_paths, list) and path not in unique_paths:
            unique_paths.append(path)

    return modules


def collect_runtime_hashes() -> dict[str, str | None]:
    paths = {
        "gptk_d3d11_sha1": GPTK_DLL_DIR / "d3d11.dll",
        "gptk_dxgi_sha1": GPTK_DLL_DIR / "dxgi.dll",
        "bottle_system32_d3d11_sha1": BOTTLE_SYSTEM32 / "d3d11.dll",
        "bottle_system32_dxgi_sha1": BOTTLE_SYSTEM32 / "dxgi.dll",
    }
    hashes: dict[str, str | None] = {}
    for key, path in paths.items():
        hashes[key] = sha1sum(path) if path.exists() else None
    return hashes


def deploy_variant(variant: str, backup_dir: Path, patched_dir: Path) -> dict[str, str | None]:
    if variant == "stock_gptk":
        shutil.copy2(backup_dir / "d3d11.dll.stock", GPTK_DLL_DIR / "d3d11.dll")
        shutil.copy2(backup_dir / "dxgi.dll.stock", GPTK_DLL_DIR / "dxgi.dll")
    elif variant == "patched_gptk":
        shutil.copy2(patched_dir / "d3d11.dll", GPTK_DLL_DIR / "d3d11.dll")
        shutil.copy2(patched_dir / "dxgi.dll", GPTK_DLL_DIR / "dxgi.dll")
    else:
        raise RuntimeError(f"unknown variant: {variant}")
    return collect_runtime_hashes()


def run_matrix() -> tuple[Path, Path, Path]:
    cleanup_cmd = [sys.executable, str(REPO / "tools/vr_stack_cleanup.py"), "--sterile-native-steam"]
    probe_exe = ensure_probe_exe()
    patched_dir = (REPO / "temp/probes/patched-wine-dlls").resolve()

    for name in ("d3d11.dll", "dxgi.dll"):
        path = patched_dir / name
        if not path.exists():
            raise RuntimeError(f"patched artifact missing: {path}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = PROBES_DIR / f"phase1-controlled-runtime-backup-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GPTK_DLL_DIR / "d3d11.dll", backup_dir / "d3d11.dll.stock")
    shutil.copy2(GPTK_DLL_DIR / "dxgi.dll", backup_dir / "dxgi.dll.stock")

    matrix_results: list[dict[str, object]] = []
    variant_hash_archive: dict[str, dict[str, str | None]] = {}

    try:
        for variant in VARIANTS:
            variant_hash_archive[variant] = deploy_variant(variant, backup_dir, patched_dir)

            for scenario in SCENARIOS:
                cleanup_rc = subprocess.run(cleanup_cmd, cwd=REPO, check=False).returncode
                if cleanup_rc != 0:
                    raise RuntimeError(f"cleanup failed before {variant}/{scenario}: rc={cleanup_rc}")

                output_file = PROBES_DIR / f"probe-content-controlled-{variant}-{scenario}-{stamp}.stdout"
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

                log_text = output_file.read_text(encoding="utf-8", errors="replace")
                verify_probe_execution(log_text, scenario, output_file)

                result = shared_content_probe.classify_result("d3dmetal", scenario, output_file, run_rc)
                payload: dict[str, object] = asdict(result)
                payload["variant"] = variant
                payload["runtime_hashes"] = variant_hash_archive[variant]
                payload["loaded_modules"] = parse_loaded_modules(log_text)
                map_match = re.search(r"WineDxgiSharedHandleMapV(\d+)_64", log_text)
                payload["map_version"] = int(map_match.group(1)) if map_match else None
                payload["saw_surrogate_warning"] = "Returning surrogate cross-process shared texture" in log_text
                payload["saw_reject_warning"] = "Cross-process shared open for token" in log_text
                payload["execution_verified"] = True
                matrix_results.append(payload)
    finally:
        shutil.copy2(backup_dir / "d3d11.dll.stock", GPTK_DLL_DIR / "d3d11.dll")
        shutil.copy2(backup_dir / "dxgi.dll.stock", GPTK_DLL_DIR / "dxgi.dll")

    restored_hashes = collect_runtime_hashes()
    restore_matches_stock = (
        restored_hashes.get("gptk_d3d11_sha1") == sha1sum(backup_dir / "d3d11.dll.stock")
        and restored_hashes.get("gptk_dxgi_sha1") == sha1sum(backup_dir / "dxgi.dll.stock")
    )

    matrix_payload: dict[str, object] = {
        "timestamp": stamp,
        "runner": "phase1-controlled-gptk-runtime",
        "probe_exe": str(probe_exe),
        "gptk_dll_dir": str(GPTK_DLL_DIR),
        "backup_dir": str(backup_dir),
        "restore_matches_stock": restore_matches_stock,
        "variant_hashes": variant_hash_archive,
        "restored_hashes": restored_hashes,
        "results": matrix_results,
    }
    matrix_archive_path = PROBES_DIR / f"probe-content-phase1-controlled-matrix-{stamp}.json"
    matrix_archive_path.write_text(json.dumps(matrix_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    matrix_path = PROBES_DIR / "probe-content-phase1-controlled-matrix.json"
    matrix_path.write_text(matrix_archive_path.read_text(encoding="utf-8"), encoding="utf-8")

    delta_payload: dict[str, dict[str, object | None]] = {}
    for scenario in SCENARIOS:
        before = next(r for r in matrix_results if r["variant"] == "stock_gptk" and r["scenario"] == scenario)
        after = next(r for r in matrix_results if r["variant"] == "patched_gptk" and r["scenario"] == scenario)
        delta_payload[scenario] = {
            "before_diagnosis": before["diagnosis"],
            "after_diagnosis": after["diagnosis"],
            "before_map_version": before["map_version"],
            "after_map_version": after["map_version"],
            "before_open_shared_hr": before["open_shared_hr"],
            "after_open_shared_hr": after["open_shared_hr"],
            "before_child_open_shared_hr": before["child_open_shared_hr"],
            "after_child_open_shared_hr": after["child_open_shared_hr"],
            "before_child_open_shared1_hr": before["child_open_shared1_hr"],
            "after_child_open_shared1_hr": after["child_open_shared1_hr"],
            "before_child_exit": before["child_openread_exit"],
            "after_child_exit": after["child_openread_exit"],
            "before_saw_surrogate_warning": before["saw_surrogate_warning"],
            "after_saw_surrogate_warning": after["saw_surrogate_warning"],
            "before_saw_reject_warning": before["saw_reject_warning"],
            "after_saw_reject_warning": after["saw_reject_warning"],
            "before_loaded_modules": before["loaded_modules"],
            "after_loaded_modules": after["loaded_modules"],
            "before_runtime_hashes": before["runtime_hashes"],
            "after_runtime_hashes": after["runtime_hashes"],
        }

    delta_path = PROBES_DIR / "probe-content-phase1-controlled-delta.json"
    delta_archive_path = PROBES_DIR / f"probe-content-phase1-controlled-delta-{stamp}.json"
    delta_archive_path.write_text(json.dumps(delta_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    delta_path.write_text(delta_archive_path.read_text(encoding="utf-8"), encoding="utf-8")

    hashes_path = PROBES_DIR / "probe-content-phase1-controlled-hashes.json"
    hashes_archive_path = PROBES_DIR / f"probe-content-phase1-controlled-hashes-{stamp}.json"
    hashes_payload = {
        "timestamp": stamp,
        "backup_dir": str(backup_dir),
        "variant_hashes": variant_hash_archive,
        "restored_hashes": restored_hashes,
        "restore_matches_stock": restore_matches_stock,
    }
    hashes_archive_path.write_text(json.dumps(hashes_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    hashes_path.write_text(hashes_archive_path.read_text(encoding="utf-8"), encoding="utf-8")

    return matrix_path, delta_path, hashes_path


def main() -> int:
    matrix_path, delta_path, hashes_path = run_matrix()
    print(matrix_path)
    print(delta_path)
    print(hashes_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
