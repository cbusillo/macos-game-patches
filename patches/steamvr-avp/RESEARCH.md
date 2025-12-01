# SteamVR to Apple Vision Pro Streaming

**Status**: Research / Feasibility Testing
**Goal**: Run Windows VR games on macOS via CrossOver, stream to Apple Vision Pro
**Last Updated**: 2025-12-01

## Problem Statement

- Apple Vision Pro users have no way to play PC VR games on macOS
- macOS native games don't support VR
- ALVR supports AVP as a client, but requires a Windows/Linux PC with GPU for the server
- We want to eliminate the need for a separate PC

## Proposed Architecture

```
┌─────────────────────────────────────────────────┐
│                  CrossOver/Wine                  │
│  ┌─────────────┐    ┌─────────────────────────┐ │
│  │  VR Game    │◀──▶│  SteamVR + Shim Driver  │ │
│  │ (SteamVR)   │    │  (receives tracking,    │ │
│  │             │    │   outputs stereo frames)│ │
│  └─────────────┘    └───────────┬─────────────┘ │
└─────────────────────────────────┼───────────────┘
                                  │ IPC (shmem/socket)
                    ┌─────────────▼─────────────┐
                    │  macOS ALVR Server        │
                    │  - Receives frames        │
                    │  - VideoToolbox encode    │
                    │  - Sends tracking TO Wine │
                    └─────────────┬─────────────┘
                                  │ WiFi 6
                    ┌─────────────▼─────────────┐
                    │   Apple Vision Pro        │
                    │   (ALVR client)           │
                    └───────────────────────────┘
```

## Feasibility Test Results (2025-12-01)

### Phase 1: SteamVR in CrossOver - PROMISING

**Test Environment:**
- CrossOver 24 on macOS (Darwin 25.2.0)
- Apple M4 Max with 110GB unified memory
- SteamVR 2.14.3 (build 1763764086)

**Target Hardware:**
- Apple Vision Pro (inside-out tracking, WiFi only - no wired connection)
- PSVR 2 controllers (need tracking bridge to SteamVR)
- No external base stations (lighthouse) needed
- No Oculus/Meta hardware

### What Works

| Component | Status | Notes |
|-----------|--------|-------|
| vrserver.exe | ✅ Works | Starts, loads all drivers, runs web server |
| vrcompositor.exe | ✅ Works | Launches, connects to vrserver |
| Null HMD driver | ✅ Works | Provides fake HMD for testing |
| MoltenVK/Vulkan | ✅ Works | 111 Vulkan extensions available |
| Driver loading | ✅ Works | lighthouse, oculus, vrlink, gamepad all load |
| vrmonitor.exe | ❌ Crashes | Null pointer dereference in Wine |

### Key Log Evidence

**vrserver successfully starts:**
```
vrserver 2.14.3 startup with PID=392, config=C:\Program Files (x86)\Steam\config
[Init] VR server 2.14.3 (v1763764086) starting up
[Web] Starting web server on port 27062
```

**Null driver activates:**
```
Using existing HMD null.Null Serial Number
[Input] openvr.component.vrcompositor (null_hmd) loading config
```

**All drivers load:**
```
Loaded server driver lighthouse (IServerTrackedDeviceProvider_004)
Loaded server driver oculus (IServerTrackedDeviceProvider_004)
Loaded server driver gamepad (IServerTrackedDeviceProvider_004)
```

**MoltenVK recognizes GPU:**
```
[mvk-info] MoltenVK version 1.2.10, supporting Vulkan version 1.2.290
GPU device: Apple M4 Max
GPU memory available: 110100 MB
```

### What Doesn't Work (Yet)

**vrmonitor crash:**
```
wine: Unhandled page fault on write access to 0000000000000000
at address 00006FFFFFF76435 (thread 024c)
```

This is a Wine compatibility bug in vrmonitor, not a fundamental blocker.

### Configuration

Enable null driver in `steamvr.vrsettings`:
```json
{
    "driver_null": {
        "enable": true,
        "loadPriority": 999
    },
    "steamvr": {
        "requireHmd": false,
        "activateMultipleDrivers": true
    }
}
```

## Updated Research Tasks

### Phase 1: Feasibility - COMPLETE

- [x] Test if SteamVR launches in CrossOver - **YES**
- [x] Identify what compatibility checks SteamVR performs - **Minimal**
- [x] Determine if SteamVR's null driver works in Wine - **YES**
- [ ] Test IPC mechanisms between Wine and macOS (shmem, sockets)

### Phase 2: Proof of Concept - IN PROGRESS

- [ ] Fix or work around vrmonitor crash
- [ ] Build minimal OpenVR driver that SteamVR accepts
- [ ] Capture a single frame from SteamVR compositor
- [ ] Transfer frame to macOS native code
- [ ] Encode with VideoToolbox

### Phase 3: Integration

- [ ] Implement ALVR protocol (or fork ALVR server)
- [ ] Add bidirectional tracking
- [ ] Optimize latency

## Components Needed

### 1. SteamVR Driver Shim (Wine-side)

A custom OpenVR driver that runs inside CrossOver/Wine:

- Presents as an HMD to SteamVR and games
- Receives head/controller tracking via IPC from macOS
- Captures rendered stereo frames from compositor
- Sends frames to macOS via shared memory or socket

