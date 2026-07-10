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

### 2026-07-10 Physical AVP Eyes-On Result

Run: the same release `iosurface-synthetic` bridge configuration and installed
physical AVP client used by the no-eyes gate.

Question: Does the already-proven encoded stream produce a visible binocular
image, and is the native stereo presentation aligned well enough to proceed to
real submitted frames?

Artifacts captured locally in the ignored directory
`.code/probes/006-native-iosurface-avp-eyes-on-20260710/`:

- `client-console.log`
- `bridge-console.log`
- `bridge-session-log.txt`

Verified:

- The client entered `Streaming` at `2144x2048` per eye with a `90.0` Hz
  refresh hint.
- The bridge continued 90-frame cadence windows with zero deadline misses and
  continuously queued and sent video packets during the observation.
- Human-observed: the synthetic pattern was visible in the headset.
- Human-observed: the left/right presentation was not aligned.
- While the headset was worn, the bridge began logging asymmetric per-eye FOV
  values and nonzero eye offsets instead of the initial zero/default view
  values.

Failed:

- Stereo alignment. The native source draws the same centered calibration
  features in both packed eye halves, but those features did not fuse into an
  aligned binocular image.

Unknown:

- The direction and magnitude of the misalignment.
- Whether the mismatch originates in packed content coordinates, projection
  metadata, eye transforms, or client-side presentation.
- Live HMD motion correctness. The client console continued to print
  `Sending fake tracking...`, so the view-parameter change is not sufficient
  evidence of valid head tracking or world locking.

Verdict: `display passed / stereo alignment failed`. VideoToolbox encode,
transport, decode, and physical display are green. The next visual work must be
a bounded stereo-contract probe, not arbitrary comfort tuning and not a claim
that the synthetic surface is product-ready.

Do not repeat:

- Do not use a single unlabeled image shift to hide whether the error is content
  packing or projection metadata.
- Do not interpret nonzero eye offsets as proof of live HMD tracking while the
  client still reports fake tracking.

Next action: add eye labels and a finite one-variable calibration sweep that
separates source-content shift from projection/FOV shift. Use one short eyes-on
run to classify the error before changing the real-game path.

### 2026-07-10 Clean Upstream Forward-Port Gate

Run: `cbusillo/ALVR@57a92083` on `feature/native-surface-contract`, based on
upstream `master` `e9b8e3ac`. This is the add-only current-upstream replacement
for the historical diagnostic branch, not a wholesale cherry-pick.

Question: Does the isolated IOSurface lease, VideoToolbox, metadata, and current
ALVR transport contract still reach the physical AVP after removing the old
shared-memory/server-core branch history?

Artifacts were captured locally in ignored `.code/probes/007-*` directories for
the bounded failure and final-pass runs.

Device-found failures fixed before the final pass:

- The existing dedicated session initially negotiated H.264 while the contract
  emits HEVC. Startup now preserves the dedicated session and sets its preferred
  codec to HEVC before `ServerCoreContext` loads it.
- A fresh or materially changed session used the first client handshake to
  persist current-upstream restart settings. With no ALVR dashboard process,
  rerunning the same bounded command is required and documented.
- Raw ALVR tracking poll timestamps used a server-uptime clock while video used
  a run-relative clock. Switching sources caused an intentional monotonicity
  failure. Tracking timestamps are now normalized into the probe-local clock,
  while reuse of one pose preserves one pose timestamp.
- Linux and Windows CI exposed macOS-only queue internals as dead code. Those
  internals are now target/test gated.
- Review exposed silent pre-connection transport drops. Cadence and summary logs
  now report ALVR-sent frames separately from VideoToolbox-encoded frames, and
  connect mode requires a real client plus at least one sent frame.

Final verified run:

- The physical AVP client entered `Streaming` at `2144x2048` per eye with a
  `90.0` Hz refresh hint.
- HEVC remained the negotiated codec and the client console contained no fatal
  decoder, panic, timeout, or crash message during the bounded run.
- The probe submitted and encoded `900/900` frames, handed `783` post-connection
  frames to ALVR transport, and achieved `90.007` FPS.
- All six IOSurface-backed leases were available at exit with `900/900`
  acquire/recycle accounting.
- Two startup deadline misses were recorded rather than hidden; the maximum was
  `19.241 ms`, followed by zero-miss steady-state cadence windows.
- The summary explicitly reported `alvr_connected=true`.

Verdict: `clean forward port passed`. The current-upstream native surface
ownership, encode, metadata, and physical-client transport contract is green.
This does not change the eyes-on result: the synthetic binocular presentation
remains misaligned, and no real Metal/CrossOver producer or fence import exists
yet.

Next action: review and merge the isolated ALVR contract, then build the labeled
one-variable stereo calibration probe before starting real GPU producer/fence
handoff work.

## Verdict

`alive with stereo blocker`: the native IOSurface/VideoToolbox/ALVR path reaches
the physical AVP at headset-rate cadence without CrossOver, and the encoded
image is visible. The binocular presentation is not aligned, so stereo geometry
remains a blocker.

## Next Action

Freeze the encode/transport portion of this synthetic mode as a passing native
surface contract. Add a labeled, one-variable stereo calibration probe under
issue #41, then use its result to constrain real GPU texture handoff work in
issue #40.
