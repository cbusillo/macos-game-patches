# Runtime Profile Producer Ownership

## Question

Can the qualified runtime resolve one sealed curated game profile, launch one
exact CrossOver producer under the detached supervisor, report `waiting` only
after authenticated producer readiness, and stop the owned process session
without adopting or signaling cached PIDs?

## Boundary

- Admit only the checked-in `freedom-locomotion` profile in this slice because
  the transactional install plan currently owns one Freedom OpenVR and graphics
  target.
- Require `start --profile freedom-locomotion`; remove the host-only public
  start shape rather than retaining a compatibility flag.
- Bind the profile validator, schema, and explicit curated profile files into
  the sealed artifact source contract.
- Reuse the existing exact installed-artifact, committed-journal, global-lock,
  launchd/Mach bridge, socket, signature, and cooperative-stop contracts.
- Advance from host `idle` through `starting-producer` to `waiting` only. Keep
  `connected`, `streaming`, and `recovering` for the following client-state
  slice.
- Hard cuts: no arbitrary profile paths, no The Lab partial launch, no research
  runner invocation, no `pgrep` or regex signaling authority, no bottle-wide
  cleanup, no cached-PID signal, no Vision Pro launch, and no data-plane change.

## Profile Admission

1. Accept one lowercase profile identifier and resolve only
   `runtime/profiles/<id>.json` below the repository profile root.
2. Reject symlinks, non-canonical JSON, filename/ID mismatch, unknown profiles,
   and profiles not listed explicitly in the runtime manifest source records.
3. Require the current profile bytes, schema, and validator to match the source
   hashes sealed into the artifact contract.
4. Reuse the profile Steam app/build/depot checks against the exact configured
   bottle without requiring a pristine pre-install game tree.
5. Project the installed game tree back to its declared stock identity by:
   - substituting the exact stock hashes restored by uninstall operations; and
   - excluding exact artifact-created files removed by uninstall operations.
6. Require the projected file count and tree hash to match the profile payload.
7. Match every declared target to the exact resolved install/uninstall
   operations for `openvr_api.dll`, `openvr_api.real.dll`, `d3d11.dll`,
   `dxgi.dll`, and `alvr_iosurface_bridge.dll`.
8. Reject any game-root mutation not owned by the selected profile. The current
   plan therefore admits Freedom and rejects The Lab with
   `profile.artifact_mismatch`.

## Producer Contract

1. Resolve profile admission in the parent before creating a generation, then
   pass the profile ID and digest to the detached child.
2. Re-resolve the same profile and digest under the supervisor-held lifecycle
   lock before launching any producer process.
3. Publish synchronized schema-v3 `idle` state with profile evidence and
   `producer.status=starting` after the exact bridge checks in. This exposes the
   identity-bound stop channel during producer startup.
4. Admit CrossOver 26.2's exact in-bundle relative `cxstart -> wine` link only
   when its sibling target is a real executable, then build the proven command
   directly as an argument vector using that launcher, the exact bottle, target
   executable, working directory, profile arguments, generation nonce, bridge
   service, runtime bridge root, geometry, and profile environment.
5. Launch `cxstart --wait-children` in a new POSIX session with stdout/stderr
   below the generation directory. Retain the live process handle as the only
   signal authority.
6. Discover the game process only as evidence. A candidate must match the
   profile pattern, belong to the owned session/process group, have an exact
   start time, use the configured bottle, and expose the expected executable.
   Zero or multiple exact candidates fail closed.
7. Require both existing authenticated bridge markers before publishing
   `waiting`:
   - one producer handshake for the exact service and generation; and
   - all three startup self-tests.
8. Bound producer startup by the curated profile timeout. The total public
   start deadline is the fixed host-start budget plus that sealed profile
   timeout; callers receive no timeout flag.
9. Monitor the direct launcher handle, exact producer identity, owned session,
   launchd service, and periodic bridge signature identity. Unexpected producer
   or service loss becomes a truthful failure, never a false live state.

## Stop Contract

1. An authenticated schema-v3 `stop` request asks the supervisor to quiesce the
   producer before acknowledging the request.
2. Revalidate the live direct child and owned session identity from the retained
   process handle. Serialized producer PIDs remain evidence only.
3. Send `SIGTERM` only to the still-exact owned process group, wait a fixed
   grace period, then send `SIGKILL` only to that same exact group if needed.
4. Require the launcher, exact producer candidate, and owned session to be gone
   before publishing `producer.status=quiesced` and acknowledging stop.
5. If identity is ambiguous or the producer remains live, return
   `producer.quiesce_failed`, preserve the bridge and all ownership evidence,
   and do not boot out launchd.
6. After acknowledgment, the existing caller performs exact registered-plist
   bootout. The supervisor observes service absence and removes its state,
   socket, plist, owner lock, and generation directory.
7. Preserve schema-v2 stop behavior so the new checkout can still stop a live
   dev9 host-only supervisor during update.
