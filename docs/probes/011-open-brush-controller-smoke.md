# Open Brush Controller Smoke

## Goal

Use Open Brush as the next real application gate for the Vision Pro ALVR path.
The probe must separate controller transport, OpenVR input compatibility, eye
texture layout, image quality, and application-specific locomotion behavior.

Freedom Locomotion is no longer the controller acceptance application. Its
latest gameplay artifact proved both PS VR2 Sense poses, both thumbsticks,
triggers, and button masks reached fake OpenVR, but the game did not move the
player predictably. The same run rendered the level at only `2808x1560`
side-by-side, which limited per-eye source detail before encoding.

## Hypothesis

Open Brush is a smaller and clearer input target because controller pose,
trigger, grip, and menu actions have immediate visual effects. Its current
Steam build is free and supports tracked controllers, but it is OpenXR-only.
The compatibility probe therefore pins the newest public desktop release that
still boots through OpenVR instead of pretending the current build fits the
existing bridge.

## Verified Runtime Selection

- Steam app `1634870`, build `23545184`, uses `UnityOpenXR.dll` and
  `openxr_loader.dll`; it does not contain an OpenVR plugin.
- GitHub desktop releases `1.0.160` and newer use Unity OpenXR. The public
  release immediately before that series, `1.0.28`, is the newest verified
  OpenVR build.
- `OpenBrush_Desktop_1.0.28.zip` has SHA-256
  `5534d2e324e3317232324fea3991d8143da7592b3e0da19e4822755e6df8e371`.
- Its `OpenBrush.exe` has SHA-256
  `38d2d0531448b07f5edae077c64100023ff743c622b27bd034750a5fd9f16d55`.
- Its `openvr_api.dll` has SHA-256
  `bd7a7958bdb647096e5e22cb4d020dd99720983f3af1cd500e8b570cfa9f017b`.
- `boot.config` explicitly contains `vr-device-list=OpenVR`, and the build
  ships `SteamVR.dll`, `SteamVR_Actions.dll`, and Vive controller bindings.

## Plan

1. Preserve the installed Steam app manifest and verify its runtime, then pin
   the newest official OpenVR desktop release with exact hashes.
2. Launch the pinned build with bounded OpenVR logging. Record which interfaces
   it requests, its graphics API, and its submitted eye texture handles, bounds,
   formats, and dimensions.
3. Do not enable the production IOSurface pool until the submitted-frame layout
   is known. Separate-eye textures, arrays, and side-by-side textures require
   different capture contracts.
4. Prove both controllers before headset interpretation: fresh non-identity
   poses, live packet numbers, trigger and grip changes, menu/button changes,
   and thumbstick changes must reach the runtime.
5. Add the smallest application-specific launcher only after the baseline logs
   identify the runtime and texture contract. Reuse the existing deterministic
   MoltenVK install, Wine desktop warm-up, bottle shutdown, and checksum restore
   helpers.
6. Run a connected Vision Pro smoke only after local nonblack imagery and the
   controller gate both pass.

## Pass Gate

- Open Brush launches through a verified OpenVR-compatible path.
- Both rendered eyes are nonblack and correctly assigned.
- Head motion remains world-locked without wrong-way motion or snapping.
- Both PS VR2 Sense controller models move with fresh tracked poses.
- Trigger, grip, menu, and at least one thumbstick change produce visible or
  logged application input.
- The producer, native bridge, encoder, and transport report no pool exhaustion,
  fence quarantine, device loss, decoder error, or release failure.
- All staged CrossOver, OpenVR, DXVK, and bridge files restore by checksum, and
  the dedicated bottle has no remaining probe processes.

## Failure Signatures

- The game selects OpenXR with no compatible runtime: stop and identify a
  supported SteamVR/OpenVR launch mode before changing the bridge.
- No `IVRCompositor::Submit` calls: the application did not enter the expected
  OpenVR rendering path.
- Controller poses are fresh but actions do not change: inspect action-manifest
  and input-source compatibility before changing controller transport.
- Actions change in fake OpenVR but not in the application: treat this as an
  application binding/runtime API problem, not a PS VR2 transport failure.
- Separate eye textures or texture arrays are submitted: do not reuse Freedom's
  side-by-side crop assumptions.
- Local imagery is already soft or low resolution: preserve the application's
  recommended render target before changing encoder resolution or bitrate.

