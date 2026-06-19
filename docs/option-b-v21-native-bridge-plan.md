# Option B: ALVR v21 Native Bridge Plan

## Decision

Option A is locally blocked. CrossOver can run enough SteamVR and ALVR to reach
AVP discovery, trust, transport, ALVR `Streaming`, and an active ALVR HMD in
SteamVR, but Windows SteamVR's compositor fails before stable scene frames
exist:

```text
Failed to create shared frame info constant buffer!
VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer
```

The failure reproduces with SteamVR's built-in `null` driver, with D3DMetal, and
with CrossOver DXVK. A verified upstream DXVK 2.7.1 DLL override also failed the
standalone D3D11 shared-handle probe. The D3D11 probe shows ordinary resource
creation succeeds, but DXGI shared-handle export does not. A later v21
shared-memory encoder-boundary build also reached active ALVR HMD registration
but still failed in `vrcompositor.exe` before ALVR could publish real frames.
That makes Option A a translation-layer external-memory problem, not an ALVR v21
source bug in the tested compositor path.

Use this as the boundary: do not spend more time on broad CrossOver SteamVR
tuning unless a future CrossOver/D3DMetal/DXVK/MoltenVK release advertises
working DXGI shared-handle support.

## Can We Patch Option A?

Only partially, and not as a practical primary track.

- D3DMetal/GPTK is closed. We can toggle it or route around it, but we cannot
  locally add the missing shared-handle support inside Apple's translation
  layer.
- DXVK, DXMT, WineD3D, and Wine are open enough to patch or override, but a real
  fix would need cross-process D3D11/DXGI shared-resource export/import on macOS
  backed by Vulkan/MoltenVK/Metal external memory and Wine handle plumbing.
- A local `d3d11.dll` or `dxgi.dll` shim could log SteamVR's exact calls or fake
  success for a diagnostic spike, but it would still need another process to
  open the handle and observe valid GPU resource contents and synchronization.
  That is a partial graphics translation layer, not a small ALVR patch.

A bounded future diagnostic is acceptable: hook `vrcompositor.exe` to log the
exact `CreateBuffer`, `GetSharedHandle`, `CreateSharedHandle`, and
`OpenSharedResource` calls. Treat it as evidence gathering only, not the default
implementation route.

## Source Branches

Use sibling source clones under `~/Developer`; do not add source submodules to
this planning repo.

Current ALVR source baseline:

```text
~/Developer/alvr
  master = d9f2b19d2b98b9d70411439fef83300c84ed171d
```

Relevant fork refs:

```text
fork/macos-v20.14.1      7d8dde1b feat: add shared memory frame transfer
                                   and macOS VideoToolbox bridge
fork/macos-videotoolbox  c6ed6352 feat(macos): Add VideoToolbox encoder skeleton
```

## What To Forward-Port

Forward-port concepts and small file groups from `fork/macos-v20.14.1`, not the
branch wholesale. The branch is based on v20 and carries large unrelated ALVR
history, workflow churn, and dependency drift.

High-value material:

- `alvr/server_openvr/cpp/shared/alvr_shm_protocol.h`  
  Starting point for an explicit Wine-to-native frame ABI.
- `alvr/server_openvr/cpp/platform/win32/VideoEncoderSharedMem.{h,cpp}`  
  CrossOver-side prototype that copies a D3D11 encoder texture to CPU staging
  and writes BGRA frames into `/tmp/alvr_frame_buffer.shm` via Wine's `Z:` drive.
- `alvr/macos_bridge/src/shared_memory.rs`  
  Native Rust mmap reader for the shared frame ring.
- `alvr/macos_bridge/src/encoder.rs`  
  First VideoToolbox HEVC wrapper. Keep as a proof scaffold; replace scalar BGRA
  to I420 conversion before performance testing matters.
- `alvr/macos_bridge/src/main.rs`  
  Shape of a native bridge process that waits for frames, encodes them, and
  sends ALVR video NALs through `ServerCoreContext`.
- macOS framework-link ideas from old `server_openvr/build.rs`: VideoToolbox,
  CoreMedia, CoreVideo, CoreFoundation, and likely IOSurface later.

Useful reference only:

- `fork/macos-videotoolbox:alvr/server_openvr/cpp/platform/macos/CEncoder.cpp`
  and `protocol.h` show an earlier VideoToolbox and Unix-socket/fd-passing
  skeleton, but the branch is less complete and appears internally stale against
  v21 interfaces.
- `VideoEncoderSocket.{h,cpp}` from `fork/macos-v20.14.1` is a debugging
  fallback reference. Prefer the later shared-memory design.

Do not forward-port:

- old workflow files, CI churn, release scripts, broad `Cargo.lock` changes, or
  unrelated v20 server/client edits;
- old `build.rs` wholesale, because v21 has newer Vulkan header handling,
  FFmpeg/dependency behavior, and Windows encoder paths;
- the Linux ARM64 detection commit unless a separate Linux ARM64 OpenVR effort
  needs it;
- any change that makes the shared-memory encoder unconditional on all Windows
  hosts.

## Critical Constraint

The old shared-memory bridge is an encoder-boundary bridge. It assumes ALVR
already received a D3D11 texture and reached `VideoEncoderSharedMem::Transmit`.
The current CrossOver SteamVR path fails earlier, inside `vrcompositor.exe`, so
the old bridge alone does not rescue Option A.

This means the Option B proof must either:

- create a native frame producer that does not depend on Windows SteamVR's
  compositor; or
- first prove a non-SteamVR or pre-compositor frame path can reach ALVR's encoder
  boundary under CrossOver.

Treat the macOS bridge as common infrastructure: it is still useful once there
is any viable frame producer.

## First Implementation Branch

Create a fresh ALVR branch from current `master` in the sibling source checkout:

```bash
cd ~/Developer/alvr
git checkout master
git pull --ff-only
git checkout -b macos-v21-native-bridge
```

Keep this planning repo focused on durable evidence and patch artifacts. Commit
source work in `~/Developer/alvr`, then export reproducible patches or PR links
back here.

## First Milestone

