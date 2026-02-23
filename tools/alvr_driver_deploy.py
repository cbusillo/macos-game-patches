#!/usr/bin/env python3

import argparse
import re
import shutil
import subprocess
from pathlib import Path


def parse_imported_dlls(dll_path: Path) -> set[str]:
    objdump = shutil.which("objdump")
    if objdump is None:
        return set()

    result = subprocess.run(
        [objdump, "-p", str(dll_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()

    imported_dlls: set[str] = set()
    pattern = re.compile(r"^\s*DLL Name:\s*(.+?)\s*$", re.IGNORECASE)
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        imported_dlls.add(match.group(1).strip().lower())
    return imported_dlls


def bottle_driver_root(bottle_name: str) -> Path:
    return (
        Path.home()
        / "Library/Application Support/CrossOver/Bottles"
        / bottle_name
        / "drive_c/Program Files (x86)/Steam/steamapps/common/SteamVR/drivers/alvr_server"
    )


def bottle_steamvr_root(bottle_name: str) -> Path:
    return (
        Path.home()
        / "Library/Application Support/CrossOver/Bottles"
        / bottle_name
        / "drive_c/Program Files (x86)/Steam/steamapps/common/SteamVR"
    )


def default_manifest_path() -> Path:
    return Path.home() / "Developer/ALVR/alvr/xtask/resources/driver.vrdrivermanifest"


def default_openvr_path(bottle_name: str) -> Path:
    return bottle_steamvr_root(bottle_name) / "bin/win64/openvr_api.dll"


def default_libvpl_path() -> Path:
    return Path.home() / "Developer/ALVR/libvpl.dll"


def maybe_copy_file(source: Path, target: Path, label: str) -> bool:
    if not source.exists():
        print(f"Skipped {label}: source missing: {source}")
        return False

    shutil.copy2(source, target)
    print(f"Deployed {label}: {target}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy ALVR OpenVR driver binaries into a CrossOver SteamVR bottle"
    )
    parser.add_argument("--bottle", default="Steam")
    parser.add_argument(
        "--dll",
        required=True,
        help="Path to built driver_alvr_server.dll",
    )
    parser.add_argument(
        "--manifest",
        default=str(default_manifest_path()),
        help="Path to driver.vrdrivermanifest",
    )
    parser.add_argument(
        "--openvr-dll",
        default=None,
        help=(
            "Optional path to openvr_api.dll. If omitted, deploy attempts "
            "auto-detection from the target bottle SteamVR bin/win64"
        ),
    )
    parser.add_argument(
        "--libvpl-dll",
        default=None,
        help=(
            "Optional path to libvpl.dll. If omitted, deploy attempts "
            "auto-detection at ~/Developer/ALVR/libvpl.dll"
        ),
    )
    args = parser.parse_args()

    dll_source = Path(args.dll)
    manifest_source = Path(args.manifest)
    driver_root = bottle_driver_root(args.bottle)
    driver_bin_root = driver_root / "bin/win64"
    dll_target = driver_root / "bin/win64/driver_alvr_server.dll"
    manifest_target = driver_root / "driver.vrdrivermanifest"

    if not dll_source.exists():
        print(f"Driver DLL not found: {dll_source}")
        return 1
    if not manifest_source.exists():
        print(f"Manifest not found: {manifest_source}")
        return 1

    driver_bin_root.mkdir(parents=True, exist_ok=True)
    (driver_root / "resources").mkdir(parents=True, exist_ok=True)

    shutil.copy2(dll_source, dll_target)
    shutil.copy2(manifest_source, manifest_target)

    openvr_source = (
        Path(args.openvr_dll)
        if args.openvr_dll is not None
        else default_openvr_path(args.bottle)
    )
    libvpl_source = (
        Path(args.libvpl_dll)
        if args.libvpl_dll is not None
        else default_libvpl_path()
    )
    maybe_copy_file(openvr_source, driver_bin_root / "openvr_api.dll", "openvr_api.dll")
    maybe_copy_file(libvpl_source, driver_bin_root / "libvpl.dll", "libvpl.dll")

    imported_dlls = parse_imported_dlls(dll_target)
    required_runtime_dlls = ["openvr_api.dll", "libvpl.dll"]
    missing_required_runtime = [
        dll_name for dll_name in required_runtime_dlls if dll_name in imported_dlls and not (driver_bin_root / dll_name).exists()
    ]
    if missing_required_runtime:
        print("Deployment failed: missing imported runtime DLLs in target folder")
        for dll_name in missing_required_runtime:
            print(f"- {dll_name}")
        return 1

    print("Deployed ALVR driver")
    print(f"- target root: {driver_root}")
    print(f"- dll: {dll_target}")
    print(f"- manifest: {manifest_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
