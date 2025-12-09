import glob
import importlib.util
import os
import sys

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


@dataclass
class PatchSegment:
    description: str
    offset: int
    original: bytes
    patched: bytes


@dataclass
class FilePatch:
    filename: str
    segments: list[PatchSegment]


@dataclass
class PatchSpec:
    game_name: str
    tested_version: Optional[str]
    files: list[FilePatch]
    find_game_path: Optional[Callable[[], Optional[Path]]] = None
    search_globs: Optional[list[str]] = None


def _check_file_status(filepath: Path, segments: Iterable[PatchSegment]) -> str:
    statuses: list[str] = []
    with open(filepath, "rb") as handle:
        for segment in segments:
            handle.seek(segment.offset)
            current = handle.read(len(segment.original))
            if current == segment.original:
                statuses.append("original")
            elif current == segment.patched:
                statuses.append("patched")
            else:
                statuses.append("unknown")

    if all(s == "original" for s in statuses):
        return "original"
    if all(s == "patched" for s in statuses):
        return "patched"
    if any(s == "unknown" for s in statuses):
        return "unknown"
    return "original"


def _backup_file(filepath: Path) -> None:
    backup_path = Path(str(filepath) + ".backup")
    if backup_path.exists():
        print(f"  Backup already exists: {backup_path.name}")
        return
    backup_path.write_bytes(filepath.read_bytes())
    print(f"  Created backup: {backup_path.name}")


def _restore_file(filepath: Path) -> None:
    backup_path = Path(str(filepath) + ".backup")
    if not backup_path.exists():
        print(f"  No backup found for: {filepath.name}")
        return
    filepath.write_bytes(backup_path.read_bytes())
    print(f"  Restored from backup: {filepath.name}")


def _apply_segments(filepath: Path, segments: Iterable[PatchSegment]) -> None:
    with open(filepath, "r+b") as handle:
        for segment in segments:
            handle.seek(segment.offset)
            handle.write(segment.patched)
            print(f"  Patched: {filepath.name}")
            print(f"    {segment.description}")


def run_patch(spec: PatchSpec, game_path: Path, restore: bool, check_only: bool) -> int:
    missing = [fp.filename for fp in spec.files if not (game_path / fp.filename).exists()]
    if missing:
        print("ERROR: Missing required files:")
        for name in missing:
            print(f"  - {name}")
        return 1

    print(f"\n{spec.game_name} macOS Patch")
    if spec.tested_version:
        print(f"Tested with version: {spec.tested_version}")
    print("=" * 50)

    print("\nChecking files...")
    statuses: dict[str, str] = {}
    for fp in spec.files:
        filepath = game_path / fp.filename
        status = _check_file_status(filepath, fp.segments)
        statuses[fp.filename] = status
        status_str = {
            "original": "✗ Not patched",
            "patched": "✓ Already patched",
            "unknown": "? Unknown version",
        }[status]
        print(f"  {fp.filename}: {status_str}")

    if check_only:
        return 0

    if restore:
        print("\nRestoring original files...")
        for fp in spec.files:
            _restore_file(game_path / fp.filename)
        print("\nRestore complete!")
        return 0

    unknown = [name for name, status in statuses.items() if status == "unknown"]
    if unknown:
        print("\nWARNING: Some files have unknown versions (may be different game build):")
        for name in unknown:
            print(f"  - {name}")
        print("Continuing; per-segment checks will fail if bytes do not match.")

    if all(status == "patched" for status in statuses.values()):
        print("\nAll files are already patched! Nothing to do.")
        return 0

    print("\nApplying patches...")
    for fp in spec.files:
        filepath = game_path / fp.filename
        if statuses[fp.filename] == "patched":
            print(f"  Skipping {fp.filename} (already patched)")
            continue
        _backup_file(filepath)
        _apply_segments(filepath, fp.segments)

    print("\n" + "=" * 50)
    print("Patching complete!")
    print("\nYou can now run the game via CrossOver/Wine.")
    print("\nNote: If the game updates, re-run this patch.")
    return 0


def _parse_bytes(hex_string: str) -> bytes:
    cleaned = (
        hex_string.replace(",", " ")
        .replace("0x", " ")
        .replace("0X", " ")
        .strip()
    )
    parts = [p for p in cleaned.split() if p]
    return bytes(int(p, 16) for p in parts)


def _build_spec_from_toml(spec_path: Path) -> PatchSpec:
    with spec_path.open("rb") as handle:
        data = tomllib.load(handle)

    files: list[FilePatch] = []
    for file_entry in data.get("files", []):
        segments: list[PatchSegment] = []
        for seg in file_entry.get("segments", []):
            segments.append(
                PatchSegment(
                    description=seg["description"],
                    offset=int(str(seg["offset"]), 0),
                    original=_parse_bytes(seg["original"]),
                    patched=_parse_bytes(seg["patched"]),
                )
            )
        files.append(
            FilePatch(
                filename=file_entry["filename"],
                segments=segments,
            )
        )

    search_globs = None
    if "autodetect" in data and "search_globs" in data["autodetect"]:
        search_globs = data["autodetect"]["search_globs"]

    return PatchSpec(
        game_name=data["game_name"],
        tested_version=data.get("tested_version"),
        files=files,
        search_globs=search_globs,
    )


def _search_globs_for_game(spec: PatchSpec) -> Optional[Path]:
    if not spec.search_globs:
        return None
    for pattern in spec.search_globs:
        expanded = os.path.expanduser(pattern)
        for path_str in glob.glob(expanded):
            candidate = Path(path_str)
            if all((candidate / fp.filename).exists() for fp in spec.files):
                return candidate
    return None


def load_spec_from_path(spec_path: Path) -> PatchSpec:
    if not spec_path.exists():
        raise FileNotFoundError(f"Spec not found: {spec_path}")

    repo_root = spec_path.resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    if spec_path.suffix.lower() == ".toml":
        return _build_spec_from_toml(spec_path)

    spec = importlib.util.spec_from_file_location("patch_spec", spec_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to import spec from {spec_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["patch_spec"] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    if not hasattr(module, "PATCH_SPEC"):
        raise AttributeError("Spec module must define PATCH_SPEC")
    return module.PATCH_SPEC  # type: ignore[return-value]


def resolve_game_path(spec: PatchSpec, override: Optional[str]) -> Optional[Path]:
    if override:
        return Path(override)
    if spec.find_game_path:
        return spec.find_game_path()
    glob_path = _search_globs_for_game(spec)
    if glob_path:
        return glob_path
    return None