Build the native macOS bridge as a v21-compatible proof that can encode and feed
the ALVR client path without SteamVR's Windows compositor.

Steps:

1. Add `alvr/macos_bridge` from `7d8dde1b` as a v21 workspace crate.
2. Update dependencies and calls for current v21 `ServerCoreContext`, especially
   `send_video_nal(timestamp, global_view_params, is_idr, nal_buffer)`.
3. Add a synthetic I420 frame source before wiring Wine/CrossOver frame input.
4. Encode synthetic frames with VideoToolbox HEVC and send codec config plus NALs
   through the ALVR server path.
5. Connect the AVP client and prove first video decode or capture the next
   client-side blocker.

This milestone answers the most important unknown: whether the v21 AVP client
can decode real VideoToolbox-generated HEVC from a native macOS ALVR server path.

Current source status:

```text
~/Developer/alvr branch macos-v21-native-bridge
  adds alvr/macos_bridge as a v21 workspace crate
  adds a synthetic I420 frame source
  adds a VideoToolbox HEVC encoder wrapper
  adds a native bridge binary that starts ServerCoreContext, sends HEVC config
  NALs, and submits synthetic video NALs with v21 ViewParams
  now defaults to a 2560x720 side-by-side stream: two 1280x720 eye halves,
  mostly identical neutral eye-local fusion content, small L/R corner markers,
  aspect-correct fallback FOV, and IPD-separated fallback per-eye ViewParams
```

Validated on macOS 27 dev beta:

```bash
CARGO_TARGET_DIR=$CARGO_TARGET_DIR \
  cargo check -p alvr_macos_bridge
```

Result: passed. A bounded 30-frame `cargo run -p alvr_macos_bridge` also passed
with the 2560x720 side-by-side stream.

Live AVP decode result on macOS 27 dev beta:

```text
Native bridge PID connected to AVP at `<AVP_LAN_IP>`.
Control socket established on TCP 9943.
Video stream sent on UDP 9944.
Server log showed handshake finished, client connected, immediate IDR requested,
HEVC decoder config sent, and continuous video NAL submission.
AVP displayed a flashing checkered synthetic pattern.
```

Interpretation: first native macOS ALVR v21 -> AVP VideoToolbox HEVC decode is
alive. The visible result was 2D and misaligned per eye because the proof sends
a single flat synthetic frame with dummy per-eye `ViewParams`, not a real stereo
frame layout or pose-correlated view geometry.

Follow-up side-by-side stereo proof:

```text
Bridge sent 2560x720 side-by-side I420: two 1280x720 eye halves.
AVP displayed the L marker in the left eye and R marker in the right eye.
The neutral center content still felt misaligned and uncomfortable.
```

Interpretation: left/right eye routing and side-by-side frame splitting are
alive. Stereo fusion is not yet correct, likely because the proof still uses
static synthetic global view params rather than pose-correlated real render view
geometry. Do not treat this as a blocker for real-frame input unless the same
misalignment persists after feeding frames generated from a real stereo view
layout.

The next proof is real frame ingress into the native bridge, before polishing
synthetic stereo comfort.

Native shared-memory ingress status:

```text
Bridge mode: ALVR_BRIDGE_INPUT=shared-memory
Writer mode: ALVR_BRIDGE_INPUT=shared-memory-writer
Shared file: /tmp/alvr_frame_buffer.shm
Format: BGRA, DXGI_FORMAT_B8G8R8A8_UNORM-compatible marker 0x57
Smoke size: 1280x360
```

Validation result: native writer produced BGRA test frames into the shared file;
the bridge consumed shared-memory frames, converted BGRA to I420, emitted HEVC
decoder config, and exited cleanly after the writer shut down. The smoke read 8
of 10 frames, with drops expected while the scalar conversion path lags the
producer.

This proves external frame ingress into the already-proven native encode/decode
path. The next live proof is to run `shared-memory` bridge mode against the AVP
while the native writer feeds `/tmp/alvr_frame_buffer.shm`.

Live AVP shared-memory result:

```text
Bridge mode: ALVR_BRIDGE_INPUT=shared-memory
Writer mode: ALVR_BRIDGE_INPUT=shared-memory-writer
Live size: 1280x360
AVP handshake reached StreamReady and client connected.
Bridge read external shared-memory frames including 0, 1, 120, 240, 360, 480,
600, and 720.
AVP displayed the externally supplied moving BGRA test pattern.
```

Interpretation: native shared-memory BGRA frame ingress is alive all the way to
AVP display. The left/right views still do not quite match, so stereo geometry
and pose-correlated view params remain open, but external frame transfer is no
longer the blocker.

## Second Milestone

Port the shared-memory input path only after the native bridge can feed the AVP
client with synthetic frames.

Steps:

1. Port `alvr_shm_protocol.h` and `VideoEncoderSharedMem.{h,cpp}` into the v21
   Windows encoder tree behind an explicit CrossOver/macOS bridge setting or
   environment variable.
2. Preserve existing AMF, NVENC, VPL, and software encoder fallback order for
   ordinary Windows hosts.
3. Start the native bridge first so `/tmp/alvr_frame_buffer.shm` exists.
4. From Wine, verify the shared file opens as `Z:\tmp\alvr_frame_buffer.shm`.
5. Use a tiny Wine-side writer or ALVR encoder-boundary test to write test BGRA
   frames into the shared-memory ring before depending on SteamVR.

Current source status:

```text
~/Developer/alvr branch macos-v21-native-bridge
  adds the shared-memory C ABI header for /tmp/alvr_frame_buffer.shm
  adds VideoEncoderSharedMem for the v21 Windows encoder boundary
  wires CEncoder to use the shared-memory encoder only when
    ALVR_MACOS_SHM_ENCODER=1
  preserves the normal AMF -> NVENC -> VPL -> SW encoder path when the env var
    is unset
  makes shared-memory mode adopt the producer-published width, height, and BGRA
    format before creating the native HEVC encoder
  bounds-checks the fixed shared-memory frame slots and rejects dimensions above
    the 4096x2048 BGRA ABI limit
  uses release/acquire config publication between the Windows writer and native
    reader, and times out clearly if no producer config arrives
  can be cross-built from macOS for this probe with ALVR_BUILD_DISABLE_VPL=1,
    which leaves the normal Windows VPL path intact unless that local build env
    is explicitly set
  treats ALVR_MACOS_SHM_ENCODER=1 as an early-HMD-registration request so SteamVR
    can test the compositor path without a separate Windows ALVR Dashboard owner
```