8. Install and uninstall continue to stop synchronized live state before the
   global lock and recheck stopped state under the lock. Failed producer
   quiescence leaves transaction targets and journals untouched.

## State Contract

Schema v3 adds exact `profile` and `producer` objects while preserving every
schema-v2 host field.

- `profile`: ID, SHA-256, app ID, build ID, and entrypoint target.
- `producer`: `starting`, `ready`, or `quiesced`; launcher PID/start time;
  session and process-group identity; active target PID/start time/executable;
  and the generation-local producer log.
- `idle`: bridge is synchronized and producer launch is in progress or has been
  quiesced during stop.
- `waiting`: the exact producer is live, the bridge handshake and startup
  self-tests passed, and no authenticated Vision Pro connection state has been
  introduced yet.

Idempotent start requires the same artifact seal plus the same profile ID and
digest. A different live profile reports `profile.conflict` and performs no
second bridge or game launch.

## Failure Taxonomy

- unknown, arbitrary, symlinked, or changed profile: `profile.invalid` or
  `profile.not_curated`;
- profile does not match installed operations: `profile.artifact_mismatch`;
- Steam app/build/depot or projected payload mismatch: `profile.not_installed`;
- CrossOver launcher or target identity unavailable: `producer.launch_invalid`;
- exact producer does not become ready: `producer.start_timeout`;
- producer exits before or after readiness: `producer.exited`;
- reused or ambiguous process identity: `producer.identity_changed`;
- exact producer session cannot be stopped: `producer.quiesce_failed`;
- dead owner with a still-observed producer: `producer.orphaned`.

## Fixture Matrix

- curated Freedom profile resolves with exact manifest source hashes;
- arbitrary paths, unknown IDs, symlinks, non-canonical JSON, and digest drift
  fail before generation mutation;
- projected installed tree reproduces the stock profile identity;
- missing, duplicated, extra, or wrong-target operations fail closed;
- The Lab is rejected rather than partially admitted;
- exact producer argv, working directory, environment, session, and log path are
  deterministic;
- bridge and producer readiness publish schema-v3 `waiting`;
- duplicate same-profile start is idempotent and different-profile start is a
  conflict;
- producer timeout, early exit, identity drift, multiple candidates, and PID
  reuse never publish a false live state;
- a pure-Python parent/child process-session fixture proves exact group cleanup
  on macOS and Linux;
- stop before producer spawn, during startup, and after readiness is bounded and
  race-safe;
- quiescence failure preserves bridge, state, socket, and ownership evidence;
- schema-v2 host-only stop remains compatible;
- install and uninstall pre-stop `waiting` before acquiring the lifecycle lock;
- journal, profile, producer, artifact, and plist drift remain fail-closed;
- final cleanup leaves no producer, service, socket, plist, owner lock,
  generation directory, or runtime-created game file.

## Validation

```bash
python3 -m py_compile \
  tools/runtime_profile.py \
  tools/runtime_profile_test.py \
  tools/runtime_control.py \
  tools/runtime_control_test.py \
  tools/runtime_start.py \
  tools/runtime_start_test.py \
  tools/runtime_cli.py \
  tools/runtime_install.py \
  tools/runtime_install_test.py
python3 tools/runtime_profile.py check
python3 tools/runtime_profile.py self-test
python3 tools/runtime_profile_test.py
python3 tools/runtime_control_test.py
python3 tools/runtime_start_test.py
python3 tools/runtime_install_test.py
python3 tools/build_runtime_artifact.py check
python3 tools/build_runtime_artifact.py self-test
```

Run the profile, control, start, and lifecycle fixtures under macOS and the
repository Python 3.12 Linux image. Keep fixture roots short enough for Darwin
filesystem Unix sockets and use event barriers or fake clocks rather than
wall-clock races.

Before physical execution, build and compare two independent artifacts from a
clean commit, Developer ID seal the exact candidate, verify and doctor it,
transactionally update the current install, and retain the exact sealed output.

## Physical Qualification

- Start the exact installed dev10 artifact with
  `--profile freedom-locomotion` and no Vision Pro client.
- Require schema-v3 `waiting`, exact profile and producer identity, one bridge
  producer handshake, three startup self-tests, and stable launchd/signature
  identity through the periodic refresh.
- Repeat start and require idempotence with no second producer or service.
- Cooperatively stop and prove exact producer-session, service, socket, plist,
  lock, and generation cleanup without broad process kills.
- Start again, then run install and uninstall pre-stop from live `waiting` and
  require zero cleanup or rollback failures plus exact stock restoration.
- End `runtime.ready` with no owned process or transaction residue.

This host-only disconnected gate does not qualify Vision Pro discovery,
streaming, controls, or headset-visible quality. Those remain the next #60
client-state slice and the waiting #67/#59 physical gates.

## Issue Routing

GitHub issue #60. Issue #62 retains Launch Services, Local Network consent,
reboot, logout/login, and update/rollback integration. Issue #67 remains the
worn The Lab controller/cadence gate that blocks #59.
