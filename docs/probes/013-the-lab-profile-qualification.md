# The Lab Packaged-Runtime Qualification

## Question

Can the immutable `mac-alvr-runtime` artifact qualify Valve's official,
unmodified The Lab build through reusable game-profile data, without changing
the proven frame, pose, transport, controller, or native handoff protocols?

## Boundary

- Steam app: `450390` (`The Lab`).
- Runtime artifact history: issue #58 artifact `1.0.0-dev1` with seal
  `ccd98aa245ce0f0b` was the initial candidate, and `1.0.0-dev2` first corrected
  its missing production `iosurface` host entrypoint. The current eye/input
  compatibility correction is packaged as `1.0.0-dev5`.
- Host matrix: Mac16,9, macOS 27.0, CrossOver 26.2, and the pinned ALVR and
  ALVR visionOS checkouts in `.github/github.json`.
- Supported title path: official OpenVR plus D3D11 only.
- Required title behavior: hub launch, one hub-to-experience transition, two
  meaningful interactive experiences, bounded declared stereo geometry, and
  PS VR2 Sense gameplay. The current stable profile intentionally fixes the
  source at `1152x1280` per eye instead of enabling adaptive quality.
- Hard cuts: no game binary patches, maintained fork, real SteamVR compositor,
  direct Vulkan capture, anti-cheat bypass, or title-specific native protocol.

## Initial Payload State

On July 17, 2026, the Steam bottle has a stale installed-state record but no
game directory:

- app manifest build: `7242747`;
- depot: `450391`;
- depot manifest: `7552577434957620257`;
- recorded size: `16625763869` bytes;
- expected install directory: `steamapps/common/The Lab`;
- local payload: absent.

The official Steam client must restore or verify the payload before any runtime
file is staged. After restoration, capture the installed build, depot manifest,
executable layout, PE identities, graphics API evidence, every stock
`openvr_api.dll` SHA-256, and the clean payload inventory.

## Reusable Profile Contract

The checked-in profile must declare and validate, without automatic discovery:

- Steam app, build, depot, and depot-manifest identity;
- bottle, install root, executable, working directory, arguments, environment,
  process matching, startup timeout, and transition processes;
- one or more OpenVR hook directories and D3D11/DXGI injection directories;
- supported graphics API and runtime selection;
- maximum source geometry, allowed source transitions, stereo eye mapping, and
  target geometry;
- controller profile and required gameplay checks;
- cadence, drop, pose-pairing, startup, recovery, and teardown thresholds; and
- stock-file hashes that must match before mutation and after cleanup.

Freedom and The Lab must both validate against the same schema. The second run
may change profile and launch metadata only; it may not rebuild or alter the
artifact payload.

## Procedure

1. Capture the stale Steam metadata and a pre-restore bottle/process snapshot.
2. Restore app `450390` through the official Steam client and capture the exact
   installed payload identity before runtime mutation.
3. Add strict profile validation plus Freedom and The Lab profiles; reject
   unknown fields, unsafe paths, duplicate hook targets, unsupported APIs,
   invalid geometry, and weakened global gates.
4. Verify the issue #58 artifact and resolve a read-only qualification plan for
   each profile against the same artifact seal.
5. Run the disconnected The Lab gate first and preserve source transitions,
   submit cadence, pool lifecycle, conversion, encode, exact pose pairing, and
   cleanup evidence.
6. If disconnected evidence passes, run the physical Vision Pro gate, exercise
   the hub and two experiences with PS VR2 Sense, relaunch or reenter the
   client, and collect the human clear/smooth observation.
7. Repeat from the same artifact using profile-only changes, then verify stock
   hashes and absence of owned jobs, locks, processes, staged files, or bridge
   state.

## Reproducible Commands

The exact profile commands will be recorded in `.github/github.json` when the
tool exists. Existing prerequisites are:

```bash
python3 tools/vr_stack_cleanup.py
python3 tools/build_runtime_artifact.py verify \
  --artifact .code/runtime-final-a/<artifact>
```

