# Space Engineers 2 – macOS Compatibility Patch

This patch bypasses the launch-time GPU gate inside `VRage.Render12.dll` and
skips an AMD AGS code path that asserts under D3DMetal.

## What it does

- Forces adapters to be treated as supported inside `VRage.Render12.dll`
- Skips the AMD AGS teraflops vendor path (both occurrences) in `VRage.Render12.dll`

Tested with Space Engineers 2 build **2.0.2.39** (Steam build **21100537**,
updated 2025-12-09).

## Requirements

- macOS 12+
- CrossOver 24+ (or Wine 8+)
- Space Engineers 2 installed in a CrossOver bottle
- Python 3.12+

## Usage

From the repo root (requires uv, Python 3.12+):

```bash
uv run patch se2                # apply
uv run patch se2 --check        # status only
uv run patch se2 --restore      # restore backups
```

### Game path

The script auto-detects CrossOver bottles. To override, pass the path to the
`Game2` folder:

```bash
uv run patch se2 --game-path \
  "/path/to/CrossOver/Bottles/Space Engineers/drive_c/Program Files (x86)/Steam/steamapps/common/SpaceEngineers2/Game2"
```

Backups with the `.backup` suffix are created next to patched files.

## Notes

- Re-run the patch after game updates.
- `--restore` puts files back from `.backup` if present.
- Default clean-patch-run args include `-windowed`, `-resolution:1600x900`,
  `-disablevsync`, `-startLast`, and `-forceAllAdaptersSupported`. If you hit
  a startup error related to splash, remove `-nosplash` from `[clear_run].args`.
- Current issue: game launches but only UI/background render; foreground/scene
  geometry is invisible. Assets have been re-synced from a pristine Windows
  install and GPU gate patches are applied; the likely cause is in the render
  pipeline (culling or final copy). See `TECHNICAL.md` for the latest findings
  and suggested next investigation steps.
- Latest (2025-12-10): Disabling main-view culling and Hi-Z via env flags did
  not restore geometry; game sometimes shows a “graphics below minimum”
  popup but still runs with blank scene. Next steps and logging plan are in
  `TECHNICAL.md`.
- See `TECHNICAL.md` for byte offsets and rationale.