## Cleanup

The probe owns the dedicated `Steam` bottle while running. It must close the
bottle before staging graphics files, keep the patched-library GUI warm-up alive
through the application run, stop every bottle process before restoring stock
MoltenVK, and preserve bounded artifacts under
`.code/probes/011-open-brush-controller-smoke/`.

## Evidence

### Runtime discovery

Artifact:
`.code/probes/011-open-brush-controller-smoke/runtime-discovery-20260715T181238Z`

- Verdict: `pass`.
- Open Brush requested `IVRCompositor_022` and initialized Direct3D 11 through
  DXVK.
- Unity reported a `separate` eye layout using multi-pass stereo.
- The fake runtime requested `1620x1800` per eye.
- Left and right submissions used distinct D3D11 texture handles, full
  `[0,0,1,1]` bounds, and `DXGI_FORMAT_R8G8B8A8_TYPELESS`.
- The combined native source contract is therefore `3240x1800`, not Freedom's
  same-handle side-by-side texture contract.

### GPU pair assembly

Artifact:
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260715T185924Z`

- Verdict: `pass` in disconnected mode.
- The shim paired every adjacent left/right submit and used one Vulkan command
  buffer to blit both RGBA eye images into the BGRA IOSurface pool halves.
- `60/60` application frames were submitted, released, and hardware encoded;
  no pool exhaustion, release drop, fence quarantine, or device loss occurred.
- A real separate-eye consumer sample returned BGRA `255,255,255,255`, proving
  nonblack content and RGBA-to-BGRA conversion through the native pool.
- Metal conversion averaged `794 us` wall time and `112 us` GPU time.
- The off-head producer tail was `42.878 FPS`. This legacy Unity application is
  currently a controller/visual smoke target, not the 90 Hz cadence acceptance
  target used by the synthetic and Freedom scheduler gates.
- The CrossOver MoltenVK, OpenVR runtime, staged DXVK DLLs, shared memory, and
  dedicated bottle all restored cleanly.

### OpenVR input compatibility

Initial connected artifact:
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260715T191029Z`

- Verdict: `fail` before controller testing.
- Open Brush requested both `IVRInput_006` and `FnTable:IVRInput_006`; the fake
  runtime exposed only `IVRInput_005` and returned interface-not-found.
- The visible Tiltasaurus diagnostic remained on wireframe and reported that it
  failed to detect VR. This was a runtime ABI failure, not a headset or
  controller-transport failure.
- The run restored all staged files cleanly.

The fake runtime now exposes the exact 26-slot `IVRInput_006` C++ and flat
function tables used by the pinned `SteamVR.dll`. Digital, analog, and pose
actions read one locked two-controller snapshot per `UpdateActionState`, so
state, change flags, and analog deltas remain frozen between action updates.
Unrestricted action queries cover both controllers. The Open Brush action
manifest maps pose, trigger, grip, thumbstick, pad, menu, primary, secondary,
and haptic actions onto the existing PS VR2 Sense shared-memory contract.

Focused artifact:
`.code/probes/011-open-brush-controller-smoke/runtime-discovery-20260715T202020Z`

- Verdict: `pass` off-head.
- Open Brush acquired both `IVRInput_006` interfaces, registered all 15
  Tilt Brush action handles, and queried both left and right input sources.
- It submitted 16 frames per eye using two distinct `1620x1800` textures with
  full bounds; no unknown input-interface request remained.
- The strengthened discovery gate now requires render submission, both input
  interfaces, and all action handles before it can pass.
- CrossOver MoltenVK, the app-local runtime, DXVK, and the work copy restored
  cleanly.

### Connected no-head diagnosis

Artifact:
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260715T202301Z`

- Verdict: `fail`, with clean restoration, after the Mac host restarted and
  before the user could enter immersive mode.
- The Vision Pro client connected twice, started streaming, created one HEVC
  decoder and `2880x1792` format description, and reported no decoder error or
  reset. Decoder bootstrap therefore still works.
- Open Brush acquired `IVRInput_006`, queried both hand sources, and produced
  `3112` separate-eye submissions. The producer tail was `32.237 FPS`.
- This was initially misclassified as a producer/native-consumer handshake
  timeout because the runner had no final native summary. The producer log
  proves otherwise: three frames were consumed successfully as bounded decoder
  bootstrap traffic, and the native consumer then returned `STATUS_FRAME_DROPPED`
  for `3109` fallback-pose frames. No exact pose could appear because immersive
  tracking was never entered.

The native probe now logs successful producer acceptance and startup self-tests,
emits cadence from received frames even when every post-bootstrap frame is
dropped, and fails explicitly if no exact render pose appears within 90 seconds
after decoder bootstrap. The runner falls back to the last cadence report when
a failed run has no final summary.

Disconnected artifact:
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260715T204815Z`

