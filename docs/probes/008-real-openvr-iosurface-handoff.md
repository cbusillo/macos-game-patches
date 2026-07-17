# Real OpenVR IOSurface Handoff

## Hypothesis

The existing app-local OpenVR submit shim can copy one real Freedom D3D11
submitted texture into a dedicated, viewless DXVK handoff texture without
changing the game's texture, the real `IVRCompositor::Submit` result, or the
existing CPU diagnostic path. After queue-ordered completion, a separate native
arm64 process should read the same sampled pixel through IOSurface and Metal.

This is the first real-producer gate for GitHub issue #53. It is intentionally a
one-shot sidecar proof before the three-slot production pool is added.

## Environment

- Parent workstream: GitHub issue #36; implementation plan: #53.
- CrossOver: 26.2, build `26.2.0.39821`.
- Process-only graphics backend: bundled DXVK
  `cxaddon-1.10.3-1-25-g737aacd`.
- Producer: Freedom Locomotion VR through the existing app-local
  `tools/openvr_submit_shim.cpp`.
- Expected source: 3240x1800 `DXGI_FORMAT_B8G8R8A8_TYPELESS`, one sample, one
  layer, render-target and shader-resource binds, with per-eye OpenVR bounds.
- Handoff: same dimensions, `DXGI_FORMAT_B8G8R8A8_UNORM`, no bind flags, no
  views, transfer-only Vulkan usage.
- Consumer: native arm64 mode of `tools/moltenvk_iosurface_probe.mm`.

## Procedure

1. Keep both real OpenVR `Submit` hooks forwarding to the real implementation
   before any capture work.
2. Keep the current CPU readback and shared-memory publication path unchanged.
3. After one successful left-eye D3D11 readback with non-fallback bounds, select
   a non-black sampled pixel and retain its source coordinate and BGRA value.
4. Create one dedicated handoff texture on the submitted texture's D3D11 device.
5. Before any view or copy uses the handoff texture, query DXVK interop and
   attach an IOSurface through the matched Wine unixlib bridge.
6. Copy the full submitted texture into the handoff texture, flush D3D11 and
   DXVK, then enqueue a queue-ordered Vulkan fence marker.
7. Return from the capture path after marker submission. A worker waits for the
   fence, writes a sidecar manifest, waits for bounded verifier acknowledgement,
   then releases the retained Mach-port reference.
8. Run the native arm64 consumer with the manifest's IOSurface ID, dimensions,
   sample coordinate, and expected BGRA value.
9. Preserve the submit shim, consumer, and existing CPU bridge logs. Remove all
   staged proof files and bridge artifacts after the bounded run.

## Expected Proof

The probe passes only when logs establish all of the following:

- the source is a real successful Freedom DirectX `Submit` with the recorded
  descriptor and non-fallback eye bounds;
- the handoff texture is dedicated, UNORM, viewless, and attached before copy;
- `CopyResource`, DXVK flush, marker submission, and fence wait succeed;
- the native verifier is arm64, imports the IOSurface, and reads the exact BGRA
  value sampled from the CPU diagnostic readback at the same full-texture
  coordinate;
- the real `Submit` return and existing CPU shared-memory pair contract continue
  to appear; and
- no full-frame CPU data is used to populate the IOSurface handoff texture.

## Failure Signatures

- Adapter is `AMD Compatibility Mode`: the process selected D3DMetal, so the
  DXVK interop run is invalid.
- Submitted descriptor or bounds differ from the recorded Freedom contract:
  record the new contract before interpreting pixel results.
- Fallback crop is used: skip the proof because the sampled coordinate cannot be
  joined reliably to the submitted bounds.
- No non-black CPU sample appears: keep waiting for a real rendered frame; do not
  accept a black-corner match.
- Bridge attach, queue marker, or fence wait fails: do not publish the surface.
- Native pixel mismatch: preserve both source and consumer values and treat the
  real-producer path as blocked.
- Existing CPU pair logs stop: the sidecar proof disrupted the baseline and is
  invalid even if its pixel matches.

## Do Not Repeat

