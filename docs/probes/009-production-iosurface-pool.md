# Production IOSurface Handoff Pool

## Goal

Turn the passing one-shot real Freedom IOSurface proof into the reusable source
for the native macOS ALVR encoder. The hot path must remain GPU-resident, never
wait for GPU completion on the OpenVR submit thread, and recycle a bounded pool
without leaking D3D11, Vulkan, IOSurface, Mach-port, or encoder resources.

This probe is the implementation record for GitHub issue #53 under parent #36.

## Proven Starting Point

- A real 3240x1800 Freedom DirectX submit renders non-black through the bounded
  DXVK/MoltenVK repair.
- A dedicated viewless `B8G8R8A8_UNORM` DXVK texture can be attached to an
  IOSurface before use, filled with `CopyResource`, and published only after a
  queue-ordered Vulkan fence.
- A separate native arm64 Metal process reads the exact coordinate-matched BGRA
  pixel from that IOSurface.
- The review-hardened proof correlates acknowledgments by nonce, submit sequence,
  and surface identity while the existing CPU pair remains live.

Detailed evidence is in
`docs/probes/008-real-openvr-iosurface-handoff.md`.

## Architecture Decision

The native-owned model passed its lifecycle gate and is now the production
direction:

1. Native ALVR creates three IOSurface-backed encoder-compatible buffers.
2. A per-user launchd/XPC service transfers retained IOSurface Mach rights to
   the Wine unixlib. The bounded probes use deprecated dynamic bootstrap
   registration only to isolate and prove the right-transfer mechanics.
3. The unixlib binds each received IOSurface to one new viewless DXVK handoff
   image with `vkUseIOSurfaceMVK(image, surface)` before any copy or view use.
4. The producer performs a startup identity self-test before accepting real
   frames.

The producer-owned model remains a fallback only if later VideoToolbox or
reconnect evidence invalidates native ownership. Do not fall back to global
IOSurface IDs as the production contract.

The native encode decision is resolved: a synchronous Metal compute pass crops
the packed BGRA IOSurface to the negotiated AVP shape, converts it to video-range
BT.709 NV12, and writes into the existing six-lease VideoToolbox surface pool.
Full-frame CPU conversion is not part of the production path.

### Production Launchd Mach Service Plan

The production discovery migration keeps the proven raw Mach message protocol
and changes only service ownership and process lifecycle:

1. The per-user launchd domain owns the fixed Mach service
   `com.alvr.macos-bridge.iosurface`.
2. A session-specific LaunchAgent plist starts the artifact-local
   `alvr_macos_bridge` with the existing nonce, source geometry, encoder, ALVR
   root, and finite validation settings.
3. The native bridge claims launchd's receive right with
   `bootstrap_check_in`; it no longer allocates a receive right or calls the
   deprecated `bootstrap_register` API.
4. The Wine unixlib continues to use `bootstrap_look_up` and the existing
   request, offer, frame-ready, and slot-release messages. IOSurface Mach-right
   transfer, slot generations, and the submit-worker hot path remain unchanged.
5. The repository run lock enforces one active producer session per user. The
   native receiver requests a kernel audit trailer for every message, derives
   the sender PID from its audit token, validates the send-once reply right, and
   requires the payload PID to match. Invalid or non-producer requests are
   drained without consuming a slot or aborting the valid session.
6. Every run archives its generated plist and launchctl state. It refuses to
   remove a pre-existing fixed-label job unless both its plist and program are
   under this probe's artifact root, waits for launchd to record `not running`
   with exit code zero, then boots out its own job before restoring staged files.

This is a launchd-managed raw Mach service, which is the supported check-in path
for existing MIG/raw-Mach protocols. Replacing the wire protocol with XPC
dictionaries would add a second broker and rewrite the hot path without solving
a current problem.

The first gate is a disconnected real-Freedom run with three startup self-tests,
zero pool exhaustion, a launchd check-in marker, exact checksum restoration, and
no remaining job in `gui/$UID`. Connected and pressure gates follow only after
that software-only result passes.

The implementation now uses this contract. `native_source.c` claims the
launchd-owned receive right, authenticates message senders from audit trailers,
drains invalid startup and non-producer frame requests until the bounded
deadline, and preserves the existing three-slot/session protocol. The real
runner generates and validates the per-session plist, archives launchctl state,
resolves the launched PID for pressure testing, proves clean process exit, and
verifies owned-job bootout before checksum restoration.

The adversarial lifecycle gate builds an artifact-local raw Mach sender. Every
launchd run injects one oversized import request before the Wine producer starts
and one oversized frame-ready message after the three startup self-tests. The
receiver must destroy both queued messages, log the two expected rejections,
and still complete the real producer session without any other rejection.

Artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T165336Z`
is the first valid launchd software gate. Its original `verdict.txt` remains
`fail` because the then-current analyzer classified two pool-priming drops as
steady-state failures. Raw-log reanalysis under the finalized gate records:

- one launchd check-in and zero rejected import requests;
- all three startup identity tests passing;
- `900/900` target frames submitted, encoded, and released, with native NV12
  leases recycled `900/900`;
- a `90.006 FPS` final `300`-frame window;
- two bounded drops before all three slots completed their first production
  release, followed by zero steady-state drops;
- zero native drops or pool exhaustion; and
- successful bootout, no remaining `gui/$UID` job, and clean checksum
  restoration.

The final analyzer keys accepted work to the frame ID returned with
`result=closed`, separates already-queued post-close work from target frames,
allows at most one pool's worth of startup priming drops, and still requires
zero steady-state producer or native drops. The original artifact verdict is
preserved alongside `current-gate-reanalysis.txt` rather than rewritten.

Later repeat artifacts exercised the same launchd lifecycle successfully but
are not cadence evidence: unrelated CodeQL, indexing, Factorio, and concurrent
agent workloads kept system load between roughly 20 and 50 and reduced short
Freedom tails below the unchanged `89.5 FPS` floor. Do not lower the cadence
gate to accommodate a loaded validation host.

Artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T180829Z`
validates the hardened service contract after sender authentication and exit
proof were added. It checked in once, accepted audit-token-authenticated Wine
messages with zero import or frame rejection, encoded and released `300/300`
target frames with zero native drops, recycled `300/300` native leases, recorded
launchd `not running` with exit code zero, and removed the job before restoring
all staged files. Its original verdict remains `fail`: a host load above 30
produced an `86.012 FPS` tail. `lifecycle-auth-reanalysis.txt` records only the
authentication, lifecycle, and cleanup result; it is not cadence evidence.

Artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T185127Z`
passes the adversarial pressure gate added after final lifecycle review. The
native receiver destroyed one queued `4,120`-byte import message and one queued
`4,120`-byte frame-ready message, then accepted the real producer with no other
rejection. The runner proved the same plist/program identity with launchd
`runs=1` at start, exit, and bootout. It applied the `750 ms` pause only after
all three production slots had released, recorded three steady-state pressure
drops, then recovered with `22` releases spanning all three slots. All `30/30`
target frames encoded and released, launchd recorded exit code zero, and cleanup
removed the owned job and restored every staged file. This bounded pressure run
is lifecycle evidence, not cadence evidence.

Artifact
`.code/probes/009-production-iosurface-pool/launchd-signal-smoke-20260716T191935Z`
then validates the final signal hardening independently of CrossOver startup. It
binds the unique job to the exact plist and program with launchd `runs=1`, sends
`SIGSTOP` and `SIGCONT` through `launchctl kill` rather than a cached numeric
PID, boots out by the owned plist path, and verifies service absence. Invalid
attempt `real-native-encode-20260716T190538Z` never reached launchd or Freedom;
it was terminated during the loaded CrossOver desktop warm-up and restored all
staged files. That warm-up now uses a five-minute wall-clock deadline instead of
a loop count whose duration could expand under host load.

Connected artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T192751Z`
resolves the user's wireframe/search state and proves the launchd-backed physical
path. Device preflight showed that the installed ALVR client was not running;
after an activated console-attached relaunch, it connected, started streaming,
created the HEVC decoder and `2880x1792` format, and displayed Freedom clearly
and smoothly. The host encoded and transported `5400/5400` target frames with
zero native pool exhaustion, no fatal decoder errors or resets, exact launchd
identity at start/exit/bootout, and clean restoration.

The artifact's original verdict remains `fail`. Discovery took roughly 80
seconds after client relaunch, producing `7150` pre-connect not-ready releases,
five steady producer pool drops, and eight pose-generation gaps. Concurrent
CodeQL and Factorio load also reduced the final `300`-frame window to
`89.387 FPS`, below the unchanged `89.5 FPS` floor. The eyes-on connected result
is valid, but a quiet-host repeat must clear those strict counters before the
automated connected gate closes. After the finite host exited, the still-running
client returned to search and resumed stale `IPD is bad` / `Missing video format`
diagnostics; deterministic suppression of those post-host messages remains the
next client cleanup task.

Connected preflight must therefore query the physical device process list and
activate the installed ALVR client when absent. A visible search/wireframe state
does not prove that the client process is alive.

### Deterministic Connected Lifecycle Plan

The connected runner will own both ends of validation instead of depending on
an externally launched headset process:

1. Resolve exactly one booted, paired, tunnel-connected physical visionOS
   device through `devicectl`; reject simulators and ambiguous matches.
2. Verify the installed ALVR bundle, terminate any stale instance, and relaunch
   it activated and console-attached into the current artifact.
3. Wait for the client's mDNS listener, advertised ALVR device ID, and current
   IPv4 address. Atomically seed that exact client ID as trusted with the current
   and manual IP in the artifact-local ALVR session before host bootstrap.
4. Record client-readiness and host-connection latency separately so startup
   discovery cannot be hidden inside frame-drop counters.
5. After finite host exit, observe a short bounded search-state interval,
   require zero repeated stale IPD/video-format diagnostics, terminate the
   runner-owned client, and archive its complete console.

The visionOS renderer will keep the same wireframe/search behavior while no
stream is active, but frame-readiness diagnostics will run only during an active
stream. Reconnect must still create a new decoder/format epoch and resume the
same immersive scene without a client reinstall.

### Deterministic Connected Lifecycle Result

Full connected artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T223050Z`
passes the strict automated gate. The runner selected the one booted physical
Vision Pro, relaunched the exact installed ALVR bundle, parsed its advertised
`21-dev12` device ID and `192.168.1.6` address, seeded that identity before host
startup, and connected the sink in `12 ms`. The producer startup barrier and
first-frame prime prevented pre-ready traffic. All `5400/5400` target frames
were submitted, encoded, and transported at `2880x1792`; producer and native
drops, pool exhaustion, and pose-generation gaps were all zero. One transient
pool saturation applied `5539 us` of bounded backpressure instead of dropping a
frame. The producer averaged `89.819 FPS` across startup and held `90.033 FPS`
over the final window, above the unchanged `89.5 FPS` floor.

After host exit, the client reported stream stop without repeated stale IPD,
origin, or video-format diagnostics. Cleanup terminated the runner-owned client,
removed the global run lock and transient launchd plist, booted out the exact
job, and restored every staged CrossOver/game file. Post-review artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T224204Z`
then passed `30/30` frames while additionally proving one Launch Services app
record, the expected Developer ID team and CDHash, the exact live executable,
and clean global-lock/plist/service teardown. The previously authorized stable
app URL is intentionally retained because moving the bundle invalidates the
macOS Local Network privacy decision.

Final review-fix artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T231338Z`
also passes `30/30`. It captures the remote client PID before mDNS readiness,
revalidates that exact process before and after the post-host observation,
measures client and sink connection from the producer-handshake gate rather than
after connection, and confirms synchronized stream-state diagnostics, zero stale
post-host messages, and exact teardown. The connection markers arrived within
the first `100 ms` polling quantum.

Eyes-on repeat
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T230058Z`
again transported `5400/5400` frames with zero producer/native drops and zero
pose-generation gaps, and the user confirmed Freedom was clear and smooth. Its
strict verdict remains `fail` only for cadence: an unrelated concurrent AV1
FFmpeg probe consumed roughly nine CPU cores while Factorio and other Code
sessions remained active, reducing the full-run rate to `48.838 FPS` and the
tail to `54.562 FPS`. That loaded-host visual artifact does not replace the
strict automated pass above; together they provide the automated and human
acceptance evidence for the same final implementation.

## Pool Contract

