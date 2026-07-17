# The Lab Packaged-Runtime Qualification

## Question

Can the immutable `mac-alvr-runtime` artifact qualify Valve's official,
unmodified The Lab build through reusable game-profile data, without changing
the proven frame, pose, transport, controller, or native handoff protocols?

## Boundary

- Steam app: `450390` (`The Lab`).
- Runtime artifact: issue #58 artifact `1.0.0-dev1` with seal `ccd98aa245ce0f0b`
  was the initial candidate. The first artifact-backed smoke proved that its
  ALVR host pin lacked the production `iosurface` entrypoint, so qualification
  now requires a `1.0.0-dev2` rebuild from the physically qualified host commit
  `229e8ced76be9b62307fe79690229c5e6bc020d5`.
- Host matrix: Mac16,9, macOS 27.0, CrossOver 26.2, and the pinned ALVR and
  ALVR visionOS checkouts in `.github/github.json`.
- Supported title path: official OpenVR plus D3D11 only.
- Required title behavior: hub launch, one hub-to-experience transition, two
  meaningful interactive experiences, adaptive source geometry, and PS VR2
  Sense gameplay.
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

`architecture-ceiling`: the official payload and reusable profiles pass strict
preflight, and the packaged runtime sustains The Lab at the required cadence,
but it has not produced visible game content.

### Final Disconnected Classification

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
