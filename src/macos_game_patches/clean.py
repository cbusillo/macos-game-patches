"""Clean caches and kill running processes without patching or launching."""

import argparse
from pathlib import Path

from .clean_run import get_darwin_cache_root, kill_processes, remove_path
from .cli import discover_specs, find_repo_root


def _resolve_spec_path(target: str) -> Path | None:
    repo_root = find_repo_root()
    specs = discover_specs(repo_root)

    if target in specs:
        return specs[target]

    candidate = Path(target)
    if candidate.exists():
        return candidate

    print(f"ERROR: Unknown patch target '{target}'. Use 'uv run patch --list' to see options.")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear caches and kill processes for a CrossOver/Wine game without launching it.",
    )
    parser.add_argument(
        "--patch-target",
        default="space-engineers-2",
        help="Patch target slug or path to a patch spec (used for defaults/validation).",
    )
    parser.add_argument("--bottle", default="Space Engineers", help="CrossOver bottle name")
    parser.add_argument("--game-folder", default="SpaceEngineers2", help="Game folder under Steam common")
    parser.add_argument("--game-exe", default=None, help="Game exe name (defaults to <game-folder>.exe)")
    args = parser.parse_args()

    if _resolve_spec_path(args.patch_target) is None:
        return 1

    bottle_root = Path.home() / "Library/Application Support/CrossOver/Bottles" / args.bottle
    game_root = bottle_root / "drive_c/Program Files (x86)/Steam/steamapps/common" / args.game_folder
    game_dir = game_root / "Game2"

    if not game_dir.exists():
        print(f"ERROR: Game directory not found: {game_dir}")
        return 1

    game_exe = args.game_exe or f"{args.game_folder}.exe"

    user_root = bottle_root / "drive_c/users/crossover/AppData"
    roaming_root = user_root / "Roaming" / args.game_folder
    shader_cache_dir = roaming_root / "Temp/ShaderCache"
    logs_dir = roaming_root / "Temp/Logs"
    d3dm_root = get_darwin_cache_root(game_exe)

    print("\nClearing shader caches...")
    remove_path(shader_cache_dir)
    remove_path(d3dm_root / "shaders.cache")

    print(f"\nLeaving logs in place at: {logs_dir}")

    print("\nKilling stray processes...")
    kill_processes([
        game_exe,
        "steam.exe",
        "steamwebhelper.exe",
        "cxmanip.exe",
        args.bottle,
    ])

    print("\nClean complete.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