All run evidence belongs under
`.code/probes/013-the-lab-profile-qualification/<UTC timestamp>/` and must name
the profile hash, artifact seal, Steam build/depot identity, command, exit code,
and cleanup result.

## Cleanup

Before and after each live attempt:

```bash
python3 tools/vr_stack_cleanup.py
```

The runner must restore every stock OpenVR and shared CrossOver file from its
run-local verified backup, remove only artifact-owned staged files, stop the
launchd job by its live service identity, and remove the runtime lock. Steam's
restored official payload is user-owned and must remain installed.

## Known Failure Signatures

- Steam reports installed while `steamapps/common/The Lab` is absent: stale
  library metadata; restore or verify through Steam before qualification.
- A stock file hash differs before staging: classify as payload/profile data and
  perform no mutation.
- The hub or a required experience uses direct Vulkan: unsupported title path;
  do not add Vulkan capture.
- The hub requires compositor behavior absent from the custom OpenVR runtime:
  architecture ceiling; update #56 and open a separately scoped issue.
- Source dimensions exceed the declared bounded pool: profile data if the
  official maximum was declared incorrectly, otherwise unsupported behavior.
- Cadence below `89.5` FPS, any steady producer/native drop, a pose-generation
  gap, or mismatched frame/pose identity: failed runtime qualification; do not
  weaken the global gates.

## Expected Artifacts

- official payload identity and clean-file inventory;
- canonical Freedom and The Lab profile JSON plus validation output;
- resolved artifact/profile qualification plans;
- disconnected and physical run logs and summaries;
- process, service, lock, staged-file, and stock-hash cleanup reports; and
- a comparison proving both titles used the same artifact seal.

## Runs

### Artifact-Backed Smoke Preflight

Run:
`the-lab/preflight-smoke-20260717T192346Z.json`

Question:
Does the restored official payload resolve safely against the issue #58
artifact without mutation?

Mode / build:
hardware-free preflight; The Lab build `7242747`; artifact seal
`ccd98aa245ce0f0b3c5164d8ab1653e53455eff3f579925f7f1fef1f23bc823f`.

Commands:
`python3 tools/runtime_profile.py preflight --profile the-lab --artifact
<dev1-artifact> --mode smoke`

Expected proof:
Exact app, depot, full tree, critical file, target path, stock OpenVR, and
artifact identities resolve read-only.

Artifacts captured:
The preflight JSON, restored Steam inventory, all 1,665 payload hashes, three
stock OpenVR hashes, launch metadata, and artifact provenance.

Logs checked:
Steam content log, app manifest, artifact verification, PE identities, and
resolved staging targets.

Human observation:
Not applicable.

Verified:
Both The Lab and Freedom profiles passed read-only preflight against the same
seal. The Lab profile resolved the hub, Secret Shop, and Robot Repair targets,
and the restored payload tree matched SHA-256
`26a731b4cdf532724df23eab23eaf863361b3698438cab981d68be3950d75011`.

Inferred:
Profile data and payload restoration are not the current blocker.

Failed / missing:
No runtime process was launched by this preflight.

Unknown:
Runtime compatibility and physical output.

Verdict:
`alive`.

Do not repeat:
Do not weaken payload-tree or stock-file checks to bypass a mismatch.

Next action:
Run the artifact-backed disconnected smoke.

Issue routing:
`#59`.

### Dev1 Artifact Runtime Failure

Run:
`the-lab/real-native-encode-20260717T192452Z`

Question:
Can the sealed issue #58 artifact start the production native IOSurface source?

Mode / build:
300-frame disconnected smoke; The Lab build `7242747`; artifact
`mac-alvr-runtime-1.0.0-dev1`; seal beginning `ccd98aa245ce0f0b`.

Commands:
`python3 tools/runtime_profile.py probe --profile the-lab --artifact
<dev1-artifact> --mode smoke`

Expected proof:
Native startup self-tests, game submissions, 300 encoded frames, strict cadence
and pose gates, and exact restoration.

Artifacts captured:
Artifact verification, payload hashes, signed bridge evidence, launchd state,
native bridge log, backups, and `restored-state.txt`.

