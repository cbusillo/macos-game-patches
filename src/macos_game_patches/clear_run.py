import argparse
import os
import shutil
import subprocess
from pathlib import Path

from .cli import discover_specs, find_repo_root
from .patch_runner import load_spec_from_path, resolve_game_path, run_patch


def kill_processes(patterns: list[str]) -> None:
    for pat in patterns:
        subprocess.run(
            ["pkill", "-f", pat],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def remove_path(path: Path) -> None:
    if not path.exists():
        print(f"  {path} not present (nothing to do)")
        return
    if path.is_file():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path, ignore_errors=True)
    print(f"  Removed {path}")


def get_darwin_cache_root(game_exe: str) -> Path:
    proc = subprocess.run(
        ["getconf", "DARWIN_USER_CACHE_DIR"],
        check=False,
        capture_output=True,
        text=True,
    )
    base = proc.stdout.strip() or "/var/folders"
    return Path(base) / "d3dm" / game_exe


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear caches, apply patch spec, and launch via CrossOver",
    )
    parser.add_argument(
        "--patch-target",
        default="space-engineers-2",
        help="Patch target slug or path to a patch.toml (or legacy patch_spec.py)",
    )
    parser.add_argument("--bottle", default="Space Engineers", help="CrossOver bottle name")
    parser.add_argument("--game-folder", default="SpaceEngineers2", help="Game folder under Steam common")
    parser.add_argument("--game-exe", default=None, help="Game exe name (defaults to <game-folder>.exe)")
    parser.add_argument(
        "--crossover-bin",
        default="/Applications/CrossOver Preview.app/Contents/SharedSupport/CrossOver/bin/wine",
        help="Path to CrossOver wine binary",
    )
    parser.add_argument("--skip-patch", action="store_true", help="Skip running the patch before launch")
    parser.add_argument("--skip-clear-args", action="store_true", help="Do not append spec-defined clear_run args to the launch command")
    parser.add_argument(
        "--use-steam-applaunch",
        action="store_true",
        help="Launch via steam.exe -applaunch <appid> (avoids custom-args popup); defaults to True",
        default=True,
    )
    args = parser.parse_args()

    game_exe = args.game_exe or f"{args.game_folder}.exe"

    repo_root = find_repo_root()
    specs = discover_specs(repo_root)

    target = args.patch_target
    if target in specs:
        spec_path = specs[target]
    else:
        spec_candidate = Path(target)
        if spec_candidate.exists():
            spec_path = spec_candidate
        else:
            print(f"ERROR: Unknown patch target '{target}'. Use --patch-target or --list via 'uv run patch -- --list'.")
            return 1

    try:
        spec = load_spec_from_path(spec_path)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Failed to load spec: {exc}")
        return 1

    bottle_root = Path.home() / "Library/Application Support/CrossOver/Bottles" / args.bottle
    game_root = bottle_root / "drive_c/Program Files (x86)/Steam/steamapps/common" / args.game_folder
    game_dir = game_root / "Game2"

    if not game_dir.exists():
        print(f"ERROR: Game directory not found: {game_dir}")
        return 1

    if not args.skip_patch:
        game_path = resolve_game_path(spec, str(game_dir))
        if game_path is None:
            print("ERROR: Could not resolve game path for patch.")
            return 1
        run_patch(spec, game_path, restore=False, check_only=False)

    launch_args = [] if args.skip_clear_args else (spec.clear_run_args or [])

    user_root = bottle_root / "drive_c/users/crossover/AppData"
    roaming_root = user_root / "Roaming" / args.game_folder
    shader_cache_dir = roaming_root / "Temp/ShaderCache"
    logs_dir = roaming_root / "Temp/Logs"
    d3dm_root = get_darwin_cache_root(game_exe)

    print("\nClearing shader caches...")
    remove_path(shader_cache_dir)
    remove_path(d3dm_root / "shaders.cache")

    print(f"Leaving logs in place at: {logs_dir}")

    print("\nKilling stray processes...")
    kill_processes([
        game_exe,
        "steam.exe",
        "steamwebhelper.exe",
        "cxmanip.exe",
        args.bottle,
    ])

    crossover_bin = Path(args.crossover_bin)
    if not crossover_bin.exists():
        print(f"ERROR: CrossOver wine binary not found at: {crossover_bin}")
        return 1

    env = dict(os.environ)
    env["WINEPREFIX"] = str(bottle_root)

    print("\nLaunching via CrossOver...")
    if args.use_steam_applaunch:
        cmd = [
            str(crossover_bin),
            "C\\Program Files (x86)\\Steam\\steam.exe",
            "-applaunch",
            "1133870",
            *launch_args,
        ]
    else:
        cmd = [
            str(crossover_bin),
            f"C:\\Program Files (x86)\\Steam\\steamapps\\common\\{args.game_folder}\\Game2\\{game_exe}",
            *launch_args,
        ]
    try:
        subprocess.Popen(cmd, env=env)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Failed to launch game: {exc}")
        return 1

    print("Launch command issued. Configure Steam launch args as needed.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
