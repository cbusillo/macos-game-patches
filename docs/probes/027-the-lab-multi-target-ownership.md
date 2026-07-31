# The Lab Multi-Target Runtime Ownership

## Goal

Generalize the packaged runtime from one independently owned Windows producer
to a finite profile-declared target set, then qualify The Lab hub, Secret Shop,
and Robot Repair transitions without weakening exact stop, recovery, rollback,
or cleanup behavior.

Issue routing: #112 under compatibility tranche #59.

## Existing Boundary

The current profile already describes all three The Lab runtime targets:

- `hub`: `TheLab/win64/TheLab.exe`;
- `secret-shop`: `SecretShop/win64/SecretShop.exe`;
- `robot-repair`: `RobotRepair/bin/win64/vr.exe`.

The research probe can stage all three OpenVR and graphics directories and has
proved the hub path. Production remains intentionally fail-closed because:

1. `launch.ownedProcess` represents zero or one steady-state executable.
2. schema-v5 state records one expected and one live owned process.
3. the supervisor discovers, confirms, monitors, and quiesces one identity.
4. the sealed production install plan owns one OpenVR and graphics directory.

The current singleton contract is qualified for Freedom and Aircar and must not
change for those profiles merely to admit The Lab.

## Contract Decisions

### Profile Ownership

Add optional `launch.ownedTargets`, a non-empty unique list of
`runtime.targets[].id` values.

- Existing `launch.ownedProcess` objects remain the singleton contract.
- `ownedTargets` is valid only when `ownedProcess` is `null`.
- Every referenced target supplies its already sealed executable and process
  pattern; the launch block does not duplicate either value.
- A profile with neither declaration remains valid for research and
  classification, but production `start` continues to reject it.
- Existing Freedom, Aircar, and evidence-bound experiment bytes remain
  unchanged.

The Lab will declare `hub`, `secret-shop`, and `robot-repair` in target order.

### Control State

Keep schema v5 byte-for-byte for singleton profiles. Publish schema v6 only for
profiles with more than one expected owned target.

Schema v6 preserves all host, service, artifact, profile, client, and diagnostic
fields. Its producer record replaces the singular fields with:

- `expectedOwnedProcesses`: the finite ordered target ID, executable, and
  process-pattern set;
- `ownedProcesses`: zero or more exact recorded identities, each with target ID,
  PID, birth token, PID version, start time, PGID, command, and executable;
  `ready` entries are live, while `quiesced` entries retain evidence for exact
  absence checks;
- the unchanged launcher identity and generation-local log;
- status `launching`, `starting`, `ready`, or `quiesced`.

Historical schema-v2/v3/v4/v5 validation, dead-owner cleanup, and stop behavior
remain supported and fail closed exactly as today.

### Discovery And Transitions

For a multi-target profile, global discovery may inspect only the declared
patterns and exact executable text mappings.

1. Read one process-table snapshot.
2. Match each candidate to exactly one expected target.
3. Require PID, birth token, PID version, start time, PGID, command, and exact
   executable mapping.
4. Reject duplicate candidates for one target, one PID matching multiple
   targets, pre-launch processes, and processes outside the launcher PGID or
   their own leader PGID.
5. Re-read every identity before publication or signaling.

The supervisor may observe the outgoing and incoming targets together during a
bounded transition. It retains both exact identities until the outgoing group
is freshly absent. `ready` requires at least one exact live target plus an
authenticated bridge handshake for a member of the published set. A target
change is not itself a failure when the new identity belongs to the sealed set
and the transition completes before `transitionTimeoutSeconds`.

### Stop Authority

Stop never signals a serialized PID or an unverified process name.

1. Revalidate every published identity independently.
2. Group identities by PGID only after exact identity checks.
3. Signal each distinct exact live group once.
4. Revalidate again before escalation.
5. Preserve state, service, socket, and owner evidence if any group remains
   live, cannot be inspected, or changes identity.
6. Accept an identity disappearing during stop only after its recorded group is
   freshly confirmed absent.

An unrelated process matching a pattern but not the exact executable mapping
never becomes authority.

### Profile-Aware Installation

Do not encode three new title-specific paths in shell cleanup or signal logic.
The production artifact path needs a profile-aware game overlay plan:

- shared host resources and bridge installation remain artifact-owned;
- selected profile targets deterministically expand the OpenVR replacement and
  graphics/bridge creation operations;
- install and uninstall plans bind the profile ID and SHA-256 into their plan
  digest and transaction journal;
- all target trees are admitted before the first mutation;
- one transaction owns every target or rolls every target back;
- legacy sealed artifacts retain their explicit singleton plan semantics.

The implementation must first prove whether the existing transaction executor
can consume the expanded operation list unchanged. Add new transaction
machinery only when a fixture demonstrates a real gap.

### Universality Boundary

The checked-in profiles certify known compatibility shapes and provide exact
regression fixtures; they are not the long-term onboarding mechanism. GitHub
issue #117 tracks generic runtime discovery plus generated machine-local locks so
compatible OpenVR/D3D11 titles can eventually be admitted without adding one
curated repository profile per game. This work keeps that future path open by
putting all title-specific filesystem and process authority in resolved profile
data rather than in start, stop, transaction, or cleanup branches.

## Implementation Slices

1. Add profile validation and resolution for `ownedTargets` with focused tests.
2. Add schema-v6 validation, status, dead-owner, and stop fixtures while
   retaining all historical schema tests.
3. Generalize supervisor discovery, confirmation, transition monitoring, state
   publication, and quiescence behind the resolved ownership collection.