Logs checked:
`native-bridge.log`, launchd states, bottle shutdown logs, and restoration
hashes.

Human observation:
Not requested because the disconnected gate failed before game launch.

Verified:
The packaged bridge exited with
`Error: unsupported ALVR_BRIDGE_INPUT=iosurface`. Artifact provenance pinned
ALVR host commit `4bd8ad054a30c3b045f2235ed94b0a4f3cd2b819`; that commit
implements older input names but not the production `iosurface` native-source
entrypoint. The
physically qualified implementation is commit
`229e8ced76be9b62307fe79690229c5e6bc020d5`. Cleanup restored all three stock
OpenVR hashes and stock MoltenVK, removed every staged file, stopped the bottle,
and removed the launchd job and lock.

Inferred:
Issue #58 sealed the wrong ALVR host revision even though its artifact structure
and binary lock were internally valid.

Failed / missing:
No producer frames reached the bridge. This is a general runtime-artifact
defect, not profile data or evidence that The Lab requires a wider architecture.

Unknown:
The Lab compatibility after the corrected artifact is built.

Verdict:
`blocked` on corrected artifact `1.0.0-dev2`.

Do not repeat:
Do not rerun seal `ccd98aa245ce0f0b` with `ALVR_BRIDGE_INPUT=iosurface`, and do
not classify its deterministic rejection as a title-specific failure.

Next action:
Rebuild and reseal the artifact from ALVR host commit `229e8ced`, then repeat
both profile preflights and the disconnected smoke.

Issue routing:
`#59` as a discovered general runtime defect; update `#58` evidence and `#56`
status when the corrected artifact passes.

### Dev2 Hub RGBA Compatibility Failure

Run:
`the-lab/real-native-encode-20260717T193547Z`

Question:
Does the corrected ALVR host artifact accept The Lab hub's official D3D11/OpenVR
submission path?

Mode / build:
300-frame disconnected smoke; The Lab build `7242747`; artifact
`mac-alvr-runtime-1.0.0-dev2`; seal beginning `4f876d46c0b98178`.

Commands:
`python3 tools/runtime_profile.py probe --profile the-lab --artifact
<dev2-artifact> --mode smoke`

Expected proof:
Producer handshake, three startup self-tests, 300 encoded frames, strict
cadence and pose gates, and exact restoration.

Artifacts captured:
Artifact verification, target-process event, game launch log, DXVK logs,
OpenVR shim and fake-runtime logs, bridge log, and restoration report.

Logs checked:
`openvr-submit-shim.log`, `fake-openvr.log`, `native-bridge.log`, The Lab DXVK
logs, process events, and `restored-state.txt`.

Human observation:
Not requested because the disconnected producer handshake did not complete.

Verified:
The corrected host accepted `ALVR_BRIDGE_INPUT=iosurface`, checked into the
launchd Mach service, and waited for the producer. The official hub loaded the
artifact's local OpenVR shim and D3D11/DXVK path, queried the declared
`1152x1280` per-eye recommendation, and submitted one side-by-side
`2304x1280` `DXGI_FORMAT_R8G8B8A8_TYPELESS` texture with left/right bounds. The
producer did not initialize because the proven transfer accepted a
single-texture BGRA source or separate-eye RGBA sources, but not a
single-texture RGBA source. Cleanup restored all stock hashes and removed all
owned state.

Inferred:
The hub's fixed-size RGBA submission is a general runtime compatibility gap. It
does not require Vulkan capture, a real compositor, title-specific protocol, or
unbounded allocation.

Failed / missing:
The producer handshake and frame gates did not run.

Unknown:
Whether later hub and experience transitions expose additional compatibility
gaps after RGBA conversion succeeds.

Verdict:
`alive` with a bounded general runtime fix.

Do not repeat:
Do not reinterpret the RGBA channel order as BGRA or bypass pixel validation.

Next action:
Allow supported single-texture RGBA images to use the existing bounded Vulkan
blit into the BGRA IOSurface pool, exercise that conversion in one of the three
startup self-tests, rebuild the same dev2 artifact contract, and repeat the
smoke.

