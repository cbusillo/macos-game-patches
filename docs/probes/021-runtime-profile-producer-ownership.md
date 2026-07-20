# Runtime Profile Producer Ownership

## Question

Can the qualified runtime resolve one sealed curated game profile, launch one
exact CrossOver producer under the detached supervisor, report `waiting` only
after authenticated producer readiness, and stop every exact owned process
group without adopting or signaling cached PIDs?

## Boundary

- Admit only the checked-in `freedom-locomotion` profile in this slice because
  the transactional install plan currently owns one Freedom OpenVR and graphics
  target.
- Require `start --profile freedom-locomotion`; remove the host-only public
  start shape rather than retaining a compatibility flag.
- Bind the profile validator, schema, and explicit curated profile files into
  the sealed artifact source contract.
- Pin the macOS host bridge to ALVR commit
  `c02bca35616ac4e3b95deae41fbbe70e2602e906` from `cbusillo/ALVR#6`, which
  exports the authenticated producer PID, PID version, and start token and uses
  one shared three-slot handshake deadline.
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
3. Publish synchronized schema-v4 `idle` state with profile evidence and
   `producer.status=launching` before CrossOver spawn, then replace it with
   `producer.status=starting` after exact launcher identity is available. This
   preserves ownership evidence for every post-spawn identity-read failure and
   exposes the identity-bound stop channel during producer startup.
4. Admit CrossOver 26.2's exact in-bundle relative `cxstart -> wine` link only
   when its sibling target is a real executable, then build the proven command
   directly as an argument vector using that launcher, the exact bottle, target
   executable, working directory, profile arguments, generation nonce, bridge
   service, runtime bridge root, geometry, and profile environment.
5. Launch `cxstart --wait-children` in a new POSIX session with stdout/stderr
   below the generation directory. Retain the live process handle plus the
   launcher's PID, birth token, start time, and process-group identity in
   supervisor memory. Do not bind launcher authority to macOS PID version:
   CrossOver legitimately increments it when `cxstart` execs `winewrapper`.
6. Resolve an optional singleton `launch.ownedProcess` from the sealed profile.
   Freedom names only
   `FreedomLocomotion/Binaries/Win64/FreedomLocomotion-Win64-Shipping.exe` and
   its Shipping-only pattern; the executable must also be a sealed critical
   file. Do not infer signal authority from every critical `.exe`.
7. Discover that steady-state process globally because CrossOver Wine may call
   `setsid()` and detach it from the launcher group. Accept exactly one live
   candidate only when its pattern, executable mapping, command basename,
   high-resolution birth token, macOS PID version, start time, process-group
   leadership, and authenticated bridge readiness all agree with the active
   generation. Count global exact candidates before filtering their group shape,
   retain the first exact candidate provisionally, and revalidate it around a
   second global scan before readiness can inherit the generation-local bridge
   markers. Zero, changed, re-executed, multiple, or unreadable candidates fail
   closed.
8. Require both existing authenticated bridge markers before publishing
   `waiting`:
   - one producer handshake naming the exact service, active generation nonce,
     bridge PID, Mach-audit-authenticated producer PID and PID version, and
     matching high-resolution process start token in the private bridge log; and
   - all three startup self-tests.
9. Bound producer startup by the curated profile timeout. The total public
   start deadline is the fixed host-start budget plus that sealed profile
   timeout; callers receive no timeout flag.
10. Monitor both live process identities, both owned groups, the launchd
    service, and periodic bridge signature identity. Unexpected producer or
    service loss becomes a truthful failure, never a false live state.

## Stop Contract

1. An authenticated schema-v4 `stop` request asks the supervisor to quiesce the
   producer before acknowledging the request.
2. Revalidate the retained launcher by PID, birth token, start time, and PGID,
   which remain stable across its admitted `exec` transition. Revalidate the
   in-memory steady-state process by PID, birth token, PID version, start time,
   PGID, command, and exact executable mapping immediately before every signal.
   Serialized producer PIDs and PGIDs remain evidence only.
3. Stop the independently grouped steady-state process first, then the launcher
   group. Send `SIGTERM`, wait a fixed grace period, and send `SIGKILL` only
   after another exact live revalidation.
4. During startup cancellation, stop the exact launcher group and perform a
   bounded reconciliation scan. An unauthenticated, ambiguous, or late detached
   candidate blocks cleanup and preserves diagnostics instead of being guessed
   or signaled.
5. Require the launcher, exact producer candidate, and every owned group to be gone
   before publishing `producer.status=quiesced` and acknowledging stop.
