# SteamVR + ALVR Plan (HEVC + Opus)

As of February 15, 2026.

## Objective

Run Windows SteamVR content through CrossOver on this Mac Studio M4 Max and
stream to Apple Vision Pro, with these non-negotiable constraints:

- Video codec: HEVC only.
- Video encode engine: native macOS `VideoToolbox` hardware encoder only.
- Software video encoding: hard fail.
- Audio codec: Opus.
- Windows VM path: optional for diagnostics only, never a fallback path for
  success.

## Confirmed Technical Starting Point

- SteamVR in CrossOver is launchable and scriptable from this workspace.
- Hardware HEVC encode is already gated by `tools/hevc_gate.py`.
- ALVR macOS server-side encoder path is currently a stub in
  `~/Developer/ALVR/alvr/server_openvr/cpp/platform/macos/CEncoder.h`.
- ALVR audio path currently transports PCM sample payloads and does not include
  Opus support in-tree.

Implication:

- We need a new bridge between the Windows OpenVR driver process in CrossOver
  and a native macOS media process that owns hardware HEVC encode.
- We need explicit Opus codec work for AVP audio path.

## Architecture

```text
CrossOver SteamVR (Windows process in Wine)
  -> ALVR OpenVR driver (Windows)
  -> frame readback to shared memory ring
  -> local IPC control channel
  -> native macOS media daemon
  -> VideoToolbox HEVC hardware encode
  -> encoded NALs back to ALVR send path
  -> ALVR transport to AVP client

Audio path:
CrossOver audio output
  -> macOS capture bridge
  -> Opus encoder
  -> ALVR audio transport
  -> Opus decoder on AVP client
```

## Current Progress

- February 15, 2026: bridge IPC schema and ring layout defined in
  `~/Developer/ALVR/alvr/server_openvr/cpp/platform/macos/VtBridgeProtocol.h`
  and
  `~/Developer/ALVR/alvr/server_openvr/cpp/platform/macos/VtBridgeProtocol.md`.
- February 15, 2026: Windows driver handshake client stub added in
  `~/Developer/ALVR/alvr/server_openvr/cpp/platform/win32/VtBridgeClient.h`
  and
  `~/Developer/ALVR/alvr/server_openvr/cpp/platform/win32/VtBridgeClient.cpp`.
- February 15, 2026: local daemon/probe harness added in this repo under
  `tools/vtbridge_*.py`.
- February 15, 2026: deterministic handshake gate added:
  `tools/vtbridge_handshake_gate.py`.
- February 15, 2026: ring-state conformance test added:
  `tools/vtbridge_ring_conformance.py`.
- February 15, 2026: Windows encoder selection now attempts `VideoEncoderVtBridge`
  first, and can be made hard-required with `ALVR_VTBRIDGE_REQUIRED=1`.
- February 15, 2026: `VideoEncoderVtBridge` now copies staging texture bytes into
  ring slots and signals `FrameReady` with slot index/payload metadata.
- February 15, 2026: daemon now returns `EncodedNal` from ring frames using a
  VideoToolbox-backed ffmpeg path for bring-up validation.
- February 15, 2026: deterministic bridge gates all pass locally:
  `vtbridge_handshake_gate`, `vtbridge_ring_conformance`, and
  `vtbridge_hw_stream_gate`.
- February 15, 2026: daemon encode path simplified to strict single-frame
  `hevc_videotoolbox` encode to avoid hidden buffering/fallback behavior during
  conformance runs.
- February 15, 2026: ALVR fork now builds again after initializing `openvr`
  submodule; `cargo check -p alvr_server_openvr` succeeds.
- February 15, 2026: SteamVR smoke with bridge-required env still routes through
  Valve `vrlink` and reports `HmdNotFound`; daemon log shows no bridge client
  connections yet. Driver registration/deployment is the active blocker.
- February 15, 2026: attempted `x86_64-pc-windows-gnu` build for
  `alvr_server_openvr`; build still fails on Windows-specific DirectX/AMF
  compilation paths (`directxcolors.h`, AMF macros). A proper Windows/MSVC
  build lane is still required for deployable driver DLLs.
- February 15, 2026: added `tools/alvr_driver_register.py` and applied it to
  the Steam bottle. SteamVR logs now explicitly show:
  `Unable to load driver alvr_server ... driver_alvr_server.dll` and
  `VRInitError_Init_FileNotFound(103)`.
  This confirms path registration works and the next blocker is producing and
  deploying `driver_alvr_server.dll`.
- February 15, 2026: Windows/MSVC `alvr_server_openvr.dll` build lane is
  working again through the Winders VM (`C:\dev\ALVR`), and deploy to the
  CrossOver bottle is repeatable.
- February 15, 2026: `driver_alvr_server.dll` runtime dependencies required in
  the target folder were identified and deployed side-by-side:
  `openvr_api.dll` and `libvpl.dll`.
- February 15, 2026: ALVR driver now loads reliably in SteamVR and advertises
  `alvr_server.1WMHH000X00000` as active HMD in `vrserver` logs.
- February 15, 2026: to avoid the `HmdNotFound` boot loop before client
  connect, fork patch enables early HMD initialization in
  `~/Developer/ALVR/alvr/server_openvr/src/lib.rs`.
