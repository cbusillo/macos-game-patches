# Aircar Packaged-Runtime Qualification

## Goal

Determine whether the official unmodified Steam build of Aircar can become the
third positive OpenVR/D3D11 title using only a curated profile and the existing
sealed Mac ALVR runtime.

Issue routing: #101, under compatibility tranche #59.

## Hypothesis

Aircar should not require a new graphics or VR architecture. The installed
payload is a compact Unreal Engine title whose shipping executable imports
`d3d11.dll`, whose engine tree contains OpenVR 1.0.16, and whose checked-in
SteamVR action manifest includes Touch, Index, Vive, WMR, and gamepad bindings.

The hypothesis is alive only if the stock OpenVR DLL can be replaced at the
declared engine path, the shipping process can be owned exactly, visible frames
reach the existing IOSurface path, PS VR2 Sense actions are usable, and cleanup
restores the exact Steam payload.

## Environment And Pinned Payload

Recorded July 30, 2026:

- Steam app: `1073390` (`Aircar`)
- build: `4505210`
- depot: `1073391`
- depot manifest: `1310052055377678008`
- installed bytes: `892980026`
- files: `38`
- projected stock tree SHA-256:
  `d7f8199a4c0649b8acfcec5d859f61373848ab4949b77618c91601c98bb32dbf`
- launch executable: `Aircar.exe`
- owned process: `Aircar/Binaries/Win64/AirCar-Win64-Shipping.exe`
- graphics directory: `Aircar/Binaries/Win64`
- stock OpenVR directory:
  `Engine/Binaries/ThirdParty/OpenVR/OpenVRv1_0_16/Win64`
- stock OpenVR SHA-256:
  `040cdd453d8794e1b8b7ee33909b81d9995e8a3ddb82878c0f0c2d3a1daae160`

The second OpenVR SDK copy under the marketplace plugin is payload evidence,
not the initial substitution target. If the first probe never reaches the
custom runtime, verify the loaded module path before changing the profile.

## Plan

1. Check the canonical profile and exact Steam payload identity.
2. Run artifact and profile hardware-free self-tests.
3. Run a 300-frame local-window smoke probe with no AVP connection.
4. Run the 5,400-frame disconnected cadence and cleanup gate.
5. Run the 81,000-frame physical Vision Pro gate and controller checklist.
6. Repeat from the same sealed runtime and verify exact stock restoration.

Do not add Aircar-specific runtime branches before module-load, frame, or input
evidence proves a shared contract is insufficient.

## Reproducible Commands

Set the sealed artifact path explicitly:

```bash
artifact=/absolute/path/to/mac-alvr-runtime-1.0.0-*/
python3 tools/runtime_profile.py check aircar
python3 tools/runtime_profile.py preflight \
  --profile aircar --artifact "$artifact" --mode smoke
python3 tools/runtime_profile.py probe \
  --profile aircar --artifact "$artifact" --mode smoke
```

After the smoke result is recorded:

```bash
python3 tools/runtime_profile.py probe \
  --profile aircar --artifact "$artifact" --mode disconnected
python3 tools/runtime_profile.py probe \
  --profile aircar --artifact "$artifact" --mode physical
```

## Expected Artifacts

Each probe writes under:

```text
.code/probes/013-the-lab-profile-qualification/aircar/
```

Record the generated preflight JSON and real-native run directory, including
module-load logs, submitted-frame contracts, cadence, drops, pose pairing,
client state when connected, exact process identity, and cleanup restoration.

## Physical Controller Checklist

- right trigger applies forward thrust;
- left trigger applies reverse thrust;
- right thumbstick controls pitch and roll;
- left thumbstick controls vertical movement and yaw;
- turbo, menu interaction, and music pause actions are reachable;
- recenter/menu behavior does not strand the process;
- left and right haptics are recorded when the title emits them.

## Cleanup

The probe must finish with:

```bash
python3 tools/vr_stack_cleanup.py
python3 tools/runtime_profile.py preflight \
  --profile aircar --artifact "$artifact" --mode smoke
```

The second preflight must see the exact stock OpenVR hash, no staged DXVK or
bridge DLLs, no runtime logs below the projected payload, and no owned process.

