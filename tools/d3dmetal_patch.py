#!/usr/bin/env python3

"""Manage reproducible byte patches for CrossOver D3DMetal experiments.

This tool is intentionally conservative:
- requires known-byte matches before patching,
- writes a one-time backup,
- supports deterministic restore,
- can enforce expected build hashes.

These patches are diagnostic scaffolding for Direct Mode interop experiments,
not a production-ready shared-resource implementation.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BINARY = Path(
    "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/apple_gptk/external/"
    "D3DMetal.framework/Versions/A/D3DMetal"
)

BACKUP_ROOT = Path("temp/crossover_bundle_backups")

# Observed on local CrossOver build used during current investigation.
KNOWN_SHA256 = "05a7beaed4494a4f5f53d3f626a82fffc3b70146436a908b7048a0632a49e1a8"


@dataclass(frozen=True)
class BinaryPatch:
    name: str
    offset: int
    original: bytes
    patched: bytes
    note: str


def backup_path(binary_path: Path) -> Path:
    resolved = binary_path.resolve()
    try:
        relative = resolved.relative_to(Path("/"))
    except ValueError:
        relative = Path(str(resolved).lstrip("/"))
    return BACKUP_ROOT / relative.with_suffix(resolved.suffix + ".mgp.d3dmetal.bak")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def patch_set_diagnostic_s_ok() -> list[BinaryPatch]:
    # Pattern at each target is: mov eax, 0x80004001; ret.
    # This diagnostic patch flips HRESULT to S_OK at the same site.
    return [
        BinaryPatch(
            name="diagnostic_get_shared_handle_return_s_ok",
            offset=0x10D832,
            original=bytes.fromhex("b801400080"),
            patched=bytes.fromhex("31c0909090"),
            note="DXGIResource::GetSharedHandle immediate E_NOTIMPL path",
        ),
        BinaryPatch(
            name="diagnostic_open_shared_resource_return_s_ok",
            offset=0x18B3FA,
            original=bytes.fromhex("b801400080"),
            patched=bytes.fromhex("31c0909090"),
            note="D3D11Device::OpenSharedResource immediate E_NOTIMPL path",
        ),
        BinaryPatch(
            name="diagnostic_open_shared_resource1_return_s_ok",
            offset=0x18BE84,
            original=bytes.fromhex("b801400080"),
            patched=bytes.fromhex("31c0909090"),
            note="D3D11Device::OpenSharedResource1 immediate E_NOTIMPL path",
        ),
    ]


def patch_set_probe_unique_hrs() -> list[BinaryPatch]:
    # Runtime callsite mapping helper.
    #
    # Each immediate E_NOTIMPL stub return is changed to a distinct HRESULT so
    # live logs can attribute which stub site is actually being exercised.
    #
    # 0x887A1001 -> GetSharedHandle
    # 0x887A1002 -> OpenSharedResource
    # 0x887A1003 -> OpenSharedResource1
    return [
        BinaryPatch(
            name="probe_get_shared_handle_unique_hr",
            offset=0x10D832,
            original=bytes.fromhex("b801400080"),
            patched=bytes.fromhex("b801107a88"),
            note="DXGIResource::GetSharedHandle immediate return -> unique HRESULT",
        ),
        BinaryPatch(
            name="probe_open_shared_resource_unique_hr",
            offset=0x18B3FA,
            original=bytes.fromhex("b801400080"),
            patched=bytes.fromhex("b802107a88"),
            note="D3D11Device::OpenSharedResource immediate return -> unique HRESULT",
        ),
        BinaryPatch(
            name="probe_open_shared_resource1_unique_hr",
            offset=0x18BE84,
            original=bytes.fromhex("b801400080"),
            patched=bytes.fromhex("b803107a88"),
            note="D3D11Device::OpenSharedResource1 immediate return -> unique HRESULT",
        ),
    ]


def patch_set_probe_getshared_int3() -> list[BinaryPatch]:
    # Aggressive callsite-probing helper.
    #
    # Replaces the immediate GetSharedHandle E_NOTIMPL return sequence with
    # INT3 instructions. If the live path executes this exact site, the process
    # should trap immediately, proving callsite reachability.
    return [
        BinaryPatch(
            name="probe_get_shared_handle_int3",
            offset=0x10D832,
            original=bytes.fromhex("b801400080"),
            patched=bytes.fromhex("cccccccccc"),
            note="DXGIResource::GetSharedHandle immediate return -> INT3 trap",
        ),
    ]


def patch_set_pointer_handle_qi() -> list[BinaryPatch]:
    # Experimental same-process pointer-handle interop.
    #
    # Strategy:
    # - GetSharedHandle returns the underlying resource interface pointer stored
    #   in DXGIResource (+0x28) as an opaque HANDLE.
    # - OpenSharedResource / OpenSharedResource1 reinterpret the HANDLE as a COM
    #   interface pointer and execute QueryInterface(riid, out).
    #
    # This is intentionally scoped to in-process behavior and is unsafe as a
    # cross-process handle model.
    return [
        BinaryPatch(
            name="exp_get_shared_handle_return_underlying_resource_ptr",
            offset=0x10D828,
            original=bytes.fromhex("48833da81e2900ff7506b801400080c3"),
            patched=bytes.fromhex("488b412848890231c0c3909090909090"),
            note="DXGIResource::GetSharedHandle -> *out = *(this+0x28), S_OK",
        ),
        BinaryPatch(
            name="exp_open_shared_resource_queryinterface_from_handle",
            offset=0x18B3F0,
            original=bytes.fromhex("48833d88ec2100ff7506b801400080c3"),
            patched=bytes.fromhex("4889d1488b024c89c24d89c8ff10c390"),
            note="D3D11Device::OpenSharedResource -> hr = handle->QI(iid, out)",
        ),
        BinaryPatch(
            name="exp_open_shared_resource1_queryinterface_from_handle",
            offset=0x18BE7A,
            original=bytes.fromhex("48833d1ee22100ff7506b801400080c3"),
            patched=bytes.fromhex("4889d1488b024c89c24d89c8ff10c390"),
            note="D3D11Device::OpenSharedResource1 -> hr = handle->QI(iid, out)",
        ),
    ]


PATCH_SET_BUILDERS = {
    "diagnostic_s_ok": patch_set_diagnostic_s_ok,
    "probe_getshared_int3": patch_set_probe_getshared_int3,
    "probe_unique_hrs": patch_set_probe_unique_hrs,
    "pointer_handle_qi": patch_set_pointer_handle_qi,
}


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
        print(f"{patch.name}: {status} offset=0x{patch.offset:x} note={patch.note}")
        if status == "unknown":
            overall = 1
    return overall


def ensure_backup(binary_path: Path, content: bytes) -> Path:
    backup = backup_path(binary_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_bytes(content)
        print(f"backup_created={backup}")
    else:
        print(f"backup_exists={backup}")
    return backup


def apply_patches(content: bytes, patches: list[BinaryPatch]) -> tuple[int, bytes]:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default=str(DEFAULT_BINARY), help="Path to D3DMetal binary")
    parser.add_argument("--patch-set", choices=sorted(PATCH_SET_BUILDERS), default="diagnostic_s_ok")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated patch names to operate on (default: all in selected patch-set)",
    )
    parser.add_argument(
        "--skip-known-hash-check",
        action="store_true",
        help="Allow operation on a D3DMetal build hash different from the currently validated build",
    )
    parser.add_argument(
        "--print-known-hash",
        action="store_true",
        help="Print known-good hash and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.print_known_hash:
        print(f"known_sha256={KNOWN_SHA256}")
        return 0

    actions = [args.check, args.apply, args.restore]
    if sum(1 for action in actions if action) != 1:
        print("choose exactly one action: --check, --apply, or --restore")
        return 1

    binary_path = Path(args.file)
    if not binary_path.exists():
        print(f"file_not_found={binary_path}")
        return 1

    if args.restore:
        return restore_backup(binary_path)

    content = binary_path.read_bytes()
    content_sha256 = sha256_bytes(content)
    print(f"file_sha256={content_sha256}")
    if not args.skip_known_hash_check and content_sha256 != KNOWN_SHA256:
        print("error=unexpected_build_hash")
        print(f"expected_sha256={KNOWN_SHA256}")
        print("hint=rerun with --skip-known-hash-check after manual verification")
        return 1

    patches = PATCH_SET_BUILDERS[args.patch_set]()
    selected_names = {name.strip() for name in args.only.split(",") if name.strip()}
    if selected_names:
        all_names = {patch.name for patch in patches}
        unknown_names = sorted(selected_names - all_names)
        if unknown_names:
            print("unknown_patch_names=" + ",".join(unknown_names))
            return 1
        patches = [patch for patch in patches if patch.name in selected_names]
        if not patches:
            print("no_patches_selected")
            return 1

    if args.check:
        return print_status(binary_path, content, patches)

    ensure_backup(binary_path, content)
    patch_exit, updated_content = apply_patches(content, patches)
    if patch_exit != 0:
        return patch_exit
    binary_path.write_bytes(updated_content)
    print(f"patched_sha256={sha256_bytes(updated_content)}")
    return print_status(binary_path, updated_content, patches)


if __name__ == "__main__":
    raise SystemExit(main())