Issue routing:
`#59` general runtime defect.

## Status

`partial-pass`: the custom runtime now produces visible The Lab eye imagery in
disconnected and physical runs. The black-eye architecture ceiling is removed.
Issue #67 remains open because PS VR2 Sense gameplay and repeatable physical
steady-tail cadence have not both passed in one acceptance run.

### Pre-Correction Disconnected Classification

Artifact `1.0.0-dev4`, seal
`805d6ca7d57b42145cb8a9fd77c347671bda20fe9f7113d020da57152e1d466a`,
encoded all 5,400 requested frames at a `89.925` FPS steady tail with zero
producer/native drops, zero pose-generation gaps, and exact cleanup. Twelve
sparse production samples were all black. The fake runtime recognized the
historical `FnTable:IVRSystem_020`, `IVRSettings_002`, and `IVROverlay_020`
interfaces, so missing those interfaces was not the remaining cause.

The host now treats transient black production frames as valid frame data while
retaining exact startup pixel failures and requiring eventual visible content.
That change is merged in `cbusillo/ALVR` PR #5. The Lab still never became
visible during the one-minute disconnected run, so physical qualification,
controller gameplay, experience transitions, and recovery cannot be claimed.
The physical Vision Pro was unavailable on July 17, 2026; the next discriminator
is a live physical run. If live tracking/focus does not produce visible content,
issue #59 has reached the declared custom-fake-runtime/compositor ceiling and
must not be closed as a successful second-title qualification.

### Compositor-Compatible Eye Submission Correction

The first actionable divergence was OpenVR input bootstrap, not the native
IOSurface or encoder path. The Lab requests both `IVRInput_007` and
`FnTable:IVRInput_007`; the fake runtime stopped at `IVRInput_006`. The exact
27-slot interface adds `GetActionBindingInfo` before the final three methods.
After that ABI became available, the same official game build submitted visible
RGBA eye content instead of the persistently black resource observed by the
pre-correction runs.

The submission path also now preserves each eye's `VRTextureBounds_t` through
the same-texture D3D11 pair and into the Vulkan blit. Cropped eye regions are
validated as disjoint left/right halves, all cropped linear-blit borders are
copied with nearest filtering to prevent sampling outside the declared region,
and full/null bounds cannot be misclassified as side-by-side stereo. The third
startup pixel test uses an inset RGBA source and samples an output corner, so
both channel conversion and crop-edge isolation fail closed before production.

Run:
`the-lab-noaq-msaa2-full/real-native-encode-20260717T224707Z`

Verified:

- The official hub submitted one `2304x1280`
  `DXGI_FORMAT_R8G8B8A8_TYPELESS` texture for both eyes with explicit
  `[0.0, 0.5]` and `[0.5, 1.0]` horizontal bounds.
- All `5400/5400` requested frames were submitted and encoded, eventual visible
  content passed, producer and native drops remained zero, pose-generation gaps
  remained zero, and the final 300-frame producer window was `90.006 FPS`.
- Cleanup restored all three stock OpenVR DLL hashes and stock MoltenVK and
  removed the launchd job, lock, staged files, and transient processes.

Physical discriminator:
`the-lab-visible-physical-full/real-native-encode-20260717T232639Z`

Human observation:
The user confirmed the Vision Pro image was clear. They left the headset before
controller testing, so the subsequent client disconnect and not-ready drops do
not qualify cadence, recovery, or gameplay and are not treated as an acceptance
pass.

### Remaining Input And Cadence Gate

The runtime now exposes The Lab's action manifest through `IVRInput_007`, maps
the declared trigger, grip, pose, thumbstick, menu, tool, movement, and haptic
actions onto the shared PS VR2 Sense state, and reports `HeadsetOnHead` as an
active head-origin action. The profile launches with `-hub` so qualification no
longer waits in the noninteractive Valve logo sequence; `-noaq -msaa 2` keeps a
fixed `1152x1280` per-eye source while retaining 2x MSAA.