The v21 writer opens the native macOS file from CrossOver/Wine as
`Z:\tmp\alvr_frame_buffer.shm`, waits for the macOS bridge to initialize the
ring, and publishes frames as `DXGI_FORMAT_B8G8R8A8_UNORM` BGRA. It stages the
actual compositor texture format on first frame. BGRA textures are copied
directly; RGBA textures are swizzled into the BGRA ring for the existing native
bridge reader.

The shared-memory handshake is producer-led. The native bridge creates the ring
and then waits for the writer to publish `config_width`, `config_height`, and
`config_format`. Once config is present, the bridge validates BGRA format,
creates the VideoToolbox encoder at that size, and rejects any later frame whose
dimensions or stride drift. The reader also consumes the lowest ready frame
number first so delayed frames do not feed VideoToolbox out of order under
backpressure.

The C/Rust ABI is guarded with source-side layout checks for the C frame header
and shared-memory offsets, and the Windows writer uses interlocked operations
when publishing `config_set`, `shutdown`, and `ALVR_FRAME_READY` across the mmap.
The Wine-visible file path is derived from the shared ABI path, so
`/tmp/alvr_frame_buffer.shm` maps to `Z:\tmp\alvr_frame_buffer.shm` in one
place.

Validation after hardening:

```bash
cargo fmt -p alvr_macos_bridge -- --check
CARGO_TARGET_DIR=$CARGO_TARGET_DIR \
  cargo check -p alvr_macos_bridge
CARGO_TARGET_DIR=$CARGO_TARGET_DIR \
  cargo build -p alvr_macos_bridge
python3 tools/vr_stack_cleanup.py
```

Result: passed. A bounded native shared-memory smoke also passed with a
`shared-memory` reader and `shared-memory-writer` producer, confirming the
producer-published config handshake still works before involving CrossOver. A
follow-up review found and fixed a deterministic C++ compile issue in the new
encoder header plus Rust-side config-load ordering and invalid-frame slot
release robustness.

This is still an encoder-boundary patch. The native reader and Windows writer
compile, and the native test writer can feed the macOS bridge, but the current
CrossOver SteamVR path still fails before ALVR reaches
`VideoEncoderSharedMem::Transmit`.

Live CrossOver proof on June 17, 2026:

1. Built `alvr_server_openvr` for `x86_64-pc-windows-msvc` from macOS with
   `ALVR_BUILD_DISABLE_VPL=1` into
   `$CARGO_TARGET_DIR`.
2. Staged the DLL as
   `C:\ALVR\v21.0.0-dev12-macos-shm-local.2026.06.17\bin\win64\driver_alvr_server.dll`.
3. Registered only that patched ALVR driver with SteamVR through `vrpathreg`.
4. Started the native bridge in `ALVR_BRIDGE_INPUT=shared-memory` mode and
   launched CrossOver SteamVR with `ALVR_MACOS_SHM_ENCODER=1`.
5. Confirmed SteamVR loaded the patched driver, started activation of
   `1WMHH000X00000`, and set `Active HMD` to `alvr_server.1WMHH000X00000`.
6. Confirmed `vrcompositor.exe` started, then failed before encoder entry:

   ```text
   Failed to create shared frame info constant buffer!
   Failed to start compositor: VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer
   ```

7. Confirmed the bridge never observed the Windows writer reach the
   shared-memory config/ready handshake, so no real Windows frames reached the
   shared-memory ring.

This closes the current Option A recheck: the patched ALVR source path no longer
blocks at HMD registration, but SteamVR's compositor still dies before the ALVR
encoder boundary.

## Performance Work

The first real performance question was whether a CPU readback bridge is even
worth prototyping on Apple Silicon. A synthetic D3D11 staging-readback probe was
run inside the CrossOver `Steam` bottle under the `AMD Compatibility Mode`
adapter reported by D3DMetal/GPTK:

```text
tools/d3d11_readback_probe.cpp
```

The probe rendered to a BGRA8 D3D11 texture, copied to a CPU-readable staging
texture, mapped it, and copied the full frame row-by-row. The default mode
copies into a heap buffer; the direct-write model copies the same full frame
into reusable destination storage to approximate writing mapped D3D rows
directly into a shared-memory slot.

Key measurements on the current Mac Studio:

- `2144x2048`, 17.56 MB, heap copy: 2.928 ms mean, 3.764 ms p99.
- `4096x2048`, 33.55 MB, heap copy: 9.323 ms mean, 21.284 ms p99,
  24.196 ms max.
- `4096x2048`, 33.55 MB, direct-write model: 5.718 ms mean,
  9.009 ms p99, 9.345 ms max.
- `4288x2048`, 35.13 MB, heap-copy stress case outside the current
  4096x2048 shared-memory ABI limit: 5.760 ms mean, 6.695 ms p99,
  7.232 ms max.

Interpretation: the CPU readback path is not free, but it is not obviously
disqualifying on this Apple Silicon machine. At the current 4096x2048
shared-memory ABI limit, the synthetic direct-write model stayed below the 90 Hz
11.1 ms frame interval, while the heap-copy path showed p99 spikes above that
budget. That leaves a tight budget once real game rendering, synchronous `Map`
stalls, BGRA conversion, VideoToolbox encode, network, and AVP decode are
included. The next prototype should therefore avoid intermediate heap copies,
use CrossOver D3D11 staging readback plus direct shared-memory writes first, and
only escalate to a GPU external-memory or IOSurface design if real-game latency
or jitter proves too high.

Known remaining costs:

- CrossOver side copies D3D11 texture to a staging texture, maps it, then copies
  BGRA into shared memory.
- the future macOS shared-memory input path will need to convert BGRA to a
  VideoToolbox-compatible format before encode.

After real-frame correctness:

