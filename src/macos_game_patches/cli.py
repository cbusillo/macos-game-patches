import argparse
import sys
from pathlib import Path
from typing import Dict

from .patch_runner import PatchSpec, load_spec_from_path, resolve_game_path, run_patch


def find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "patches").exists():
            return parent
    return Path.cwd()


def discover_specs(repo_root: Path) -> Dict[str, Path]:
    specs: Dict[str, Path] = {}
    patches_dir = repo_root / "patches"
    for spec_path in patches_dir.glob("*/patch_spec.py"):
        slug = spec_path.parent.name
        specs[slug] = spec_path
        # simple short alias: take letters/numbers from first letters
        short = "".join(part[0] for part in slug.split("-"))
        if short and short not in specs:
            specs[short] = spec_path
    for spec_path in patches_dir.glob("*/patch.toml"):
        slug = spec_path.parent.name
        specs.setdefault(slug, spec_path)
        short = "".join(part[0] for part in slug.split("-"))
        specs.setdefault(short, spec_path)
    return specs


def build_parser(available_specs: Dict[str, Path]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run macOS game patch specs (CrossOver/Wine)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Patch target slug (e.g., 'space-engineers-2' or 'se2') or path to a patch.toml",
    )
    parser.add_argument(
        "--game-path",
        help="Override game install path (folder containing patched files)",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Restore from backups instead of applying the patch",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check patch status only",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available specs and exit",
    )

    parser.set_defaults(available_specs=available_specs)
    return parser


def main(argv: list[str] | None = None) -> int:
    repo_root = find_repo_root()
    specs = discover_specs(repo_root)
    parser = build_parser(specs)
    args = parser.parse_args(argv)

    if args.list:
        if not specs:
            print("No patch specs found under 'patches/'.")
            return 1
        print("Available patch specs:")
        for slug, path in sorted(specs.items()):
            print(f"  {slug}: {path}")
        return 0

    if not args.target:
        parser.error("target is required unless --list is provided")

    target = args.target
    spec_path: Path

    if target in specs:
        spec_path = specs[target]
    else:
        maybe_path = Path(target)
        if maybe_path.exists():
            spec_path = maybe_path
        else:
            parser.error(f"Unknown target '{target}'. Use --list to see options.")
            return 1

    try:
        spec: PatchSpec = load_spec_from_path(spec_path)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Failed to load spec from {spec_path}: {exc}")
        return 1

    game_path = resolve_game_path(spec, args.game_path)
    if game_path is None:
        print("ERROR: Could not determine game path. Provide --game-path.")
        return 1
    if not game_path.exists():
        print(f"ERROR: Path does not exist: {game_path}")
        return 1

    return run_patch(spec, game_path, restore=args.restore, check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