Do not infer controller failure from the earlier logo-scene runs: that scene
only queried `HeadsetOnHead`. Conversely, do not claim gameplay from tracked
controller poses alone. A valid completion run must actively supply both hands,
highlight and select a hub destination, exercise trigger and grip in gameplay,
retain at least `89.5 FPS` in the final 300-frame window, and complete cleanup.

The July 18 physical transport-only run
`the-lab-hub-controller-physical/real-native-encode-20260718T001219Z`
transported `16200/16200` frames with zero producer/native drops and zero pose
gaps, but the client supplied fallback tracking and the tail was `89.414 FPS`.
It is useful negative evidence, not an acceptance run.

The post-review hardening run
`the-lab-review-fixes/real-native-encode-20260718T014958Z` passed all three
startup pixel tests, including the cropped RGBA output corner at
`3239,1799`, then submitted and encoded `1800/1800` visible frames with zero
drops, zero pose gaps, and exact cleanup. Its `84.202 FPS` tail is correctness
evidence only and does not replace the required cadence run.

### Sealed Dev5 Post-Merge Qualification

The canonical post-merge profile and sealed dev5 artifact were rerun without
source, profile, artifact, or gate changes. The first attempt in this series,
`the-lab/real-native-encode-20260718T024801Z`, submitted and encoded
`5400/5400` frames with visible-content validation, zero native drops, and zero
pose-generation gaps, but its final 300-frame window reached only `79.521 FPS`.
Exact cleanup passed. This remains preserved negative cadence evidence.

Resolution, MSAA, and logging diagnostics did not justify lowering quality or
weakening the gate. Reduced eye sizes ended between `83.895` and `86.316 FPS`;
the short 2x-MSAA baseline reached `75.448 FPS`; a short no-MSAA run reached
`88.305 FPS`, but the full no-MSAA run fell to `69.795 FPS` and introduced two
steady producer drops. Reducing DXVK logging cut its log volume but produced a
`50.294 FPS` tail on the successful retry; the D3D11 log fell from `34500` to
`278` lines. These were temporary diagnostic variants with relaxed cadence
bounds, not qualification passes.

Two consecutive exact reruns then passed the unchanged `89.5 FPS` gate:

- `the-lab/real-native-encode-20260718T030850Z` submitted and encoded
  `5400/5400` frames with visible-content validation and reached `89.817 FPS`
  over the final 300 frames.
- `the-lab/real-native-encode-20260718T031058Z` submitted and encoded
  `5400/5400` frames with visible-content validation and reached `90.006 FPS`
  over the final 300 frames.

Both passes used artifact seal
`50999eee45412dbc2272159759ad5c046e3264914d7e7728aac87099972b649f`,
reported zero producer/native drops, zero pose-generation gaps, no post-close
submissions, visible-content validation, and exact cleanup. The evidence does
not implicate IOSurface handoff, encoding, artifact identity, geometry, or
cleanup; the remaining variability is in host/game cadence. The fixed
`1152x1280` per-eye source with 2x MSAA remains the canonical candidate profile.
The open acceptance item is still one worn physical run that combines live PS
VR2 Sense gameplay with the same final-window cadence gate.

Reproducible artifact-backed commands after the dev5 artifact is sealed:

```bash
python3 tools/runtime_profile.py probe \
  --profile the-lab --artifact <dev5-artifact> --mode disconnected
python3 tools/runtime_profile.py probe \
  --profile the-lab --artifact <dev5-artifact> --mode physical
python3 tools/vr_stack_cleanup.py
```

Issue routing:
merge the reusable eye/input compatibility correction without closing #67;
then complete #67's physical controller/cadence gate before unblocking #59.

### Beta 4 Automated Qualification Without Human Observation

On July 29, 2026, the Mac and connected Vision Pro were on macOS 27 beta 4
build `26A5388g` and visionOS 27 beta 4 build `24M5326g`. The installed ALVR
client remained version `20.14.5` build `3` with protocol `21-dev12`. Testing
used sealed schema-v5 artifact
`1c5e8f81ee7923d4f50bcfb218f2fa06175331a97dc5526af6b226f865def5a7`.

