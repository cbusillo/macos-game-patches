# SteamVR Tutorial Unity Performance Control

## Goal

Determine whether Open Brush's approximately `34 FPS` producer ceiling is a
general Unity 2019/OpenVR problem under CrossOver or an application-specific
Open Brush rendering problem.

This is a software-only performance discriminator. It does not replace the
official Freedom lifecycle and headset acceptance run, and it does not make
SteamVR Tutorial a gameplay target.

## Control Selection

Use the already-installed official SteamVR Tutorial from SteamVR app `250820`,
build `23791826`. This avoids another download and consumes only bounded probe
artifacts.

- `steamvr_tutorial.exe` has SHA-256
  `46ae66c3f38952659c56ba4fe4678d157b0f8ca8ee49b29595a1db556b4a02a7`.
- `UnityPlayer.dll` has SHA-256
  `9e0289b7c0abfc5e21d3b1cc90cda0eae1119ee34dedc013819d1a681968dbb7`
  and identifies itself as Unity `2019.3.1f1`.
- Open Brush `1.0.28` identifies itself as Unity `2019.4.25f1`.
- Both applications ship the same stock `openvr_api.dll` hash,
  `bd7a7958bdb647096e5e22cb4d020dd99720983f3af1cd500e8b570cfa9f017b`.
- SteamVR Tutorial's `boot.config` enables VR and selects `OpenVR`.

The installed Tutorial directory contains remnants from older shim probes. Do
not mutate or normalize that directory in place. Make an APFS clone for each
run, restore the verified stock OpenVR DLL inside the clone, and let the common
native IOSurface runner stage and restore only that clone.

## Hypothesis

If SteamVR Tutorial sustains close to the fake runtime's `90 Hz` deadline while
Open Brush remains near `34 FPS`, Unity 2019 and the shared CrossOver/DXVK/
MoltenVK/IOSurface/encoder path are not generally capped at that rate. The
remaining boundary is Open Brush itself or its separate-eye multi-pass render
path.

If Tutorial also settles near `34 FPS`, treat the slowdown as a shared Unity
2019/OpenVR or CrossOver presentation problem and stop the Freedom acceptance
run until that common boundary is understood.

Tutorial previously submitted one side-by-side texture, while Open Brush uses
two separate eye textures. A fast Tutorial result therefore narrows the problem
but does not independently prove that every Unity multi-pass application is
fast.

## Plan

1. Run the sterile VR stack cleanup and confirm no game, bridge, or probe
   process remains.
2. Clone the official SteamVR Tutorial directory into a bounded run workspace,
   install the verified stock OpenVR DLL in the clone, and verify the executable,
   Unity player, and runtime hashes.
3. Reuse the production native IOSurface runner in disconnected mode for `900`
   pacing calls. Keep the fake runtime deadline pacing at `90 Hz`, request
   `1080x1344` per eye to match the maintained Open Brush profile, and record the
   submitted texture layout and dimensions before interpreting cadence. Treat
   the common native verdict as an expected failure because Tutorial's RGBA
   same-texture path is not accepted by the BGRA pool fast path.
4. Run a fresh Open Brush `1.0.28` control in the same session with the same
   per-eye target, encoder output, native bridge, and graphics stack. Compare
   both final `300`-frame producer windows rather than relying only on an older
   artifact.
5. Route only the resulting direction change to issues #41, #53, and #36. Keep
   commands, logs, hashes, counters, and timing details in this probe ledger.

## Decision Gate

- **Application-specific:** Tutorial's final `300` frames sustain at least
  `60 FPS` while the fresh Open Brush control remains at or below `40 FPS`, the
  Tutorial source contract and cleanup pass, and Open Brush has no producer,
  pool, or native drops.
- **Shared Unity boundary:** final `300` frames remain at or below `40 FPS` with
  clean submission, conversion, and encode counters.
- **Observer effect:** both applications remain at or below `40 FPS` until a
  shared diagnostic or runtime behavior is removed, then improve materially
  without changing either official binary.
- **Ambiguous:** cadence is between `40` and `80 FPS`, the application never
  reaches continuous VR submission, or startup/UI behavior dominates the
  sample. Do not download another game until the ambiguity is identified.

## Expected Artifacts

Preserve the bounded run under
`.code/probes/012-steamvr-tutorial-unity-performance-control/` with:

- exact source and staged hashes;
- launch arguments and environment;
- submit-shim, fake-runtime, DXVK, MoltenVK, and native-bridge logs;
- producer cadence and final-window cadence;
- submitted texture format, dimensions, bounds, and eye-layout classification;
- the expected failed common native verdict plus the target-specific control
  verdict;
