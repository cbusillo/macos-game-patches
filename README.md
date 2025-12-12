# VR on macOS (Apple-only)

This repo is a workspace for running Windows VR games on Apple hardware.

Primary goal:

- Run the game under CrossOver/Wine on Apple Silicon.
- Provide a VR runtime path that works without a Windows PC.
- Stream to Apple Vision Pro (ALVR client) with hardware encoding on Apple Silicon.

This repo currently contains two major tracks:

1. **VR runtime experiments** (ALVR + OpenVR work)
2. **Binary patch specs** that bypass unnecessary compatibility checks in games

## Start Here

- Repo overview: `README.md` (this file)
- VR runtime work: `vr-on-macos/README.md`
- Patch CLI usage: `docs/cli.md`
- Dev environments (macOS + optional Windows): `docs/dev-environments.md`
- Tools list: `TOOLS.md`
- Cache clear + launch helper: `uv run clean-patch-run -- --help`
- Clean only (no patch/launch): `uv run clean -- --help`

Patch specs are data-only TOML files under `patches/<game>/`; shared code lives
in `src/macos_game_patches/`.

VR runtime work lives under `vr-on-macos/`.

Local-only host notes (not committed): see `.local.md.example`.

## Why This Exists

Many Windows games (including VR titles) perform hardware checks that incorrectly
reject macOS systems:

- **GPU capability checks** that fail on Apple Silicon (M1/M2/M3/M4)
- **Driver version checks** that fail under Wine's emulation layer
- **DirectX feature checks** that translation layers don't fully expose

These patches bypass unnecessary compatibility checks, allowing games to run when the underlying hardware is actually capable.

For VR specifically, the long-term goal is to run Windows VR titles under
CrossOver while presenting a compatible OpenVR/SteamVR-like runtime backed by an
Apple-native streaming stack (ALVR), so video encoding can use Apple Silicon
hardware.

## Available Patches

| Game                                           | Status    | Issue                             | Solution     |
|------------------------------------------------|-----------|-----------------------------------|--------------|
| [Space Engineers 2](patches/space-engineers-2) | ✅ Working | FP64 shader check, driver version | Binary patch |

## VR Runtime Status

VR work-in-progress docs live under `vr-on-macos/`.

If you're trying to understand the current blocker quickly, start with:

- `vr-on-macos/ALVR/docs/CURRENT_STATUS_AND_NEXT_STEPS.md`
- `vr-on-macos/ALVR/docs/VRCLIENT_MACOS_STATUS.md`

## Quick Start

```bash
# Clone the repo
git clone <repo-url>
cd <repo-folder>

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
- Apple Silicon recommended
- CrossOver (or Wine)
- Python 3.12+ (use `uv` for pinned tooling)

Some work (reverse engineering / Windows-side tooling) is easier with access to a
native Windows dev environment; keep any machine-specific details in `.local.md`
(gitignored) so this repo stays contributor-friendly.

## How Patches Work

Each patch is a data spec (`patch.toml`) consumed by the shared runner that:

1. **Detects** the game installation automatically
2. **Backs up** original files before modification
3. **Patches** specific bytes in game binaries
4. **Verifies** the patch was applied correctly

All patches are reversible with the `--restore` flag.

## Local Docs (gitignored)

Use `.local.md` for machine-specific details (hostnames, mounts, personal paths,
credentials, etc.).

- Copy `.local.md.example` to `.local.md` and edit it.
- `.local.md` is intentionally gitignored.

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
