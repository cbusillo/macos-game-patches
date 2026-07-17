# Reproducible Mac ALVR Runtime v1 Scope

This document freezes the product and technical boundary established by GitHub
issue #57 on July 17, 2026. GitHub issue #56 remains the source of truth for
execution status and dependencies. This file records the durable contract, not
the live plan state.

## Goal

The v1 runtime lets one technical owner run curated, unmodified Windows OpenVR
games through CrossOver on one qualified Apple Silicon Mac and stream them to
one Apple Vision Pro through ALVR. The accepted experience is clear, smooth,
world-locked, controller-driven gameplay with deterministic recovery and exact
cleanup.

The current runtime is a custom OpenVR implementation built from
`fake_openvr_real.cpp` and `openvr_submit_shim.cpp`. It does not run the real
SteamVR compositor. Real SteamVR, OpenXR translation, and direct Vulkan capture
are later architecture questions and are not part of the v1 claim.

## Product Boundary

### Primary Operator

The initial product is single-machine and single-signing-identity:

- one technical owner;
- one qualified Mac and one dedicated CrossOver Steam bottle;
- one Apple Vision Pro with the locally signed ALVR client;
- PS VR2 Sense controllers paired directly to Vision Pro;
- user-owned CrossOver, Steam, game licenses, Apple development tools, and
  signing credentials;
- a CLI-first workflow with manually authored game profiles; and
- manual ALVR client launch on Vision Pro when requested by the host.

Supporting a second user, Mac, signing identity, headset, or CrossOver version
requires explicit requalification. Public distribution is not implied.

### Planned Operator Journey

The artifact `check`, `validate`, `build`, `verify`, `compare`, and read-only
`plan` verbs now exist as the issue #58 implementation documented in
`reproducible-runtime-artifact.md`. Installation, doctor, lifecycle, and
uninstallation remain planned product verbs; the current research runner
performs their equivalent orchestration.

1. Install the user-owned prerequisites and restore the exact pinned source
   checkouts.
2. Validate and build the immutable unsigned runtime payload from the checked-in
   manifest and lock, then seal it as a separate signing step.
3. Run `install` and `doctor`; no mutable game or bottle state is committed
   unless every preflight passes.
4. Open ALVR on Vision Pro when prompted.
5. Run `start <profile>` and confirm `status` reaches `streaming`.
6. Play, inspect health when needed, then run `stop`.
7. Run `uninstall` to remove every owned service, lock, registration, staged
   file, and mutable session artifact while restoring original hashes.

## Qualified Baseline

The matrix below records the only physically qualified configuration. It is a
pin, not a minimum-version promise.

<!-- markdownlint-disable MD013 -->

| Component       | Qualified state                                                   | v1 support statement                                              |
| --------------- | ----------------------------------------------------------------- | ----------------------------------------------------------------- |
| Host            | Mac Studio `Mac16,9`, M4 Max, 128 GB                              | Other Apple Silicon Macs are unqualified                          |
| macOS           | 27.0 beta, build `26A5378n`                                       | Exact build until another build passes the full matrix            |
| CrossOver       | 26.2.0 build `39821`                                              | User-supplied; dedicated `Steam` bottle                           |
| GPTK            | 4.0                                                               | Supplied through the qualified CrossOver installation             |
| Xcode           | 27.0 build `27A5194q`                                             | Build/install prerequisite, not a steady-state runtime dependency |
| visionOS        | 27.0 beta, build `24M5316k`                                       | One physical Apple Vision Pro                                     |
| ALVR protocol   | `21.0.0-dev12`                                                    | Exact host/client protocol pin                                    |
| ALVR host       | `cbusillo/ALVR@229e8ced76be9b62307fe79690229c5e6bc020d5`          | Production native IOSurface lifecycle                             |
| visionOS client | `cbusillo/alvr-visionos@171cd9dca5ef85c9dfd9f35c565c265c08e8ce82` | Locally signed device build                                       |
| Client core     | `cbusillo/ALVR@109643c88e402b36766020b8f6a99ea48aa8d55f`          | Branch `visionos-client-mdns-c5d8bd26`                            |
| DXVK            | CrossOver DXVK 1.10.3 plus `dxvk-1.10.3-freedom-macos.patch`      | Local build; binary is not committed                              |
| MoltenVK        | CrossOver 26.2 source plus `moltenvk-freedom-geometry-mesh.patch` | Local build with Metal argument buffers disabled                  |
| Input           | PS VR2 Sense through the ALVR `PSVR2Sense` profile                | Other controllers and hand tracking are unqualified               |
| Primary title   | Freedom Locomotion VR, Steam app `584170`, local build `1797135`  | Existing physical acceptance baseline                             |

