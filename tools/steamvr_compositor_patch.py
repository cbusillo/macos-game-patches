#!/usr/bin/env python3

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BinaryPatch:
    name: str
    offset: int
    original: bytes
    patched: bytes


def default_compositor_path(bottle_name: str) -> Path:
    return (
        Path.home()
        / "Library/Application Support/CrossOver/Bottles"
        / bottle_name
        / "drive_c/Program Files (x86)/Steam/steamapps/common/SteamVR/bin/win64/vrcompositor.exe"
    )


def backup_path(binary_path: Path) -> Path:
    return binary_path.with_suffix(binary_path.suffix + ".mgp.bak")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def patch_set() -> list[BinaryPatch]:
    return [
        BinaryPatch(
            name="ignore_secondary_shared_buffer_null_branch",
            offset=0x414A9,
            original=bytes.fromhex("0f84ec1c0000"),
            patched=bytes.fromhex("909090909090"),
        ),
        BinaryPatch(
            name="ignore_missing_fallback_sync_texture_shared_handle",
            offset=0x276A5,
            original=bytes.fromhex("751b"),
            patched=bytes.fromhex("eb1b"),
        ),
        BinaryPatch(
            name="treat_mirror_texture_primary_create_failure_as_nonfatal",
            offset=0x6FA48,
            original=bytes.fromhex("b8df010000"),
            patched=bytes.fromhex("31c0909090"),
        ),
        BinaryPatch(
            name="treat_mirror_texture_secondary_create_failure_as_nonfatal",
            offset=0x6FA7A,
            original=bytes.fromhex("b8e0010000"),
            patched=bytes.fromhex("31c0909090"),
        ),
        BinaryPatch(
            name="treat_create_mirror_textures_return_code_as_nonfatal",
            offset=0x43072,
            original=bytes.fromhex("b8e3010000"),
            patched=bytes.fromhex("31c0909090"),
        ),
        BinaryPatch(
            name="treat_create_driver_direct_mode_resolve_textures_as_nonfatal",
            offset=0x4308D,
            original=bytes.fromhex("b8f4010000"),
            patched=bytes.fromhex("31c0909090"),
        ),
        BinaryPatch(
            name="treat_driver_direct_mode_resolve_texture_alloc_failure_as_nonfatal",
            offset=0x429AC,
            original=bytes.fromhex("b8f4010000"),
            patched=bytes.fromhex("31c0909090"),
        ),
        BinaryPatch(
            name="treat_create_driver_direct_mode_resolve_textures_failure_as_nonfatal",
            offset=0x27333,
            original=bytes.fromhex("b8db010000"),
            patched=bytes.fromhex("31c0909090"),
        ),
    ]


def patch_status(content: bytes, patch: BinaryPatch) -> str:
    current = content[patch.offset : patch.offset + len(patch.original)]
    if current == patch.original:
        return "original"
    if current == patch.patched:
        return "patched"
    return "unknown"


def print_status(binary_path: Path, content: bytes, patches: list[BinaryPatch]) -> int:
    print(f"file={binary_path}")
    print(f"sha256={sha256_bytes(content)}")
    overall = 0
    for patch in patches:
        status = patch_status(content, patch)
        print(f"{patch.name}: {status}")
        if status == "unknown":
            overall = 1
    return overall


def ensure_backup(binary_path: Path, content: bytes) -> Path:
    backup = backup_path(binary_path)
    if not backup.exists():
        backup.write_bytes(content)
        print(f"backup_created={backup}")
    else:
        print(f"backup_exists={backup}")
    return backup


def apply_patches(binary_path: Path, content: bytes, patches: list[BinaryPatch]) -> tuple[int, bytes]:
    updated = bytearray(content)
    exit_code = 0
    for patch in patches:
        status = patch_status(content, patch)
        if status == "patched":
            print(f"{patch.name}: already patched")
            continue
        if status == "unknown":
            print(f"{patch.name}: unexpected bytes at offset 0x{patch.offset:x}")
            exit_code = 1
            continue
        updated[patch.offset : patch.offset + len(patch.original)] = patch.patched
        print(f"{patch.name}: patched")
    return exit_code, bytes(updated)


def restore_backup(binary_path: Path) -> int:
    backup = backup_path(binary_path)
    if not backup.exists():
        print(f"backup_missing={backup}")
        return 1
    binary_path.write_bytes(backup.read_bytes())
    print(f"restored_from={backup}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bottle", default="Steam")
    parser.add_argument("--file", default=None)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated patch names to operate on (default: all)",
    )
    args = parser.parse_args()

    actions = [args.check, args.apply, args.restore]
    if sum(1 for action in actions if action) != 1:
        print("choose exactly one action: --check, --apply, or --restore")
        return 1

    binary_path = Path(args.file) if args.file else default_compositor_path(args.bottle)
    if not binary_path.exists():
        print(f"file_not_found={binary_path}")
        return 1

    if args.restore:
        return restore_backup(binary_path)

    content = binary_path.read_bytes()
    selected_names = {name.strip() for name in args.only.split(",") if name.strip()}
    patches = patch_set()
    if selected_names:
        patches = [patch for patch in patches if patch.name in selected_names]
        unknown_names = sorted(selected_names - {patch.name for patch in patch_set()})
        if unknown_names:
            print("unknown_patch_names=" + ",".join(unknown_names))
            return 1
        if not patches:
            print("no_patches_selected")
            return 1

    if args.check:
        return print_status(binary_path, content, patches)

    ensure_backup(binary_path, content)
    patch_exit, updated_content = apply_patches(binary_path, content, patches)
    if patch_exit != 0:
        return patch_exit
    binary_path.write_bytes(updated_content)
    print(f"patched_sha256={sha256_bytes(updated_content)}")
    return print_status(binary_path, updated_content, patches)


if __name__ == "__main__":
    raise SystemExit(main())
