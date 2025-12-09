# Space Engineers 2 – Technical Notes

## Root cause

- Launcher rejects GPUs without FP64 shader ops by checking
  `ForceAllAdaptersSupported` in `VRage.Render.dll`.
- Renderer also enters an AMD AGS teraflops path that asserts under
  Wine/Metal.

## Patch points

- `VRage.Render12.dll` @ `0x3C143`: vendor ID -> `-1` (skip AMD AGS path)
- `VRage.Render12.dll` @ `0x99136`: same vendor tweak (second occurrence)
- `VRage.Render12.dll` @ `0x9925F`: force adapter support override (push `true` instead of calling `RenderConfiguration.ForceAllAdaptersSupported`)

Tested on build **2.0.2.39** (Steam build **21100537**, updated 2025-12-09). Offsets are unchanged from 2.0.2.21.

## Notes for future builds

- If offsets shift, search for the AMD vendor ID literal `0x00001002` near
  AGS teraflops calls.
- `ForceAllAdaptersSupported` is a trivial property getter; its IL should be
  `ldarg.0; ldfld; ret`.
- Patch runner validates both original and patched byte sequences and will
  warn if bytes differ.