<!-- markdownlint-enable MD013 -->

The installed Vision Pro app reports marketing version `20.14.5`, build `3`,
while its patched client core negotiates protocol `21-dev12`. Source commits and
protocol identity are authoritative; the marketing version alone is not.

### Patch Pins

- `patches/crossover-dxvk/dxvk-1.10.3-freedom-macos.patch`:
  `06be780bd80241a8a682a907ef15f98b5034fab428510c38b525934727d7c497`
- `patches/crossover-moltenvk/moltenvk-freedom-geometry-mesh.patch`:
  `f02212a4fcc2f077e33961cc22ee09efb846f8cf8ab9ef1fed67f630ddb9df43`
- `patches/alvr-visionos/alvr-v21-client-core-abi.patch`:
  `17ef018b90b8658d74fb36d4e7be8585009cc23c65352cb37c7d83c18085f01d`
- `patches/alvr-visionos/alvr-open-brush-startup-liveness.patch`:
  `3ce4ee530ab52c049d3d494970106c24e42fcbab0c4bbfcd9c4bd76f64983b5c`

These hashes pin the checked-in review artifacts. The qualified fork commits
remain the executable source identity; the artifact manifest must separately
record all generated binary hashes.

## Capability Inventory

### Runtime Candidates

The owner and cleanup column describes intended v1 behavior. Today the focused
runner in `tools/run_real_native_iosurface_probe.sh` owns equivalent cleanup and
restoration checks.

<!-- markdownlint-disable MD013 -->

| Component                      | Role                                                                     | Supply class                                           | Owner and cleanup                                                       |
| ------------------------------ | ------------------------------------------------------------------------ | ------------------------------------------------------ | ----------------------------------------------------------------------- |
| `alvr_macos_bridge`            | IOSurface receive, Metal conversion, VideoToolbox encode, ALVR transport | Build from pinned ALVR source                          | Runtime artifact; stop and uninstall remove the sealed bundle           |
| OpenVR shim and custom runtime | Capture `Submit`, pace poses, feed controllers, publish IOSurfaces       | Repository source                                      | Runtime artifact; restore the game's original OpenVR DLL exactly        |
| Wine bridge DLL/unixlib        | Cross the Wine/macOS boundary and transfer Mach rights                   | Repository source built in the pinned CrossOver tree   | Runtime artifact; restore or remove staged CrossOver source/build files |
| Patched DXVK                   | D3D11 capture and IOSurface publication                                  | Build from CrossOver DXVK source plus repository patch | Runtime artifact; restore original game DLLs by hash                    |
| Patched MoltenVK               | Vulkan-to-Metal implementation required by the DXVK path                 | Build from CrossOver source plus repository patch      | Runtime artifact; restore CrossOver's original dylib by hash            |
| visionOS client                | Decode, render, tracking, PS VR2 input, mDNS identity                    | Build from pinned sibling source                       | User-installed signed app; host uninstall does not delete it            |
| Launchd Mach service           | Own the native receiver and fixed bootstrap name                         | Generated runtime configuration                        | Runtime owns the job and plist; both must be absent after uninstall     |
| ALVR session/profile           | Trusted client identity and per-game runtime settings                    | Generated mutable state                                | Artifact-local or transactionally backed up and restored                |

<!-- markdownlint-enable MD013 -->

The custom OpenVR runtime has a probe-oriented name but is part of the current
shipping architecture. Issue #59 may generalize it; it may not silently replace
it with a real SteamVR compositor or direct Vulkan capture.

### User-Supplied Inputs

- Apple hardware, local network, and physical play space;
- CrossOver, GPTK, Steam, and official game payloads;
- Xcode, visionOS SDK, Metal toolchain, and a Developer ID identity;
- macOS Local Network consent for the stable signed bridge bundle URL;
- the locally signed ALVR visionOS application; and
- PS VR2 Sense controllers and their visionOS accessory authorization.

These inputs are prerequisites, not redistributable runtime payloads.

### Repository-Owned Sources And Evidence

- bridge, OpenVR, protocol, cleanup, and runner source under `tools/`;
- patch artifacts under `patches/`;
- decision records and probe verdicts under `docs/`; and
- machine-readable workflow and routing facts in `.github/github.json`.

