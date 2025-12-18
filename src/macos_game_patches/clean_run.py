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


def _build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
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
    parser.add_argument("--skip-clean", action="store_true", help="Skip cache/process cleanup")
    parser.add_argument(
        "--skip-spec-args",
        "--skip-clear-args",
        dest="skip_spec_args",
        action="store_true",
        help="Do not append spec-defined launch args to the launch command",
    )
    parser.add_argument(
        "--no-steam-applaunch",
        action="store_true",
        help="Launch the game executable directly instead of steam -applaunch",
    )
    parser.add_argument(
        "--steam-extra",
        nargs="*",
        default=None,
        help="Extra args to append after -applaunch <appid> (useful for Steam launch options).",
    )
    return parser


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


def _launch(
    *,
    bottle_root: Path,
    crossover_bin: Path,
    game_folder: str,
    game_exe: str,
    steam_app_id: str | None,
    launch_args: list[str],
    steam_extra: list[str],
    no_steam_applaunch: bool,
) -> int:
    if not crossover_bin.exists():
        print(f"ERROR: CrossOver wine binary not found at: {crossover_bin}")
        return 1

    env = dict(os.environ)
    env["WINEPREFIX"] = str(bottle_root)

    print("\nLaunching via CrossOver...")
    if no_steam_applaunch or not steam_app_id:
        cmd = [
            str(crossover_bin),
            f"C:\\Program Files (x86)\\Steam\\steamapps\\common\\{game_folder}\\Game2\\{game_exe}",
            *launch_args,
        ]
    else:
        cmd = [
            str(crossover_bin),
            "C:\\Program Files (x86)\\Steam\\steam.exe",
            "-applaunch",
            steam_app_id,
            *launch_args,
            *steam_extra,
        ]

    try:
        subprocess.Popen(cmd, env=env)
    except OSError as exc:
        print(f"ERROR: Failed to launch game: {exc}")
        return 1

    print("Launch command issued.")
    return 0


def _clean(bottle_root: Path, game_folder: str, game_exe: str, bottle_name: str) -> None:
    user_root = bottle_root / "drive_c/users/crossover/AppData"
    roaming_root = user_root / "Roaming" / game_folder
    shader_cache_dir = roaming_root / "Temp/ShaderCache"
    logs_dir = roaming_root / "Temp/Logs"
    d3dm_root = get_darwin_cache_root(game_exe)

    print("\nClearing shader caches...")
    remove_path(shader_cache_dir)
    remove_path(d3dm_root / "shaders.cache")

    print(f"Leaving logs in place at: {logs_dir}")

    print("\nKilling stray processes...")
    kill_processes(
        [
            game_exe,
            "steam.exe",
            "steamwebhelper.exe",
            "cxmanip.exe",
            bottle_name,
        ]
    )


def _main(*, default_do_clean: bool, default_do_patch: bool, description: str) -> int:
    parser = _build_parser(description)
    args = parser.parse_args()

    spec_path = _resolve_spec_path(args.patch_target)
    if spec_path is None:
        return 1

    try:
        spec = load_spec_from_path(spec_path)
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: Failed to load spec: {exc}")
        return 1

    game_exe = args.game_exe or f"{args.game_folder}.exe"

    bottle_root = Path.home() / "Library/Application Support/CrossOver/Bottles" / args.bottle
    game_root = bottle_root / "drive_c/Program Files (x86)/Steam/steamapps/common" / args.game_folder
    game_dir = game_root / "Game2"

    if not game_dir.exists():
        print(f"ERROR: Game directory not found: {game_dir}")
        return 1

    if default_do_patch and not args.skip_patch:
        game_path = resolve_game_path(spec, str(game_dir))
        if game_path is None:
            print("ERROR: Could not resolve game path for patch.")
            return 1
        run_patch(spec, game_path, restore=False, check_only=False)

    if default_do_clean and not args.skip_clean:
        _clean(bottle_root, args.game_folder, game_exe, args.bottle)

    launch_args = [] if args.skip_spec_args else (spec.clear_run_args or [])
    steam_extra = args.steam_extra or []

    return _launch(
        bottle_root=bottle_root,
        crossover_bin=Path(args.crossover_bin),
        game_folder=args.game_folder,
        game_exe=game_exe,
        steam_app_id=spec.clear_run_steam_app_id,
        launch_args=launch_args,
        steam_extra=steam_extra,
        no_steam_applaunch=args.no_steam_applaunch,
    )


def main_clean_patch_run() -> int:
    return _main(
        default_do_clean=True,
        default_do_patch=True,
        description="Clean caches, apply patch spec, and launch via CrossOver",
    )


def main_clean_run() -> int:
    return _main(
        default_do_clean=True,
        default_do_patch=False,
        description="Clean caches and launch via CrossOver",
    )


def main_run() -> int:
    return _main(
        default_do_clean=False,
        default_do_patch=False,
        description="Launch via CrossOver",
    )


def main_clear_run() -> int:
    print("DEPRECATED: 'clear-run' was renamed to 'clean-run'.")
    print("Use: uv run clean-patch-run  (or: uv run clean-run)")
    return main_clean_patch_run()


def main() -> int:
    return main_clean_patch_run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