- Exactly three long-lived slots for the first implementation.
- Each slot has a stable surface identity and a monotonically increasing lease
  generation.
- Producer states are `available`, `copy-submitted`, and `awaiting-release`.
- The OpenVR submit hook may acquire an available slot, enqueue copy plus marker,
  and return. If no slot is immediately available, it waits up to `100 ms` for
  one exact release before dropping the sidecar frame; the real `Submit` result
  remains unchanged. Connected acceptance requires zero such drops and records
  the count and maximum duration of bounded backpressure waits.
- A producer worker waits for the marker, sends frame-ready metadata, and never
  reuses the slot until the native encoder returns the matching generation.
- The native side validates strictly increasing frame IDs and video timestamps,
  retains the imported surface through VideoToolbox output, and releases the
  exact slot generation afterward.
- Reconnect creates a new session nonce and invalidates every prior lease.

## IPC Contract

Startup messages must establish protocol version, session nonce, peer process
identity, dimensions, format, slot count, and one retained IOSurface Mach right
per slot. A self-test writes a unique marker through each DXVK image and requires
the native process to report the same marker from the corresponding imported
surface before real frames are enabled.

Per-frame messages carry only bounded metadata: session nonce, slot index,
generation, frame ID, video timestamp, pose timestamp when known, source bounds,
and flags. Release messages echo session nonce, slot index, and generation.
Unknown sessions, stale generations, duplicate releases, malformed messages,
and peer disconnects fail closed for the pool while the real OpenVR submit path
continues fail-open.

## Implementation Order

1. Prove the chosen local Mach/XPC discovery and retained-right transfer with one
   native-created IOSurface and a Wine unixlib identity round trip.
2. Add the three-slot DXVK producer with nonblocking acquisition and worker-owned
   marker waits.
3. Add the native receiver, one-time imports, lease generations, and identity
   self-test.
4. Connect imported surfaces to the native ALVR encoder contract.
5. Run finite real Freedom cadence tests before any headset interpretation.

## Capability And Binding Evidence

Run the bounded gates in this order:

```bash
bash tools/run_iosurface_mach_port_probe.sh
bash tools/run_wine_iosurface_mach_port_probe.sh
bash tools/run_dxvk_native_iosurface_bind_probe.sh
```

The latest passing artifacts are:

- `.code/probes/009-production-iosurface-pool/mach-right-20260711T192410Z`
  - native arm64 server to translated x86_64 client;
  - exact BGRA `37,122,195,255` after the sender released its local IOSurface
    reference and original Mach send right.
- `.code/probes/009-production-iosurface-pool/wine-mach-right-20260711T192904Z`
  - native arm64 server to the real CrossOver Wine unixlib;
  - retained-right lookup, exact metadata/pixel validation, nonce-bound ack, and
    no staged bottle leftovers.
- `.code/probes/009-production-iosurface-pool/native-bind-20260711T193824Z`
  - native arm64 owner created IOSurface 558;
  - Wine imported the retained right and MoltenVK bound it to the fresh viewless
    16x8 DXVK image with `vkUseIOSurfaceMVK(image, surface)`;
  - D3D11 `CopyResource`, DXVK flush, queue-ordered marker submit, and Vulkan
    fence wait all returned success;
  - the native owner read exact BGRA `37,122,195,255` from the same IOSurface;
  - both processes exited zero and staged files were removed.

These gates establish native-owned surface identity and cross-process lifetime.
The later real-device matrix establishes sustained recycling, nonblocking pool
exhaustion, native conversion, hardware encode, and bounded AVP transport setup.

## No-Eyes Validation

A production candidate must demonstrate all of the following. The bounded
transport gate now covers:

- real successful Freedom submits remain unchanged;
- no full-frame CPU pixel path populates the production surfaces;
- the submit thread performs no fence wait and has bounded sidecar time;
- all three startup identity markers match their intended slots;
- frame IDs, generations, and timestamps remain ordered;
- pool exhaustion drops frames instead of blocking;
- every accepted lease is recycled exactly once;
- VideoToolbox produces one output for every accepted source frame;
- staged CrossOver and game files restore to their pristine hashes.

The remaining production gates are eyes-on content and cadence, graceful process
exit, native-peer exit, reconnect, device loss, and the launchd/XPC replacement
for the bounded dynamic bootstrap service.

## Passing Real-Device Evidence

The bounded no-eyes implementation gate passes on the physical Apple Vision Pro.
The final three runs share the exact same source manifest
`9b55192656591c24ae7ec6913f034b97e5def598e1db84e3241685606a2ff989`:

- `.code/probes/009-production-iosurface-pool/real-native-encode-20260711T225330Z`
  - 300 real Freedom frames accepted, Metal-converted, hardware-HEVC encoded,
    and recycled with zero producer or native drops;
  - Metal conversion averaged 703 microseconds with a 5377 microsecond maximum;
  - all six NV12 leases returned and staged files restored by checksum.
- `.code/probes/009-production-iosurface-pool/real-native-encode-20260711T225438Z`
  - a controlled 750 ms native pause exercised all three source slots;
  - five sidecar frames dropped under producer-pool exhaustion without changing
    the real game submit result;
  - all 30 accepted frames encoded, with zero native NV12-pool exhaustion.
- `.code/probes/009-production-iosurface-pool/real-native-encode-20260711T225647Z`
  - 30/30 hardware-encoded frames entered ALVR's live video queue;
  - the AVP reported a successful connection and streaming start, received HEVC
    VPS/SPS/PPS, created a `2752x1792` `hvc1` VideoToolbox format, and applied the
    90 Hz preference;
  - 82 frames were intentionally released before encode because the off-head AVP
    did not provide a fresh tracking sample; native NV12-pool exhaustion was zero;
  - teardown restored the stock MoltenVK and OpenVR hashes and removed every
    staged bridge/DXVK file.

The real source is `3240x1800`. The negotiated output is `2752x1792`: each eye
keeps 1376 horizontal pixels, crops 244 inner pixels, and center-crops four rows
from the top and bottom. Chroma is tagged centered to match the 2x2 Metal
downsampling phase.

Normal production frames now perform metadata, identity, generation, timestamp,
and synchronization validation only. Exact startup markers validate all three
slots. Real content is allowed to be completely black without triggering a
full-frame CPU scan or terminating the receiver. Therefore production-content
and displayed-frame cadence are explicitly eyes-on gates, not claims made by the
bounded no-eyes verdict.