The first disconnected attempt,
`the-lab/real-native-encode-20260729T205625Z`, failed before game launch. The
research runner could not replace the retained stable bridge because the sealed
bundle tree correctly had read-only directory modes. The production
transaction path already handles cleanup modes, but the research runner still
used a bare `rm -rf`. The focused fix verifies bundle type, ownership, signing
identity, required owner marker, and absence of symlinks before making only its
directories owner-writable and removing the exact owned tree. Development-mode
bundles now receive the same marker before signing.

A follow-up smoke also proved that a full-command-line `pgrep` can mistake an
AI review prompt containing `alvr_macos_bridge` for the bridge itself. The
runner now checks the exact executable basename, preserving the fail-closed
single-bridge guard without coupling test execution to unrelated process text.
After that correction, smoke run
`the-lab/real-native-encode-20260729T215051Z` passed with `300/300` frames,
`90.164 FPS` in its final 100-frame producer window, zero producer/native
drops, and exact cleanup while the review process remained active.

Strict payload preflight then detected Finder-created `.DS_Store` files rather
than weakening the full-tree identity check. The files were removed while
Finder was temporarily paused and automatically resumed after each probe.

Disconnected run `the-lab/real-native-encode-20260729T210501Z` reached the real
data plane and proved:

- `5400/5400` submitted, received, encoded, and released frames;
- visible-content validation;
- zero producer/native drops, pool exhaustion, and pose-generation gaps;
- all three startup self-tests and exact launchd identity checks; and
- exact restoration of stock MoltenVK, all three OpenVR DLLs, staged files,
  launchd state, locks, and processes.

The run remained negative cadence evidence: its final 300-frame window was
`88.227 FPS`, below the `89.5 FPS` gate. Multiple high-CPU coding, game, and UI
workloads were active on the host, so this result requires a quiet-host rerun
and does not identify a rendering or transport regression.

Two connected startup attempts,
`the-lab/real-native-encode-20260729T210918Z` and
`the-lab/real-native-encode-20260729T211117Z`, launched the exact AVP app,
published the expected mDNS identity, negotiated `21-dev12`, connected the
native sink, created the `1440x1792` HEVC stream, and began tracking. Both were
stopped by the strict startup timing gate before the full frame run. Exact
cleanup passed.

The immediate connected retry
`the-lab/real-native-encode-20260729T211234Z` satisfied the startup admission
gate and ran for approximately 15 minutes. Its client and sink markers were
already present in the first post-handshake poll, so the recorded `0 ms` values
mean same-sample observation rather than precise sub-millisecond timing:

- `81000/81000` frames were submitted, encoded, and transported;
- the final 300-frame producer window was `90.006 FPS`;
- producer/native drops, pool exhaustion, and pose-generation gaps were zero;
- `80999` frames used exact shared poses and one bootstrap frame used fallback;
- the client created three decoder instances and one matching format with zero
  decoder errors or resets;
- post-host observation found stream stop with no stale IPD, origin, or format
  diagnostics; and
- the owned AVP client, launchd job, bottle processes, staged files, lock, and
  stock hashes were restored exactly.

The run's only failed gate was the expected multi-target transition: the hub was
seen, but Secret Shop and Robot Repair were not selected because no human eyes
or controller interaction were available. No claim is made for visual clarity,
smoothness, or PS VR2 Sense gameplay.

After the runner hardening, short connected diagnostic
`the-lab/real-native-encode-20260729T220040Z` exercised the updated polling path
against the physical headset with a deliberately reduced frame count and
transition wait. It encoded and transported `900/900` frames, reached
`90.006 FPS` in the final 300-frame window, recorded zero producer/native drops
and pose-generation gaps, created clean decoder/format state, passed post-host
observation, and restored exact host/device state. Its connection evidence
labels both client and sink `0 ms` values as same-sample poll observations. The
diagnostic verdict remained `fail` only because the profile's all-target gate
still saw the hub alone; it is confirmation of the updated automated path, not
a substitute for the worn acceptance run.