## Known Failure Signatures

- `preflight.hash`: wrong or modified stock OpenVR DLL.
- `preflight.payload`: Steam update, residual runtime file, or incomplete
  cleanup changed the projected tree.
- no custom-runtime calls: the marketplace plugin OpenVR copy may be the loaded
  module; prove the module path before adding a second target.
- shipping process timeout: the bootstrap executable or process pattern is
  wrong, or a prerequisite dialog intercepted launch.
- OpenVR interface error: Aircar requests an interface not yet implemented by
  the custom runtime.
- black, flashing, or mono output: source geometry or Unreal texture semantics
  differ from the existing D3D11 contract.
- action-manifest or binding failure: the title depends on SteamVR Input
  behavior beyond the current PS VR2 Sense mapping.

## Current Evidence

The official payload installed successfully on July 29, 2026. Static inventory
confirms an x86-64 Unreal shipping executable, direct `d3d11.dll` and `dxgi.dll`
imports, OpenVR 1.0.16, SteamVR Input actions, two tracked controller poses,
dual thumbsticks, analog forward/reverse thrust, buttons, and bilateral
haptics. No game binaries have been modified.

### Shared Compatibility Repairs

Aircar exposed two shared runtime assumptions rather than a title-specific
branch:

- The fake compositor returned length `1` for an empty Vulkan instance or
  device-extension string. Aircar's Unreal/DXVK path treated that as one blank
  extension and failed Vulkan instance creation. Returning `0` for no required
  extensions fixed startup. The corrected x86-64 PE runtime SHA-256 is
  `57bcf6160d2e94b372ebf399611be2df282c952caaa80f847035a46413985476`.
- Aircar creates `*_d3d9.log` beside its launcher even though rendering uses
  D3D11. Profile preflight, log archival, restoration, and final absence checks
  now cover D3D9 logs alongside D3D11 and DXGI logs.

No Aircar executable, asset, or checked-in binding was patched.

### Automated Qualification

- Smoke run
  `.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T014246Z`
  submitted and encoded `300/300` frames, reached `90.000` FPS in the final
  window, recorded zero producer/native drops, and restored exact stock state.
- Disconnected run
  `.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T015049Z`
  submitted and encoded `5,400/5,400` visible frames, reached `90.006` FPS in
  the final window, recorded zero producer/native drops, and restored exact
  stock state including D3D9 log cleanup.
- Connected run
  `.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T020657Z`
  encoded and transported `81,000/81,000` frames, reached `89.979` FPS in the
  final window, recorded zero producer drops, pool exhaustion, pose-generation
  gaps, decoder errors, or decoder resets, and restored host/device state. Six
  frames arrived before the ALVR sink-connected event and were classified as
  startup `not_ready` drops.
- Repeat connected run
  `.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T022429Z`
  encoded and transported `81,000/81,000` frames, reached `90.006` FPS in the
  final window, paired `80,999` exact poses with one bootstrap fallback,
  recorded zero producer drops, pool exhaustion, pose-generation gaps, decoder
  errors, or decoder resets, and restored host/device state. Eleven frames were
  startup `not_ready` drops before `alvr_sink connected epoch=1`.

The repeated connected evidence establishes the Aircar frame, encode,
transport, decoder, pose, cadence, client-stop, and cleanup paths. The only
strict automated-gate miss is the shared host startup race: the native source
created the ALVR sink only after producer self-tests and immediately released
the producer barrier.

### Sink-Startup Candidate

ALVR PR #8 merged the sink-startup repair as
`256940512454ab0dabe07ee90675d4d9188faf5c`. It starts the ALVR sink before
waiting for the producer handshake, preserving the supported client-absent
waiting state while giving an already-running Vision Pro client the full game
startup interval to connect. All `12` upstream CI checks passed, including the
macOS, Linux, Windows, Android, MSRV, license, test, and artifact-build lanes.
A clean release rebuild from the exact merge produced bridge SHA-256
`d0235ae91833c556bed6339fd8c8626603f5c59dd63eabd8d8f9cb54c909aeac`
and CDHash `c0436a85cc6d4f226e2bfa432a4fa79a3dfc800f`.