Run the disconnected, pressure, or connected form with:

```bash
ALVR_CHECKOUT=/path/to/native-surface-contract \
ALVR_NATIVE_PROBE_FRAMES=300 \
ALVR_NATIVE_PROBE_CONNECT=false \
bash tools/run_real_native_iosurface_probe.sh

ALVR_CHECKOUT=/path/to/native-surface-contract \
ALVR_NATIVE_PROBE_FRAMES=30 \
ALVR_NATIVE_PROBE_PRESSURE_PAUSE_MS=750 \
bash tools/run_real_native_iosurface_probe.sh

ALVR_CHECKOUT=/path/to/native-surface-contract \
ALVR_NATIVE_PROBE_FRAMES=30 \
ALVR_NATIVE_PROBE_CONNECT=true \
bash tools/run_real_native_iosurface_probe.sh
```

The runner builds the Rust bridge, OpenVR shim, and fake runtime from their
recorded sources, forces fresh Wine bridge objects, executes an artifact-local
copy of the Rust binary, and snapshots the source inputs and dirty patches. A
per-repository lock owns shared mutations; the Wine source directory and staged
runtime files are restored on every exit. ALVR restart/trust state stays under
the gitignored
`.code/state/alvr-native-runtime/`, with pre/post hashes archived per run. The
runner never writes the user's primary ALVR session directory.

DXVK, the patched CrossOver MoltenVK, and the fake OpenVR runtime remain pinned
prebuilt inputs from probes 006-008. Their hashes are recorded, but rebuilding
those dependencies is outside this bounded runner.

## Source-Image Invalidation And Repair

The July 12, 2026 eyes-on results below predate the local-window gate in probe
010. The user observed the same blue tint and flashing in Freedom's macOS window,
which proved the corruption occurred before IOSurface conversion, VideoToolbox,
ALVR, or the AVP renderer.

Probe 010 isolated two renderer faults. MoltenVK Metal argument buffers reduced
every submitted eye frame to blue plus alpha with red and green exactly zero.
DXVK 1.10.3 also rebuilt the swapchain continuously whenever MoltenVK returned
the usable `VK_SUBOPTIMAL_KHR` result for Freedom's intentionally scaled window.

The production launch contract now includes
`MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS=0`, and the pinned DXVK source accepts
`VK_SUBOPTIMAL_KHR` during present synchronization. A clean local run produced
full-color screenshots, populated B/G/R channels for both eyes, only two startup
swapchains, and pristine cleanup. Earlier pool cadence, lease, IPC, and transport
measurements remain valid engineering evidence, but their visual output is not
valid Freedom color or stereo evidence.

### Dynamic Source Geometry

The first 81,000-frame gameplay pilot,
`.code/probes/009-production-iosurface-pool/real-native-encode-20260714T180628Z`,
reached Freedom's configuration area with working PS VR2 controls, then stopped
publishing when the real level loaded. The submitted side-by-side D3D11 texture
changed from `3240x1800` to `2808x1560`; the producer deliberately failed closed
with `reason=source-size-change` because `CopyResource` requires equal source and
destination dimensions. The AVP decoder starvation that followed was downstream
of that producer stop, not a network or decoder root cause.

The production pool now treats `ALVR_IOSURFACE_SOURCE_WIDTH/HEIGHT` as its fixed
native handoff dimensions instead of inferring the pool contract from the first
game texture. Equal-size `3240x1800` frames retain the proven `CopyResource` fast
path. Other even, aspect-matched BGRA textures use a GPU-only Vulkan transfer:

1. Query the submitted DXVK texture's `VkImage` and preferred layout, then flush
   outstanding DXVK rendering commands without changing either physical layout.
2. Record forward transitions, the stereo blits, and both restore transitions in
   one raw Vulkan command buffer. This avoids exposing a transfer-layout window
   where another D3D11 thread could interleave a submission.
3. Scale each eye independently into the corresponding fixed output half.
   Interior pixels use linear filtering; the two eye-boundary columns use
   nearest filtering so a linear footprint cannot cross the stereo split.
4. Fence the atomic transition/blit/restore command before native publication.

Each slot owns its command pool and command buffer. Worker resources are created
before GPU submission, so allocation failures cannot make the OpenVR submit
thread wait for a fence. A timed-out fence quarantines its worker and slot until
the fence signals or the device is lost; an in-flight command buffer is never
destroyed or recycled after a timeout.

Every launch now replaces the old one-pixel startup checks with three real
`1620x900 -> 3240x1800` stereo resamples. The samples cover a left-eye interior
pixel and both sides of the stereo boundary. Final off-head artifact
`real-native-encode-20260714T203309Z` passed all three linear-resize checks,
300/300 frames, a `90.000` FPS steady-state tail, zero producer or native drops,
684 microseconds average Metal conversion wall time, 93 microseconds average
Metal GPU time, and clean restoration.

The focused level-load gate sets
`ALVR_NATIVE_PROBE_EXPECT_SOURCE_TRANSITION=2808x1560`. A passing connected run
must record a real source transition, at least one `2808x1560 -> 3240x1800`
stereo transfer, and a non-black native consumer sample after the resized stream
has stabilized. Synthetic startup coverage alone cannot satisfy that gate.

## Eyes-On Attempts And Timestamp Recovery

The first real-content immersive attempt produced only the client's cyan/blue
wireframe fallback. Artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260711T235100Z`
shows why: fresh tracking was available for about 1.4 seconds, so only 13 real
frames were accepted before the client stopped providing tracked poses. The
renderer intentionally keeps the wireframe visible while its queued-frame, IPD,
and world-origin gates stabilize. This attempt therefore did not run long enough
to distinguish decoded real content from the startup fallback.

The native sink was also unnecessarily requiring a new tracking packet for every
video frame, even though its metadata contract permits multiple video frames to
reuse one pose sample. The first workaround removed that gate but synthesized
video time by adding one nanosecond per frame to the last tracking timestamp.
Artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260712T000947Z`
transported 300/300 frames with that workaround, but it was not valid timing or
visual evidence because the video clock advanced far more slowly than real time.

