# Patch CLI

Use the packaged entry point `patch` (via uv) to apply or inspect patches.

## Basic commands

- List available patch specs:
  - `uv run patch -- --list`
- Apply Space Engineers 2 patch:
  - `uv run patch se2`
- Check status without modifying files:
  - `uv run patch se2 --check`
- Restore from backups:
  - `uv run patch se2 --restore`
- Clear caches and launch via CrossOver (defaults to SE2):
  - `uv run clear-run`
  - See `uv run clear-run -- --help` for bottle/game overrides.

## Arguments

- `target` (positional): slug like `space-engineers-2` (alias: `se2`) or a
  path to a `patch.toml`.
- `--game-path`: override auto-detected game folder (path that contains the
  files listed in the spec).
- `--restore`: copy `.backup` files back over the patched files.
- `--check`: report patch status only.
- `--list`: show discovered specs and exit.

## Requirements

- Python 3.12+
- `uv` installed (`pip install uv`), or use `python -m macos_game_patches.cli`
  directly if you prefer.