The repository's MIT license applies to original repository material where no
third-party notice says otherwise. Patch files, derived code, generated
binaries, game payloads, Apple credentials, and CrossOver remain subject to
their upstream licenses and associated notice, attribution, and source
obligations. The artifact manifest must record the exact license boundary before
any payload is distributed.

### Probe-Only Scaffolding

The large `run_real_native_iosurface_probe.sh` runner remains validation
orchestration, not the product interface. Synthetic Mach-right probes,
oversized-message probes, standalone D3D tests, source-frame controls, and
artifact-local diagnostics remain evidence tools. Runtime extraction must not
ship every probe merely because the runner currently builds it.

### Generated State

Run logs, backups, shader dumps, ALVR session data, launchd evidence, process
snapshots, and physical-run artifacts stay under gitignored `.code/` paths.
Decisive results must be summarized in the owning probe document, issue, or PR;
owner-only logs are supporting evidence, not the only durable record.

## Frozen Runtime Contracts

The following contracts may be wrapped or extracted but not weakened:

1. Official game binaries are fixed inputs. No custom or maintained game forks.
2. The v1 capture boundary is OpenVR over D3D11 through DXVK. Unsupported
   OpenXR or direct Vulkan paths fail closed without broadening the milestone.
3. The production hot path stays GPU-resident: DXVK/MoltenVK to IOSurface to
   Metal/NV12 to VideoToolbox to ALVR. No full-frame CPU pixel path.
4. The native-owned pool remains bounded, generation-checked, lease-released,
   and initialized before producer traffic. The qualified implementation uses
   three IOSurface slots and six VideoToolbox leases.
5. Geometry is profile-declared and bounded before pool creation. Freedom's
   profile retains a `3240x1800` pool and explicitly accepts its
   `2808x1560` level-load transition.
6. Producer pacing remains rational-deadline 90 Hz. The strict quiet-host gate
   remains `89.5` to `90.5` FPS with zero steady-state producer/native drops.
7. Every encoded frame uses the exact paired render-pose generation. Pose gaps,
   stale generations, or mismatched identities fail validation.
8. OpenVR submission remains fail-open for the game while the sidecar transport
   fails closed and reports the reason.
9. The per-user launchd service retains kernel audit-trailer sender
   authentication, send-once reply validation, session nonce, slot identity,
   generation checks, and live PID/signature verification.
10. The already-authorized stable signed bundle URL remains fixed until #62
    proves a consent-preserving replacement.
11. Stop, crash, partial startup, and uninstall restore original hashes and
    leave no owned job, plist, lock, process, staged game file, or bridge state.
12. Physical Vision Pro observation and PS VR2 Sense gameplay remain required;
    synthetic or simulator evidence cannot replace them.

Loaded-host cadence artifacts are diagnostic evidence, not permission to lower
the strict gate. The runtime should surface contention while qualification runs
on a declared quiet host.

## Second-Title Decision

The Lab is selected as the next generality qualification title. It is not yet
part of the qualified runtime matrix.

Valve publishes The Lab as free, VR-only, tracked-controller software under
Steam app `450390`. Valve's published Lab renderer uses Unity and adaptive
quality that changes rendering resolution to maintain VR cadence. Its hub,
transitions, and interaction-heavy experiences exercise materially different
rendering and controller behavior from the Unreal Engine 4 Freedom baseline.

Open Brush 1.0.28 and SteamVR Tutorial remain frozen reference evidence rather
than the release generality title. Open Brush already completed its controller
and logger-discriminator purpose, requires an archived OpenVR build, and was not
run through the future packaged-runtime interface. SteamVR Tutorial is a cadence
control with an expected native-format failure, not a gameplay target. Counting
either one would avoid the new compatibility evidence that issue #59 exists to
collect.

The local Steam manifest records build `7242747`, but the 16.6 GB payload is not
present in the bottle as of July 17, 2026. Qualification begins by restoring the
official payload and recording its installed build, depot identity, executable
layout, graphics API, and OpenVR DLL before modifying runtime state.

### Qualification Boundary

- Verify that the hub and at least two meaningful interactive experiences use
  the supported D3D11/OpenVR path.
- Exercise one hub-to-experience transition, adaptive geometry behavior, and
  tracked-controller gameplay.
- Reuse the packaged runtime; after any general runtime fix, the repeat run may
  change only profile and launch metadata.
- Run the disconnected contract gate before physical Vision Pro validation.
- Preserve the existing cadence, pose, transport, controller, recovery, and
  teardown gates.
- Keep the title out of routine CI because of payload size and physical-hardware
  requirements.

### Hard Cut Lines

