#!/usr/bin/env python3

"""Build and deploy the ALVR visionOS client to a paired Apple Vision Pro.

This is an operator helper for local device deployment (not App Store export).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_REPO = Path("~/Developer/ALVR-visionos").expanduser()
DEFAULT_PROJECT = "ALVRClient.xcodeproj"
DEFAULT_SCHEME = "ALVRClient"
DEFAULT_DEVICE = "Apple Vision Pro"
DEFAULT_CONFIGURATION = "Release"
DEFAULT_BUNDLE_ID = "com.shinycomputers.alvrclient"


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command))
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and install ALVR visionOS app")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="path to ALVR-visionos checkout")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="xcodeproj path relative to --repo")
    parser.add_argument("--scheme", default=DEFAULT_SCHEME, help="Xcode scheme name")
    parser.add_argument(
        "--configuration",
        default=DEFAULT_CONFIGURATION,
        choices=["Debug", "Release"],
        help="Xcode configuration",
    )
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="devicectl device name/UDID")
    parser.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID, help="app bundle identifier")
    parser.add_argument(
        "--derived-data",
        type=Path,
        default=None,
        help="derived data directory (defaults to <repo>/build/DerivedData)",
    )
    parser.add_argument("--skip-build", action="store_true", help="skip xcodebuild and use existing .app")
    parser.add_argument("--no-launch", action="store_true", help="install only, do not launch")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    repo = args.repo.expanduser().resolve()
    project = repo / args.project
    derived_data = args.derived_data.expanduser().resolve() if args.derived_data else repo / "build/DerivedData"
    products_dir = derived_data / "Build/Products"
    platform_dir = products_dir / ("Debug-xros" if args.configuration == "Debug" else "Release-xros")
    app_path = platform_dir / "ALVRClient.app"

    if not project.exists():
        print(f"ERROR: project not found: {project}", file=sys.stderr)
        return 1

    if not args.skip_build:
        build_cmd = [
            "xcodebuild",
            "-project",
            str(project),
            "-scheme",
            args.scheme,
            "-configuration",
            args.configuration,
            "-destination",
            "generic/platform=visionOS",
            "-derivedDataPath",
            str(derived_data),
            "build",
        ]
        build = run(build_cmd, cwd=repo)
        if build.returncode != 0:
            print("ERROR: xcodebuild failed", file=sys.stderr)
            return build.returncode

    if not app_path.exists():
        print(f"ERROR: built app not found at {app_path}", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(prefix="avp-install-", suffix=".json", delete=False) as install_tmp:
        install_json_path = Path(install_tmp.name)
    install_cmd = [
        "xcrun",
        "devicectl",
        "device",
        "install",
        "app",
        "--device",
        args.device,
        str(app_path),
        "--json-output",
        str(install_json_path),
    ]
    install = run(install_cmd)
    if install.returncode != 0:
        print("ERROR: app install failed", file=sys.stderr)
        return install.returncode

    if not args.no_launch:
        with tempfile.NamedTemporaryFile(prefix="avp-launch-", suffix=".json", delete=False) as launch_tmp:
            launch_json_path = Path(launch_tmp.name)
        launch_cmd = [
            "xcrun",
            "devicectl",
            "device",
            "process",
            "launch",
            "--device",
            args.device,
            "--terminate-existing",
            args.bundle_id,
            "--json-output",
            str(launch_json_path),
        ]
        launch = run(launch_cmd)
        if launch.returncode != 0:
            print("ERROR: app launch failed", file=sys.stderr)
            return launch.returncode

    summary = {
        "repo": str(repo),
        "project": str(project),
        "scheme": args.scheme,
        "configuration": args.configuration,
        "derived_data": str(derived_data),
        "app_path": str(app_path),
        "device": args.device,
        "bundle_id": args.bundle_id,
        "installed": True,
        "launched": not args.no_launch,
    }
    print("DEPLOY_SUMMARY=" + json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