- Verdict: `pass` off-head after the observability change.
- The producer handshake and all three startup self-tests were recorded
  explicitly. `120/120` separate-eye frames were received, encoded, and
  released with zero drops.
- Producer cadence was `25.909 FPS`; Metal conversion averaged `921 us` wall
  time and `105 us` GPU time. Cleanup restored every staged file.

Connected unworn artifact:
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260715T204945Z`

- Verdict: expected `fail`, with bridge status `1` and clean restoration.
- The AVP client connected, created its HEVC decoder and `2880x1792` format,
  while the native source explicitly accepted the producer and passed startup
  self-tests.
- The native source received `2996` application frames, transported one bounded
  decoder-bootstrap frame, and released `2995` fallback frames as not-ready.
- After exactly 90 seconds without immersive tracking it failed with
  `ALVR exact render pose did not become ready`, rather than masquerading as a
  producer handshake failure or waiting for the outer runner timeout.
- The probe intentionally does not publish synthetic identity tracking or send
  unlimited fallback-pose video after decoder bootstrap. Exact render-pose
  pairing remains the connected acceptance contract.

### Physical failure diagnosis

Initial physical artifact:
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260715T212016Z`

- The user reported jerky motion and an apparent crash after controller-button
  input. The game and input runtime did not crash: Open Brush continued through
  `2161` separate-eye submissions, the native path transported `900` exact-pose
  frames, and the runner ended the process normally with clean restoration.
- Both controller sources became ready. The logs recorded left and right
  thumbsticks, a right trigger value of `0.546`, and a right pressed-button mask.
  Button input was therefore correlated with the visible failure but was not
  its cause.
- The Vision Pro client rendered no new decoded frame for 30 seconds, emitted
  `Fatal decoder error, restarting connection: Gimme frames >:(`, and restarted
  its stream. The application-side `IPD is bad` message was a downstream
  wireframe/display gate, not the decoder or input failure.
- A captured HEVC access unit from
  `real-native-encode-20260715T213531Z` decoded successfully through both
  `ffmpeg`'s VideoToolbox path and a focused reproduction of the client's
  Annex-B-to-`CMSampleBuffer` conversion. The encoder shape, parameter sets,
  dimensions, and native bitstream were valid.
- Packet capture artifact
  `real-native-encode-20260715T221141Z/network.pcap` contains all `60/60`
  server-to-client video packets on ALVR stream `3`, indices `0..59`, with every
  shard set complete. It simultaneously contains `73/73` client-to-server
  tracking packets on stream `0`. The network, packet IDs, and UDP reassembly
  framing were not the blocker.

The root cause was the Vision Pro client's pre-immersive fake tracking. It sent
both eyes at X `0.0` and used a zero quaternion for the HMD. The macOS bridge
correctly rejected both values, transported one bounded decoder-bootstrap IDR,
then withheld fallback-pose frames while waiting for exact tracking. The client
received that first IDR before immersive rendering and intentionally discarded
it. This made render startup depend on a later IDR and left the 30-second
watchdog vulnerable during the transition into immersive mode.

### Startup liveness fix

- `WorldTracker.sendFakeTracking` now sends an identity HMD orientation and a
  valid `64 mm` stereo pair at eye X positions `-0.032` and `0.032`. The bridge
  accepts and publishes both the view and HMD pose before immersive entry.
- The native session now selects explicit `8,000,000` byte send and receive
  buffers for both server and client. ALVR's `Maximum` setting attempted
  `u32::MAX`, which macOS rejected and left the UDP send buffer at `9216` bytes.
- Server UDP send failures are no longer discarded with `.ok()`: they are logged
  with frame metadata and request an IDR for recovery.
- The Vision Pro client now emits bounded packet/decode cadence and reports
  synchronous or callback-side VideoToolbox errors instead of suppressing them.

