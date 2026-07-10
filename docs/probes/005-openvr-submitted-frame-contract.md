# OpenVR Submitted Frame Contract

## Hypothesis

The Mac ALVR path can stop guessing at headset visuals only after it records a
complete submitted-frame contract from a real OpenVR producer. A frame is not
ready for AVP validation until its pixels can be paired with the exact eye,
crop, projection, pose, timing, and synchronization metadata that ALVR needs to
present it.

## Environment

- Owning plan: GitHub issue #38, child of #36.
- Primary target: Freedom Locomotion VR, using the Windows-proven Unreal OpenVR
  DLL load path from the reference baseline.
- Capture boundary: app-local `IVRCompositor::Submit` plus the corresponding
  `IVRSystem` and `IVRCompositor` pose/projection calls visible to the app.
- Current tools: `tools/openvr_submit_shim.cpp`, `tools/fake_openvr_real.cpp`,
  `tools/openvr_app_loop_probe.cpp`, and native macOS bridge logs from the
  sibling ALVR checkout.

## Contract Fields

Each accepted frame pair must identify the source, geometry, timing, and sync
state below. Missing fields should be logged explicitly as unknown, not silently
replaced by synthetic defaults.

### Source Texture

- OpenVR interface version and submit ABI shape: C++ object table or C function
  table.
- Eye: `Eye_Left` or `Eye_Right`.
- Texture handle identity and texture type.
- D3D format, width, height, mip count, sample count, bind flags, usage, misc
  flags, and array size.
- Color space and submit flags, especially `Submit_TextureWithPose`,
  `Submit_TextureWithDepth`, and `Submit_LensDistortionAlreadyApplied`.

### Crop And Packing

- Raw `VRTextureBounds_t` for each submit call.
- Derived source crop in texture pixels.
- Any fallback rule used when bounds are absent or invalid.
- Any diagnostic inner crop, scale divisor, or side-by-side packing transform.
- Final packed output width, height, per-eye source size, and per-eye output
  placement.

### Projection And View Geometry

- `IVRSystem::GetRecommendedRenderTargetSize`.
- `IVRSystem::GetProjectionRaw` for each eye.
- `IVRSystem::GetProjectionMatrix` if available in the active interface.
- `IVRSystem::GetEyeToHeadTransform` for each eye.
- Tracking universe origin from `IVRCompositor::GetTrackingSpace`.
- Runtime IPD or inferred eye separation when available.
- The exact mapping from those values to ALVR `ViewParams`.

### Pose And Timing

- `WaitGetPoses`, `GetLastPoses`, or `GetLastPoseForTrackedDeviceIndex` call
  used for the render pose.
- HMD pose validity, tracking result, connected state, and raw 3x4 transform.
- Pose timestamp or the best available clock-domain marker for the sampled pose.
- Pose generation: which `WaitGetPoses`, `GetLastPoses`, or explicit
  texture-with-pose sample the app most likely used when rendering this submit.
- Submit call timestamp for each eye.
- Pairing rule that decides which left and right submits form one video frame.
- Encoder/video timestamp and whether it is derived from the encoder clock or a
  producer clock.

### Synchronization

- Whether `Submit` has returned before capture reads the texture.
- Whether capture is synchronous inside the app's render thread or deferred to a
  worker after an explicit GPU readiness signal.
- D3D copy path used for capture: source texture, resolved texture, staging
  texture, map wait, and copy timing.
- Any GPU fence, keyed mutex, shared handle, IOSurface, or external-memory token
  available to a future no-readback path.
- Backpressure rule when one eye is late, repeated, or missing.

## Logging Acceptance Criteria

A run satisfies #38 only when one real or contract-faithful producer emits a
bounded log sample that shows:

- one left/right frame pair with complete source texture descriptors;
- raw and derived bounds for both eyes;
- projection raw values and eye-to-head transforms for both eyes;
- render pose validity and the pose used for that frame;
- submit timing and frame-pair timing;
- the ALVR `ViewParams` values that would be sent for that frame;
- whether the pixel path used CPU readback, GPU surface handoff, or a diagnostic
  fallback;
- and an explicit list of any fields still unknown.

Headset appearance is not an acceptance criterion for this probe. AVP visual
testing belongs to #41 and should happen only after the log answers the contract
question for the frame under test.

## Current Tool Gaps

