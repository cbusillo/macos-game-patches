# Real OpenVR World-Locked Geometry Probe

## Hypothesis

The Option B transport path is viable only if a real Windows/OpenVR app can
submit world-locked 3D frames that remain coherent on AVP. Synthetic grids,
SteamVR Tutorial's 2D intro screens, and head-locked canned stereo scenes are no
longer sufficient evidence for geometry comfort.

## Environment

- Repo: `macos-game-patches`, branch `handoff/alvr-shared-memory-black-pause`.
- Sibling ALVR checkout: `~/Developer/alvr`, branch
  `diagnostic/bgra-nv12-probe`.
- CrossOver bottle: `Steam`.
- First target: `Freedom Locomotion VR`, because it is a real 3D app with an
  app-local OpenVR DLL already present.
- Fallback target: The Lab `RobotRepair`, after collecting why it previously
  loaded the shim and requested `IVRCompositor_013` without producing Submit
  frames.
- Excluded target: SteamVR Tutorial, unless there is a concrete way to progress
  through its 2D intro into a real world-locked 3D scene.

## Command Or Procedure

1. Run sterile cleanup before each live attempt, including stale Wine/CrossOver
   producers.
2. Start the native macOS bridge in shared-memory mode from the ALVR checkout.
3. Stage the app-local shim as the target app's `openvr_api.dll` and preserve the
   original app-local DLL as `openvr_api.real.dll`, or stage the fake runtime as
   `openvr_api.real.dll` when testing against the fake-HMD path.
4. Start with `ALVR_SHIM_INNER_CROP_PX=224`, the current packing hypothesis from
   controlled AVP checks.
5. Launch the real app through CrossOver.
6. Inspect logs before asking for headset time. Only request AVP eyes when the
   shim has published real paired Submit frames from world-locked 3D content.

## Evidence Artifacts

- `Z:\tmp\alvr_openvr_submit_shim.log`: loader, interface wrap, Submit metadata,
  texture size/format, bounds, crop, packed output size, and timing.
- `Z:\tmp\fake_openvr_real.log`: fake runtime interface calls when applicable.
- Native bridge logs: shared-memory config, frame reads, view params, encode
  config, and shape-change failures.
- AVP visual observation only after real 3D Submit frames are confirmed.

## Evidence Log

June 20, 2026:

- Rebuilt the current app-local shim and fake runtime, then staged them into all
  known Freedom Locomotion VR OpenVR DLL locations: the app root,
  `FreedomLocomotion/Binaries/Win64`, and
  `Engine/Binaries/ThirdParty/OpenVR/OpenVRv1_0_2/Win64`.
- Freedom Locomotion VR launched both through the root launcher and directly via
  `FreedomLocomotion-Win64-Shipping.exe`, with `ALVR_SHIM_INNER_CROP_PX=224`.
  Both launches crashed before the shim or fake runtime wrote logs. The only
  visible CrossOver log clue was D3DMetal reporting an unsupported D3D11
  timestamp query, followed by UE4 `CrashReportClient.exe` and crash contexts
  under `AppData/Local/FreedomLocomotion/Saved/Logs`.
- Robot Repair was staged with the same current shim/fake runtime pair and
  launched from `RobotRepair/bin/win64`. It exited before OpenVR shim activity
  with `CAppSystemDict:Unable to load interface factory VR_001 from vr
(Dependency of application)`.
- No AVP eyes were requested because neither real 3D target produced paired
  Submit frames during this pass.
- Follow-up cleanup found four stale Wine `C:\Program Files\Bonjour\mDNSResponder.exe`
  processes pegging one CPU core each, plus old Tutorial `UnityCrashHandler64.exe`
  helpers. `tools/vr_stack_cleanup.py` now matches those stale probe leftovers so
  future sterile runs clean them automatically.
- Real Windows baseline confirmed Freedom Locomotion VR immediately starts in an
  immersive 360-degree VR environment. This validates Freedom as the primary
  real-world target and removes the need to return to SteamVR Tutorial's 2D
  startup screens for geometry work.
- Live Windows module inventory showed Freedom's UE4 shipping process loading
  `openvr_api.dll` from
  `Engine\Binaries\ThirdParty\OpenVR\OpenVRv1_0_2\Win64`, then SteamVR's
  `vrclient_x64.dll`. Prioritize that Unreal OpenVR folder for the next
  CrossOver app-local shim pass instead of treating all candidate DLL locations
  as equally likely.
- CrossOver staging was de-noised to the Windows-proven Unreal OpenVR folder
  only: the app root and `FreedomLocomotion/Binaries/Win64` were restored to
  their original `openvr_api.dll` files, while
  `Engine/Binaries/ThirdParty/OpenVR/OpenVRv1_0_2/Win64/openvr_api.dll` kept the
  submit shim and `openvr_api.real.dll` kept the fake runtime.
- With `ALVR_SHIM_INNER_CROP_PX=224`, direct launch of
  `FreedomLocomotion-Win64-Shipping.exe` under CrossOver reached sustained
  paired Submit frames. The shim logged a `3240x1800` D3D11 texture, submitted
  bounds split left `[0.0, 0.0, 0.5, 1.0]` and right `[0.5, 0.0, 1.0, 1.0]`,
  per-eye crops of `1620x1800`, and packed shared-memory output of
  `2792x1800` after the `224 px` inner crop. Source frame stats remained
  nonzero for both eyes.
- The native macOS bridge consumed those Freedom frames from shared memory,
  repeatedly logging `shared memory configured: 2792x1800 format=0x57` and frame
  reads through at least frame `8280`, with BGRA-to-NV12 conversion timings
  present. This moves the current blocker past loader/startup/Submit capture and
  into AVP connection plus visual comfort validation.
- The AVP was still actively streaming from the Windows ALVR server during this
  run, so the macOS bridge could not complete its client connection. Do not
  interpret lack of headset output from this run as a CrossOver producer failure.

## Verdict

`producer path green`: Freedom Locomotion VR under CrossOver now reaches
sustained paired Submit frames and the native macOS bridge reads them from
shared memory. The remaining active check is whether the AVP client can connect
to the macOS bridge and whether the real Freedom scene is visually aligned and
comfortable.

## Next Action

The CPU/shared-memory producer proof is complete and should remain a diagnostic
baseline. Do not resume by keeping the Freedom producer running for an open-ended
visual-tuning session. Use issue #39 to establish native
IOSurface/VideoToolbox transport and AVP cadence first. Return to Freedom for
one bounded comparison after a production-candidate texture handoff exists, or
earlier only when a specific diagnostic question cannot be answered by the
native surface path.
