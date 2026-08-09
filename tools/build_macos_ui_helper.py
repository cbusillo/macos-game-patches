#!/usr/bin/env python3
"""Build and verify the signed, one-shot macOS Lab UI helper app."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import plistlib
import secrets
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tools" / "macos_ui_helper"
PLIST = PACKAGE / "Resources" / "Info.plist"
BUNDLE_ID = "com.alvr.macos-game-patches.ui-helper"
TEAM_ID = "MM5YXC7T6E"
IDENTITY = "Developer ID Application: Shiny Computers Leasing LLC (MM5YXC7T6E)"
EXECUTABLE = "macos-ui-helper"
INSTALL_ROOT = Path.home() / "Library" / "Application Support" / "MacOSGamePatches" / "UIHelper"
INSTALL_BUNDLE = INSTALL_ROOT / "MacOSUIHelper.app"
CLIENT_SOURCE = ROOT / "tools" / "macos_ui_helper_client.py"
CLIENT_DESTINATION = INSTALL_ROOT / "macos_ui_helper_client.py"


class BuildError(RuntimeError):
    pass


def run(command: list[str], *, capture: bool = True) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise BuildError(f"command failed ({completed.returncode}): {' '.join(command)}\n{detail}")
    return "\n".join(
        part for part in ((completed.stdout or "").strip(), (completed.stderr or "").strip()) if part
    )


def check_contract(*, require_identity: bool = False) -> dict[str, object]:
    if sys.platform != "darwin":
        raise BuildError("the helper can only be built on macOS")
    for path in (PACKAGE / "Package.swift", PLIST):
        if not path.is_file():
            raise BuildError(f"missing required input: {path.relative_to(ROOT)}")
    with PLIST.open("rb") as handle:
        metadata = plistlib.load(handle)
    expected = {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": EXECUTABLE,
        "CFBundlePackageType": "APPL",
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise BuildError(f"Info.plist {key} must be {value!r}")
    identities = run(["security", "find-identity", "-v", "-p", "codesigning"])
    identity_available = IDENTITY in identities
    if require_identity and not identity_available:
        raise BuildError(f"missing signing identity: {IDENTITY}")
    return {
        "bundleID": BUNDLE_ID,
        "teamID": TEAM_ID,
        "identity": IDENTITY,
        "identityAvailable": identity_available,
        "swift": run(["swift", "--version"]).splitlines()[0],
    }


def verify(bundle: Path) -> dict[str, object]:
    executable = bundle / "Contents" / "MacOS" / EXECUTABLE
    if bundle.is_symlink() or not executable.is_file() or executable.is_symlink():
        raise BuildError("bundle or executable is missing or symlinked")
    if any(path.is_symlink() for path in bundle.rglob("*")):
        raise BuildError("helper bundle must not contain symlinks")
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(bundle)])
    signature = run(["codesign", "-d", "--verbose=4", str(bundle)])
    signature_requirements = (
        f"Identifier={BUNDLE_ID}",
        f"TeamIdentifier={TEAM_ID}",
        "Authority=Developer ID Application:",
        "flags=0x10000(runtime)",
    )
    if not all(fragment in signature for fragment in signature_requirements):
        raise BuildError("signed identity does not match the helper contract")
    architectures = run(["lipo", "-archs", str(executable)]).split()
    if architectures != ["arm64"]:
        raise BuildError(f"expected arm64-only executable, found: {architectures}")
    designated = run(["codesign", "-d", "-r-", str(bundle)])
    designated_requirements = (
        f'identifier "{BUNDLE_ID}"',
        "anchor apple generic",
        "certificate 1[field.1.2.840.113635.100.6.2.6]",
        "certificate leaf[field.1.2.840.113635.100.6.1.13]",
        f"certificate leaf[subject.OU] = {TEAM_ID}",
    )
    if not all(fragment in designated for fragment in designated_requirements):
        raise BuildError("designated requirement is not Developer ID constrained")
    uuid = run(["dwarfdump", "--uuid", str(executable)]).splitlines()[0]
    return {
        "bundle": str(bundle),
        "bundleID": BUNDLE_ID,
        "teamID": TEAM_ID,
        "architectures": " ".join(architectures),
        "designatedRequirement": designated,
        "uuid": uuid,
    }


def build(output_root: Path) -> dict[str, object]:
    check_contract(require_identity=True)
    output_root = output_root.resolve()
    bundle = output_root / "MacOSUIHelper.app"
    if bundle.exists() or bundle.is_symlink():
        if bundle.is_symlink():
            raise BuildError(f"refusing to replace symlinked bundle: {bundle}")
        shutil.rmtree(bundle)

    environment = os.environ.copy()
    environment.update({"MACOSX_DEPLOYMENT_TARGET": "14.0", "SWIFT_DETERMINISTIC_HASHING": "1"})
    completed = subprocess.run(
        ["swift", "build", "--package-path", str(PACKAGE), "--configuration", "release", "--arch", "arm64"],
        cwd=ROOT,
        env=environment,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise BuildError(completed.stderr.strip() or "Swift release build failed")
    binary_root = Path(run([
        "swift", "build", "--package-path", str(PACKAGE), "--configuration", "release",
        "--arch", "arm64", "--show-bin-path",
    ]))
    source_binary = binary_root / EXECUTABLE
    if not source_binary.is_file():
        raise BuildError(f"missing release binary: {source_binary}")

    executable_dir = bundle / "Contents" / "MacOS"
    executable_dir.mkdir(parents=True, mode=0o755)
    shutil.copy2(PLIST, bundle / "Contents" / "Info.plist")
    shutil.copy2(source_binary, executable_dir / EXECUTABLE)
    os.chmod(executable_dir / EXECUTABLE, 0o755)
    run([
        "codesign", "--force", "--sign", IDENTITY, "--options", "runtime",
        "--timestamp=none", str(bundle),
    ])
    return verify(bundle)


def reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.exists() and current.is_symlink():
            raise BuildError(f"refusing symlinked install component: {current}")


def rename_swap(first: Path, second: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.renameatx_np
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-2, os.fsencode(first), -2, os.fsencode(second), 0x00000002) != 0:
        error_number = ctypes.get_errno()
        raise BuildError(f"atomic helper swap failed: {os.strerror(error_number)}")


def install(output_root: Path) -> dict[str, object]:
    source_bundle = output_root.resolve() / "MacOSUIHelper.app"
    source_identity = verify(source_bundle)
    reject_symlink_components(INSTALL_ROOT)
    INSTALL_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(INSTALL_ROOT, 0o700)

    token = secrets.token_hex(8)
    staged_bundle = INSTALL_ROOT / f".MacOSUIHelper.app.stage-{token}"
    staged_client = INSTALL_ROOT / f".macos_ui_helper_client.py.stage-{token}"
    if staged_bundle.exists() or staged_bundle.is_symlink() or staged_client.exists() or staged_client.is_symlink():
        raise BuildError("install staging path already exists")
    try:
        shutil.copytree(source_bundle, staged_bundle, symlinks=False)
        verify(staged_bundle)
        shutil.copy2(CLIENT_SOURCE, staged_client)
        os.chmod(staged_client, 0o700)

        if INSTALL_BUNDLE.exists() or INSTALL_BUNDLE.is_symlink():
            if INSTALL_BUNDLE.is_symlink():
                raise BuildError(f"refusing symlinked installed bundle: {INSTALL_BUNDLE}")
            rename_swap(staged_bundle, INSTALL_BUNDLE)
            shutil.rmtree(staged_bundle)
        else:
            os.rename(staged_bundle, INSTALL_BUNDLE)
        os.replace(staged_client, CLIENT_DESTINATION)
    finally:
        if staged_bundle.exists() and not staged_bundle.is_symlink():
            shutil.rmtree(staged_bundle)
        elif staged_bundle.is_symlink():
            staged_bundle.unlink()
        staged_client.unlink(missing_ok=True)

    installed_identity = verify(INSTALL_BUNDLE)
    return {
        "source": source_identity,
        "installed": installed_identity,
        "client": str(CLIENT_DESTINATION),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "build", "verify", "install"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / ".code" / "macos-ui-helper-build",
    )
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = check_contract()
        elif args.command == "build":
            result = build(args.output_root)
        elif args.command == "install":
            result = install(args.output_root)
        else:
            result = verify(args.output_root.resolve() / "MacOSUIHelper.app")
    except BuildError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