Clean connected artifact:
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260715T225109Z`

- Verdict: `pass` off-head with the clean client core.
- The bridge received `908` application frames, submitted, encoded, and
  transported `900/900`, paired `899` exact poses, and dropped only eight
  startup frames. There were no pool-exhaustion, generation-gap, decoder, or
  socket-send errors.
- Producer tail cadence was `32.988 FPS`; the encoded stream averaged
  `25.485 Mbps`. The client created one `2880x1792` HEVC decoder in one stream
  epoch, and exact frame-pose milestones appeared at frames `300`, `600`, and
  `900`.
- A final normal-runtime-root proof,
  `real-native-encode-20260715T225700Z`, generated all four `8,000,000` byte
  socket settings without a temporary session override, transported `30/30`
  frames, paired `29` exact poses, and restored cleanly.
- The longer no-head soak
  `real-native-encode-20260715T224027Z` transported `2400/2400` frames over
  `79.095` seconds with `2399` exact-pose frames, three startup drops, one stream
  epoch, and zero decoder errors. No `Opening Immersive Space` event appeared,
  so it is not a physical visual or controller acceptance result.

### Physical controller acceptance and cadence blocker

Physical worn artifact:
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260715T235233Z`

- The client emitted `Opening Immersive Space`, and the user saw Open Brush in
  the headset. The view started smooth and then became jerky.
- Both PS VR2 Sense controllers became ready. Open Brush received both
  thumbsticks, both triggers, and pressed-button masks from both hands. The user
  confirmed that the controllers worked in the application.
- The native path transported `3724` frames before the comparison run was
  requested, with no pool-exhaustion drops or pose-generation gaps. The client
  submitted and decoded matching cadence milestones through frame `3300`.
- The fixed `33 ms` `WaitGetPoses` delay left application cadence well below the
  headset's `90 Hz` refresh target, so this run passes physical controller
  acceptance but not visual smoothness acceptance.