- `openvr_submit_shim.cpp` already records submit metadata, bounds, texture
  descriptors, crop, packed output size, and copy timing. It does not yet log
  the full per-frame projection, eye-to-head, tracking-space, or `ViewParams`
  mapping needed for #38 acceptance.
- `fake_openvr_real.cpp` publishes pose snapshots into shared memory for the
  current diagnostic path, but the contract must distinguish real app/runtime
  pose sampling from synthetic or fallback pose snapshots.
- `openvr_app_loop_probe.cpp` is useful as a contract-faithful producer because
  it calls `GetEyeToHeadTransform`, `GetProjectionRaw`, `WaitGetPoses`, and
  paired `Submit`, but it is not a substitute for a real app sample from Freedom
  Locomotion VR.
- The native bridge logs must show which `ViewParams` were actually paired with
  the encoded frame, otherwise the submit-side metadata cannot be compared to
  what AVP received.
- The current pose snapshot path can read a fresh HMD pose at submit time. #38
  must prove or replace that pairing, because a submit-time pose is not
  necessarily the render pose used by the app for the submitted texture.

## Instrumentation Status

June 23, 2026:

- `tools/fake_openvr_real.cpp` now logs contract-shaped render metadata for the
  fake-runtime path: recommended render target size, raw projection values,
  eye-to-head matrix, compositor pose API, pose generation, pose timestamp,
  pose validity, tracking result, connection state, and raw HMD pose matrix.
- The fake runtime caches the shared-memory mapping used for view and pose
  snapshots so the pose hot path does not reopen and remap the Wine `Z:` path on
  every `WaitGetPoses` call.
- `tools/openvr_submit_shim.cpp` now logs a `Submit pair contract` line for the
  first pair and periodic samples. It includes the left/right submit ordinals,
  left/right submit timestamps, pose timestamp copied from the fake-runtime
  frame pose, clock-domain labels, video timestamp, output size, view-param
  provenance, explicit `sync=synchronous-submit-readback`, and an
  `unknown_fields=[...]` list.
- The native macOS bridge logs `encoded frame contract view_params` on keyframes,
  including the resolved-for-send timestamp, both FOVs, both pose positions, and
  both pose orientations.
- `tools/openvr_submit_shim.cpp` now includes pose sequence/generation,
  explicit `view_params_source`, clock-domain labels, and an
  `unknown_fields=[...]` list in the `Submit pair contract` line so the next
  capture can be reduced mechanically. It also emits a contract sample as soon
  as pose provenance first appears after startup warm-up.

The next live sample should collect these three log families together:

```text
IVRSystem::GetProjectionRaw return ...
IVRSystem::GetEyeToHeadTransform matrix=...
IVRCompositor::WaitGetPoses contract ...
IVRCompositor::WaitGetPoses hmd_pose=...
Submit pair contract ...
encoded frame contract view_params ...
```

## OpenXR Equivalent

The immediate target is OpenVR, but the contract should map cleanly to OpenXR so
future GPTK/CrossOver work can use whichever runtime boundary is easier to
instrument.

- Eye index: index in `XrCompositionLayerProjection.views`.
- Texture identity: `XrSwapchain`, acquired image index, graphics API image, and
  image array layer.
- Crop: `XrSwapchainSubImage.imageRect` and `arrayIndex`.
- Projection: `XrCompositionLayerProjectionView.fov`.
- Eye/view pose: `XrCompositionLayerProjectionView.pose`, usually from
  `xrLocateViews`.
- Frame timing: `XrFrameState.predictedDisplayTime`, locate time, and
  `xrEndFrame` display time.
- Tracking state: `XrViewStateFlags` orientation/position validity and tracking
  bits.
- Sync: `xrAcquireSwapchainImage`, `xrWaitSwapchainImage`, rendering completion,
  and `xrReleaseSwapchainImage` ownership.

Do not assume OpenVR projection or eye-to-head values are permanently static.
They can be cached as an optimization only after the probe records when the app
queried them and whether they changed during the sampled scene.

## Procedure

1. Run the Windows reference baseline first if the target app or scene changed.
2. Launch the CrossOver target with sterile cleanup and the app-local shim in the
   Windows-proven OpenVR DLL location.
3. Capture a short submit-side log window around the first stable world-locked
   scene.