- replace scalar conversion with VideoToolbox-compatible `CVPixelBuffer` input,
  Accelerate, or a Metal conversion pass;
- measure D3D11 staging readback under CrossOver separately from VideoToolbox
  encode time;
- consider IOSurface/Metal shared texture paths only after the basic ALVR client
  decode path is proven.

## Wine-Side Producer Probe

Before building a real OpenVR `IVRCompositor::Submit` interposer, isolate the
next missing link: a Windows process running inside CrossOver must be able to
write D3D11-produced BGRA frames into the native macOS shared-memory ring.

A standalone producer probe now exists in this planning repo:

```text
tools/d3d11_shm_writer_probe.cpp
```

It includes the real ALVR shared-memory ABI header from the sibling source
checkout, opens `/tmp/alvr_frame_buffer.shm` from Wine as
`Z:\tmp\alvr_frame_buffer.shm`, waits for the native bridge's `initialized`
flag, publishes BGRA config, renders a D3D11 BGRA8 render target, copies it to a
staging texture, maps it, and writes frames into the same 3-slot ring consumed by
`alvr_macos_bridge`.

Build command:

```bash
x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
  -static-libstdc++ tools/d3d11_shm_writer_probe.cpp \
  -I$HOME/Developer/alvr/alvr/server_openvr/cpp \
  -ld3d11 -ldxgi -lole32 \
  -o $PROBE_OUT/d3d11_shm_writer_probe.exe
```

Initial smoke from the CrossOver `Steam` bottle:

```text
C:\alvr-probes\d3d11_shm_writer_probe.exe \
  --width 640 --height 360 --frames 1 --fps 1
D3D11CreateDevice                OK hr=0x00000000
Adapter: AMD Compatibility Mode
published frame=0 dropped=0
done frames=1 dropped=0 ... frames_written=1 frames_encoded=0 shm_dropped=0
```

That smoke proves the Windows executable loads under CrossOver, creates D3D11
resources, maps the Wine-visible shared file, and writes one frame into an
already-initialized ring. It does not prove live AVP display because no native
bridge reader was alive during the smoke; the file was leftover state.

Live bridge run on June 17, 2026:

```text
ALVR_BRIDGE_INPUT=shared-memory \
  CARGO_TARGET_DIR=$CARGO_TARGET_DIR \
  cargo run -p alvr_macos_bridge

C:\alvr-probes\d3d11_shm_writer_probe.exe \
  --width 2560 --height 720 --frames 2400 --fps 20
```

The `macos_bridge` session initially only had a trusted Bonjour client with no
manual IP. The AVP stayed on `Connecting` until the bridge session was given a
trusted manual client entry for `<AVP_LAN_IP>`. After restarting the
bridge with that entry, the server connected to the AVP and reached
`Streaming`:

```text
14:14:01.130 [INFO] client connected; requesting immediate IDR
14:14:16.395 [INFO] shared memory configured: 2560x720 format=0x57
14:14:16.640 [INFO] sending HEVC decoder config (79 bytes)
14:14:17.029 [INFO] read shared-memory frame 9
14:16:17.717 [INFO] read shared-memory frame 2280
```

Writer result:

```text
done frames=2400 dropped=139 elapsed_ms=127577.8 \
  frames_written=2261 frames_encoded=2259 shm_dropped=139
```

Follow-up visual-observation run:

```text
C:\alvr-probes\d3d11_shm_writer_probe.exe \
  --width 2560 --height 720 --frames 6000 --fps 20
```

The bridge connected to the AVP before producer config was available, triggered
one expected early client decoder reset (`Gimme frames >:(`), then reconnected
after the writer started publishing frames:

```text
14:31:36.813 [INFO] client connected; requesting immediate IDR
14:31:36.862 [INFO] client requested IDR
14:31:42.235 [INFO] read shared-memory frame 120
14:34:40.677 [INFO] read shared-memory frame 3480
14:36:48.221 [INFO] read shared-memory frame 5880
```

Writer result:

```text
done frames=6000 dropped=320 elapsed_ms=318750.6 \
  frames_written=5680 frames_encoded=5678 shm_dropped=320
```

Final bridge session state remained `Streaming` for the manual AVP client at
`<AVP_LAN_IP>`.

Visual confirmation run:

The writer overlay was made deliberately obvious: alternating red/blue eye
halves, large white center markers, and a moving white vertical wipe. The user
confirmed video on the AVP/iPad mirror:

```text
Alternating blue and red, one eye blue and one eye red, then rotating.
White box in the middle.
White bar wiping across the screen, visible in one eye at a time.
```

This is the expected visual for the current side-by-side debug pattern. The
pattern is uncomfortable by design and is not intended for headset comfort.

Interpretation: real D3D11 frames produced inside CrossOver are now crossing the
Wine `Z:\tmp` mmap boundary, being read by the native macOS bridge, encoded by
VideoToolbox, sent while the AVP session is in `Streaming`, and visibly decoded
on the AVP. This proves Windows-side D3D11 frame ingress into the native macOS
ALVR bridge end to end. The remaining Option B gap is real-game frame capture,
not CrossOver-to-macOS shared-memory transport or AVP decode.

The next live proof is:

1. Run `python3 tools/vr_stack_cleanup.py`.
2. Start `alvr_macos_bridge` with `ALVR_BRIDGE_INPUT=shared-memory`.
3. Launch the AVP ALVR client and enter the stream view if needed.
4. Run the CrossOver writer probe from `C:\alvr-probes`.
5. Confirm the AVP displays the D3D11-produced animated pattern and the bridge
   increments `frames_encoded`.

Green result: achieved. Real Windows-side D3D11 frames and Wine-side mmap
coherence are proven, leaving only the real-game frame source/interposer as the
remaining Option B gap.

## OpenVR Submit Shim Prototype

The next prototype is an app-local `openvr_api.dll` proxy that forwards normal
OpenVR calls to a real DLL while intercepting `IVRCompositor_027::Submit`. This
is the first attempt to replace the synthetic D3D11 writer with frames submitted
by an actual OpenVR app or game.

Artifact:

```text
$PROBE_OUT/openvr_api.dll
```

