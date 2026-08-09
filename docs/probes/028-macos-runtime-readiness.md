# macOS Runtime Readiness

## Goal

Move the stable macOS bridge identity from research-runner assumptions into the
production runtime lifecycle without requiring Xcode or `devicectl` during
steady-state operation.

Issue routing: #62 under runtime plan #56.

## Bounded Slice

This slice is hardware-free and covers four deterministic contracts:

1. Xcode remains sealed build evidence but is not a runtime `doctor` or `start`
   prerequisite for an already-built artifact.
2. The stable bridge bundle has exactly one matching Launch Services record at
   its retained URL, bundle identifier, Team ID, and CDHash.
3. Install repairs missing or stale Launch Services registration only after the
   stable bundle transaction commits; start and status remain read-only.
4. Start output identifies a healthy host that is waiting for the operator to
   open ALVR on Vision Pro and allow Local Network access when prompted.

The slice does not claim to read macOS Local Network TCC state. Apple exposes no
general supported permission-status query for this app-level consent, so
missing, denied, and pending consent remain a physical qualification matrix.

## Safety Contract

- Launch Services inspection is read-only and bounded.
- Registration uses the system `lsregister -f` path and never unregisters the
  retained consent anchor during ordinary uninstall.
- A registration failure after filesystem commit is reported as committed but
  incomplete lifecycle readiness; it does not roll back exact installed bytes.
- Install preflight does not reject a repairable missing registration before it
  has committed the stable bundle.
- A committed install journal remains valid while post-commit cleanup progress
  is recoverable and has no recorded cleanup, rollback, or transaction failure.
- Production runtime commands do not invoke `xcodebuild`, `xcrun`, or
  `devicectl` after artifact construction and sealing.

## Reproducible Validation

```sh
uv run python tools/build_runtime_artifact.py self-test
uv run python tools/runtime_control_test.py
uv run python tools/runtime_install_test.py
uv run python tools/runtime_start_test.py
uv run python tools/runtime_transaction_test.py
uv run python tools/runtime_cli.py --help
```

Read-only host inspection:

```sh
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/\
LaunchServices.framework/Support/lsregister
"$LSREGISTER" -dump
```

## Expected Artifacts

- fixture evidence for one exact Launch Services record;
- fixture failures for missing, duplicate, wrong-path, wrong-Team-ID, and
  wrong-CDHash registrations;
- fixture evidence that install can repair registration after commit;
- fixture evidence that missing Xcode no longer blocks steady-state doctor;
- start JSON and text showing the client waiting state and operator guidance;
- physical follow-up evidence for Local Network allowed, denied, pending,
  reboot, logout/login, update, rollback, and stale registration scenarios.

## Known Failure Signatures

- `launch_services.missing`: no record exists for the stable bundle identifier.
- `launch_services.ambiguous`: multiple records exist for the identifier.
- `launch_services.identity_mismatch`: path, Team ID, or CDHash differs.
- `launch_services.registration_failed`: `lsregister -f` did not complete.
- `client.telemetry_missing`: bridge telemetry was not published before the
  bounded startup deadline; do not relabel this as a permission denial.
- `runtime.not_installed`: no exact committed install journal matches the plan.

## Cleanup

- Keep the retained stable bridge bundle and its Launch Services registration
  across ordinary uninstall so macOS can preserve consent where supported.
- Remove only fixture state created under temporary test directories.
- Do not mutate the live Launch Services database during unit tests.
- Archive probe evidence under `.code/probes/028-macos-runtime-readiness/` and
  remove transient logs after durable facts are recorded here and in issue #62.

## Physical Follow-Up

After deterministic validation lands, qualify the first Local Network prompt,
allowed/denied/pending behavior, client absence and relaunch, reboot,
logout/login, update, rollback, and uninstall on the stable Developer ID signed
bundle. Record exact bundle URL, bundle identifier, Team ID, CDHash, Launch
Services records, service PID, client state timing, and cleanup evidence.

## Actual Evidence

Hardware-free validation on 2026-08-08 passes:

- 73 runtime-control fixtures, including build-only Xcode scoping and exact
  Launch Services missing, duplicate, path, Team ID, CDHash, query, symlink,
  and registration-postcondition cases;
- 31 install fixtures, including committed registration failure and idempotent
  retry without filesystem rollback;
- 77 start fixtures, including client serialization, legacy schema-v1 startup
  report compatibility, idempotent waiting-state replay, and recoverable
  committed cleanup progress;
- 31 transaction fixtures;
- artifact contract check and all artifact self-tests;
- Python compilation, JSON parsing, CLI help, diff whitespace, and Markdown
  lint.

Read-only inspection of the live host Launch Services database found exactly
one matching record for the retained bundle path, bundle identifier
`com.alvr.macos-bridge.iosurface`, Team ID `MM5YXC7T6E`, and CDHash
`1731a67fa327ca7c1576f63a084cc3b39f095b41`.

After merge, full artifact input validation passed at source commit
`574b1802abb6b8031d98ac3b3e73bec61bac10ef`. An ignored uv environment now
provides Python 3.12.11 to IntelliJ, and changed-file JetBrains inspection is
green with zero findings.

### Secondary M2 Lane

On 2026-08-09, `chris-mbp` was prepared as an exploratory secondary host without
touching its dirty legacy checkout:

- a clean sibling checkout at `~/Developer/macos-game-patches-m2` is pinned to
  merge commit `574b1802abb6b8031d98ac3b3e73bec61bac10ef`;
- an ignored uv environment provides Python 3.12.11;
- exact CrossOver 26.2 build `26.2.0.39821` is installed under
  `~/Applications/CrossOver.app` with lane-local bindings;
- current-contract sealed artifact
  `e1f253784776501fe33c8c8d6a65e99696869c37db6c84d33e57a1cec8dbd2b1`
  verifies on the M2;
- the stable bridge at
  `~/Developer/macos-game-patches-m2/.code/state/alvr-macos-bridge/`
  `ALVRMacOSBridge.app` verifies as bundle ID
  `com.alvr.macos-bridge.iosurface`, Team ID `MM5YXC7T6E`, and CDHash
  `1731a67fa327ca7c1576f63a084cc3b39f095b41`;
- Launch Services has exactly one matching record for that stable M2 URL;
- all 73 control, 31 install, 77 start, and 31 transaction fixtures pass on the
  M2, together with artifact contract checks and self-tests;
- production `doctor` reports 18 passes and one deliberate failure:
  `prerequisite.host_model` records actual `Mac14,6` versus required `Mac16,9`.

The M2 lane may qualify Launch Services and Local Network behavior and provide
secondary compatibility evidence. It does not satisfy the pinned M4 production
host contract and cannot replace M4 cadence, thermal, game, controller, or
release qualification.

## Verdict

Hardware-free slice passes. Stable Launch Services registration and
steady-state removal of Xcode are implemented and deterministic. Local Network
pending/denied/allowed behavior and persistence across reboot, logout/login,
update, rollback, and uninstall remain the physical issue-#62 gate. The M2 is
ready for exploratory privacy testing while the M4 remains authoritative for
release qualification.