- Do not change the version-5 CPU shared-memory ABI for this one-shot proof.
- Do not modify or retrofit the game's submitted texture.
- Do not create a render-target or shader-resource view for the handoff texture.
- Do not wait for native verification on the OpenVR submit thread.
- Do not treat a global IOSurface ID as the production IPC contract; it is
  permitted only for this bounded unsandboxed verifier.
- Do not ask for headset interpretation. This gate is deterministic and must
  pass before real AVP visual validation resumes.

## Evidence Log

### 2026-07-11 Real Freedom DXVK Rendering Blocker

Run: Freedom Locomotion VR with the app-local submit shim and fake runtime,
process-only DXVK, the matched Wine IOSurface bridge, and the native macOS CPU
diagnostic bridge.

Artifacts:

- `.code/probes/008-real-openvr-iosurface/freedom-20260711T154847Z/freedom-launch.log`
- `.code/probes/008-real-openvr-iosurface/freedom-20260711T154847Z/openvr-submit-shim.log`
- `.code/probes/008-real-openvr-iosurface/freedom-20260711T154847Z/bridge-console.log`

Verified:

- The real game reached successful DirectX submits with the expected 3240x1800
  typeless BGRA descriptor, bind flags `0x28`, and non-fallback left/right
  bounds.
- Every sampled CPU diagnostic frame had alpha 255 but RGB maximum 0. The shim
  therefore correctly refused to select a false-positive black pixel or publish
  an IOSurface manifest.
- MoltenVK repeatedly failed the game's geometry pipelines while converting
  DXVK shaders to Metal mesh shaders. The generated MSL referenced undeclared
  `s_per_vertex` and `meshStream` symbols and returned
  `VK_ERROR_INVALID_SHADER_NV`.
- The failure occurs before the IOSurface handoff. Probes 006 and 007 remain
  valid capability proofs, but the installed DXVK/MoltenVK pair is not yet a
  valid real Freedom renderer on macOS 27 and Apple M4 Max.

Inferred: building the three-slot DXVK pool now would optimize a backend that
cannot produce valid real-game pixels. Backend repair or a D3DMetal-native
extraction path must be decided first.

Do not repeat:

- Do not accept all-black alpha-valid frames as handoff evidence.
- Do not retry the contract-faithful app-loop scene; it selected incompatible
  adapter objects before reaching the sidecar proof and added no real-game
  evidence.

### 2026-07-11 Renderer Repair

The bounded DXVK/MoltenVK repair produced a valid non-black Freedom frame.

- CrossOver's DXVK 1.10.3 source was rebuilt with upstream DXVK commit
  `d93568f1` adapted to the older compiler. This removed the invalid geometry
  payload access that generated undeclared `s_per_vertex` references.
- MoltenVK's SPIRV-Cross geometry-to-mesh path was repaired so generated helper
  calls receive `meshStream`, object-wrapper arguments retain the Metal
  argument-buffer order, and the mesh payload preserves reflected vertex-output
  slots even when the geometry shader does not consume every slot.
- The exact Freedom geometry shader that previously failed generated accepted
  Metal source, and the real submitted texture reported
  `nonzero_pixels=2916000`, `max_color=255`, and `max_alpha=255`.

The first non-black run then crashed before the IOSurface proof emitted a
success or failure line. The UE minidump recorded an execute access violation at
`0x6fffca700000`; the return address mapped to `openvr_api.dll+0x7e02` inside
`DxvkIosurfaceSubmitProof::captureOnce`. Disassembly of the linked shim and its
object file showed that MinGW optimized the locally declared pure-virtual DXVK
COM fallback into an unconditional `__cxa_pure_virtual` call. The linked target
was invalid after relocation.

The proof now uses an explicit COM vtable layout for the two private DXVK
interop interfaces. This retains the published interface slot order without
giving the optimizer an abstract C++ type it can devirtualize. The rebuilt
object and DLL contain no `__cxa_pure_virtual` relocation in the proof path.

Shim build command:

```bash
x86_64-w64-mingw32-g++ -O2 -g -std=c++20 \
  -static -static-libgcc -static-libstdc++ -shared \
  tools/openvr_submit_shim.cpp tools/dxvk_iosurface_submit_proof.cpp \
  -I$HOME/Developer/alvr/openvr/headers \
  -I$HOME/Developer/alvr/alvr/server_openvr/cpp \
  -I/opt/homebrew/include \
  -ld3d11 -ldxgi -lole32 \
  -Wl,--out-implib,$PROBE_OUT/openvr_api_shim.lib \
  -o $PROBE_OUT/openvr_api.dll
```

