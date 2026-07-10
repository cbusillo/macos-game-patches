# Windows Reference VR Baseline

## Hypothesis

Before interpreting CrossOver/AVP geometry failures, capture a known-good real
Windows VR baseline for the target app. The baseline should answer what the app
normally shows first, how to reach world-locked 3D content, and what rendering
shape the OpenVR runtime receives.

## Environment

- Host: real Windows machine or VM with a working SteamVR/OpenVR headset path.
- Target app: the same real app selected for the CrossOver probe, preferably
  Freedom Locomotion VR first, then Robot Repair if needed.
- Runtime: normal SteamVR/OpenVR. Start without the CrossOver app-local shim; if
  the app behaves normally, a second pass may stage the submit shim against the
  real Windows SteamVR `openvr_api.dll` for metadata-only capture.
- Prepared Windows ALVR package on Winders:
  `C:\Users\gaming\Desktop\Installations\ALVR Windows v21.0.0-dev12 nightly 2026.06.16\`.
  It contains `ALVR Dashboard.exe`, `driver.vrdrivermanifest`, and the
  `bin\win64\` streamer/runtime DLLs copied from the v21 nightly bundle. A
  desktop shortcut is available at
  `C:\Users\gaming\Desktop\ALVR v21 nightly Dashboard.lnk`.
- Optional tools: SteamVR frame timing, mirror window capture, app logs,
  Process Monitor/module-load logs, GPU capture, and any OpenVR capture/debug
  overlay that does not alter app behavior.

## Command Or Procedure

1. Launch the target app normally on Windows.
2. Record the startup path from launch to the first true world-locked 3D scene.
3. Capture whether the first visible screens are menus, calibration, loading,
   intro panels, flat overlays, or already a 3D scene.
4. Move or rotate the headset in the real Windows run and note what should stay
   fixed in the world.
5. Record the expected first useful AVP-eye question for the same scene in the
   CrossOver path.
6. If tooling is available, capture OpenVR render facts without changing app
   behavior. If the app remains stable, run a second metadata pass with the
   submit shim staged app-local against the real Windows runtime.

## Evidence Artifacts

- App target, exact executable, and launch route.
- Short screen recording or screenshots of the path to first world-locked 3D.
- Notes for any required controller input, menu selection, recenter action, or
  wait time before the scene becomes useful.
- Whether the scene should be seated/standing, room-scale, front-facing, or
  controller-gated.
- Exact interface versions requested, including `IVRSystem`, `IVRCompositor`,
  `IVRApplications`, `IVRInput`, `IVRRenderModels`, and any legacy app-system
  surface such as Robot Repair's `VR_001` dependency.
- SteamVR render target size and per-eye recommended resolution, if visible.
- Per-eye submit metadata: texture type, DXGI format, dimensions, sample count,
  array size, color space, submit flags, and `VRTextureBounds_t`.
- Projection and pose metadata: `GetProjectionRaw`, `GetProjectionMatrix`,
  `GetEyeToHeadTransform`, HMD pose validity, pose cadence, and any runtime IPD
  or hidden-area assumptions visible through tooling.
- One representative left/right frame pair from a stable world-locked scene, if
  practical to capture without changing app behavior.
- Frame timing or performance notes only if they explain visible behavior or
  startup failure.

## Evidence Log

June 20, 2026:

- Freedom Locomotion VR starts immediately in an immersive 360-degree VR
  environment on the real Windows host. This is a useful world-locked baseline:
  there are no SteamVR Tutorial-style 2D intro screens to progress through before
  reaching real 3D content.
- Treat Freedom Locomotion VR as the primary baseline target for the next
  CrossOver/AVP comparison. The expected first useful headset question is whether
  the AVP view also appears as a surrounding 360-degree environment whose world
  stays fixed as the viewer moves, rather than as head-locked cards, drifting
  boxes, wireframe fallback, or flat overlay content.
- Live Winders/ALVR run confirmed the working baseline stack while Freedom was
  streaming to AVP:
  - ALVR Dashboard process from the v21 dev12 install folder under
    `C:\Users\gaming\Desktop\Installations\`.
  - Freedom app processes from the Steam `Freedom Locomotion VR` install:
    launcher `FreedomLocomotion.exe` and UE4 shipping executable
    `FreedomLocomotion-Win64-Shipping.exe`.
  - SteamVR runtime: normal Steam install under
    `C:\Program Files (x86)\Steam\steamapps\common\SteamVR`.
  - `openvrpaths.vrpath` `external_drivers` points at the v21 dev12 ALVR folder
    above, not the older `v20.14.1` or `v21.0.0-dev13+nightly.2026.06.14`
    folders still present under `Desktop\installations`.
- ALVR `session.json` during the live stream recorded:
  - `server_version`: `21.0.0-dev12+nightly.2026.06.16`.
  - Trusted AVP client at `192.168.1.6`, `connection_state`: `Streaming`.
  - OpenVR eye and target eye resolution: `2144x2048` per eye.
  - Refresh rate: `90` Hz.
  - Preferred video codec: `H264`; bitrate mode: constant `30` Mbps.
  - Foveated encoding enabled with center size `0.45 x 0.4`, center shift
    `0.4 x 0.1`, and edge ratio `4.0 x 5.0`.
- Live module inventory for `FreedomLocomotion-Win64-Shipping.exe` showed the
  app loading OpenVR from its bundled Unreal Engine path:
  `Engine\Binaries\ThirdParty\OpenVR\OpenVRv1_0_2\Win64\openvr_api.dll`.
  The process also loaded SteamVR's `vrclient_x64.dll`, Windows `d3d11.dll` and
  `dxgi.dll`, and NVIDIA D3D driver modules. This makes the Unreal OpenVR
  third-party folder the first CrossOver shim staging target.
- SteamVR settings on the working Windows baseline recorded `ActualHMDDriver` as
  `alvr_server`, `HMDManufacturer` as `Oculus`, `HMDModel` as `Miramar`, and
  SteamVR `ipd` as approximately `0.063`. The ALVR driver manifest name is
  `alvr_server` with `redirectsDisplay: true`.

## CrossOver Mapping

- Match the same executable and app-local `openvr_api.dll` load location.
- Match OpenVR interface versions before comparing visuals.
- Match recommended render target size, submitted eye dimensions, bounds, format,
  and MSAA shape before tuning crop/projection.
- Treat `ALVR_SHIM_INNER_CROP_PX=224` as a hypothesis to validate against the
  Windows frame/bounds reference, not as a fixed constant.
- Compare at least one stable Windows left/right frame pair against the
  CrossOver shim-packed BGRA frame before judging AVP comfort.
- Ask for AVP eyes only after CrossOver logs show repeated pose acquisition or an
  explicitly understood fake-pose limitation plus paired real app Submit frames.

## Verdict

`passed for target selection`: Freedom Locomotion VR is confirmed to enter a
real immersive 3D environment immediately on Windows. The remaining baseline
work is to capture render metadata and any short screen/video evidence needed to
compare the same scene against CrossOver/AVP output.

## Next Action

Use Freedom Locomotion VR as the primary target. Capture the OpenVR render
metadata for its first 360-degree scene, then retry the CrossOver app-local shim
against the same executable and compare the first paired Submit frames against
the Windows baseline before asking for another AVP visual check.