The next physical eyes-on run,
`.code/probes/009-production-iosurface-pool/real-native-encode-20260712T001608Z`,
hard-failed. The user saw a flashing blue/cyan, two-dimensional, HMD-relative,
misaligned wireframe rather than Freedom. The current-run AVP log recorded 249
stutter events, repeated decoder resets, and a 1.022-second video lag behind the
latest requested tracking timestamp. The visible image was therefore the
client's fallback, not evidence about Freedom color, stereo geometry, or world
locking. Synthetic `+1 ns` frame timestamps are a `do-not-repeat` setup.

The corrected sink preserves each producer `video_timestamp_ns`, reuses the
latest valid tracked pose until tracking advances, and maps pose-time deltas into
the producer video clock. Video cadence is no longer gated by fresh tracking,
but video time still advances at the real submit cadence. Unit tests cover pose
reuse and source-clock reset, including a reset where video time is behind the
previous mapped pose time.

After a clean Device Hub relaunch of the physical AVP client, artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260712T004137Z`
passed: 300/300 real Freedom frames were received, encoded, and transported in
10.874 seconds; native and producer drops were zero; all six encoder leases were
recycled; the AVP created one HEVC decoder at 2752x1792; and the current-run log
contained no stutter event, lag spike, or encoder reset. Cleanup restored the
CrossOver MoltenVK and game OpenVR DLL to their pinned hashes. An immediately
preceding run (`...T003805Z`) correctly failed because the old off-head AVP app
instance had already invalidated its layer and never connected; it is not a
bridge regression.

## Full-Geometry Freedom Recovery

The earlier native IOSurface runs transported real pixels but had dropped the
reverse metadata channel used by the June shared-memory path. Freedom therefore
rendered from fake OpenVR's identity pose and symmetric fallback projection even
when the AVP client had live tracking. The native sink now publishes current AVP
HMD pose and asymmetric local eye FOV/IPD through a metadata-only
`/tmp/alvr_frame_buffer.shm` header while image transport remains GPU-resident.
The fake runtime consumes `shared-hmd-pose` and `shared view` data again.

Human testing then separated a diagnostic packing workaround from the real
geometry contract. Inner-eye crops at 244 and 212 pixels changed convergence,
and a cropped-FOV metadata remap made the right eye substantially worse. These
results confirm that hand-tuned convergence crops are not a production geometry
solution. The production converter now resamples each complete 1620x1800 eye to
the negotiated 1440x1792 eye surface, preserving optical center, full FOV, eye
transform, and live pose without a convergence offset.

Artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260712T144056Z`
is the first valid full-geometry Freedom result. The user observed that the
robot and room converged naturally and tracked as expected. Logs recorded 3,699
transported frames before the intentional stop, 3,709 producer submissions over
67.938 seconds (54.594 FPS), zero pool-exhaustion drops, two startup swapchains,
3,412 shared-HMD-pose reads, and 20,473 shared-view reads. Metal conversion
averaged 0.815 ms and peaked at 6.143 ms. The client reported zero stutter events,
but the human still described motion as somewhat stuttery and image quality as
not high. Stereo convergence and world locking therefore pass; source cadence,
frame pacing, resampling quality, and bitrate remain open.

A post-pass review hardened this recovered baseline without changing projection
or eye geometry. The metadata channel now uses an arm64-safe writer barrier for
pose and view snapshots, preserves an existing mapped header across bridge
restarts, atomically publishes heartbeat/shutdown state, and treats a transient
ALVR disconnect as a dropped frame rather than a fatal bridge error. Stream
dimensions are also constrained to ALVR's 32-pixel per-eye alignment, while
source eye widths must keep the NV12 chroma boundary even.

## OpenVR Running-Start Cadence Recovery

The remaining approximately 54.6 FPS cadence was not an IOSurface, converter,
encoder, or network limit. Fake OpenVR implemented `WaitGetPoses` as an
unconditional 11 ms sleep, so Freedom's render and submit work was added after
the nominal frame interval. A disconnected zero-sleep control in artifact
`real-native-encode-20260712T154028Z` completed 900/900 frames at 96.670 FPS
with zero drops, proving that the producer has enough headroom for a 90 Hz
session.

The fake compositor now uses one monotonic QPC clock and absolute rational 90 Hz
running-start deadlines. Late calls advance to the newest eligible frame without
replaying missed deadlines, while bounded sleep/yield/spin waiting handles the
last part of each interval. `GetTimeSinceLastVsync`, frame timing, and remaining
frame time share the same epoch. Pacing logs are sampled, and the runner gates
the final steady-state window instead of treating launch warm-up as production
cadence.

Artifact `real-native-encode-20260712T161039Z` passed disconnected validation
with 600/600 frames, zero drops, zero pool exhaustion, and 90.006 FPS across the
final 300 submissions. Connected artifact
`real-native-encode-20260712T160646Z` transported and decoded 900/900 frames at
the correct 2880x1792 shape; its final 300 producer submissions also measured
90.006 FPS. A connected zero-sleep diagnostic reached 106.885 producer FPS but
caused repeated decoder reconfiguration, confirming that uncapped overproduction
is not the production solution. The target is one new source frame per 90 Hz
display interval, with reprojection reserved for occasional missed frames.

Independent post-implementation review found no remaining cadence blocker after
hardening exact rational-boundary inversion, QPC fallback continuity,
non-300-frame runner accounting, and fixed-sleep diagnostic verification. These
changes do not alter the validated full-eye geometry, projection, IPD, or pose
contract.

## Physical Cadence A/B And Pose-Pairing Gate

The physical back-to-back comparison rejected producer cadence as the complete
fix. Fixed-sleep reference artifact `real-native-encode-20260712T193744Z`
transported 2700 frames with natural convergence and expected world locking, but
the user described headset motion as jittery and jumpy. The Freedom mirror on
macOS was smooth and appeared to track correctly, isolating the visible defect
to the headset delivery and presentation path.

Deadline-paced candidate `real-native-encode-20260712T194251Z` then transported
7200/7200 frames with zero native drops, zero pool exhaustion, and 90.006 FPS
across the final 300 producer submissions. Despite that machine pass, the user
reported a visual regression: during head motion the image seemed to begin
moving with the head and then jump back to its world-locked position. This is a
failed human cadence gate, not an ambiguous quality preference.