Verdict at this point:
`alive` on the beta 4 host/headset pair. Automated rendering, transport, pose,
cadence, client lifecycle, and cleanup evidence pass. Issue #67 still required
one worn run that selected a hub destination, exercised trigger and grip in
gameplay, confirmed clear/smooth output, and repeated exact cleanup. The worn
result below supersedes that remaining-gate statement.

### Worn Hub Acceptance After PR 92

PR #92 merged on July 29, 2026 as
`2315465091cfa1608871a6ccf914044461b09241`. The operator then ran the unchanged
physical The Lab profile from merged `main` with the Vision Pro and both PS VR2
Sense controllers. The retained run is
`the-lab/real-native-encode-20260729T224407Z`.

Human observation and interaction passed the compositor/input gate:

- the Vision Pro image was stable, smooth, and usable;
- the only visual qualification note was softer apparent resolution, with no
  black frames, flashing, warping, or distracting artifacts;
- right-thumbstick turning worked;
- holding the left thumbstick click while aiming at the floor displayed and
  committed teleport locomotion; and
- trigger and grip visibly responded in The Lab.

Retained `IVRInput` telemetry independently recorded teleport, trigger, and
grip state transitions from both hands. Only the hub process ran. Secret Shop
and Robot Repair transitions therefore remain #59's multi-target packaged
runtime work rather than part of the completed #67 eye-submission gate.

The operator ended the worn run after sufficient human validation instead of
waiting for the full `81000`-frame duration. Stopping only the outer
`runtime_profile.py probe` process left the child probe, launchd service, and
game alive; signaling the actual `run_real_native_iosurface_probe.sh` process
then invoked its verified restoration trap. Final state was exact:

- runtime status `stopped` with no transient owned state;
- no native bridge, game, or profile process;
- no launchd job or plist;
- no shared-memory or temporary probe files; and
- no `unexpected-present` restoration entry.

This is a known operator-stop failure signature for #62's lifecycle matrix:
programmatic termination of the profile wrapper must be tested separately from
terminal process-group interruption, and the wrapper should not leave a live
probe child.

Issue #67 closed from combined evidence. Full connected run
`the-lab/real-native-encode-20260729T211234Z` supplies the strict `90.006 FPS`,
zero-drop, zero-pose-gap, decoder, and cleanup evidence; the worn run supplies
the user-observed visual and PS VR2 Sense interaction evidence. The softer
resolution remains a #59/#63 quality follow-up. The current profile's fixed
`1152x1280` per-eye source should be compared with a bounded higher-resolution
candidate before attributing the softness only to the title.

### Dev13 Disconnected Cadence After PR 119

PR #119 merged on July 31, 2026 as
`8afef96d92569717b2cf757abf2780963dd8e3d4`. After Factorio was closed, the
unchanged dev13 artifact and The Lab profile ran two consecutive disconnected
gates from exact stock state:

- `the-lab/real-native-encode-20260731T201317Z`;
- `the-lab/real-native-encode-20260731T201624Z`.

The host was materially quieter than the earlier load-contaminated attempt,
although several Code and Steam helper processes remained active. Both runs
submitted, released, received, and encoded all `5400/5400` frames, validated
visible content, and reached `89.979 FPS` over the final 300-frame window. Both
reported zero producer startup or steady-state drops, zero native drops, zero
pool-exhaustion drops, zero pose-generation gaps, no post-close submissions,
three producer and native startup self-tests, and passing pacing/drop gates.
The second run recorded one bounded producer backpressure wait of `7976 us`
without a drop.

Whole-run producer/native effective rates were `87.703/86.199 FPS` and
`86.822/85.336 FPS`; these include startup and teardown overhead and are not the
profile's steady-tail cadence gate. The disconnected target gate correctly saw
the hub alone and passed. Secret Shop and Robot Repair selection remains part
of the worn multi-target transition session.

Both restoration traps returned status zero. Final runtime status was
`runtime.ready`; the launchd job, owner, plist, lock, shared memory, probe files,
and created game overlays were absent; default cleanup matched no process; and
all three stock OpenVR hashes matched. The dev13 disconnected cadence gate is
qualified. Physical experience transitions remain open under issue #112.