Source:

```text
tools/openvr_submit_shim.cpp
```

Build command:

```bash
x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
  -static-libstdc++ -shared tools/openvr_submit_shim.cpp \
  -I$HOME/Developer/alvr/openvr/headers \
  -I$HOME/Developer/alvr/alvr/server_openvr/cpp \
  -ld3d11 -ldxgi -lole32 \
  -Wl,--out-implib,$PROBE_OUT/\
openvr_api_shim.lib \
  -o $PROBE_OUT/openvr_api.dll
```

Fake-runtime build command:

```bash
x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
  -static-libstdc++ -shared tools/fake_openvr_real.cpp \
  -I$HOME/Developer/alvr/openvr/headers \
  -I$HOME/Developer/alvr/alvr/server_openvr/cpp \
  -o $PROBE_OUT/fake_openvr_real.dll
```

Submit smoke build command:

```bash
x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
  -static-libstdc++ tools/openvr_submit_smoke.cpp \
  -I$HOME/Developer/alvr/openvr/headers \
  -L$HOME/Developer/alvr/openvr/lib/win64 -lopenvr_api \
  -ld3d11 -ldxgi -lole32 \
  -o $PROBE_OUT/openvr_submit_smoke.exe
```

The shim exports the current OpenVR loader entrypoints used by the app-facing
`openvr_api.dll` surface:

```text
VR_GetGenericInterface
VR_GetInitToken
VR_GetRuntimePath
VR_GetStringForHmdError
VR_GetVRInitErrorAsEnglishDescription
VR_GetVRInitErrorAsSymbol
VR_InitInternal
VR_InitInternal2
VR_IsHmdPresent
VR_IsInterfaceVersionValid
VR_IsRuntimeInstalled
VR_RuntimePath
VR_ShutdownInternal
```

It wraps exactly `IVRCompositor_027` and `FnTable:IVRCompositor_027`. Unknown
compositor versions pass through untouched. DirectX `Texture_t` submissions are
forwarded to the real OpenVR `Submit`, then captured synchronously before the
hook returns so reused app textures cannot race the readback. The shim copies
BGRA/RGBA D3D11 textures to staging memory, pairs left and right eyes into one
side-by-side BGRA frame, and publishes that frame into the same
`/tmp/alvr_frame_buffer.shm` ring already proven by the D3D11 writer probe.
Capture failure is fail-open: the real OpenVR `Submit` return value is
preserved.

Safe staging rules:

1. Do not replace SteamVR's runtime `openvr_api.dll` in place.
2. Stage the shim app-local beside a small OpenVR test app or chosen game.
3. Preserve the original app-local DLL as `openvr_api.real.dll`, or set
   `ALVR_OPENVR_REAL_DLL` to an absolute path for the real DLL.
4. Start the native bridge first with `ALVR_BRIDGE_INPUT=shared-memory` so the
   shared-memory file is freshly initialized.
5. Launch the Windows OpenVR app through CrossOver.
6. Inspect `Z:\tmp\alvr_openvr_submit_shim.log` for loader, interface-wrap, and
   frame-publication lines.

Smoke ladder:

1. DLL loads and forwards `VR_InitInternal` / `VR_GetGenericInterface` without
   breaking app startup.
2. `VR_GetGenericInterface(IVRCompositor_027)` or the matching FnTable request is
   logged and wrapped.
3. `Submit` logs/captures texture type, format, and size.
4. The shim publishes paired left/right BGRA frames into the bridge ring.
5. The AVP displays content from a real OpenVR submitter.

Smoke result on June 17, 2026:

```text
tools/openvr_submit_smoke.cpp
tools/fake_openvr_real.cpp
```

Two app-local smoke stages were run from the CrossOver `Steam` bottle.

First, using SteamVR's real `openvr_api.dll` as `openvr_api.real.dll` proved the
runtime path but stopped before `Submit`:

```text
runtime_installed=1
got_runtime_path=1
path=C:\Program Files (x86)\Steam\steamapps\common\SteamVR
SetDllDirectory=C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin
AddDllDirectory=C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64
VR_Init failed: Hmd Not Found (108)
```

Interpretation: the smoke can load the SteamVR runtime once the SteamVR DLL
search path includes `bin\win64`, but a scene app still needs a live SteamVR HMD
session before it can reach real compositor submit calls.

Second, using a fake app-local `openvr_api.real.dll` proved the shim ABI and
DirectX submit capture path without SteamVR/HMD state:

```text
VR_GetGenericInterface IVRCompositor_027 -> 00006ffff5589020
wrapped C++ IVRCompositor object=00006ffff5589020
staging ready eye=0 format=87 size=640x360
staging ready eye=1 format=87 size=640x360
mapped shared memory and published config 1280x360
published Submit pair frame=0 output=1280x360 left=640x360 right=640x360
published Submit pair frame=89 output=1280x360 left=640x360 right=640x360
published Submit pair frame=179 output=1280x360 left=640x360 right=640x360
```

Interpretation: the app-local proxy loads, forwards to the real DLL, wraps the
C++ `IVRCompositor_027` object, preserves real `Submit` success, synchronously
captures DirectX D3D11 textures before returning from the hook, stages BGRA
frames, pairs left/right eyes, and publishes frames into the shared-memory ring.
The remaining real-app gap is SteamVR runtime/HMD state and a real submitter,
not the proxy's basic ABI or D3D11 capture path.

Follow-up ABI and stale-state hardening:

```text
IVRCompositor_027 C++ vtable slots: 51
IVRCompositor_027 C FnTable slots: 51
Submit slot: 5
No-bridge C++ smoke: wrapped Submit, staged both eyes, blocked stale shm
No-bridge C FnTable smoke: wrapped Submit, staged both eyes, blocked stale shm
Stale D3D writer probe: exited nonzero before publishing config
```

