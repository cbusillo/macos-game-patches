# Space Engineers 2 – Technical Notes

## Root cause

- Launcher rejects GPUs without FP64 shader ops by checking
  `ForceAllAdaptersSupported` in `VRage.Render.dll`.
- Renderer also enters an AMD AGS teraflops path that asserts under
  Wine/Metal.

## Patch points

- `VRage.Render.dll` @ `0x5856C`: `ldfld; ret` -> `ldc.i4.1; ret` (bypass GPU gate)
- `VRage.Render12.dll` @ `0x3C143`: vendor ID -> `-1` (skip AMD AGS path)
- `VRage.Render12.dll` @ `0x99136`: same vendor tweak (second occurrence)

Tested on build **2.0.2.21** (hash matches 2.0.2.39).

## Notes for future builds

- If offsets shift, search for the AMD vendor ID literal `0x00001002` near
  AGS teraflops calls.
- `ForceAllAdaptersSupported` is a trivial property getter; its IL should be
  `ldarg.0; ldfld; ret`.
- Patch runner validates both original and patched byte sequences and will
  warn if bytes differ.