4. Capture the native bridge log window for the same frame ids or timestamps.
5. Reduce the logs into a single frame-pair contract table.
6. Mark each field as real, synthetic fallback, inferred, or missing.
7. Only then decide whether the next implementation belongs to #39, #40, or #41.

## Failure Signatures

- A frame reaches AVP but the submit log lacks projection or eye-to-head data:
  do not tune visuals; instrument the contract first.
- Bounds are present but the derived crop/packing is not recorded: do not adjust
  `ALVR_SHIM_INNER_CROP_PX`; record the transform chain first.
- Pose validity is false, stale, or synthetic while the visual feels fixed: do
  not call it world-locked evidence.
- Left/right submits are paired only by arrival order under drops or repeats:
  add an explicit pairing rule before judging stereo comfort.
- Video timestamps mix producer pose time and encoder send time without labels:
  separate clock domains before evaluating motion or warp.

## Evidence Log

### 2026-06-23 Freedom Locomotion VR Tracking-Space Contract Acceptance

Run: Short bounded rerun using `tools/launch_freedom_crossover.py`, which hides
the launched CrossOver/Freedom windows during startup, with the updated fake
runtime staged as Freedom's `openvr_api.real.dll`.

Question: Does the joined #38 submitted-frame contract now include the missing
tracking-space return value?

Mode / build: current `tools/openvr_submit_shim.cpp` and
`tools/fake_openvr_real.cpp`; native bridge in `shared-memory` mode; Freedom
launched with `ALVR_SHIM_INNER_CROP_PX=224` through the hidden launch helper.

Commands: sterile cleanup, archived prior logs, started `alvr_macos_bridge`, ran
`python3 tools/launch_freedom_crossover.py --hide-seconds 20`, polled for
`GetTrackingSpace return`, `Submit pair contract`, and bridge `ViewParams`, then
cleaned up all Wine/CrossOver/bridge processes.

Expected proof: a post-warm-up contract window containing `GetTrackingSpace
return`, joined submit-pair metadata, fake-runtime pose/projection, and bridge
`ViewParams`.

Artifacts captured: `/tmp/alvr_openvr_submit_shim.log`,
`/tmp/fake_openvr_real.log`,
`$HOME/Library/Application Support/alvr/macos_bridge/session_log.txt`, and poll
transcript under `.code/agents/1064/`.

Verified:

- `GetTrackingSpace return origin=1(TrackingUniverseStanding)` appears repeatedly
  in `/tmp/fake_openvr_real.log`.
- Representative submit frame `359` logged left/right ordinals `719/720`,
  `pose_generation=268`, `pose_sequence=536`, clock-domain labels,
  `view_params_source=shared-view-shared-hmd-pose-frame-pose`, and
  `sync=synchronous-submit-readback`.
- Bridge frame/window at `11:20:12.111` logged encoded `ViewParams` with shared
  FOV and non-zero HMD-derived per-eye poses.
- The hidden launch helper kept the launch path scriptable and non-disruptive for
  future runs.

Inferred:

- #38 now has enough evidence to accept the OpenVR submitted-frame contract for
  the current Freedom/CrossOver readback path, with limitations explicitly
  carried forward.

Failed / missing:

- The submit shim's `unknown_fields` still includes `tracking_space_return`
  because the shim does not consume fake-runtime return logs directly. The field
  is present in the captured fake-runtime log and should be resolved during
  reduction rather than used as a reason for another #38 rerun.
- `clock_alignment` remains unknown across `wine-steady` and
  `shared-hmd-pose-timestamp`.
- `exact_app_render_pose_pairing` remains best-available/unproven: the contract
  records the latest fake-runtime pose at submit capture time, not proof of
  Freedom's internal cached render pose.
- `projection_matrix_return` remains not applicable for this observed Freedom
  path because Freedom uses `GetProjectionRaw`.
- #41 remains blocked: bridge cadence continued to show `emitted=0` after AVP
  client decoder/transport failures.

Verdict: `accepted with known limitations` for #38. The next blocker is not the
submitted-frame contract; it is #42, the AVP emitted-frame transport/decoder
recovery path blocking #41.

Do not repeat: do not rerun #38 solely to remove the submit shim's static
`tracking_space_return` unknown field; the reduction has real fake-runtime return
evidence. Do not ask for headset visuals while cadence shows `emitted=0`.

Next action: move to #42 and determine why the bridge reads/encodes frames but
does not emit them to the AVP client after decoder/connection failure.

