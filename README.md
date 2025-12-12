# macOS Game Patches

Binary patches to run Windows games on macOS via CrossOver, Wine, or Game Porting Toolkit.

## Start Here

- User docs: `README.md` (this file)
- Game docs: `patches/space-engineers-2/README.md`
- Patch CLI usage: `docs/cli.md`
- Tools list: `TOOLS.md`
- Cache clear + launch helper: `uv run clean-patch-run -- --help`
- Clean only (no patch/launch): `uv run clean -- --help`
- Patch specs are data-only TOML files under `patches/<game>/`; shared code
  lives in `src/macos_game_patches/`.

## Why This Exists

Many Windows games perform hardware checks that incorrectly reject macOS systems:

- **GPU capability checks** that fail on Apple Silicon (M1/M2/M3/M4)
- **Driver version checks** that fail under Wine's emulation layer
- **DirectX feature checks** that translation layers don't fully expose

These patches bypass unnecessary compatibility checks, allowing games to run when the underlying hardware is actually capable.

## Available Patches

| Game                                           | Status    | Issue                             | Solution     |
|------------------------------------------------|-----------|-----------------------------------|--------------|
| [Space Engineers 2](patches/space-engineers-2) | ✅ Working | FP64 shader check, driver version | Binary patch |

## Quick Start

```bash
# Clone the repo
git clone https://github.com/cbusillo/macos-game-patches.git
cd macos-game-patches

# Run a specific patch (uses pyproject entrypoint)
uv run patch se2

# Status / restore examples
uv run patch se2 --check
uv run patch se2 --restore
```

Or run directly without cloning:

```bash
# (raw-run example removed for now; run from local clone)| python3
```

## Working files (do not commit binaries)

- Use `temp/` for any extracted game DLLs, EXEs, dumps, or other proprietary assets. This folder is gitignored.
- Patch scripts create `.backup` files next to binaries; these are also ignored.
- Keep docs and scripts under version control; keep vendor binaries out.

## Requirements

- macOS 12+ (Monterey or later)
- [CrossOver](https://www.codeweavers.com/crossover) 24+ or Wine 8+
- Python 3.13+ (use `uv` for pinned tooling)
- Game installed via Steam in CrossOver/Wine

## How Patches Work

Each patch is a data spec (`patch.toml`) consumed by the shared runner that:

1. **Detects** the game installation automatically
2. **Backs up** original files before modification
3. **Patches** specific bytes in game binaries
4. **Verifies** the patch was applied correctly

All patches are reversible with the `--restore` flag.

## Contributing

### Adding a New Patch

1. Create a folder under `patches/` with the game name (lowercase, hyphens)
2. Include:
   - `patch.toml` - Data-only patch spec
   - `README.md` - Documentation for users
   - `TECHNICAL.md` - Technical details for developers

### Patch Development Process

1. **Identify the issue** - Check game logs for error messages
2. **Decompile** - Use ILSpy, dnSpy, or Ghidra to analyze binaries
3. **Find the check** - Locate the compatibility check in code
4. **Create minimal patch** - Modify only what's necessary
5. **Test thoroughly** - Verify game runs and patch is reversible
6. **Document everything** - Others should understand what and why

See [AGENTS.md](AGENTS.md) for AI-assisted development guidelines.

## Disclaimer

- These patches modify game files - use at your own risk
- Always keep backups (patches create them automatically)
- Patches may break after game updates
- This project is not affiliated with any game developers

## License

MIT License - See [LICENSE](LICENSE) for details.

## Credits

- Patches developed with [Claude Code](https://claude.ai/code)
- Thanks to the CrossOver, Wine, and DXVK communities
