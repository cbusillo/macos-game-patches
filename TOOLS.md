# macOS Game Patches – Tooling

This repo is currently scratch-only, but we keep a short list of tools that are
useful for SE2 and other Windows-on-macOS patch work.

## Binary / IL inspection

- `dnfile` (Python): quick IL + metadata inspection without Windows. Install:
  `python3 -m pip install dnfile`.
- `ilspycmd` (dotnet global tool): CLI decompiler. Install:
  `dotnet tool install -g ilspycmd`.
- `dotnet-ildasm` (dotnet global tool): dump IL. Install:
  `dotnet tool install -g dotnet-ildasm`.
- `strings` / `xxd` (macOS): fast sanity checks of offsets/bytes.

## Wine/CrossOver runtime

- `uv run clean-patch-run -- --help` — clear caches, reapply a patch spec, and launch
- `uv run clean -- --help` — clear caches and kill processes (no patch or launch)
  via CrossOver. Defaults target `space-engineers-2`; customize with
  `--patch-target`, `--bottle`, `--game-folder`, etc.

If you add other repeat-use tools, list them here so future sessions can find