**Reference projects:**
- [ALVR](https://github.com/alvr-org/ALVR) - Rust-based, has driver code
- [OpenComposite](https://gitlab.com/znixian/OpenOVR) - Intercepts OpenVR calls
- [Monado](https://monado.freedesktop.org/) - Open source OpenXR runtime

### 2. macOS ALVR Server

Native macOS application:

- Receives stereo frames from Wine via IPC
- Encodes using VideoToolbox (H.264/HEVC hardware encoding)
- Implements ALVR streaming protocol
- Receives tracking data from AVP, forwards to Wine driver

**Key technologies:**
- VideoToolbox for hardware encoding
- Metal for any GPU-side frame manipulation
- Unix domain sockets or shared memory for Wine IPC

### 3. ALVR Client (Existing)

ALVR already has an Apple Vision Pro client:
- https://github.com/alvr-org/ALVR
- Handles decoding, display, tracking

## Technical Challenges

### Latency Budget

VR requires <20ms motion-to-photon. Budget breakdown:

| Stage | Target | Notes |
|-------|--------|-------|
| Tracking AVP→Mac | 2ms | WiFi 6 (no wired option) |
| Tracking Mac→Wine | 1ms | IPC |
| Game render | 8ms | 120fps target |
| Frame Wine→Mac | 1ms | Shared memory |
| Encode | 3ms | VideoToolbox |
| Network | 3ms | WiFi 6 |
| Decode + display | 2ms | AVP |
| **Total** | **20ms** | Tight but possible |

### Frame Capture

Options for getting frames out of SteamVR:

1. **Compositor hook** - Intercept before final present
2. **Custom driver** - Driver receives frames for "display"
3. **Desktop capture** - Fallback, higher latency

### Wine/macOS IPC

Proven mechanisms:
- Unix domain sockets (works in Wine)
- POSIX shared memory (`shm_open`)
- Memory-mapped files
- Winelib hybrid applications

### Controller Input (PSVR 2)

**Solved by visionOS 26** - PSVR 2 controllers are natively supported.

Data flow:
```
PSVR2 controllers → visionOS 26 → ALVR client → WiFi → macOS ALVR server → Wine driver → SteamVR
```

No custom macOS PSVR2 driver needed. ALVR client on AVP receives controller data from visionOS and forwards it over the network.

## Alternative Approaches Considered

### Native Monado (Rejected)

- Monado is open source OpenXR
- Could run natively on macOS
- **Problem**: macOS games don't have VR support, defeats purpose

### Port ALVR Server to macOS (Partial)

- ALVR server is Rust, could compile for macOS
- **Problem**: Still needs SteamVR, which needs Windows
- Could work if combined with Wine approach

### WiVRn Instead of ALVR

- Cleaner codebase than ALVR
- Also supports Quest
- **Unknown**: AVP client support?
- Worth investigating as alternative

## Prior Art

| Project | URL | Relevance |
|---------|-----|-----------|
| ALVR | https://github.com/alvr-org/ALVR | AVP client, server reference |
| WiVRn | https://github.com/WiVRn/WiVRn | Alternative streamer |
| OpenComposite | https://gitlab.com/znixian/OpenOVR | OpenVR interception |
| Monado | https://monado.freedesktop.org/ | Open XR runtime |
| iVRy | https://www.ivrydrivers.com/ | Commercial, shows it's possible |

## Answered Questions

1. **Does SteamVR even launch in CrossOver?** - YES! vrserver and vrcompositor work
2. **Can SteamVR's null driver work in Wine?** - YES! Provides fake HMD
3. **What's the minimum viable driver SteamVR will accept?** - The null driver shows it's simple

## Remaining Questions

1. Can a custom SteamVR driver in Wine communicate with macOS native code?
2. Can we reuse ALVR's encoding/protocol code directly?
3. What tracking data format does AVP's ALVR client expect?
4. How to fix vrmonitor crash (or is it needed at all)?

## Next Steps

1. ~~Install SteamVR in CrossOver, document what happens~~ DONE
2. ~~Fork ALVR, analyze architecture~~ DONE - forked to cbusillo/ALVR
3. Investigate vrmonitor crash - may not be needed if we run headless
4. Implement macOS CEncoder with VideoToolbox
5. Test Wine↔macOS Unix socket IPC
6. Build and test on CrossOver

## ALVR Architecture Analysis

**Fork location:** https://github.com/cbusillo/ALVR
**Local clone:** `/Users/cbusillo/Developer/claude-local-machine/scratch/ALVR`

### Key Discovery: macOS Stub Already Exists

ALVR has a `platform/macos/` folder with a stub `CEncoder.h`:
- Empty implementation - just placeholder methods
- Need to implement: `Init()`, `Run()`, `CaptureFrame()`, `InsertIDR()`

### Linux Implementation (Reference)

The Linux `CEncoder` uses Unix sockets for IPC:
```cpp
pollfd m_socket;
std::string m_socketPath;
int m_fds[6];  // File descriptors for DMA-BUF
```

This pattern works for Wine→macOS communication.

### Platform Structure

```
alvr/server_openvr/cpp/platform/
├── linux/     ← 23 files, FFmpeg/VAAPI encoding
├── macos/     ← 2 files, STUB ONLY
└── win32/     ← 32 files, D3D11/NVENC/AMF encoding
```

### What We Need to Implement

1. **macOS CEncoder** - Receive frames via Unix socket, encode with VideoToolbox
2. **macOS FrameRender** - Handle GPU texture sharing
3. **Socket protocol** - Match Linux pattern for Wine compatibility
4. **VideoToolbox integration** - H.264/HEVC hardware encoding
