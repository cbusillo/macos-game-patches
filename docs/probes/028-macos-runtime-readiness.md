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
- `launch_services.bundle_missing`: the retained stable app is absent.
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

Physical Local Network qualification then exercised the exact retained bridge
identity in
`m2-consent-20260809T005112Z`:

- a native arm64 producer imported all three IOSurface slots, passed all three
  startup self-tests, and released the startup barrier without requiring the
  CrossOver bottle or a game process;
- the untouched stable bridge opened TCP `8082` and IPv4/IPv6 mDNS sockets on
  `5353`, but its first launchd-owned network attempt showed no visible prompt;
  `UserEventAgent` repeatedly recorded `Local Network blocked`, allowed state
  `0`, for production Mach-O UUID
  `5C137FC7-CC15-3CE2-AC16-D7DC2E532FC8`;
- a foreground helper with the same bundle identifier and Team ID could show
  the prompt, but its grant did not transfer because it had a different Mach-O
  UUID;
- a temporary foreground-capable copy preserved the exact production Mach-O
  bytes and UUID while changing only bundle metadata and signature. Launchd
  still could not present a prompt, but registering that exact UUID made the
  System Settings entry actionable. Toggling `ALVR macOS Bridge` off and back
  on authorized the UUID;
- after restoring the untouched stable app, its original CDHash
  `1731a67fa327ca7c1576f63a084cc3b39f095b41` completed the same authenticated
  producer handshake with zero Local Network blocked events during the
  observation window. The remaining `Host is down`, `No route to host`, or
  timeout result reflected the offline trusted client and is not used as a
  permission verdict;
- cleanup unloaded the test LaunchAgent, stopped the bridge and producer,
  removed both temporary app copies, and restored exactly one Launch Services
  record for the stable URL, Team ID, bundle ID, and original CDHash.

This qualifies denied-state detection and allowed-state recovery on the M2. It
also exposes a production UX blocker: the background-only launchd bridge cannot
reliably present its first Local Network prompt. A foreground consent trigger
or explicit System Settings remediation must be product-owned before the
runtime can promise first-run self-service.

### Foreground Consent Slice Plan

The next bounded implementation keeps one executable identity for both modes:

1. add a foreground `--local-network-consent` mode to the native bridge binary;
2. package the stable app as foreground-capable while keeping ordinary launchd
   execution headless;
3. add a production CLI command that verifies the exact installed bundle and
   Launch Services record before launching that mode through Launch Services;
4. cover command construction, identity refusal, result mapping, and legacy
   report behavior with hardware-free fixtures;
5. rebuild and seal the bridge, then repeat first prompt, allowed recovery, and
   exact cleanup on the M2 before any M4 release claim.

A separate helper executable is not acceptable because the physical matrix
proved that a grant for a different Mach-O UUID does not authorize the bridge.
The runtime must not rewrite or resign the installed stable app to obtain
consent.

### Foreground Consent Evidence

On 2026-08-09, the bounded foreground slice passed on `chris-mbp`:

- macOS runtime source `a77fc8f54c2be54f6300c19ba3ef2c650d337eab`
  pins ALVR host source `9bc309546fd1c4cdb229ec2a5f11e304154dfc3d`;
- sealed dev15 artifact
  `5ca1da2e103e2ec084560b2f5810ce7c6e0724183414b9100df14c4e6039f735`
  verifies on the M2;
- the installed stable app has bundle ID
  `com.alvr.macos-bridge.iosurface`, Team ID `MM5YXC7T6E`, CDHash
  `01b9c2734e227f9b70803956b157dec3766f7569`, and Mach-O UUID
  `B611B6E5-39AA-356B-AFBE-412F46E630EE`;
- Launch Services reports exactly one record for that URL and identity;
- the same executable launched through `open -W -n` registered as a visible,
  frontmost AppKit application and displayed the product-owned consent alert;
- its real Network.framework Bonjour browser reported ready, the bridge wrote a
  private `0600` result with `networkAvailable: true`, and the process exited
  after Continue;
- the observation window recorded zero `Local Network blocked` events, and
  final cleanup found no bridge service or bridge process while retaining the
  exact stable Launch Services record;
- all 76 control, 31 install, 82 start, and 31 transaction fixtures pass on the
  M2 with artifact checks and self-tests; all 30 ALVR bridge library tests pass
  on the build host.