The next contract boundary is exact render-pose pairing. Fake OpenVR snapshots
the HMD pose returned to Freedom by `WaitGetPoses`, but the IOSurface frame-ready
message currently carries only the producer video timestamp. The native sink
then samples whatever tracking pose is latest when the IOSurface reaches macOS.
That can associate a frame rendered from pose A with pose B for ALVR's client
reprojection. Higher producer cadence makes the race more frequent rather than
fixing it, which matches the observed brief head lock followed by correction.

Repeated AVP `DecoderConfig` messages are retained as telemetry but are not the
leading explanation: the candidate created one CoreMedia format description,
and the additional config messages clustered during immersive startup and IDR
recovery. The next experiment must carry the exact frame pose and its source
timestamp through the IOSurface protocol, reject missing or regressing pose
metadata, and report pose-to-submit age before another headset comparison.

## Exact Render-Pose IOSurface Contract

The IOSurface protocol now carries the exact OpenVR render pose with each frame:
its source timestamp, a monotonic paired-pose generation, and the 3x4
device-to-absolute matrix captured from the pose returned to Freedom. Pool mode
maps only the 640-byte metadata header rather than the legacy full pixel-buffer
mapping. Reused or stale pose generations, including a bridge-session change,
are marked as fallback identity instead of being mislabeled as shared tracking.

The native receiver validates the protocol-v2 matrix and generation before
conversion. Disconnected diagnostics may encode explicitly flagged fallback
identity poses. A connected bridge admits at most three explicitly counted
fallback frames per connection epoch while the client lacks HEVC decoder
configuration, then drops every later fallback before VideoToolbox submission.
These decoder-bootstrap frames use video time without seeding the tracking
clock. `AlvrVideoSink` therefore builds every post-bootstrap outgoing global eye
pose from the exact head pose used for that image, not from a later tracking
sample observed when macOS receives the IOSurface.

The runner now records producer and native paired/fallback counts, generation
gaps, reused source timestamps, decoder-bootstrap counts and epochs, and the
last paired generation. A connected pass permits only the per-epoch bounded
decoder bootstrap; zero bootstrap frames are valid when exact pose metadata is
ready for the first keyframe. Every other encoded frame must have a shared paired
pose.
Repeated source timestamps remain telemetry because a tracking sample may
legitimately cover multiple render frames, while paired generations must remain
gap-free. The producer/native protocol headers must also compare byte-for-byte
before any runtime mutation begins.

Disconnected artifact `real-native-encode-20260712T204545Z` passed 300/300
frames with zero drops, an 89.918 FPS steady-state tail, protocol-v2 fallback
metadata on every off-head frame, and clean restoration. Seventeen macOS bridge
tests, MinGW C++ syntax validation, protocol-header equality, and independent
ABI/Mach-message review also pass.

Clean-restart artifact `real-native-encode-20260714T150116Z` then exposed a
bootstrap cycle: strict fallback rejection prevented the first keyframe and
decoder configuration, while the immersive client did not publish a valid pose
without a video format. The bounded decoder-bootstrap path breaks that cycle
without weakening steady state.

Physical artifact `real-native-encode-20260714T152912Z` encoded and transported
900/900 frames: 896 exact paired poses plus four bootstrap frames across two
connection epochs, with zero pose-generation gaps, zero pool exhaustion, and a
90.006 FPS final 300-frame producer window. The AVP created a 2880x1792 format,
published real tracking, and transitioned from wireframe to Freedom; the user
reported that it looked good. Confirmation artifact
`real-native-encode-20260714T153206Z` repeated the result with 300/300 frames,
296 exact poses, four bounded bootstrap frames across two epochs, zero
generation gaps, an 89.979 FPS final window, and clean restoration. Both runs
recorded `fail` under superseded global-bootstrap and console-connection
assertions; their saved counters satisfy the corrected per-epoch gate.

## Filtered Full-Eye Resampling And Rate Telemetry

The production converter now replaces nearest-neighbor source selection with a
single-pass Metal bilinear sampler. Output pixel centers map into eye-local
source coordinates and clamp to half-texel limits before the eye base offset is
added, so the texture filter cannot sample across the stereo seam. The existing
video-range BT.709 luma/chroma conversion and 2x2 NV12 chroma average remain
unchanged. Source and destination widths must be divisible by four so each eye
also remains aligned to the NV12 chroma grid.

The bridge reports Metal wall and GPU execution time plus encoded and
transported bytes, normalized Mbps, keyframe bytes, and maximum frame size.
Decoder errors and resets are preserved in the runner status as quality
telemetry. The rate metric normalizes bytes by encoded frames and the negotiated
FPS, so pre-immersive readiness waits do not dilute the result.

Disconnected artifact `real-native-encode-20260714T160930Z` passed 300/300
frames with zero drops, an 89.918 FPS producer tail, 50.463 Mbps encoded rate,
631 microseconds average conversion wall time, 94 microseconds average GPU time,
and clean restoration. Physical artifact
`real-native-encode-20260714T161536Z` then passed 900/900 encoded and transported
frames with a 89.979 FPS tail, 50.281 Mbps, 718 microseconds average wall time,
100 microseconds average GPU time, zero pool exhaustion, and zero pose-generation
gaps. The user judged the image slightly better or about the same and confirmed
that motion remained stable. Twenty focused bridge tests include interpolation,
video-range, and cross-eye boundary coverage.

## PSVR2 Gameplay-Control Gate

The first gameplay pilot uses a paired PS VR2 Sense controller set as the input
source. The visionOS client already supports accessory-tracked Sense poses and
button/axis events on visionOS 26, but the native macOS path currently stops at
the ALVR server: the shared-memory feedback contract carries only HMD/view data,
and fake OpenVR returns identity controller poses with zeroed controller state.

The implementation gate is:

1. Force the generated ALVR session to the `PSVR2Sense` controller profile with
   tracked controllers enabled.
2. Extend the versioned shared-memory contract with two platform-neutral
   controller snapshots: pose, velocities, pressed/touched masks, five legacy
   OpenVR axes, packet number, and freshness timestamps.
3. Publish left/right ALVR hand motions and mapped button events from the native
   sink, then consume only fresh snapshots in fake OpenVR.