After the patched DXVK, MoltenVK, Wine unixlib bridge, native arm64 consumer,
fake OpenVR runtime, and shim artifacts exist at the paths recorded by probes
006 through 008, run the checksum-guarded real gate with:

```bash
bash tools/run_real_openvr_iosurface_probe.sh
```

The command starts a fresh native shared-memory bridge, removes all prior
ready/done sidecars before launch, stages only checksum-verified probe files,
writes the verifier acknowledgment through a temporary file plus atomic rename,
archives bounded logs, and removes the ready/done files again during restoration.
It exits nonzero unless the native arm64 pixel, nonce-bound acknowledgment,
legacy CPU pair, native bridge read, and restoration gates all pass.

### 2026-07-11 Real Freedom IOSurface Pass

Passing run:

```text
.code/probes/008-real-openvr-iosurface/
  dxvk-d93568f1-interop-vtable-real-20260711T183513Z/
```

Verified sequence:

- Freedom submitted the expected 3240x1800 typeless BGRA texture with bind flags
  `0x28` and non-fallback left-eye bounds.
- The CPU diagnostic selected full-texture coordinate `(888,952)` with BGRA
  `252,0,0,255` from a non-black real frame.
- The sidecar created a dedicated 3240x1800 `B8G8R8A8_UNORM` texture with no
  D3D bind flags. DXVK exposed Vulkan format 44 with transfer source/destination
  usage `0x3`.
- The Wine unixlib bridge attached IOSurface ID 3591 before the copy.
- `CopyResource`, D3D flush, DXVK flush, locked queue marker submission, and
  fence wait all succeeded. Copy plus flush took 632 microseconds; marker submit
  took 770 microseconds; the worker's fence wait took 1 microsecond.
- A separate native arm64, non-translated process imported IOSurface 3591
  through Metal and read `252,0,0,255` at `(888,952)`, exactly matching the real
  submitted-frame diagnostic.
- The ready and done records were published atomically and bound to proof nonce
  `10390388676`, submit sequence 1, and surface ID 3591. The worker rejected any
  uncorrelated acknowledgment, released the retained Mach-port right, and logged
  `result=pass`.
- The worker used a native thread entry compatible with
  `FreeLibraryAndExitThread`; all C++ state was destroyed before unloading the
  pinned shim module, and the fence was destroyed on every wait result.
- The existing CPU bridge remained live. The shim published a 2792x1800 paired
  Submit frame, and the native bridge configured that shape and read frames 0
  through 9 while the IOSurface proof ran.
- Cleanup restored CrossOver's MoltenVK and Freedom's original OpenVR DLL by
  checksum and removed every app-local DXVK and bridge artifact.

This passes the real-producer gate without using CPU pixels to populate the
IOSurface. The CPU readback exists only as a one-shot independent oracle and is
not part of the handoff data path.

The earlier run ending in `20260711T181420Z` established the coordinate-matched
GPU handoff but ran while the legacy CPU bridge heartbeat was stale. Review of
that run led to the nonce-bound acknowledgment, native worker entry, complete
fence cleanup, and live CPU-pair non-regression gate used by the passing run
above.

## Verdict

`passed` for the one-shot real Freedom producer gate. A real OpenVR D3D11 submit
now reaches a separate native arm64 Metal process through a dedicated
DXVK/MoltenVK IOSurface with an exact coordinate-matched pixel.

## Next Action

Build the production three-slot handoff pool from this proven sequence:

1. Allocate and attach three dedicated viewless handoff textures at startup.
2. Keep queue marker waits and slot recycling on worker threads; never wait for
   GPU completion on the OpenVR submit thread.
3. Replace the global IOSurface ID and sidecar files with retained Mach-port
   transfer over a bounded native IPC channel plus a startup identity self-test.
4. Feed the imported surfaces directly into the native ALVR encode-surface
   contract without full-frame CPU readback.
5. After cadence, lease recycling, and cleanup gates pass, resume AVP eyes-on
   validation with real Freedom stereo rather than an artificial plane.