Interpretation: the shim now copies the compositor table shape that matches the
local OpenVR headers instead of using guessed sizes. The fake runtime exposes
both the C++ `IVRCompositor_027` object and the C
`FnTable:IVRCompositor_027` table, so both wrapper paths are covered. The native
bridge shared-memory ABI also now uses reserved header bytes for a bridge-owned
session id and heartbeat. Windows-side writers require that heartbeat to be
fresh, with a small Wine/native clock-skew tolerance, before publishing config
or frames. This prevents leftover `/tmp/alvr_frame_buffer.shm` files from
creating false-green smoke results when no macOS bridge is alive.

Live AVP submit-shim smoke:

```text
Bridge mode: ALVR_BRIDGE_INPUT=shared-memory
Submitter: tools/openvr_submit_smoke.cpp through CrossOver Steam bottle
Real DLL: tools/fake_openvr_real.cpp staged as openvr_api.real.dll
Shim: tools/openvr_submit_shim.cpp staged as app-local openvr_api.dll
Frame size: 2560x720 side-by-side BGRA, two 1280x720 eyes
Result: pass
```

The AVP client connected, the native bridge waited for producer config, the
shim captured D3D11 `TextureType_DirectX` Submit calls, published the
side-by-side BGRA config, and the bridge read frames 0 through 9 before sending
HEVC decoder config. The smoke completed cleanly, and the bridge was stopped
afterward with no stale Wine/CrossOver processes left behind.

Representative log evidence:

```text
client connected; requesting immediate IDR
shared memory configured: 2560x720 format=0x57
read shared-memory frame 0
sending HEVC decoder config (79 bytes)
read shared-memory frame 9
wrapped C++ IVRCompositor object=... real_submit=...
staging ready eye=0 format=87 size=1280x720
staging ready eye=1 format=87 size=1280x720
published Submit pair frame=0 output=2560x720 left=1280x720 right=1280x720
published Submit pair frame=89 output=2560x720 left=1280x720 right=1280x720
```

Interpretation: Option B's core transport is alive for CrossOver-side D3D11
Submit textures. The remaining gap is no longer native VideoToolbox encode,
AVP decode, Wine-visible shared memory, or app-local OpenVR proxy mechanics. It
is getting the same shim in front of a real SteamVR/OpenVR submitter and
handling any real-app texture-shape details such as bounds, MSAA, or runtime HMD
state.

Real SteamVR runtime check:

```text
Submitter: tools/openvr_submit_smoke.cpp
Shim: app-local openvr_api.dll
Real DLL: SteamVR/bin/win64/openvr_api.dll through ALVR_OPENVR_REAL_DLL
Bridge mode: ALVR_BRIDGE_INPUT=shared-memory
Result: blocked before Submit
```

The shim loaded SteamVR's real `openvr_api.dll`, but the tiny D3D11 submitter
stopped at `VR_Init failed: Hmd Not Found (108)`. No
`VR_GetGenericInterface(IVRCompositor_027)` call was observed, no Submit calls
were wrapped, and the native bridge stayed waiting for producer config. Cleanup
terminated two leftover CrossOver processes and ended with `remaining=0`.

Interpretation: using SteamVR's real runtime is still blocked before the app can
submit frames. The next productive target is not a larger game or SteamVR tool;
it is a null-runtime-backed real OpenVR app path. Grow the fake
`openvr_api.real.dll` beyond the direct compositor-only smoke so it exposes the
minimal `IVRSystem`/pose/compositor surface a small real D3D11 OpenVR client
expects, then use that client to exercise real app lifecycle details such as
recommended render target size, `WaitGetPoses`, `VRTextureBounds_t`, RGBA/BGRA,
and MSAA.

The earlier macOS ALVR panic path in `rfd` was addressed by making the bridge
use headless server-core logging. Normal server-core logging still installs the
popup callback for non-bridge consumers, but `alvr_macos_bridge` now keeps
session/crash logs and the panic hook without trying to show native dialogs.

Null-runtime expansion on June 17, 2026:

```text
Fake runtime: tools/fake_openvr_real.cpp
Submitter: tools/openvr_submit_smoke.cpp
Shim: tools/openvr_submit_shim.cpp
CrossOver bottle: Steam
```

The fake runtime now exposes `IVRSystem_022`, `FnTable:IVRSystem_022`,
`IVRCompositor_027`, and `FnTable:IVRCompositor_027`. The `IVRSystem` surface is
minimal but enough for a D3D11 OpenVR app lifecycle: recommended render target
size, projection and eye transforms, DXGI output info, HMD pose, HMD class and
properties, event polling, and runtime version.

Smoke matrix passed with the fake runtime staged directly as app-local
`openvr_api.dll`:

```text
openvr_submit_smoke.exe --frames 3 --fps 5
openvr_submit_smoke.exe --frames 3 --fps 5 --direct-interface
openvr_submit_smoke.exe --frames 3 --fps 5 --c-fntable
openvr_submit_smoke.exe --frames 3 --fps 5 --rgba
openvr_submit_smoke.exe --frames 3 --fps 5 --bounds
openvr_submit_smoke.exe --frames 1 --fps 5 --msaa 4
```

The same matrix also passed with the app-local shim staged as `openvr_api.dll`
and the fake runtime staged as `openvr_api.real.dll`. Shim logs showed the C++
and C FnTable compositor paths wrapped, BGRA format `87` and RGBA format `28`
staged for both eyes, and the expected current MSAA boundary:

```text
unsupported Submit texture eye=0 shape=640x360 samples=4 array=1 mips=1
unsupported Submit texture eye=1 shape=640x360 samples=4 array=1 mips=1
```

Interpretation: the null-runtime path is now a useful harness for real-app
lifecycle details without relying on SteamVR HMD state. The next concrete target
is to put a small real D3D11 OpenVR sample or stripped-down app loop on top of
this fake runtime, then decide whether `VRTextureBounds_t` cropping and MSAA
resolve support are needed before trying a full game.

Typed D3D11 OpenVR app-loop probe on June 17, 2026:

```text
App loop: tools/openvr_app_loop_probe.cpp
Fake runtime: tools/fake_openvr_real.cpp
Shim: tools/openvr_submit_shim.cpp
CrossOver bottle: Steam
```