The physical ordering run used precursor candidate
`f5de372eb9c44d6507e0b4b215fec4bc72f7bd8b` in sealed artifact
`.code/runtime-aircar-patched-bridge-sealed/mac-alvr-runtime-1.0.0-dev10-e0df564915760ed164b5569de21178e0b95d62f1c83cdbc2867346e6c81d3324`.

Physical repeat
`.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T030646Z-patched-bridge`
proved the new ordering: `native_source ALVR client telemetry enabled` appears
before the producer handshake. macOS then rejected the rebuilt bridge's manual
client route with `No route to host (os error 65)`, so no client or sink
connection event occurred. The bounded gate stopped the run and restored the
game, bottle, launchd job, stock DLL, staged files, logs, shared memory, and
stable qualified bridge exactly. This is consistent with Local Network consent
being tied to the rebuilt code identity; resetting the known-good bridge's
consent was intentionally avoided.

At that stage the verdict was `automated-compatible`, not yet
production-admitted. The rebuilt-identity candidate still required Local
Network consent plus a worn clear/smooth and PS VR2 Sense gameplay check. The
identity-preserved candidate below superseded that blocked path.

Curating the Aircar profile in the development artifact makes the candidate
reproducible; it does not change the production boundary, which remains
Freedom-only. Profile preflight now passes the global zero producer/native-drop
limits into the real runner, and the runner includes them in its common verdict
gate. The earlier connected runs therefore remain useful compatibility evidence
but cannot pass the physical admission gate with their startup `not_ready`
drops.

### Identity-Preserved Windows Candidate

The follow-up kept the already-authorized bridge bundle byte-for-byte at
Developer ID CDHash `1731a67fa327ca7c1576f63a084cc3b39f095b41` and rebuilt
only the Windows OpenVR sidecar from the current source. This separated the shared
startup/controller repairs from macOS Local Network consent and proved that a
runtime payload update does not require a bridge identity change.

- Physical run
  `.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T195747Z`
  passed with `5,400/5,400` submitted, encoded, and transported frames,
  `89.682` FPS in the steady tail, zero producer/native drops, zero pose gaps,
  no decoder errors or resets, exact client stop, and exact host/game cleanup.
  The producer waited for a valid connected stream contract before releasing
  production frames, eliminating all startup `not_ready` drops.
- Worn controller run
  `.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T202919Z`
  proved both PS VR2 Sense controller poses and live Aircar actions. Both
  thumbsticks produced two-axis values, both triggers produced analog values,
  and the menu, pause-music, Turbo, and menu-interaction actions produced
  digital transitions through the exact stable handles recorded in the Aircar
  profile. The user confirmed the controls reached the game. Bilateral haptic
  handles resolved, but the title did not issue a haptic command during this
  interval.

The worn run also supplied a recovery boundary: visionOS backgrounded the
client after a system-level button press while Aircar continued running. The
client re-entered on stream epoch `3`. The startup-only candidate sent `34`
frames while the client was unavailable; the ongoing producer gate reduced the
same forced terminate/relaunch case to one boundary `not_ready` frame, zero
producer steady-state drops, and automatic resume on epoch `3` in
`.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T205938Z`.
The remaining boundary frame is the event that lets the retained bridge observe
the disconnect; eliminating it would require a bridge-side protocol change and
therefore a new consent-qualified code identity.

The user-induced replacement client PID was intentionally not terminated by
cleanup because it no longer matched the runner-owned PID. Host/game files,
launchd state, shared memory, and stock DLLs still restored exactly. This is a
safe identity refusal, not leaked host state.

### Preserved-Bundle Dev11 Artifact

Runtime artifact `1.0.0-dev11` now supports `preserved-bundle` sealing. It
copies the already authorized app tree without invoking the signer or rewriting
its signed in-bundle attestation, while independently updating the Windows
runtime payload. The final sealed artifact is:

```text
.code/runtime-aircar-preserved-dev11-a92da5d/
  mac-alvr-runtime-1.0.0-dev11-
  180f8dd0f73a1290505b89d0f9c27b4169e0c65e2804b39462d68d694b6b4e56
```