The post-review rebuild changed only the signed artifact metadata and CDHash;
the foreground-qualified Mach-O bytes and UUID are unchanged. The final app was
installed and passed exact Launch Services and doctor verification. A console
lock interrupted the redundant visible repeat, so the pending foreground test
process was terminated and no new prompt result is claimed for that repeat.

### Post-Merge Persistence Baseline

On 2026-08-09, the clean M2 lane was synchronized to merged `main` at
`493a83045107578869122c8a0ff7426d40e31688`. Evidence is archived under
`.code/probes/028-macos-runtime-readiness/persistence-baseline-20260809T051412Z/`:

- sealed artifact
  `5ca1da2e103e2ec084560b2f5810ce7c6e0724183414b9100df14c4e6039f735`
  verifies exactly;
- runtime status is `installed`, with no launchd service or bridge process and
  exactly one Launch Services record for the final stable URL, bundle ID, Team
  ID, and CDHash;
- doctor still reports 18 passes and only the deliberate Mac14,6 versus
  Mac16,9 host-model failure;
- the cleaned exploratory lane has no committed transaction journal and no The
  Lab install root. The production `consent` command therefore stopped safely
  at `preflight.missing` before launching or mutating the stable app;
- direct exact-app launches from SSH reached `NSAlert runModal`, but the modal
  was not exposed to SSH-session accessibility automation. Controlled cleanup
  produced one `cancelled` result with `networkAvailable: null` and one run with
  no result; neither run recorded a `Local Network blocked` event, and both
  ended with no service or bridge process;
- FileVault is enabled and automatic login is disabled, so an unattended reboot
  would stop at interactive login. Reboot and logout/login persistence remain
  deferred until a physical or Screen Sharing operator can restore the same GUI
  session; and
- update, rollback, and ordinary uninstall were not attempted without a
  committed install plan and verified candidate artifacts. The M2 host-model
  mismatch must not be bypassed to manufacture that state.

These post-merge probes qualify the exact retained identity and cleanup state,
but they do not add an allowed, denied, pending, reboot, logout/login, update,
rollback, or uninstall permission verdict.

### Consent Admission Repair

On 2026-08-09, detached post-merge review found that production `consent`
required an `installed` detail that the exact Launch Services validator never
returns on success. Source commit `c3b6f14a5fb9263851f649d7b98b96b647ee2589`
now admits the validator's `pass` result directly while preserving every
missing, ambiguous, wrong-path, wrong-Team-ID, wrong-CDHash, query, and active
service refusal.

- all 82 start, 76 control, 31 install, and 31 transaction fixtures pass;
- artifact contract checks and all artifact self-tests pass;
- sealed artifact
  `c615297f7553f0f92072884a388ad5cce7d3290f49d3578ee7d20c0986f1ab58`
  verifies with the same foreground-qualified Mach-O UUID
  `B611B6E5-39AA-356B-AFBE-412F46E630EE`;
- the rebuilt Developer ID signature has Team ID `MM5YXC7T6E`, bundle ID
  `com.alvr.macos-bridge.iosurface`, and CDHash
  `40df4909a1a674f2d6702d024c69d15cc023a475`; and
- no new Local Network permission verdict is claimed until this exact sealed
  artifact is installed and exercised from the M2 host-local GUI Lab session.

This proves the visible exact-executable foreground path and the already-allowed
result. A clean-user first prompt, explicit deny for the final UUID, and
persistence across reboot, logout/login, update, rollback, and uninstall remain
physical follow-up gates.

The M2 lane may qualify Launch Services and Local Network behavior and provide
secondary compatibility evidence. It does not satisfy the pinned M4 production
host contract and cannot replace M4 cadence, thermal, game, controller, or
release qualification.

## Verdict

Hardware-free and foreground-consent slices pass. Stable Launch Services
registration, steady-state removal of Xcode, and the exact-executable visible
consent path are implemented. The M2 qualifies historical denied-state evidence,
allowed recovery, and a final dev15 foreground `ready` result while preserving
one stable identity. Clean-user first prompt, explicit deny for the final UUID,
pending-state behavior, and persistence across reboot, logout/login, update,
rollback, and uninstall remain physical issue-#62 gates. The M4 remains
authoritative for release qualification.