The app-loop probe uses normal typed OpenVR entrypoints instead of manually
indexing compositor slots. It calls `vr::VR_Init`, `vr::VRSystem`,
`vr::VRCompositor`, `GetRecommendedRenderTargetSize`, HMD properties,
projection and eye transforms, `WaitGetPoses`, per-eye D3D11 rendering,
`Submit`, and `PostPresentHandoff`. It also honors the adapter returned by
`GetDXGIOutputInfo` when creating its D3D11 device, which better matches the
texture-device contract real OpenVR runtimes expect.

Build command:

```bash
x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
  -static-libstdc++ tools/openvr_app_loop_probe.cpp \
  -I$HOME/Developer/alvr/openvr/headers \
  -L$HOME/Developer/alvr/openvr/lib/win64 -lopenvr_api \
  -ld3d11 -ldxgi -lole32 \
  -o $PROBE_OUT/openvr_app_loop_probe.exe
```

Direct fake-runtime matrix passed:

```text
openvr_app_loop_probe.exe --frames 3 --fps 5
openvr_app_loop_probe.exe --frames 3 --fps 5 --rgba
openvr_app_loop_probe.exe --frames 3 --fps 5 --bounds
openvr_app_loop_probe.exe --frames 1 --fps 5 --msaa 4
openvr_app_loop_probe.exe --frames 1 --fps 5 --msaa 4 --submit-msaa
```

Shim-fronted fake-runtime matrix passed with the same commands, staged as:

```text
openvr_api.dll: tools/openvr_submit_shim.cpp artifact
openvr_api.real.dll: tools/fake_openvr_real.cpp artifact
```

After review, the matrix was rerun with two probe-quality fixes in place:

- repeated `FnTable:IVRCompositor_027` requests reuse the already wrapped C
  table instead of allocating duplicate wrappers;
- the typed app-loop creates its D3D11 device on the OpenVR-reported DXGI
  adapter when the runtime provides one;
- normal `WaitGetPoses` or `Submit` compositor errors now make the probe exit
  nonzero, while `--submit-msaa` remains an explicitly warned boundary case;
- MSAA render targets no longer request shader-resource binding; only the
  single-sample submit/resolve textures are shader-resource bindable.

Representative app-loop output:

```text
recommended_render_target=1280x720 selected=1280x720
dxgi_adapter_index=0 runtime=FakeOpenVR_022
hmd_class=1 connected=1 model=FakeOpenVR Null HMD display_hz=90.0
projection_left [1.00 1.00 1.00 1.00]
eye_to_head_left x=-0.032 diag=[1.00 1.00 1.00]
OpenVR app loop width=1280 height=720 frames=3 fps=5
creating D3D11 device on OpenVR adapter 0
frame=0 pose=0 hmd_valid=1 left_submit=0 right_submit=0
```

The rerun stayed green for BGRA, RGBA, bounds forwarding, resolved MSAA, and raw
MSAA boundary mode. Raw MSAA still prints a warning because real OpenVR runtimes
may reject submitted multisampled textures.

Representative shim evidence:

```text
wrapped C++ IVRCompositor object=... real_submit=...
staging ready eye=0 format=87 size=1280x720
staging ready eye=1 format=87 size=1280x720
staging ready eye=0 format=28 size=1280x720
staging ready eye=1 format=28 size=1280x720
unsupported Submit texture eye=0 shape=1280x720 samples=4 array=1 mips=1
unsupported Submit texture eye=1 shape=1280x720 samples=4 array=1 mips=1
```

Interpretation: a normal typed D3D11 OpenVR app lifecycle now works against the
fake runtime and through the app-local shim. Resolved MSAA is green: by default
`--msaa 4` renders to MSAA targets, resolves into single-sample submit textures,
and the shim stages them normally. Raw submitted MSAA remains an explicit
boundary test through `--submit-msaa` and is still logged/skipped by the shim.

Live AVP app-loop result on June 18, 2026:

```text
Bridge mode: ALVR_BRIDGE_INPUT=shared-memory
Producer: C:\alvr-probes\app-loop-openvr\openvr_app_loop_probe.exe
Producer config: 2560x720 side-by-side BGRA, 1280x720 per eye
```

The native bridge was started, the AVP client was waiting, and the typed
CrossOver OpenVR app-loop producer was launched through the app-local shim. The
shim mapped `/tmp/alvr_frame_buffer.shm`, published a 2560x720 config, and kept
publishing paired Submit frames. The first long run displayed a red block on the
AVP while the producer was alive, then fell back to wireframe after the producer
completed. A second long run with a clearer generated pattern showed red/blue
per-eye content, dark cross reference lines, and a moving white stripe. The
left/right consistency and alignment were bad and uncomfortable, but visible
images and motion were present.

Follow-up diagnostic modes narrowed the issue:

- `--mono` submitted the same texture to both eyes. The AVP showed the same image
  in both eyes, but with remaining left/right offset, which points away from
  random transport corruption and toward presentation geometry/alignment.
- Accidentally overlapping producers produced a busy inconsistent image; after
  all old Wine child processes were killed, exactly one producer gave stable
  output.
- `--left-only` produced pattern content in the left eye and black in the right;
  the iPad mirror showed the left-eye pattern.
- `--right-only` produced black in the left eye and blue pattern content in the
  right; the iPad mirror went black because it mirrors the left eye.

These single-eye tests prove the side-by-side seam and eye routing are sane for
the probe. The remaining stereo discomfort is not caused by left/right swapping,
duplicate producers, or obvious shared-memory packing corruption.

Alignment calibration then added a grid/ruler and right-eye content-shift flags
to the app-loop probe. In the baseline mono grid, vertical alignment looked
basically perfect, while horizontal alignment was about eight 32-pixel grid
squares off, or roughly 256 pixels. Running mono with `--right-shift-x -256`
made the center look about perfect. Motion was not smooth and the left/right
edges remained bad because this is a content-space diagnostic shift, not a real
projection or view-parameter correction. This establishes the next bridge-side
target: correct the horizontal eye presentation geometry without baking a
constant content shift into the transport path.

