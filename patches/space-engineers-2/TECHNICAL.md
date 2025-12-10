# Space Engineers 2 – Technical Notes

## Root cause

- Renderer checks adapter support and enters an AMD AGS teraflops path inside
  `VRage.Render12.dll` that fails on Wine/Metal.

## Patch points

- `VRage.Render12.dll` @ `0x3C143`: vendor ID -> `-1` (skip AMD AGS path)
- `VRage.Render12.dll` @ `0x99136`: same vendor tweak (second occurrence)
- `VRage.Render12.dll` @ `0x9925F`: force adapter support override (push `true` instead of calling `RenderConfiguration.ForceAllAdaptersSupported`)

Tested on build **2.0.2.39** (Steam build **21100537**, updated 2025-12-09). Offsets are unchanged from 2.0.2.21.

## Observations (2025-12-09 run)

- GPU gate is bypassed; adapter is reported as supported in Render12 logs.
- Foreground geometry is missing because many shaders fail to load, e.g.
  `geometry/materials/flatcolor*.hlsl`, `shadedcolor*.hlsl`, and physics debug/SSR shaders are reported missing in `SpaceEngineers2_251209_192504_437_2044_Render12.log`.
- Recommended next action: verify game files in Steam inside the CrossOver bottle to restore missing shader/content files.

## Notes for future builds

- If offsets shift, search for the AMD vendor ID literal `0x00001002` near
  AGS teraflops calls.
- `ForceAllAdaptersSupported` is a trivial property getter; its IL should be
  `ldarg.0; ldfld; ret`.
- Patch runner validates both original and patched byte sequences and will
  warn if bytes differ.