- February 15, 2026: major runtime blocker discovered independent of ALVR:
  `vrcompositor` fails under CrossOver with
  `VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer` on both
  `d3dmetal` and `dxvk` backends.
- February 15, 2026: compositor compatibility patching progressed past startup
  blockers; ALVR-forced runs now reach `Startup Complete` but hit
  `Exception c0000005` shortly after startup.
- February 15, 2026: direct-mode-off experiment in ALVR (env
  `ALVR_DISABLE_DIRECT_MODE=1`) flips compositor to desktop display path but
  does not yet eliminate the post-start crash or `Headset Error (-202)`.
- February 15, 2026: direct-mode-on + bridge-required runs still show
  `CreateSwapTextureSet GetSharedHandle ... Not implemented.` from ALVR in
  `vrserver` logs, and SteamVR fails with `(-203)`.
- Mirror snapshot recorded in `docs/bridge-ipc-v1.md`.

## Phase Plan

## Phase 0: Lock Baselines

Tasks:

1. Record ALVR revision with `python3 tools/alvr_lock.py`.
2. Capture null-driver SteamVR baseline with
   `python3 tools/steamvr_smoke.py --mode null`.
3. Capture HEVC hardware baseline with `python3 tools/hevc_gate.py`.

Gate:

- All three commands complete and produce reproducible run artifacts.

Acceptance:

- We can rerun baseline collection at will and compare logs over time.

## Phase 1: Driver Load Reliability

Tasks:

1. Force SteamVR to load the ALVR driver in CrossOver.
2. Extend smoke capture to assert ALVR driver load markers in logs.
3. Fail fast on `HmdNotFound` loops when driver should own HMD state.

Gate:

- Driver loads deterministically across repeated launches.

Acceptance:

- Run bundles clearly show driver load lifecycle and stable process state.

## Phase 2: Bridge IPC Skeleton

Tasks:

1. Define versioned control protocol (`hello`, `configure`, `frame-ready`,
   `encoded-nal`, `stats`, `fatal`).
2. Implement local authenticated control socket on loopback.
3. Implement shared memory ring for uncompressed frame payloads.
4. Add loopback harness that sends synthetic frames and receives mock encoded
   outputs.

Gate:

- IPC survives daemon restarts and SteamVR restarts without manual cleanup.

Acceptance:

- Loopback test demonstrates stable throughput and no unbounded queue growth.

## Phase 3: Native HEVC Video Bridge

Tasks:

1. Add a Windows-side encoder backend in ALVR driver that forwards raw frames
   to the macOS daemon.
2. Implement VideoToolbox session creation with hardware-required semantics.
3. Convert VideoToolbox output to Annex B NAL stream and feed ALVR NAL path.
4. Expose per-frame timing (`readback`, `queue`, `encode`, `return`).

Gate:

- If hardware-accelerated HEVC cannot be proven at runtime, stop stream
  immediately.

Acceptance:

- First stable in-headset SteamVR scene with hardware-only HEVC encode.

## Phase 4: Opus Audio Bring-Up

Tasks:

1. Add audio codec negotiation field (PCM and Opus explicit).
2. Implement Opus encode on server side for game audio (48 kHz stereo target).
3. Implement Opus decode on AVP client with jitter buffer.
4. Add timestamping and drift metrics against video timeline.

Gate:

- Opus path must run continuously without periodic dropouts.

Acceptance:

- AVP playback is intelligible, low-latency, and stable over at least
  30 minutes.

## Phase 5: Tracking/Input Integration

Tasks:

1. Validate head pose round-trip latency and stability.
2. Validate controller and hand input mapping for at least one title.
3. Add recovery behavior for network blips and daemon reconnects.

Gate:

- Input latency remains within playable threshold for target test title.

Acceptance:

- First playable session with reproducible launch sequence.

## Phase 6: Hardening

Tasks:

1. Run 60-minute soak tests with metrics capture.
2. Add bitrate and keyframe policies for stable motion scenes.
3. Build issue matrix by game, SteamVR build, and ALVR commit.

Gate:

- No fatal crashes and no software-encode regressions in soak runs.

Acceptance:

- Repeatable “known good” profile documented for this exact host + HMD.

## Observability Requirements

- Shared run ID across SteamVR logs, ALVR driver logs, and media daemon logs.
- Structured per-frame metrics for video and per-packet metrics for audio.
- Automatic archive per run under `temp/vr_runs/` with machine-readable summary.

## Immediate Next Three Actions

1. Isolate compositor failure in a deterministic gate using
   `python3 tools/steamvr_smoke.py --mode null --graphics-backend <backend>`
   and assert whether `VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer`
   appears.
2. Build and document a SteamVR compositor compatibility patch strategy focused
   on shared constant-buffer creation failure under Wine/CrossOver.
3. After compositor bring-up is stable, rerun bridge-required smoke with daemon
   active and assert first `vtbridge` handshake in daemon logs.
4. Keep Opus implementation design in parallel with explicit packet header and
   jitter buffer contract so audio work can proceed once video runtime is
   unblocked.
