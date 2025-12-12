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
- Current state (2025-12-10):
  - GPU gate is bypassed; adapter reported as supported.
  - Assets re-synced from pristine Windows install; no missing-material errors remain.
  - Game launches on macOS/CrossOver but only UI/background render; scene/foreground geometry is invisible.
  - Likely root cause: render pipeline stage (culling or final copy) rather than content.

### Additional experiments (2025-12-10)

- Added env toggles to disable main-view culling and Hi-Z:
  - `SE2_DISABLE_CULL=1` short-circuits both cull passes in `CullingJob`.
  - `SE2_DISABLE_HZB=1` skips `BuildHiZBuffer`.
  - Result: geometry still invisible; blank scene persists.
- Occasional popup (“graphics below minimum”) appears, but game continues to
  run with blank scene.
- Procdump attach inside the bottle was blocked (`Access denied`); no full
  dump captured yet. Windows VM runs fine, so the issue is Wine/Metal specific.
- WINEDEBUG logs gathered so far are too short (process exits early in traces);
  need a full-run log captured via `cxstart --cx-log /tmp/se2_winedbg_full.log`.

## Suggested next debugging steps

- Instrument `SceneDrawSystem`:
  - Log per-frame main-view cull/entity counts after culling.
  - Optionally dump GBuffer/depth snapshot to confirm geometry draw.
  - Add a transient solid-color overlay after tone mapping to verify final copy/present path.
- If cull counts are zero, temporarily disable culling (`MainViewCulling` first/second pass) to confirm visibility path.
- If cull counts are non-zero but output is blank, inspect GBuffer → lighting → final copy path for Metal/translation issues.
- Keep adapter-support override and vendor ID patches in place; they are confirmed working.

## Notes for future builds

- If offsets shift, search for the AMD vendor ID literal `0x00001002` near
  AGS teraflops calls.
- `ForceAllAdaptersSupported` is a trivial property getter; its IL should be
  `ldarg.0; ldfld; ret`.
- Patch runner validates both original and patched byte sequences and will
  warn if bytes differ.
