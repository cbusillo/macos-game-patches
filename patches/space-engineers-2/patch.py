#!/usr/bin/env python3
"""
Space Engineers 2 - macOS/CrossOver Compatibility Patch
========================================================

This patch enables Space Engineers 2 to run on macOS via CrossOver/Wine
by bypassing GPU compatibility checks that incorrectly reject Apple Silicon GPUs.

What it patches:
1. VRage.Render.dll - Makes ForceAllAdaptersSupported return true
2. VRage.Render12.dll - Bypasses the IsSupported check in renderer init

Why it's needed:
- Apple Silicon GPUs don't support FP64 (double-precision float) shaders
- SE2 checks for FP64 support and refuses to run without it
- The game doesn't actually USE FP64 - it's just a compatibility check
- This patch bypasses those checks, allowing the game to run

Usage:
    python3 patch_se2_macos.py [game_path]

    If game_path is not provided, it will look in common CrossOver locations.

Restore original files:
    python3 patch_se2_macos.py --restore [game_path]

License: MIT - Use at your own risk
"""

import argparse
import hashlib
import shutil
import sys
from pathlib import Path
from typing import Literal

TESTED_VERSION = "2.0.2.14"

PATCHES = {
    "VRage.Render.dll": {
        "description": "ForceAllAdaptersSupported - always return true",
        "offset": 0x58588,
        "original": bytes([0x02, 0x7b, 0x58, 0x0d, 0x00, 0x04, 0x2a]),
        "patched": bytes([0x17, 0x00, 0x00, 0x00, 0x00, 0x00, 0x2a]),
    },
    "VRage.Render12.dll": {
        "description": "IsSupported bypass - skip GPU compatibility check",
        "offset": 0x81FF2,
        "original": bytes([0x02, 0x28, 0xe4, 0x14, 0x00, 0x06]),
        "patched": bytes([0x00, 0x17, 0x00, 0x00, 0x00, 0x00]),
    },
}

def find_game_path() -> Path | None:
    common_paths = [
        Path.home() / "Library/Application Support/CrossOver/Bottles",
        Path("/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bottles"),
    ]

    for bottles_path in common_paths:
        if not bottles_path.exists():
            continue

        for bottle in bottles_path.iterdir():
            if not bottle.is_dir():
                continue

            game_path = bottle / "drive_c/Program Files (x86)/Steam/steamapps/common/SpaceEngineers2/Game2"
            if game_path.exists():
                return game_path

    return None

def get_file_hash(filepath: Path) -> str:
    md5 = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            md5.update(chunk)
    return md5.hexdigest()

def check_patch_status(filepath: Path, patch_info: dict[str, int| bytes]) -> Literal["original", "patched", "unknown"]:
    with open(filepath, 'rb') as f:
        f.seek(patch_info["offset"])
        current_bytes = f.read(len(patch_info["original"]))

    if current_bytes == patch_info["original"]:
        return "original"
    elif current_bytes == patch_info["patched"]:
        return "patched"
    else:
        return "unknown"

def backup_file(filepath: Path) -> bool:
    backup_path = Path(str(filepath) + ".backup")
    if not backup_path.exists():
        shutil.copy2(filepath, backup_path)
        print(f"  Created backup: {backup_path.name}")
        return True
    else:
        print(f"  Backup already exists: {backup_path.name}")
        return True

def restore_file(filepath: Path) -> bool:
    backup_path = Path(str(filepath) + ".backup")
    if backup_path.exists():
        shutil.copy2(backup_path, filepath)
        print(f"  Restored from backup: {filepath.name}")
        return True
    else:
        print(f"  No backup found for: {filepath.name}")
        return False

def apply_patch(filepath: Path, patch_info: dict[str, int| bytes]) -> None:
    with open(filepath, 'r+b') as f:
        f.seek(patch_info["offset"])
        f.write(patch_info["patched"])
    print(f"  Patched: {filepath.name}")
    print(f"    {patch_info['description']}")

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch Space Engineers 2 for macOS/CrossOver compatibility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "game_path",
        nargs="?",
        help="Path to SE2 Game2 folder (auto-detected if not provided)"
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Restore original files from backups"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check patch status without modifying files"
    )

    args = parser.parse_args()

    if args.game_path:
        game_path = Path(args.game_path)
    else:
        game_path = find_game_path()
        if game_path:
            print(f"Found game at: {game_path}")
        else:
            print("ERROR: Could not find Space Engineers 2 installation.")
            print("Please provide the path to the Game2 folder as an argument.")
            print("\nExample:")
            print('  python3 patch_se2_macos.py "/path/to/SpaceEngineers2/Game2"')
            sys.exit(1)

    if not game_path.exists():
        print(f"ERROR: Path does not exist: {game_path}")
        sys.exit(1)

    missing_files = []
    for filename in PATCHES:
        filepath = game_path / filename
        if not filepath.exists():
            missing_files.append(filename)

    if missing_files:
        print("ERROR: Missing required files:")
        for f in missing_files:
            print(f"  - {f}")
        print("\nMake sure you're pointing to the correct Game2 folder.")
        sys.exit(1)

    print(f"\nSpace Engineers 2 macOS Patch")
    print(f"Tested with version: {TESTED_VERSION}")
    print("=" * 50)

    print("\nChecking files...")
    statuses = {}
    for filename, patch_info in PATCHES.items():
        filepath = game_path / filename
        status = check_patch_status(filepath, patch_info)
        statuses[filename] = status
        status_str = {
            "original": "✗ Not patched",
            "patched": "✓ Already patched",
            "unknown": "? Unknown version"
        }[status]
        print(f"  {filename}: {status_str}")

    if args.check:
        sys.exit(0)

    if args.restore:
        print("\nRestoring original files...")
        for filename in PATCHES:
            filepath = game_path / filename
            restore_file(filepath)
        print("\nRestore complete!")
        sys.exit(0)

    unknown_files = [f for f, s in statuses.items() if s == "unknown"]
    if unknown_files:
        print("\nWARNING: Some files have unknown versions (may be different game version):")
        for f in unknown_files:
            print(f"  - {f}")
        response = input("\nContinue anyway? (y/N): ")
        if response.lower() != 'y':
            print("Aborted.")
            sys.exit(1)

    if all(s == "patched" for s in statuses.values()):
        print("\nAll files are already patched! Nothing to do.")
        sys.exit(0)

    print("\nApplying patches...")
    for filename, patch_info in PATCHES.items():
        filepath = game_path / filename

        if statuses[filename] == "patched":
            print(f"  Skipping {filename} (already patched)")
            continue

        backup_file(filepath)

        apply_patch(filepath, patch_info)

    print("\n" + "=" * 50)
    print("Patching complete!")
    print("\nYou can now run Space Engineers 2 via CrossOver.")
    print("\nNote: If the game updates, you may need to re-run this patch.")
    print("To restore original files: python3 patch_se2_macos.py --restore")

if __name__ == "__main__":
    main()
