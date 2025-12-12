
# Design: OpenVR macOS Bridge

This doc captures a minimal “bridge” architecture for running **Windows OpenVR
titles** under CrossOver/Wine while keeping the VR runtime and streaming stack
native to Apple hardware.

The intended end state for this repo is an **Apple-only** pipeline:

- Game runs in CrossOver/Wine on Apple Silicon
- VR runtime compatibility layer satisfies OpenVR/SteamVR expectations
- Streaming server runs native on Apple Silicon (hardware encoding)
- Client runs on Apple Vision Pro

## Components

### Windows side (under Wine)

- `openvr_api.dll` / OpenVR client entry points loaded by the game
- Implements enough of `IVRSystem`, `IVRCompositor`, etc. to keep titles alive
- Shares state with macOS-side runtime (shared memory / sockets)

### macOS side (native)

- Tracks state (HMD pose, controllers, timing)
- Provides the compositor loop contract:
  - `WaitGetPoses()` cadence
  - `Submit()` accepting frames and advancing frame index
- Feeds an Apple-native encoder + transport layer

## Message flow (conceptual)

1. Game queries `IVRSystem` capabilities and starts rendering.
2. Game enters compositor loop:
   - calls `IVRCompositor::WaitGetPoses()` once per frame
   - submits textures via `IVRCompositor::Submit()`
3. The Windows-side shim forwards calls and surfaces to macOS.
4. macOS runtime publishes poses/timing and consumes submitted frames.
5. Encoder/transport streams to headset.

## Notes

- Many titles will not render continuously unless `WaitGetPoses()` blocks and
  returns plausible timing/poses.
- Stack correctness is often about “small expectations” (settings, events,
  input paths) rather than one big API.
