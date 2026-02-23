# Bridge IPC v1 Snapshot

As of February 15, 2026.

This file mirrors the current protocol definition created in the ALVR fork:

- `~/Developer/ALVR/alvr/server_openvr/cpp/platform/macos/VtBridgeProtocol.h`
- `~/Developer/ALVR/alvr/server_openvr/cpp/platform/macos/VtBridgeProtocol.md`

## Purpose

Define the first local bridge contract between the ALVR Windows driver inside
CrossOver and a native macOS media daemon responsible for hardware HEVC encode.

## v1 Decisions

- Control plane: loopback TCP with explicit frame envelope.
- Data plane: shared memory ring with slot state transitions.
- Input pixel format: BGRA8.
- Output bitstream: HEVC Annex B.
- Strict hardware requirement for VideoToolbox encode.
- Fail fast on protocol mismatch and non-hardware encode.

## Message Set

- `HelloRequest`
- `HelloResponse`
- `ConfigureVideoRequest`
- `ConfigureVideoResponse`
- `FrameReady`
- `VideoConfig`
- `EncodedNal`
- `Stats`
- `Fatal`
- `Ping`
- `Pong`

## Ring Layout Summary

- `RingHeader`
- repeated slot records:
  - `RingSlotHeader`
  - raw frame payload

Slot state machine:

- `Empty -> Writing -> Ready -> Reading -> Empty`

## Next Implementation Step

Implement skeleton endpoints using this protocol:

1. Windows driver bridge client stub.
2. macOS daemon handshake server stub.
3. Loopback conformance test for message framing and slot transitions.

## Current Status

- Windows driver-side client stub created in ALVR fork:
  `~/Developer/ALVR/alvr/server_openvr/cpp/platform/win32/VtBridgeClient.h`
  and
  `~/Developer/ALVR/alvr/server_openvr/cpp/platform/win32/VtBridgeClient.cpp`.
- Windows bridge encoder path currently integrated into ALVR encoder selection:
  `~/Developer/ALVR/alvr/server_openvr/cpp/platform/win32/VideoEncoderVtBridge.h`
  and
  `~/Developer/ALVR/alvr/server_openvr/cpp/platform/win32/VideoEncoderVtBridge.cpp`.
- Local daemon/probe harness added in this repo:
  - `tools/vtbridge_protocol.py`
  - `tools/vtbridge_daemon.py`
  - `tools/vtbridge_probe.py`
  - `tools/vtbridge_ring_conformance.py`

Current execution behavior:

- Windows bridge client now writes mapped staging texture bytes into ring slots
  and sends `FrameReady` with slot metadata.
- Daemon reads ring payloads, performs single-frame HEVC encode using
  `hevc_videotoolbox`, and emits `EncodedNal` (and `VideoConfig` when available).

Bridge-related environment controls:

- `ALVR_VTBRIDGE_PORT`: override loopback control port.
- `ALVR_VTBRIDGE_RING_PATH`: override Windows-visible ring path.
- `ALVR_VTBRIDGE_REQUIRED=1`: fail encoder init if bridge is unavailable.

## Quick Handshake Test

```bash
python3 tools/vtbridge_daemon.py --port 37329 --accept-configure --report-hardware-active
```

In another shell:

```bash
python3 tools/vtbridge_probe.py --port 37329
```

Or run the deterministic gate bundle flow:

```bash
python3 tools/vtbridge_handshake_gate.py
```

Optional hardware probe during configure:

```bash
python3 tools/vtbridge_daemon.py --port 37329 --accept-configure --enforce-hw-hevc
```

Ring-state conformance test:

```bash
python3 tools/vtbridge_ring_conformance.py
```