Issue routing: close #38; route AVP visual validation blocking to #42 and #41.

### 2026-06-23 Freedom Locomotion VR Joined Contract Capture

Run: Bounded rerun of Freedom Locomotion VR through CrossOver Steam with the
updated app-local OpenVR submit shim and fake runtime staged in Freedom's Unreal
OpenVR DLL directory, plus the native macOS bridge in shared-memory mode.

Question: Does the updated instrumentation produce a mechanically reducible
post-warm-up left/right submit contract for one real Freedom frame pair?

Mode / build: `macos-game-patches` on `handoff/alvr-shared-memory-black-pause`,
`alvr` sibling checkout on `diagnostic/bgra-nv12-probe`; rebuilt
`tools/openvr_submit_shim.cpp` and `tools/fake_openvr_real.cpp` into
`/tmp/alvr-contract-build`; staged as `openvr_api.dll` and
`openvr_api.real.dll`; bridge launched with `ALVR_BRIDGE_INPUT=shared-memory` and
`ALVR_BRIDGE_FPS=90`; Freedom launched with `ALVR_SHIM_INNER_CROP_PX=224`.

Commands: sterile cleanup with `tools/vr_stack_cleanup.py`; MinGW builds for the
two Windows DLLs; `cargo run -p alvr_macos_bridge`; CrossOver Wine launch of
`FreedomLocomotion-Win64-Shipping.exe`; polling for `Submit pair contract`,
fake-runtime pose/projection, and bridge `encoded frame contract view_params`.

Expected proof: one post-warm-up frame pair with submit ordinals, source texture,
bounds/crop, pose generation, clock labels, `view_params_source`, explicit
unknown fields, fake-runtime pose/projection, and bridge `ViewParams` close
enough in the log window to reduce without headset interpretation.

Artifacts captured: `/tmp/alvr_openvr_submit_shim.log`,
`/tmp/fake_openvr_real.log`,
`$HOME/Library/Application Support/alvr/macos_bridge/session_log.txt`, and the
poll transcript saved under `.code/agents/967/`.

Logs checked: submit diagnostics, D3D descriptors, submit crops, `Submit pair
contract`, `GetProjectionRaw return`, `GetEyeToHeadTransform matrix`,
`WaitGetPoses contract`, `WaitGetPoses hmd_pose`, shared-memory frame reads,
bridge cadence, and `encoded frame contract view_params`.

Human observation: none requested. This was a contract run. The AVP visual gate
remains blocked by the transport/client issue below.

Verified:

- The updated shim emitted 11 sampled `Submit pair contract` records: 2
  pre-warm-up records with `pose_source=missing`, then 9 post-warm-up records
  with `pose_source=fake-runtime-frame-pose-from-pose-api`.
- Representative submit frame `359` logged left/right ordinals `719/720`,
  `submit_clock=wine-steady`, `pose_generation=268`, `pose_sequence=536`,
  `pose_clock=shared-hmd-pose-timestamp`, `video_clock=wine-steady`,
  `view_params_source=shared-view-shared-hmd-pose-frame-pose`, and
  `unknown_fields=[clock_alignment,tracking_space_return,projection_matrix_return,exact_app_render_pose_pairing]`.
- The same run logged real DirectX source descriptors and crops: `3240x1800`,
  format `90`, single-sample, side-by-side bounds, `1620x1800` per-eye crops,
  packed to `2792x1800` with `inner_crop=224`.
- Fake runtime logs around the representative window show shared-view projection
  raw values for both eyes, shared-view eye-to-head transforms, and valid
  `WaitGetPoses` HMD pose data.
- Native bridge logs show shared-memory frame `360` read at `11:05:57.347` and
  encoded-frame `ViewParams` at `11:05:57.358` with shared FOV and non-zero
  per-eye pose values.
- After review, `tools/fake_openvr_real.cpp` was patched to log the actual
  `GetTrackingSpace` return value for both C++ and function-table entry points;
  the rebuilt fake runtime was staged for the next run.

Inferred:

- Frame `359` / bridge frame `360` can now be manually joined across the three
  logs using the submit-pair contract, pose generation, and nearby bridge frame
  timing.
- The `projection_matrix_return` field is not applicable for this Freedom path
  because the app uses `GetProjectionRaw`; no `GetProjectionMatrix` call was
  observed.

