# Native IOSurface AVP Live Gate

## Hypothesis

The native macOS bridge can feed an IOSurface-compatible NV12 pixel-buffer pool
directly into VideoToolbox, connect to the physical AVP v21 client, and sustain
headset-rate encode and transport cadence without a full-frame CPU copy between
the native source and encoder.

## Environment

- Mac: Apple Silicon, macOS 27.0.0.
- Device: physical Apple Vision Pro, visionOS 27.0, connected through Xcode
  Device Hub over the CoreDevice local-network tunnel.
- Device state: paired, booted, Developer Mode enabled, unlocked.
- ALVR source: `cbusillo/ALVR@4bd8ad05` on
  `diagnostic/bgra-nv12-probe`.
- visionOS client: `cbusillo/alvr-visionos@3a00da3`, with ALVR submodule
  `cbusillo/ALVR@109643c8`.
- Installed client bundle: `com.shinycomputers.probe.alvrclient`.

## Commands

Device preflight, with the physical device identifier substituted locally:

```bash
xcrun devicectl list devices
xcrun devicectl device info details --device <AVP_DEVICE_ID>
xcrun devicectl device info lockState --device <AVP_DEVICE_ID>
xcrun devicectl device info apps --device <AVP_DEVICE_ID>
```

Bridge build and launch:

```bash
python3 tools/vr_stack_cleanup.py --json

cd ~/Developer/alvr
cargo build --release -p alvr_macos_bridge

ALVR_BRIDGE_ROOT="$HOME/Library/Application Support/alvr/macos_bridge" \
ALVR_BRIDGE_INPUT=iosurface-synthetic \
ALVR_BRIDGE_WIDTH=3664 \
ALVR_BRIDGE_HEIGHT=1920 \
ALVR_BRIDGE_FPS=90 \
ALVR_BRIDGE_BITRATE_BPS=50000000 \
target/release/alvr_macos_bridge
```

Client launch and console capture:

```bash
xcrun devicectl device process launch \
  --device <AVP_DEVICE_ID> \
  --terminate-existing \
  --console \
  com.shinycomputers.probe.alvrclient
```

## Evidence Log

### 2026-07-10 Physical AVP No-Eyes Pass

Run: Native IOSurface synthetic source, followed by two physical-client launches
to exercise initial connection and reconnect behavior.

Question: Can the native surface, VideoToolbox, ALVR transport, and physical AVP
client reach and hold a 90 Hz streaming session without requiring CrossOver or
headset visual interpretation?

Mode / build: release `alvr_macos_bridge`, `iosurface-synthetic`, `3664x1920`,
90 FPS, 50 Mbps, installed v21-dev12 AVP client.

Expected proof:

- physical client reaches `Streaming`;
- negotiated refresh hint is 90 Hz;
- bridge cadence remains approximately 90 frames per second;
- encoded packets are queued and sent after connection and reconnect;
- no fatal decoder error appears in the client console.

Artifacts captured locally in the ignored directory
`.code/probes/006-native-iosurface-avp-live-gate-20260710/`:

- `device-details.json`
- `device-apps.json`
- `avp-client-console.log`
- `bridge-session-log.txt`

Verified:

- CoreDevice reported the physical AVP connected, paired, booted, Developer Mode
  enabled, and unlocked.
- The installed client reported ALVR `21.0.0-dev12`, discovered the bridge over
  mDNS, connected successfully, and entered `Streaming`.
- `StreamingStarted` reported `view_width=2144`, `view_height=2048`, and
  `refresh_rate_hint=90.0`.
- The bridge reported `frames=90 emitted=89` after startup and then sustained
  90-frame cadence windows with zero deadline misses.
- After each client connection the bridge requested an immediate IDR, queued
  encoded packets, and logged continuing `sent video stream packet` samples.
- A terminate-and-relaunch cycle reconnected and resumed packet transmission.
- The captured client console contained no fatal decoder, panic, timeout, or
  crash message during the observed streaming windows.

Inferred:

- The native IOSurface-compatible pixel-buffer ownership, VideoToolbox encode,
  ALVR packet path, and physical-client session lifecycle are healthy enough to
  proceed to a bounded headset visual check.
- Transient extra connection attempts can time out or be refused after a valid
  session is already sending packets. They did not interrupt the observed active
  stream, but the redundant connection behavior should remain visible in logs.

Failed / missing:

- The headset was not worn, so the client emitted fake tracking rather than a
  valid live HMD pose.
- CoreDevice reported that screenshot and display-information capabilities are
  unsupported for this physical AVP, so no remote visual artifact was captured.

Unknown:

- Whether the synthetic image was decoded and displayed correctly in both eyes.
- Stereo alignment, depth, edge coverage, world locking, motion response, and
  perceived latency.
- Real tracking/view-pose correctness while the headset is worn.

Verdict: `alive / no-eyes pass`. Native surface cadence and transport to the
physical client are green. The remaining gate is explicitly human-observed
display and tracking behavior, not server-side encode or packet delivery.

Do not repeat:

- Do not treat fake tracking or zeroed client eye poses from an unworn headset
  as evidence about world locking or stereo correctness.
- Do not retry `devicectl` screenshot capture on this device unless a future
  CoreDevice/Xcode version advertises that capability.
- Do not ask for headset interpretation before the bridge logs client-connected,
  IDR, emitted, queued, and sent evidence for the same run.

Next action: perform one short eyes-on run using the same native surface mode.
Confirm that a moving synthetic pattern is visible in both eyes and remains
stable long enough to classify decoder/display success. Route native surface
facts to #39 and human observations to #41.

## Verdict

`alive`: the native IOSurface/VideoToolbox/ALVR path reaches the physical AVP at
headset-rate cadence without CrossOver. Visual correctness remains deliberately
unclaimed until an eyes-on run is recorded.

## Next Action

Use the same commands for a bounded eyes-on display check, then freeze this
synthetic mode as a native encode-surface contract probe and move production
work toward real GPU texture handoff in issue #40.