4. Run a bounded controller smoke before gameplay: both controllers must become
   connected, both poses must be non-identity, each thumbstick and trigger must
   change live state, and stale/disconnected input must fail closed.
5. Only after that smoke passes, run the finite 81,000-frame gameplay pilot with
   the existing automatic restoration and transport/pose/cadence gates.

Expected artifacts remain under
`.code/probes/009-production-iosurface-pool/real-native-encode-*`. The controller
smoke must preserve the generated session, fake OpenVR log, AVP console log,
bridge summary, and restoration checks. Known client limitations are capacitive
touch and grip/trigger proximity fidelity; primary controller pose, buttons,
thumbsticks, triggers, and grip clicks remain the acceptance surface.

Controller smoke artifact `real-native-encode-20260714T180349Z` passed 5400/5400
encoded and transported frames with zero pool exhaustion, a 50.107 Mbps encoded
rate, 631 microseconds average Metal wall time, 98 microseconds average GPU time,
and clean restoration. Freedom queried both legacy OpenVR controller states;
both fresh spatial poses arrived, both thumbsticks changed, and the right trigger
and button mask changed while the game was rendering. The user then confirmed
that the in-game controller moved and responded. This closes the gameplay-input
gate for the first finite pilot; it does not claim full capacitive-touch or
haptic fidelity.

### Post-Logger Disconnected Soak

The fake OpenVR runtime logger correction from probe 012 was followed by a
one-minute official Freedom regression with transport disabled. Cold artifact
`real-native-encode-20260716T112522Z` sustained `89.979 FPS` in its final
`300`-frame window and encoded `5400/5400` frames, but recorded one source-pool
exhaustion at submit sequence `12`. No native frame, encoder, or pose-generation
drop followed that startup transient.

Immediate clean repeat artifact `real-native-encode-20260716T112715Z` passed
`5400/5400` submitted, encoded, and released frames with zero producer or native
drops, a `90.006 FPS` final window, `610 us` average Metal conversion, and clean
checksum restoration. The buffered logger therefore does not regress Freedom,
and the shared three-slot source pool remains stable after cold initialization.

## Physical Lifecycle And Reconnect Gate

Official Freedom artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T132419Z`
exercised the current production path on a physical Apple Vision Pro. The run
kept the official game binary, buffered fake-runtime logger, three-slot source
pool, full-eye bilinear conversion, hardware HEVC encoder, pose contract, and
`2880x1792` output fixed.

- `verified`: the bridge received `29,810` source frames, encoded `27,000`,
  transported `26,906`, used all six encoder leases without pool exhaustion,
  finished at `89.682 FPS` over the final `300` producer submissions, averaged
  `784 us` Metal conversion wall time, and restored the stock MoltenVK and
  OpenVR files by checksum.
- `human-observed`: the initial Freedom view was clear and smooth; Digital Crown
  recenter kept the immersive stream live; a 15-second headset removal and
  reentry returned smoothly and remained visible; terminating and relaunching
  the ALVR client while the host stayed active reconnected to another clear,
  smooth immersive view.
- `verified`: both client epochs logged `Successful connection!`,
  `streaming started`, and `Opening Immersive Space`. Raw client logs preserve
  transient VideoToolbox callback errors and decoder recreation around lifecycle
  transitions, but each epoch resumed video cadence. Fatal decoder watchdog
  messages in the reconnect log begin only after the finite host run ended.
- `failed`: the generic runner's strict steady-state verdict is `fail`. The
  deliberate client outage produced `2,810` expected not-ready drops, and `94`
  encoded frames were not transported while the connection changed. This run
  must not be used as a no-drop steady-state result.
- `inferred`: the first console-attached foreground process logged
  `App in background, exiting`, while the user still observed a live, smooth
  immersive scene. The exact visionOS foreground/immersive process ownership is
  not exposed by the captured console.
- `unknown`: graceful behavior when the host disappears without a finite test
  boundary, physical device loss, and long-session thermal stability remain
  unmeasured.
- `do-not-repeat`: do not classify the post-host decoder watchdog as an app
  crash, and do not interpret expected reconnect drops as a source-pool or
  performance regression.

Verdict: the current official-binary stack passes the human-visible initial
view, recenter, headset removal/reentry, and client-process reconnect gates. The
earlier performance blocker was a diagnostic observer effect, not a practical
Unity or transport ceiling. Remaining work is production hardening rather than
another game-specific or custom-build performance experiment.

## Fifteen-Minute Soak And Host-Loss Recovery

The first post-restart cold attempt,
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T150956Z`,
failed before publishing a frame. MoltenVK reported
`VK_ERROR_OUT_OF_DEVICE_MEMORY` with an `Invalid Resource` command-buffer
failure immediately after the first stereo submit. The runner recorded zero
accepted frames and restored every staged file. The immediate warmed retry did
not reproduce the failure, so this remains a cold-start transient rather than a
pool-capacity or sustained-runtime result.

Official Freedom artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T152355Z`
then passed a physical `81,000`-frame soak:

- `verified`: `81,000/81,000` target frames were encoded and transported, the
  final `300` producer submissions held `90.006 FPS`, native conversion averaged
  `734 us`, and the six encoder leases finished with zero pool exhaustion.
- `verified`: four frames arrived before ALVR was ready, then all target frames
  completed. Exact pose pairing covered `80,999` encoded frames with five
  startup fallbacks and zero pose-generation gaps.
- `human-observed`: Freedom was clear and smooth at the start and remained clear
  and smooth throughout approximately 15 minutes of uninterrupted normal head
  movement.
- `verified`: the raw AVP log preserved 20 transient VideoToolbox callback
  errors and decoder recreation, but transport cadence continued and the user
  observed no corresponding visual degradation.
- `human-observed`: after the finite host stopped, ALVR returned to its search
  screen instead of crashing or trapping the user in an unusable immersive
  view.
- `do-not-repeat`: do not interpret the four pre-stream not-ready frames as lost
  target frames, and do not treat the non-reproducing cold `Invalid Resource`
  attempt as a reason to change pool size or encoder configuration.

Without manually relaunching ALVR, short host-reappearance artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260716T154756Z`
then created new stream and VideoToolbox format epochs, resumed decoded-frame
cadence, and encoded and transported `2700/2700` target frames with a
`90.006 FPS` tail and zero pool exhaustion. The client required `1,976`
not-ready source frames before accepting the new host. The user looked only
after the 30-second stream had ended and saw the expected wireframe/connecting
fallback, so active recovery is `verified` by machine evidence but remains
`unknown` as a direct human observation.

