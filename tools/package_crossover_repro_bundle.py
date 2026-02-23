#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


REQUIRED_RELATIVE_PATHS = [
    "config/outcome.json",
    "config/meta.json",
    "config/steamvr.vrsettings.before.json",
    "config/steamvr.vrsettings.after.json",
    "logs/session_log.txt",
    "logs/session_log.delta.txt",
    "logs/vrserver.delta.txt",
    "logs/vrcompositor.delta.txt",
    "logs/vtbridge-daemon.log",
]


def resolve_run_dir(run_dir: str) -> Path:
    path = Path(run_dir).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise SystemExit(f"run directory not found: {path}")
    return path


def collect_existing_files(run_dir: Path) -> tuple[list[Path], list[str]]:
    existing: list[Path] = []
    missing: list[str] = []
    for rel in REQUIRED_RELATIVE_PATHS:
        candidate = run_dir / rel
        if candidate.exists() and candidate.is_file():
            existing.append(candidate)
        else:
            missing.append(rel)
    return existing, missing


def write_zip(run_dir: Path, output_zip: Path, files: list[Path], missing: list[str]) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "run_dir": str(run_dir),
            "included": [str(path.relative_to(run_dir)) for path in files],
            "missing": missing,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for path in files:
            arcname = path.relative_to(run_dir)
            zf.write(path, arcname)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package a CrossOver repro bundle zip")
    parser.add_argument("--run-dir", required=True, help="run directory under temp/vr_runs")
    parser.add_argument(
        "--output",
        help="output zip path (default: <run-dir>/repro-bundle.zip)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = resolve_run_dir(args.run_dir)
    output_zip = Path(args.output).expanduser().resolve() if args.output else run_dir / "repro-bundle.zip"
    files, missing = collect_existing_files(run_dir)
    if not files:
        raise SystemExit("no known repro files found; check run directory path")

    write_zip(run_dir, output_zip, files, missing)
    print(json.dumps({"output": str(output_zip), "included": len(files), "missing": missing}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