The artifact preserves bridge tree SHA-256
`2f42d727ba5e8588a0c6434761a2460887bb09e728c5b30492e4dc88c691ca24`
and Developer ID CDHash `1731a67fa327ca7c1576f63a084cc3b39f095b41`.
Independent artifact verification passes, and the dry-run plan is ready with
`27` install operations, `15` uninstall operations, and no blockers.

The first artifact smoke exposed a compatibility regression: the ongoing
client-readiness callback was enabled for disconnected runs, so the producer
correctly waited for a client that smoke mode intentionally does not launch.
Commit `84ccf616c9e93d71ca740b002dc259c24bc74a2c` scopes both the startup and
ongoing gate to `ALVR_IOSURFACE_REQUIRE_CLIENT`. The connected recovery path is
unchanged, while disconnected production no longer maps or waits for client
telemetry.

Commit `a92da5da5d20287b024663ed850a3d21091bf0c3` teaches the runtime
control plane to derive the same canonical signed owner marker for preserved
bundles instead of requiring the removed generated-file declaration. The final
artifact's complete `payload/` tree is byte-for-byte identical to qualified seal
`68cb6fdf9fe3544b385c9570a27b4aa4224b17da25172a49e9993b2cdef0d5f2`;
only contract and control-plane provenance changed.

- Payload-equivalent warm smoke
  `.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T223917Z`
  passed with `300/300` submitted and encoded frames, `90.000` FPS in the
  steady tail, zero producer/native drops, clean bridge exit, and exact stock
  restoration.
- Payload-equivalent disconnected qualification
  `.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260730T224010Z`
  passed with `5,400/5,400` submitted and encoded frames, `89.979` FPS in the
  steady tail, zero producer/native drops, zero pose-generation gaps, clean
  bridge exit, and exact stock restoration.

Direct final-seal smoke reruns each still submitted and encoded `300/300`
frames with zero drops, zero pose-generation gaps, clean bridge exit, and exact
restoration. Their short cadence windows were invalidated by unrelated host
contention with load average above `47`; they are retained as functional and
cleanup evidence, not cadence qualification. The longer passing qualification
above remains applicable because every runtime payload byte is identical.

### Final-Seal Owner Acceptance

Final-seal physical run
`.code/probes/013-the-lab-profile-qualification/aircar/real-native-encode-20260731T004915Z`
ran for `52,883` submitted frames and `592,563` ms of producer time before the
owner intentionally ended it. The owner reported smooth, fully usable operation
from the exact dev11 package, with lower apparent resolution as the only quality
concern. Cleanup still completed with `restore_status=0`.

The strict runner result remains recorded as `fail`; it is not rewritten as a
completed `81,000`-frame soak. The interrupted run recorded one boundary
`not_ready` drop, a `89.414` FPS steady-tail sample just below the `89.5` gate,
and a decoder/bridge exit after production stopped. Those facts make this run
owner-acceptance evidence, not the strict automated cadence result.

Production admission instead uses the complete same-payload evidence set:

- the final dev11 payload is byte-for-byte identical to the earlier qualified
  seal;
- physical run `real-native-encode-20260730T195747Z` passed `5,400/5,400`
  submitted, encoded, and transported frames at `89.682` FPS with zero drops,
  zero pose gaps, and exact cleanup;
- worn run `real-native-encode-20260730T202919Z` proved both controller poses,
  both thumbsticks, both triggers, steering/thrust, menu, pause, Turbo, and
  interaction actions;
- reconnect run `real-native-encode-20260730T205938Z` resumed automatically on
  stream epoch `3` with zero producer steady-state drops; and
- the exact final seal received direct owner confirmation of smooth usable
  output before the owner-ended stop.

Current verdict: Aircar is production-admitted as the third positive
OpenVR/D3D11 title. Physical video, cadence, startup, controller input,
reconnect, exact restoration, and frozen preserved-bundle packaging are proven
without a title-specific runtime branch. The owner accepted the composite
evidence and declined another full-duration replay. Haptic output remains
unobserved rather than failed because Aircar emitted no haptic command during
the observed intervals. The softer image is a non-blocking resolution-quality
follow-up under compatibility tranche #59 and profile experiment #109.