6. If identity is ambiguous or the producer remains live, return
   `producer.quiesce_failed`, preserve the bridge and all ownership evidence,
   and do not boot out launchd.
7. After acknowledgment, the caller re-inspects launchd ownership and signature
   identity and re-hashes the synchronized plist and bridge immediately before
   exact registered-plist bootout. The supervisor observes service absence and
   removes its state, socket, plist, owner lock, and generation directory. The
   supervisor applies the larger of 40 seconds or the sealed transition timeout
   plus ten seconds. The stop RPC allows 1,230 seconds, covering both the
   profile schema's 600-second maximum startup window and 600-second maximum
   transition window plus quiescence margin, while ping remains independently
   bounded at five seconds.
8. Preserve schema-v2 stop behavior during update. Treat every schema-v3
   Freedom state as ownership-incomplete and require it to be stopped with its
   originating runtime before update; the old supervisor cannot prove that no
   detached Shipping group exists.
9. A dead schema-v4 owner triggers identity-anchored recorded-group checks and a
   non-signaling global exact executable/pattern scan. Reused PGIDs whose leader
   no longer matches its recorded exec-stable identity are ignored: launcher
   anchors use birth token plus start time, while owned-process anchors also use
   PID version. An original leaderless group, exact candidate, unreadable scan,
   incomplete discovery, malformed state, or unsupported state preserves all
   ownership evidence instead of permitting stale cleanup.
10. Install and uninstall continue to stop synchronized live state before the
    global lock and recheck stopped state under the lock. Failed producer
    quiescence leaves transaction targets and journals untouched.

## State Contract

Schema v4 preserves every host field and separates launcher identity from the
optional independently grouped owned process.

- `profile`: ID, SHA-256, app ID, build ID, and entrypoint target.
- `producer`: `launching`, `starting`, `ready`, or `quiesced`; optional launcher
  PID, birth token, start time, and PGID while launch is still incomplete;
  expected profile-owned executable/pattern evidence; optional live
  owned-process PID, birth token, PID version, start time, PGID, command, and
  executable evidence; and the generation-local producer log.
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

## Known Failure Signature

On July 19, 2026, two physical qualification starts reached `waiting` and their
tracked `cxstart` groups stopped successfully, but Wine had moved each
`FreedomLocomotion-Win64-Shipping.exe` into a new session and PGID. The two
Shipping processes remained alive with PPID 1 after state cleanup. Both were
stopped manually only after command, start-time, PGID-leader, and exact `lsof`
executable checks. No further physical start is allowed until schema-v4
dual-group ownership passes the full cleanup gate.

## Fixture Matrix

- curated Freedom profile resolves with exact manifest source hashes;
- arbitrary paths, unknown IDs, symlinks, non-canonical JSON, and digest drift
  fail before generation mutation;
- projected installed tree reproduces the stock profile identity;
- missing, duplicated, extra, or wrong-target operations fail closed;
- The Lab is rejected rather than partially admitted;
- exact producer argv, working directory, environment, session, and log path are
  deterministic;
- pre-launch state preserves ownership evidence when any immediate launcher
  identity read fails;
- bridge and producer readiness publish schema-v4 `waiting`;
- duplicate same-profile start is idempotent and different-profile start is a
  conflict;
- producer timeout, early exit, identity drift, re-exec, multiple candidates,
  and PID reuse never publish a false live state;
- malformed ownership state and unrecorded exact dead-owner candidates preserve
  state without launchd bootout or cached-PID signaling;
- an acknowledged stop revalidates exact launchd/content identity before
  bootout, and ping/stop retain separate bounded RPC deadlines;
- a pure-Python parent/child fixture whose producer calls `setsid()` proves
  exact cleanup of launcher and detached process groups on macOS and Linux;
- stop before producer spawn, during startup, and after readiness is bounded and
  race-safe;
- target exit during stop revalidation is accepted only after its group is
  freshly confirmed absent;
- a same-PGID target becomes the exact signal anchor if its launcher exits, and
  the stop RPC remains live across the maximum queued startup plus transition
  windows;
- quiescence failure preserves bridge, state, socket, and ownership evidence;
- schema-v2 stop remains compatible and schema-v3 Freedom fails closed;
- dead schema-v4 state preserves evidence when either recorded group is live and
  ignores a recorded PGID only after its reused leader identity is proven;
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
- Require schema-v4 `waiting`, exact profile and dual producer identity, one bridge
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