Failed / missing:

- The contract still self-reports `clock_alignment`; pose timestamps and
  submit/video timestamps are labeled but not aligned across clock domains, so
  latency deltas across those fields are not valid yet.
- The exact app render-pose pairing remains methodological rather than verified:
  the submit contract records the latest pose generation visible at submit time,
  not proof that Freedom rendered the submitted texture using that exact
  generation.
- The live capture self-reported `tracking_space_return` as unknown; the code is
  patched for the next capture but this captured run does not contain that return
  value.
- Separate from #38, the bridge cadence showed `emitted=0` after AVP client
  decoder/timeout failures, so this run did not produce valid AVP visual
  evidence.

Unknown:

- Clock-domain offset between `shared-hmd-pose-timestamp` and `wine-steady`.
- Exact render-pose generation used internally by Freedom for the submitted
  texture.
- Whether the AVP client transport recovers after the observed decoder error
  without restarting the client/bridge session.

Verdict: `partial-pass / still blocked`. The joined contract logging gap is
resolved, and frame `359` can be reduced mechanically. #38 needs one more short
capture with the staged tracking-space-return logging to remove that trivial
unknown, while keeping `exact_app_render_pose_pairing` explicitly classified as
best-available/unproven. #41 remains blocked by the separate `emitted=0` client
transport failure.

Do not repeat: do not ask for headset visuals from a run whose bridge cadence has
`emitted=0` or whose AVP client has just failed with a decoder/timeout error.

Next action: rerun only long enough to capture one post-warm-up frame with
`GetTrackingSpace return` present, then mark #38 accepted with
`exact_app_render_pose_pairing` as an explicit methodological limitation. In
parallel, route the `emitted=0` / AVP decoder recovery issue to #41 or a focused
transport child.

Issue routing: #38 for the final tracking-space-return rerun and accepted joined
contract table; #41 for AVP client/transport validation blocking `emitted=0`.

### 2026-06-23 Freedom Locomotion VR CrossOver Contract Capture

Run: Freedom Locomotion VR through CrossOver Steam with the app-local OpenVR
submit shim, fake runtime, and native macOS bridge in shared-memory mode.

Question: Can one real Freedom left/right submit pair satisfy the #38 submitted
frame contract closely enough to allow AVP validation under #41?

Mode / build: `macos-game-patches` on `handoff/alvr-shared-memory-black-pause`,
`alvr` sibling checkout on `diagnostic/bgra-nv12-probe`; DLLs rebuilt into
`/tmp/alvr-contract-build`; staged as `openvr_api.dll` plus
`openvr_api.real.dll` in Freedom's Windows-proven Unreal OpenVR DLL directory;
bridge launched with `ALVR_BRIDGE_INPUT=shared-memory` and
`ALVR_BRIDGE_FPS=90`; Freedom launched with `ALVR_SHIM_INNER_CROP_PX=224`.

Commands: sterile cleanup with `tools/vr_stack_cleanup.py`; MinGW builds for
`tools/fake_openvr_real.cpp` and `tools/openvr_submit_shim.cpp`; `cargo run -p
alvr_macos_bridge` in the sibling ALVR checkout; CrossOver Wine launch of
`FreedomLocomotion-Win64-Shipping.exe`.

Expected proof: one bounded sample with source texture descriptors, raw and
derived bounds, projection raw values, eye-to-head transforms, pose API contract,
HMD pose matrix, submit pair timing, bridge `ViewParams`, pixel path, and an
explicit unknown-field list.

Artifacts captured: `/tmp/alvr_openvr_submit_shim.log`,
`/tmp/fake_openvr_real.log`,
`$HOME/Library/Application Support/alvr/macos_bridge/session_log.txt`, and
`$HOME/Library/Application Support/alvr/macos_bridge/session.json`.

Logs checked: searched the three logs for `GetProjectionRaw return`,
`GetEyeToHeadTransform matrix`, `WaitGetPoses contract`, `WaitGetPoses
hmd_pose`, `Submit pair contract`, `encoded frame contract view_params`, shared
memory frame reads, submit crops, D3D descriptors, and explicit missing/fallback
markers.

Human observation: none requested. The Evidence Gate stopped AVP visual
interpretation because the run was a contract capture, not a comfort test.

Verified:

- The AVP client `applevisionpro` was connected to the native bridge with
  `connection_state` set to `Streaming` in `session.json`.