- pool submission, release, encode, and drop counters; and
- restored-state checksums.

## Failure Signatures

- No continuous `IVRCompositor::Submit` stream: Tutorial did not progress past
  its startup UI, so the run is not a performance result.
- Source dimensions or eye layout differ from the recorded contract: stop and
  update the runner inputs before comparing cadence.
- Producer cadence is low while submit capture, IOSurface conversion, or encode
  time is high: isolate that measured shared stage before blaming Unity.
- Producer cadence is low while the shared stages remain sub-millisecond and
  drop-free: the application or graphics presentation path is the boundary.
- Any installed SteamVR file changes after the run: cleanup failed and the
  result is invalid.

## Evidence

### Matched Pre-Fix Control

SteamVR Tutorial artifact
`.code/probes/012-steamvr-tutorial-unity-performance-control/real-native-encode-20260716T105815Z`
used the official hashes above and submitted a `2160x1344`
`DXGI_FORMAT_R8G8B8A8_TYPELESS` texture with split left/right bounds. Its final
`300` calls took `9.618` seconds, or `31.192 FPS`.

The native bridge did not receive these frames because the production pool's
same-texture fast path accepts BGRA, while Tutorial submits RGBA. That makes the
artifact valid producer-cadence evidence but not an IOSurface or encode result.
The installed Tutorial tree hash was identical before and after the run.

The same-session Open Brush artifact
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260716T110421Z`
submitted, encoded, and released `900/900` frames with no drops. Its final
`300`-frame window reached only `38.182 FPS`. Two Unity 2019/OpenVR applications
therefore reproduced the low cadence before their texture layouts diverged in
the production handoff path.

### Observer-Effect Root Cause

The fake OpenVR runtime's diagnostic `log_line()` opened
`Z:\\tmp\\fake_openvr_real.log`, appended one line, and closed the file for
every logged API call. Unity queried compositor, tracked-device, and input state
many times per frame. The pre-fix Tutorial run generated a `42 MB` runtime log
and `2700` pacing calls while repeatedly paying that synchronous open/write/
close cost.

The runtime now keeps one locked `1 MiB` buffered file open, preserves every
diagnostic line, flushes every `256` lines, and flushes pacing summaries and
shutdown explicitly. No game binary or graphics setting changed.

### Post-Fix Controls

Hardened Tutorial artifact
`.code/probes/012-steamvr-tutorial-unity-performance-control/real-native-encode-20260716T113326Z`
used the same official binary, fake render target, and graphics stack. The
target-specific runner stopped only after the flushed `900`-call pacing summary,
verified both split-bound `2160x1344` RGBA submissions, and recorded a
`90.000 FPS` final `300`-call window. `control-verdict.txt` reports `pass`; the
common native verdict remains the expected `fail` because no RGBA same-texture
frame enters the BGRA pool path. The installed Tutorial tree hash was unchanged,
the cloned workspace was removed, and restoration passed.

The first post-fix Open Brush artifact
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260716T111010Z`
reached `89.094 FPS` in its final window but recorded one startup pool
exhaustion at submit sequence `14`. The clean repeat artifact
`.code/probes/011-open-brush-controller-smoke/real-native-encode-20260716T111208Z`
passed: `900/900` frames submitted, encoded, and released, zero producer or
native drops, `89.925 FPS` in the final `300` frames, and `520 us` average native
conversion. OpenVR, MoltenVK, staged graphics files, the cloned Tutorial tree,
and the probe lock all restored cleanly.

## Cleanup

The probe must shut down the dedicated `Steam` bottle before and after the run,
restore CrossOver MoltenVK by checksum, remove staged DXVK and bridge files,
delete the cloned Tutorial workspace, remove the probe lock, and leave the
installed SteamVR Tutorial directory byte-for-byte unchanged.

## Current Status

`cadence-discriminator: passed; native-handoff: expected-fail` on July 16, 2026
UTC. The approximately `34 FPS` ceiling was a probe observer effect caused by
synchronous fake-runtime logging, not an inherent Open Brush, Unity 2019,
CrossOver, IOSurface, or encoder limit. The target-specific Tutorial control now
terminates reproducibly with a passing cadence verdict while preserving the
expected RGBA-to-BGRA native-format failure.

No second download was warranted to answer this probe's performance question.
That historical decision does not replace issue #59's separate packaged-runtime
generality gate. This probe is complete and remains a performance discriminator,
not a supported gameplay target.
