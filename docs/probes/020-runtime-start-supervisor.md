# Runtime Start Supervisor

## Question

Can the qualified sealed runtime start one exact launchd/Mach bridge under a
single durable supervisor, report truthful live ownership, and stop without
signaling cached PIDs or weakening the transactional installed-layout gate?

## Boundary

- Add a host-only `start` slice before game/profile ownership.
- Introduce `idle` for a synchronized live supervisor and checked-in Mach
  bridge that is waiting for an authenticated game producer.
- Keep `waiting`, `connected`, `streaming`, and `recovering` reserved for the
  following profile/game/client slice.
- Reuse the fixed lifecycle lock, `native-probe.lock`, synchronized state file,
  sealed launch-agent template, stable signed bridge URL, and exact stop
  identity checks.
- Hard cuts: no game launch, Vision Pro launch, Local Network remediation,
  launchd PID signaling, silent stale-state deletion, alternate bridge URL,
  data-plane change, or configurable timeout flags.

## Start Contract

1. Verify the sealed artifact and checked-in contract, then require the exact
   transactionally installed layout rather than doctor readiness alone.
2. Spawn one detached internal supervisor with a fresh positive generation.
   The child acquires the fixed global lifecycle lock for its full lifetime.
3. Create the private owner lock and run directory without following symlinks.
   Record the supervisor PID and exact process start time.
4. Render the artifact-verified launch-agent template with the stable bridge
   program, private log/session paths, and one generation-specific IOSurface
   nonce. Publish the plist atomically at the declared transient path.
5. Refuse a foreign or contradictory launchd job. Bootstrap and kickstart the
   exact plist only when the target is absent.
6. Require two stable launchd samples with one unchanged positive PID, one run,
   the exact registered plist and program, exact installed/live Developer ID
   identity, and a fresh generation-owned bridge check-in marker.
7. Publish synchronized `idle` state atomically and let the parent `start`
   command return success. The supervisor continues monitoring exact service
   identity and exits through exact cleanup when the service is booted out.
8. `stop` requests the identity-bound supervisor channel, boots out only the
   verified registered plist, and waits a bounded five seconds for the live
   supervisor to remove its lock, state, plist, and run directory. An
   unresponsive live owner is preserved and reported rather than signaled.
9. Install and uninstall detect a synchronized schema-v2 supervisor, stop it
   before acquiring the lifecycle lock, then re-check stopped state while the
   lock is held.

## Timeouts

- lifecycle lock: immediate non-blocking acquisition;
- individual launchctl, codesign, lsof, and ps command: 10 seconds;
- launchd and bridge check-in readiness: 10 seconds;
- total detached-child startup: 30 seconds;
- stable readiness samples: two samples at least 100 milliseconds apart;
- supervisor socket and stop cadence: 250 milliseconds;
- lightweight launchd identity cadence: 1 second;
- full live executable/signature identity refresh: 30 seconds;
- cooperative stop cleanup: 5 seconds.

The parent passes one absolute monotonic deadline to the child and every
startup-path command is clipped to its remaining budget. These are contract
constants, not caller-selectable flags.

## Fixture Matrix

- exact installed start reaches `idle` with synchronized owner, service,
  artifact, generation, signature, and nonce evidence;
- repeated start of the same live artifact is idempotent and performs no second
  bootstrap or kickstart;
- a different artifact, unsealed artifact, failed doctor, uninstalled layout,
  incomplete transaction, or plan drift blocks before supervisor mutation;
- lifecycle lock contention returns `transaction.busy`;
- foreign launchd path, program, plist, marker, signature, CDHash, or symlink is
  preserved and refused;
- bootstrap, kickstart, readiness, state-write, and child-start failures clean
  only the state created by that generation;
- PID, run count, registered path, program, signature, or fresh check-in drift
  during the two readiness samples fails closed;
- status reports `idle` only for a fully synchronized live identity;
- stop waits for cooperative supervisor cleanup and never calls launchctl
  `kill` or sends a cached PID signal;
- an unresponsive live supervisor remains intact and reports a stable failure;
- dead-owner exact state uses the existing bootout and stale cleanup fallback;
- reused dead-owner PIDs are distinguished by process start time before stale
  cleanup;
- schema-v2 stale cleanup requires exact recorded plist and bridge hashes;
- a verified live prior-contract supervisor remains stoppable during update or
  after its sealed artifact is moved, while new starts still require the current
  checked-in contract;
- install and uninstall stop a live synchronized supervisor before lock retry;
- a clean committed terminal journal from a prior plan is archived before the
  new install plan runs, while incomplete or dirty prior journals remain
  fail-closed;
- human and JSON start output and usage errors remain stable.

## Validation

```bash
python3 -m py_compile \
  tools/runtime_control.py \
  tools/runtime_cli.py \
  tools/runtime_start.py \
  tools/runtime_control_test.py \
  tools/runtime_start_test.py \
  tools/runtime_install.py \
  tools/runtime_install_test.py
python3 tools/runtime_control_test.py
python3 tools/runtime_start_test.py
python3 tools/runtime_install_test.py
python3 tools/runtime_cli.py --help
python3 tools/build_runtime_artifact.py check
python3 tools/build_runtime_artifact.py self-test
```

Run the control and lifecycle fixtures under macOS and the repository's Python
3.12 Linux image. Before any physical start, build, compare, Developer ID seal,
verify, and doctor a fresh artifact from a clean commit.

## Cleanup

- The supervisor owns only its declared lock, state, plist, run directory, log,
  session root, and exact launchd job.
- Startup failure removes only paths created by that generation after exact
  identity checks.
- Normal stop leaves the retained signed bridge and transaction backups intact
  while removing all live control-plane state.
- Remove fixture roots and rejected build outputs after validation. Preserve the
  final sealed artifact needed by the next profile/game slice.

## Known Failure Signatures

- runtime is not transactionally installed: `runtime.not_installed`;
- another lifecycle command or supervisor owns the fixed lock:
  `transaction.busy`;
- a live or stale foreign job occupies the label: `service.foreign`;
- exact bridge check-in is not proven before the deadline:
  `runtime.start_timeout`;
- synchronized child startup fails: `runtime.start_failed` with its private log
  preserved;
- a prior plan left a non-clean terminal journal:
  `transaction.journal_mismatch` or `transaction.cleanup_failed`;
- verified service bootout succeeds but the live supervisor does not clean up:
  `owner.unresponsive` with the owner state preserved.

## Expected Artifacts

- a detached bounded supervisor and thin `start` CLI command;
- one truthful `idle` live state with exact owner/service/artifact identity;
- deterministic launchd, child-start, synchronization, timeout, and cleanup
  fixtures;
- a fresh sealed artifact and read-only physical host-start qualification;
- an explicit handoff to profile/game ownership and client state transitions.

## Issue Routing

GitHub issue #60. Issue #62 retains Launch Services, Local Network consent,
reboot, logout/login, and update/rollback integration.

## Next Action

Implement the host-only supervisor and fixtures, review its identity and cleanup
boundaries, merge it, then add profile/game ownership and the remaining live
state transitions as the next #60 slice.