4. Materialize and bind the profile-aware install/uninstall operation set.
5. Update The Lab profile and build a new sealed artifact only after every
   hardware-free fixture is green.
6. Run disconnected hub and simulated transition gates before requesting an
   in-headset experience transition.

Each slice must remain reviewable and leave production start fail-closed until
its complete authority chain is present.

As of July 31, 2026, slices 1 through 5 are complete in hardware-free fixtures.
The checked-in The Lab profile owns `hub`, `secret-shop`, and `robot-repair`.
Start materializes the same profile-aware filesystem plan used by install and
uninstall, requires the exact journal-v3 profile identity and semantic digest,
and retains the journal-v2 artifact-only fallback for existing singleton
installs. Profiles with no resolved owned process still fail before plan
inspection, and singleton supervisor publication remains schema v5.

Two independent dev12 builds produced unsealed seal
`478fc9892477a0ca05ddcdcc1cb0e27dec5aa543e6e653f7a104c4eccd661bfa`.
Preserved-bundle sealing produced identical verified seal
`248bd2c213bfc9acfeb0ea7218ff27941567b822b061e6e72a88028f8e7d8a00`
with the qualified Developer ID CDHash and bundle tree unchanged. All 18 doctor
checks pass. The profile-aware The Lab plan is ready with no blockers: 51
install operations at digest
`735a8191863b9193558bc3de25599c54e09c706edc63fbb5afe78eee676dbcb7`
and 27 uninstall operations at digest
`6ffd0ae9c0febb0388ab06aae4c2343739992cdf4891e45cdba868d63c62f307`.
All 15 profile, 72 start, 67 control, 30 install, and 31 transaction fixtures
pass with artifact contract checks and self-tests. No disconnected launch,
in-headset transition, or physical experience claim is made by slice 5; those
remain slice 6.

## Hardware-Free Fixture Matrix

- existing singleton profile/state/start/stop/install fixtures remain green;
- `ownedTargets` rejects unknown, duplicate, empty, and mixed ownership modes;
- three exact detached target processes resolve deterministically;
- sequential hub to experience replacement publishes the new exact identity;
- bounded overlap retains outgoing and incoming identities without duplication;
- two exact candidates for one target fail before publication;
- one PID matching multiple target patterns fails as ambiguous;
- a process predating the active launcher is rejected;
- PID reuse, PID-version drift, birth-token drift, command drift, executable
  drift, and PGID drift fail closed;
- stop signals each distinct exact PGID once and never signals an unrelated
  pattern match;
- partial SIGTERM/SIGKILL failure preserves all ownership evidence;
- dead-owner cleanup refuses bridge teardown while any exact recorded or
  unrecorded declared target remains live;
- expanded install rolls back every target after a deterministic mid-plan
  failure;
- interrupted uninstall restores every stock OpenVR hash and removes every
  artifact-created file;
- legacy sealed singleton artifacts retain their current plan and state
  behavior.

## Reproducible Commands

Use repository-defined Python entry points through `uv`:

```bash
uv run python tools/runtime_profile.py check
uv run python tools/runtime_profile.py self-test
uv run python tools/runtime_profile_test.py
uv run python tools/runtime_control_test.py
uv run python tools/runtime_start_test.py
uv run python tools/runtime_install_test.py
```

Before any live or simulated probe:

```bash
uv run python tools/vr_stack_cleanup.py
```

Record the exact artifact seal, profile SHA-256, generated plan digest,
transaction ID, state schema, target identities, signal decisions, restoration
hashes, and cleanup result.

## Expected Artifacts

- profile/schema validation output;
- schema-v6 state fixtures for launch, transition, ready, stop, and failure;
- deterministic multi-process fixture logs and identity snapshots;
- expanded install/uninstall plan plus journal evidence;
- disconnected The Lab run directories below
  `.code/probes/013-the-lab-profile-qualification/the-lab/`;
- final physical transition evidence only after hardware-free acceptance.

## Known Failure Signatures

- `profile.invalid`: ownership declarations are empty, duplicated, mixed, or
  reference unknown targets;
- `profile.artifact_mismatch`: selected target operations do not exactly match
  the sealed profile-aware plan;
- `producer.identity_unavailable`: exact target identity cannot be inspected;
- `producer.identity_changed`: duplicate, reused, ambiguous, or drifting target
  identity;
- `producer.start_timeout`: no exact target and authenticated bridge handshake
  complete before the startup deadline;
- `producer.transition_timeout`: no declared exact target stabilizes before the
  transition deadline;
- `producer.quiesce_failed`: at least one exact owned group remains live or
  cannot be revalidated;
- `producer.orphaned`: a dead owner leaves a recorded or unrecorded exact target
  live;
- `runtime.transaction_failed`: a multi-target mutation cannot commit or roll
  back exactly.

## Cleanup

Hardware-free fixtures use temporary roots and must leave no process, socket,
lock, journal, or staged file. Any interrupted live probe must run the exact
cleanup tool and recheck all three The Lab stock OpenVR hashes before another
launch.

Never use bottle-wide termination or path/name-only deletion as cleanup.

## Human Gate

After all hardware-free gates pass, one worn session must prove:

1. hub launch and controls;
2. hub to Secret Shop and return;
3. hub to Robot Repair and return;
4. exact status during each transition;
5. owner-requested stop from each experience;
6. client/headset recovery without orphaned targets;
7. exact uninstall and stock restoration.

The user judges visual output, controls, and responsiveness. Process ownership,
identity, signaling, rollback, and cleanup remain machine-enforced gates.