- The submit shim captured real Freedom DirectX submits: texture type DirectX,
  `3240x1800`, format `90`, one sample, one array layer, `bind=0x28`,
  `usage=0`, and `misc=0x0`.
- Submit bounds were real and non-fallback for the sampled side-by-side texture:
  left raw `[0.0000 0.0000 0.5000 1.0000]`, right raw
  `[0.5000 0.0000 1.0000 1.0000]`, each derived to `1620x1800` source crops.
- The submit shim packed `2792x1800` output with `inner_crop=224` and logged
  `sync=synchronous-submit-readback` plus copy timings.
- The fake runtime logged projection raw values from shared view data for both
  eyes, eye-to-head matrices for both eyes, `WaitGetPoses contract` entries,
  and HMD pose matrices with `hmd_valid=1`, `tracking_result=200`,
  `connected=1`, and `source=shared-hmd-pose` after startup warm-up.
- The submit shim logged 68 sampled `Submit pair contract` records. The first 12
  had `pose_source=missing`; 56 later samples had
  `pose_source=fake-runtime-frame-pose`.
- The native bridge logged 69 `encoded frame contract view_params` samples. The
  first 13 were fallback-style identity/zero pose samples; 56 later samples had
  shared FOV and non-zero HMD-derived per-eye poses.

Inferred:

- The real Freedom producer, fake runtime, submit shim, shared-memory bridge,
  encoder path, and AVP client transport were alive at the same time after
  warm-up.
- The later submitted frames likely used the pose API path rather than the
  fallback identity path, because submit-pair pose provenance and bridge
  `ViewParams` both switched to shared pose/view data.

Failed / missing:

- The run does not satisfy #38 because it does not reduce one exact left/right
  submit pair into a joined contract record tying submit ordinals, pose
  generation, projection/eye-to-head values, bridge `ViewParams`, and unknown
  fields together.
- The pose logged by the submit shim is still only the latest fake-runtime frame
  pose snapshot available at submit capture time. It is not yet proven to be the
  exact render pose Freedom used to draw the submitted texture.
- `view_params_source=shared-memory-or-bridge-fallback` was ambiguous in this
  capture.
- Pose, submit, and video timestamps were not explicitly labeled by clock domain
  in this capture, so latency deltas across those fields must remain unknown.
- `GetTrackingSpace` return value and `GetProjectionMatrix` return values were
  not reduced into the contract sample.
- No explicit unknown-field list was emitted by the live run logs.

Unknown:

- Exact app render-pose pairing for each submitted texture.
- Whether any app path uses cached projection or tracking-space state not visible
  in the sampled submit-pair log.
- Backpressure behavior beyond the current `latest-left-right` pairing rule
  when an eye is late, repeated, or missing.

Verdict: `blocked`. This is strong partial evidence for the real Freedom
transport and shared pose/view path, but it is not a #38 pass and must not be
used as #41 AVP visual-validation evidence.

Do not repeat: do not ask for AVP visual comfort, warp, recenter, or world-lock
judgment from a run whose submit log lacks one joined contract record and an
explicit unknown-field list.

Next action: rerun Freedom after the submit shim emits pose sequence/generation,
explicit `view_params_source`, and `unknown_fields=[...]` in `Submit pair
contract`; reduce one post-warm-up frame pair before any AVP headset question.

Issue routing: #38 remains active for joined submitted-frame contract logging and
reduction. #41 remains blocked for visual validation until #38 has an accepted
joined contract sample.

## Verdict

`accepted with known limitations`: the 2026-06-23 real Freedom captures now
provide a mechanically reducible OpenVR submitted-frame contract for the current
CrossOver readback path. Remaining limitations are explicitly classified:
`clock_alignment`, `exact_app_render_pose_pairing`, and non-use of
`GetProjectionMatrix` on this Freedom path. Issue #38 is complete. Issue #42
subsequently proved that the native bridge queues and sends encoded packets after
client recovery, so its former `emitted=0` blocker is also complete.

## Next Action

Use issue #39 to validate the native IOSurface/VideoToolbox source against the
physical AVP at headset cadence. Keep the exact app render-pose pairing
limitation explicit, but do not expand the fake runtime solely to remove that
accepted unknown. Route future CrossOver texture-handoff work to #40 and human
visual observations to #41.