No-fixed-sleep comparison artifact:
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260716T000217Z`

- The deadline pacer transported `5400/5400` frames with `5399` exact poses,
  zero producer or native drops, and clean restoration.
- Producer cadence was `24.360 FPS` overall and `20.770 FPS` in the final
  300-frame window, below the required `25 FPS` floor and far below `90 Hz`.
- The user reported that this pass felt about the same. Removing the artificial
  sleep therefore improved some frame windows but did not resolve headset
  jerkiness.
- The next performance experiment should reduce or disable the `3840x2160`
  desktop mirror and lower the per-eye render target, then require sustained
  cadence materially closer to the headset refresh rate before another comfort
  judgment.

### Low-load source-cadence gate

The next run is software-only. The Open Brush launcher should use a `1280x720`
windowed desktop mirror, an aspect-correct `1080x1344` render target per eye,
and a fixed `2160x1344` producer surface that the native bridge scales to the
existing `2880x1792` ALVR output. Keep the deadline pacer active; do not set
`ALVR_FAKE_WAIT_GET_POSES_SLEEP_MS`.

Command:

```bash
ALVR_NATIVE_PROBE_CONNECT=false \
ALVR_NATIVE_PROBE_FRAMES=900 \
bash tools/run_open_brush_native_probe.sh
```

Expected proof:

- the Unity launch command contains the windowed `1280x720` mirror arguments;
- all `900` separate-eye frames are submitted, encoded, and released with no
  producer, pool, pose-generation, or native drop;
- the final 300-frame source window sustains at least `60 FPS`;
- the runner restores stock OpenVR and MoltenVK and removes all staged files.

Known failure signatures:

- a `3840x2160` swapchain after the new launch arguments means Unity ignored
  the mirror override;
- a tail below `60 FPS` with sub-millisecond bridge conversion means the
  remaining boundary is Open Brush, Unity, or CrossOver presentation rather
  than the IOSurface pool or encoder;
- any source-size mismatch, pool exhaustion, or generation gap invalidates the
  performance comparison.

Results:

- Baseline low-load artifact
  `real-native-encode-20260716T005736Z` proved that Unity honored the
  `1280x720` mirror and that Open Brush submitted `1080x1344` per-eye textures.
  It released `900/900` frames with no drops, but the final 300-frame cadence
  was only `23.403 FPS`.
- The player log contained `628` repeated Windows video errors while trying to
  prepare the bundled `animated-logo.mp4`. The error path could not obtain a
  shared handle from `IDXGIResource` under CrossOver.
- Artifact `real-native-encode-20260716T010331Z` temporarily removed that sample
  video while preserving the video directory, eliminating the repeated errors.
  Tail cadence improved to `34.164 FPS`, a real but insufficient gain.
- Artifact `real-native-encode-20260716T010649Z` reduced the mirror to
  `960x540` and the per-eye target to `720x896`. Its `34.171 FPS` tail was
  effectively identical, proving that pixel workload was no longer the limit.
- Artifact `real-native-encode-20260716T011005Z` applied
  `dxgi.syncInterval = 0`; DXVK confirmed `VK_PRESENT_MODE_IMMEDIATE_KHR`, but
  tail cadence remained `32.886 FPS`. Desktop VSync was not the cap.
- Artifact `real-native-encode-20260716T011417Z` forced Open Brush quality level
  `0` and regressed the tail to `9.989 FPS`. Do not repeat that override.
- Every discriminator submitted and released `900/900` frames with zero pool,
  pose-generation, or native drops and roughly sub-millisecond native
  conversion. The acceptance gate therefore fails at the legacy Open Brush
  `1.0.28` Unity 2019 OpenVR multi-pass source path, not at the GPU-resident
  bridge, encoder, or transport.

The maintained launcher now suppresses the broken default sample video for the
duration of each run, restores it afterward, supports multiple Unity arguments,
and accepts aspect-checked mirror and eye-size overrides. Keep the default
`1280x720` mirror and `1080x1344` per-eye target for image quality; the smaller
target has no cadence benefit. Final launcher smoke artifact
`real-native-encode-20260716T012146Z` submitted and released `300/300` frames,
captured no video errors, and restored the sample video, OpenVR DLL, MoltenVK,
and probe lock. Its short startup-inclusive cadence is not a performance result.

### Unity Control And Logger Root Cause

The official SteamVR Tutorial control in
`docs/probes/012-steamvr-tutorial-unity-performance-control.md` reproduced the
same low cadence with Unity `2019.3.1f1`, the same stock OpenVR DLL, a
resolution-matched `1080x1344` per-eye target, and single-pass side-by-side
submission. Its pre-fix final window reached `31.192 FPS`, while the matched
Open Brush run reached `38.182 FPS`.

The shared cause was probe instrumentation. The fake OpenVR runtime opened,
wrote, and closed its diagnostic file for every logged API call. Replacing that
path with one locked `1 MiB` buffered file retained every diagnostic while
removing the synchronous file-open cost from Unity's per-frame compositor,
tracked-device, and input queries.

Post-fix artifact `real-native-encode-20260716T111010Z` immediately raised Open
Brush's final window to `89.094 FPS`; it had one startup pool exhaustion at
submit sequence `14`. Clean repeat artifact
`real-native-encode-20260716T111208Z` passed with `900/900` submitted, encoded,
and released frames, zero producer or native drops, a `89.925 FPS` final window,
and `520 us` average native conversion. The official game binary and maintained
`1280x720` mirror plus `1080x1344` per-eye profile did not change.

Extended disconnected soak artifact
`real-native-encode-20260716T112248Z` then passed `5400/5400` submitted,
encoded, and released frames with zero producer or native drops, a
`90.006 FPS` final window, `449 us` average native conversion, and clean
restoration. This covers one continuous minute at the configured headset rate
without requiring the headset or transport.

## Current Status

`controller acceptance and software-only smoothness passed` on July 16, 2026
UTC. Runtime selection, separate-eye discovery, GPU pair assembly,
`IVRInput_006`, both physical controller sources, Open Brush actions, exact-pose
pairing, hardware encode, UDP delivery, decoder submission, sustained
`90.006 FPS` producer cadence across a `5400`-frame disconnected soak, and
cleanup are verified. The earlier
approximately `34 FPS` result was synchronous fake-runtime logging overhead,
not a legacy Unity multi-pass limit.

Open Brush has completed its controller and performance-discriminator purpose.
Do not download another Unity game or build a custom game fork. Keep the
buffered runtime logger, production pool, encoder, bitrate, and output geometry
fixed, then resume official Freedom recenter, reconnect, headset
removal/reentry, and soak validation. An unmodified official OpenXR release may
only be considered later as a separate bounded runtime probe.
