#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
from typing import Any


def bottle_root(bottle_name: str) -> Path:
    return Path.home() / "Library/Application Support/CrossOver/Bottles" / bottle_name


def steamvr_settings_path(root: Path) -> Path:
    return root / "drive_c/Program Files (x86)/Steam/config/steamvr.vrsettings"


def openvr_paths_path(root: Path) -> Path:
    return root / "drive_c/users/crossover/AppData/Local/openvr/openvrpaths.vrpath"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def ensure_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def update_settings(settings: dict[str, Any], force_driver: str) -> None:
    steamvr = settings.setdefault("steamvr", {})
    steamvr["forcedDriver"] = force_driver
    steamvr["activateMultipleDrivers"] = False
    steamvr["requireHmd"] = True
    steamvr["enableHomeApp"] = False


def update_openvr_paths(paths_json: dict[str, Any], driver_dir: str) -> None:
    drivers = ensure_list(paths_json.get("external_drivers"))
    if driver_dir not in drivers:
        drivers.append(driver_dir)
    paths_json["external_drivers"] = drivers


def make_backup(path: Path) -> Path:
    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Force SteamVR in a CrossOver bottle to use ALVR driver and register external driver path"
    )
    parser.add_argument("--bottle", default="Steam")
    parser.add_argument(
        "--driver-dir",
        default=r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR\drivers\alvr_server",
    )
    parser.add_argument("--force-driver", default="alvr_server")
    args = parser.parse_args()

    root = bottle_root(args.bottle)
    settings_path = steamvr_settings_path(root)
    openvr_path = openvr_paths_path(root)

    if not root.exists():
        print(f"Bottle not found: {root}")
        return 1
    if not settings_path.exists():
        print(f"SteamVR settings not found: {settings_path}")
        return 1
    if not openvr_path.exists():
        print(f"OpenVR paths file not found: {openvr_path}")
        return 1

    settings_backup = make_backup(settings_path)
    openvr_backup = make_backup(openvr_path)

    settings_json = load_json(settings_path)
    openvr_json = load_json(openvr_path)

    update_settings(settings_json, args.force_driver)
    update_openvr_paths(openvr_json, args.driver_dir)

    write_json(settings_path, settings_json)
    write_json(openvr_path, openvr_json)

    print("Updated SteamVR driver registration")
    print(f"- settings: {settings_path}")
    print(f"- settings backup: {settings_backup}")
    print(f"- openvr paths: {openvr_path}")
    print(f"- openvr backup: {openvr_backup}")
    print(f"- forced driver: {args.force_driver}")
    print(f"- external driver: {args.driver_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