Verdict: the longer official-binary soak and human-visible host-loss recovery
pass. The AVP validation plan no longer blocks architecture work. Remaining
client lifecycle hardening is diagnostic cleanup after host loss, not evidence
of a frame-path, performance, or comfort blocker.

## Failure Signatures

- `vkUseIOSurfaceMVK` rejects a native-provided surface: preserve the exact
  result and test producer-owned transfer instead of retrying the same binding.
- Imported slot identity does not match: stop the pool before accepting real
  frames.
- Slot generation mismatch or duplicate release: reset the IPC session.
- Pool remains exhausted: inspect native retention and encoder callbacks; do not
  add more slots until the leak or latency is understood.
- VideoToolbox rejects BGRA or falls behind: measure a Metal-to-NV12 path before
  considering any CPU conversion.
- Connected host transport passes but `avp-client-console.log` is empty: restart
  the console-attached Device Hub launch; connected verdicts require current-run
  device evidence.
- `Handle spike`, repeated decoder resets, or rapidly increasing stutter counts:
  verify that video uses the producer timestamp. Do not substitute a reused
  tracking timestamp or synthesize cadence with one-nanosecond increments.
- Real frame cadence passes but visual geometry is wrong: resume #41 with real
  content; do not tune another artificial plane.
- AVP logs `AAAAAA []` after both Sense controllers are paired and awake: stop
  before gameplay and verify visionOS accessory authorization/build support.
- Controller state is present but poses remain identity or packet numbers stop:
  reject the smoke as stale shared-memory input rather than extending timeouts.
- `source-size-change` reappears or the expected `2808x1560` transition has no
  `transfer=stereo-*` submission: reject the level-load gate before subjective
  testing.
- `iosurface pool fence quarantine` appears: preserve the artifact and stop;
  never recycle or destroy the affected slot while its GPU work may be pending.
- The native bridge times out before any pool import and `freedom-launch.log`
  contains only `wineboot`/service startup: this is a cold CrossOver bottle, not
  a frame-path result. On this host a fully cold `Steam` bottle took about 163
  seconds, and installing a newly signed temporary MoltenVK can trigger another
  Rosetta translation delay even while the bottle remains warm. The runner uses
  the patched library's existing deterministic ad-hoc identity, installs it by
  atomic rename so the stock file's inode cannot retain a stale Rosetta
  supplement, then loads that exact signed library once in an x86_64 helper
  before starting the native bridge. The focused bridge still allows 600
  seconds for the first producer handshake; its per-frame timeout and cadence
  gates remain unchanged.
- Unreal asserts in `WindowsD3D11Viewport.cpp` with
  `CreateSwapChain ... DXGI_ERROR_INVALID_CALL` before OpenVR loads: the first
  GUI launch raced Wine desktop creation. A diagnostic DXVK build proved the
  same descriptor succeeds with `HWND=0x2006e`, `3200x1800`, and `windowed=1`
  once the desktop exists. The runner must stop the Steam bottle, install and
  prewarm the patched MoltenVK, then keep a small Notepad window open until the
  game exits so the game never becomes the bottle's first GUI process.
- Rosetta reports `Attachment of code signature supplement failed` for the
  temporary CrossOver `libMoltenVK.dylib`: the fixed application path reused a
  stale AOT entry after repeated stock/patched swaps. The runner atomically
  installs the already signed patched binary on a fresh inode, verifies its
  embedded signature, prewarms it under x86_64, then atomically restores the
  original CodeWeavers-signed binary on exit.

## Cleanup

Each run must terminate the producer and native receiver, invalidate the session,
release all Mach rights and IOSurface references, remove temporary IPC endpoints,
stop all processes in the dedicated `Steam` test bottle before restoring the
stock graphics library, restore CrossOver and game DLLs by checksum, and preserve
bounded logs under `.code/probes/009-production-iosurface-pool/`.

## Current Status

`stereo geometry, producer cadence, decoder bootstrap, and exact pose-to-frame
pairing pass the connected physical gate; filtered full-eye resampling also
passes performance and visual acceptance`. The
source-image regression is repaired and covered by the local gate in probe 010.
Artifact
`.code/probes/010-freedom-local-window-regression/custom-dxvk-patched-mvk-20260712T031143Z`
shows stable full-color Freedom output, populated B/G/R channels in both submitted
eye textures, only two startup swapchains, and clean restoration. The required
launch contract disables Metal argument buffers and accepts
`VK_SUBOPTIMAL_KHR` during DXVK present synchronization.

The corrected production no-headset artifact
`.code/probes/009-production-iosurface-pool/real-native-encode-20260712T033029Z`
then received, submitted, encoded, and released 300/300 real Freedom frames in
6.987 seconds, or 42.937 effective FPS, with zero producer/native drops, zero
pool exhaustion, all three startup identity tests passing, and clean checksum
restoration. Client transport was intentionally disabled, so this run is not a
headset visual or 90 Hz display-cadence proof.

The full-eye-scale physical result in `real-native-encode-20260712T144056Z`
restores natural convergence and expected tracking without manual crop tuning.
The 90 Hz producer recovery, exact frame-pose pairing, and filtered full-eye
resampling are now technically and physically proven; cadence without the exact
metadata remains a rejected path. The July 16 lifecycle artifact adds passing
Digital Crown recenter, headset removal/reentry, and full ALVR client relaunch
while preserving a clear, smooth physical image. Real gameplay testing can
begin with this stack. The subsequent `81,000`-frame physical soak remained
clear and smooth throughout and returned to ALVR search after host exit. AVP
validation issue #41 is therefore complete. Shipping readiness now requires the
implementation and evidence to be consolidated for review. The launchd pressure
and connected cadence gates now pass, physical-client discovery and trust are
seeded before host startup, production frames cannot outrun transport readiness,
bounded producer backpressure avoids transient pool loss, post-host diagnostics
remain quiet, and launchd/client/file cleanup is exact. No geometry, pool-size,
encoder, or game-specific pivot is indicated.