Do not add direct Vulkan capture, a real SteamVR compositor, game binary
patches, a maintained game fork, anti-cheat bypasses, or title-specific changes
to the native frame protocol to make The Lab pass. An individual experience
that is direct-Vulkan-only may be excluded if the hub and two qualifying
D3D11/OpenVR experiences pass. If the hub itself cannot run inside this
boundary, the active GitHub plan must select a replacement rather than expanding
v1 silently.

Primary references:

- [The Lab Steam record](https://store.steampowered.com/app/450390/The_Lab/)
- [Valve's Lab renderer](https://github.com/ValveSoftware/the_lab_renderer)

## Failure And Remediation Contract

<!-- markdownlint-disable MD013 -->

| State                                       | Required behavior                                       | Remediation boundary                                                              |
| ------------------------------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Missing or mismatched input                 | `doctor` reports the exact pin and performs no mutation | Restore the documented version or rebuild the manifest                            |
| Signature or live identity mismatch         | Refuse startup and preserve evidence                    | Replace only the owned sealed artifact; never signal an unverified PID            |
| Local Network pending or denied             | Wait without crashing and identify the stable bundle    | Grant access to the same URL; do not move or rewrite the authorized bundle        |
| Vision Pro absent, locked, or client closed | Enter a visible waiting state                           | Unlock the device and manually open ALVR; no steady-state `devicectl` requirement |
| Unsupported game API or geometry            | Keep the game fail-open and sidecar fail-closed         | Correct profile data or mark the title unsupported; do not widen v1 architecture  |
| Pool pressure or pose gap                   | Bound the event and fail the qualification gate         | Preserve counters and diagnose; never hide or average away the drop               |
| Host contention                             | Report contention without changing thresholds           | Re-run the release gate on the declared quiet host                                |
| Crash or partial startup                    | Run owned cleanup and preserve the journal              | Restore exact hashes before another start                                         |
| Failed restoration                          | Refuse another install/start                            | Require explicit repair from the recorded backups and hashes                      |
| Version drift                               | Mark the configuration unsupported                      | Pin or complete the full requalification sequence                                 |

<!-- markdownlint-enable MD013 -->

## Evidence And Cleanup Ownership

- Build evidence owns source commits, patch identities, normalized manifests,
  binary hashes, architectures, and signatures.
- Disconnected evidence owns source geometry, submissions, pool lifecycle,
  conversion, encode, release, and cleanup without a headset.
- Physical evidence owns negotiated output, transport, decode delivery, exact
  poses, controllers, visual quality, reconnect, and headset lifecycle.
- Transaction evidence owns pre/post hashes, operation journals, launchd and
  Launch Services state, locks, processes, and idempotent rollback.
- The probe ledger owns durable vocabulary and run-record shape. New live title
  work starts with a new probe document before new scripts are added.

No run is accepted solely from chat or headset commentary. Human observation
must be paired with the required machine evidence.

## Patch And Version Policy

- Pin exact commits, game builds, patch files, and generated payload hashes. Do
  not use floating `latest` references in a qualified manifest.
- Keep active upstream work in sibling forks. This repository stores patch and
  evidence artifacts, not vendored source trees or generated binaries.
- A change to macOS, visionOS, CrossOver, DXVK, MoltenVK, ALVR, Xcode, signing
  identity, game build, or stable bundle URL invalidates the affected support
  claim until requalified.
- Requalification proceeds in order: build/static checks, disconnected frame
  contract, physical streaming/controllers, recovery, soak when applicable,
  then exact cleanup.
- Security-required upgrades may disable a pin immediately, but they do not
  inherit the old compatibility claim without evidence.
- Prepare upstreamable patches after the packaged runtime proves the boundary;
  do not block v1 on upstream acceptance.

## Explicit Non-Goals

- real SteamVR compositor operation;
- OpenXR-only titles or direct Vulkan capture;
- public, notarized, or App Store distribution;
- bundled CrossOver, Steam, game payloads, or Apple credentials;
- GUI management, automatic updates, or telemetry;
- automatic game-profile or geometry discovery;
- Vision Pro hand tracking or controllers other than PS VR2 Sense;
- anti-cheat or DRM bypass;
- multi-user, multi-Mac, multi-headset, or unattended operation; and
- compatibility claims outside the exact qualified matrix.

## Requalification Triggers

Update this contract and the GitHub issue graph when evidence changes the
product boundary, supported API, runtime architecture, title selection, signing
model, or cleanup ownership. Routine implementation progress belongs in GitHub
issue #56 and its sub-issues rather than this document.
