# Space Engineers 2 - macOS Patch

Run Space Engineers 2 on macOS via CrossOver or Wine.

## Status

✅ **Working** - Tested with version 2.0.2.14 on Apple Silicon (M4 Max)

## The Problem

Space Engineers 2 refuses to launch on macOS with a "NoSupportedDevice" error because:

1. **FP64 Check**: The game requires GPUs that support double-precision float shaders. Apple Silicon GPUs don't support FP64 in hardware.

2. **Driver Version**: The game checks for minimum driver versions that Wine doesn't report correctly.

The game doesn't actually *use* FP64 shaders - it's just checking for a "modern GPU". This patch bypasses those checks.

## Requirements

- macOS 12+ with Apple Silicon or Intel
- [CrossOver](https://www.codeweavers.com/crossover) 24+ (recommended) or Wine 8+
- Space Engineers 2 installed via Steam
- Python 3.9+

## Installation

### 1. Install the Game

1. Create a CrossOver bottle (Windows 10 64-bit)
2. Install Steam in the bottle
3. Install Space Engineers 2
4. Try to launch - it will fail with "NoSupportedDevice"

### 2. Apply the Patch

```bash
# From the repo root
python3 patches/space-engineers-2/patch.py

# Or specify path manually
python3 patches/space-engineers-2/patch.py "/path/to/SpaceEngineers2/Game2"
```

### 3. Launch the Game

Start Space Engineers 2 normally through Steam/CrossOver.

## Commands

```bash
# Check if patches are applied
python3 patches/space-engineers-2/patch.py --check

# Apply patches
python3 patches/space-engineers-2/patch.py

# Restore original files
python3 patches/space-engineers-2/patch.py --restore
```

## What Gets Modified

| File | Change |
|------|--------|
| `VRage.Render.dll` | `ForceAllAdaptersSupported` returns `true` |
| `VRage.Render12.dll` | `IsSupported` check always passes |

Backups are created automatically (`.backup` extension).

## Troubleshooting

### Game still shows "NoSupportedDevice"
- Verify game files in Steam, then re-apply patch
- Check that you're pointing to the correct `Game2` folder

### "Unknown version" warning
- Game may have updated - patch might still work
- Try applying anyway, restore if it fails

### Game crashes after loading
- Try switching graphics backend in CrossOver (D3DMetal vs DXVK)
- Lower graphics settings

### Performance issues
- Use D3DMetal backend (better for Apple Silicon)
- Reduce resolution and graphics quality
- Close other applications

## Tested Configurations

| macOS | CrossOver | Game Version | Status |
|-------|-----------|--------------|--------|
| 15.1 (Sequoia) | 24.0.5 | 2.0.2.14 | ✅ Working |
| 15.1 (Sequoia) | 24.0.5 | 1.5.0.3105 | ✅ Working |

## Technical Details

See [TECHNICAL.md](TECHNICAL.md) for reverse engineering notes and patch details.