Bridge-side FOV/ViewParams shift trials were attempted next, but the dynamic
red/blue pattern and flickering output made the result too difficult to judge by
human eyes. Treat those observations only as evidence that the current visual
stimulus is a bad calibration instrument; do not treat labels like "worse" or
"overcorrected" as measured alignment data. Before continuing geometry tuning,
use a static low-flicker alignment target and re-confirm the known producer-side
`--right-shift-x -256` baseline.

The app-loop probe now has a `--static-pattern` mode for that purpose. The
pattern path also supports both BGRA and RGBA submit formats and reuses one
staging texture instead of allocating a staging texture every frame, so probe
timing and texture-format coverage are less misleading.

Live follow-up: `--static-pattern --mono --right-shift-x -256` was still too
busy and hard to interpret on-device. A later static alignment-grid mode made
the calibration usable: baseline `--alignment-grid --mono` showed each eye's
own image centered, but the binocular view placed the center box around `-2`
and `+2` major grid marks. Re-running mono with `--right-shift-x -256` fused the
image into a perfectly aligned 2D target in 3D space. Re-running stereo with the
same `--right-shift-x -256` kept the center aligned while showing distinct left
and right labels.

This proves the rough sign and magnitude of the horizontal correction. It also
proves the current content-space shift is only a diagnostic: alignment is good
near the center, but the image disappears near one edge because the shifted
right-eye content runs out inside the fixed per-eye texture. Do not bake
`--right-shift-x -256` into the transport path as the final fix. The next
geometry target is to apply the equivalent horizontal correction through view
parameters, projection/bounds handling, or crop/packing geometry so center
alignment is preserved without losing edge coverage.

Follow-up packing diagnostic: the native bridge-side
`ALVR_BRIDGE_RIGHT_EYE_SHIFT_X_PX=-256` trial did not visibly move the AVP
alignment, so the correction was tested at the shim packing boundary instead.
`tools/openvr_submit_shim.cpp` now has an opt-in
`ALVR_SHIM_INNER_CROP_PX` diagnostic. With `ALVR_SHIM_INNER_CROP_PX=256`, the
shim crops the inner/nose-side 256 pixels from the paired eye strips, publishing
`2048x720` side-by-side frames from `1280x720` source eyes. A fresh AVP static
grid run on June 18, 2026 showed the center boxes closer/fused, proving the
side-by-side packing geometry can influence the visible alignment. Treat this
as evidence for the coordinate model, not as the final production geometry: the
durable fix still needs projection/ViewParams or real OpenVR projection data so
center alignment and full edge coverage can coexist.

This crosses the main Option B transport milestone: CrossOver D3D11 OpenVR
`Submit` frames can flow through the app-local shim, Wine-visible shared memory,
the native macOS ALVR bridge, VideoToolbox encode, and the AVP client.

Live SteamVR Tutorial timing smoke on June 18, 2026 then changed the priority.
The shim captured real D3D11 SteamVR Tutorial frames and the bridge delivered
them to AVP with protocol-v2 timing instrumentation. After warmup, the producer
side was not the dominant cost:

```text
real OpenVR Submit:        usually ~0.5-0.8 ms
producer capture:          ~5-6 ms
D3D map/readback:          ~2.5-3.1 ms
pixel copy:                ~1.1 ms
left/right pair copy:      ~0.15-0.2 ms
native BGRA-to-I420:       ~36-37 ms/frame
```

Interpretation: the CrossOver D3D readback path is not yet disqualified. The
dominant measured blocker is the native bridge's scalar BGRA-to-I420 conversion,
which is far too slow for a comfort-ready VR path and also makes visual alignment
judgment noisy. The next milestone is therefore native bridge pixel-path
performance, before more stereo-comfort tuning.

Preferred first spike: replace the scalar loop with Accelerate/vImage
BGRA-to-NV12 conversion and feed the encoder through a pooled `CVPixelBuffer`
path. The current `shiguredo_video_toolbox` wrapper already exposes `Nv12` and
`encode_pixel_buffer`, while a direct `kCVPixelFormatType_32BGRA` VideoToolbox
path would require extending or bypassing the wrapper. Treat direct BGRA encode
as the second feasibility probe if the vImage/NV12 path is still too slow or too
jittery. Defer Metal/IOSurface conversion until CPU BGRA-to-NV12 is measured and
shown insufficient. After conversion is in the low single-digit milliseconds,
rerun the same SteamVR Tutorial smoke and then return to stereo geometry, eye
alignment, bounds/crop handling, and real-app texture formats.

Next-turn validation target for the vImage/NV12 spike:

```text
bgra_to_nv12 p50: < 4 ms at 2560x720
bgra_to_nv12 p99: < 8 ms at 2560x720
AVP display: still alive with no obvious channel swap
bridge drops: no systematic drops at the smoke-test frame rate
```

Known first-prototype limits:

- Resolved MSAA is green in `openvr_app_loop_probe.cpp`; raw multisampled submit
  textures are logged and skipped. If a target app submits MSAA textures instead
  of resolved textures, add shim-side `ResolveSubresource` or app-specific
  resolve handling.
- `VRTextureBounds_t` is forwarded to the real runtime and accepted by the smoke
  path, but shim capture still reads the full texture. Apps that submit subrects
  or flipped bounds may display cropped or inverted frames until capture applies
  bounds.
- Readback is synchronous on the `Submit` hook path. It measured around 2.5-3.1
  ms for D3D map/readback in the SteamVR Tutorial smoke, so do not optimize it
  before fixing the much larger native BGRA-to-I420 conversion cost. If the
  conversion spike exposes capture as the next bottleneck, add an async
  private-copy/staging queue with explicit backpressure.
- Shared-memory liveness now comes from the bridge-owned session id and
  heartbeat in the mmap header rather than the file timestamp.

## Stop Conditions

Stop the old CrossOver SteamVR Option A path unless one of these changes:

- CrossOver/D3DMetal/DXVK/MoltenVK gains working DXGI shared-handle support on
  macOS;
- a diagnostic shim proves SteamVR's failing shared frame-info buffer is only a
  capability check and can be bypassed without cross-process GPU resource use;
- an older SteamVR build reaches ALVR's encoder boundary without the failing
  shared-resource path.

Until then, spend implementation effort on the native v21 bridge and AVP decode
proof.
