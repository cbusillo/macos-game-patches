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
- `preflight.missing`: a required profile path such as the Freedom install root
  is absent before committed-install admission can run.
- `runtime.not_installed`: no exact committed install journal matches the plan.
- `local_network.timeout`: the bounded foreground `open -W` command did not
  complete before the runtime deadline; inspect the bridge and result path
  before retrying. The private consent directory is retained so a late result
  cannot recreate it with unsafe permissions and does not block the next run.
- `local_network.unavailable` or `local_network.os_error`: the bounded system
  command could not be executed.
- `local_network.launch_failed`: Launch Services returned nonzero without a
  valid foreground result.
- `local_network.result_missing` or `local_network.result_invalid`: the bridge
  did not publish the exact private result contract.
- `service.running`: an active bridge service must stop before foreground
  consent.

## Cleanup

- Keep the retained stable bridge bundle and its Launch Services registration
  across ordinary uninstall so macOS can preserve consent where supported.
- Remove only fixture state created under temporary test directories.
- Do not mutate the live Launch Services database during unit tests.
- Archive probe evidence under `.code/probes/028-macos-runtime-readiness/` and
  remove transient logs after durable facts are recorded here and in issue #62.

## Physical Follow-Up

After deterministic validation lands, qualify first-prompt mechanics and
allowed/denied/pending behavior with isolated M2 apps that use both a distinct
bundle identifier and a distinct Mach-O UUID. Never deny the retained stable
identity. If exact clean-user behavior for the stable identity is still needed,
use a separate local macOS user and record it as a distinct operator gate.
Qualify client absence and relaunch, reboot, logout/login, update, rollback, and
uninstall on the stable Developer ID signed bundle without changing its consent
state destructively. Record exact bundle URL, bundle identifier, Team ID,
CDHash, Mach-O UUID, Launch Services records, service PID, client state timing,
and cleanup evidence.

## Remaining Qualification Plan

The remaining matrix is split by host and consent identity. Do not weaken the
sealed host-model contract to manufacture production install state on the M2,
and never use the release-authoritative M4 UUID for a deny experiment.

- **M2 consent-state classification:** use throwaway apps with distinct bundle
  identifiers and Mach-O UUIDs for first prompt, pending, explicit deny, and
  allow recovery.
- **M4 production `consent`:** require an exact committed install journal,
  exact Launch Services identity, and foreground consent result.
- **M4 lifecycle matrix:** test retained UUID, changed CDHash, changed UUID,
  rollback, and retained-anchor uninstall behavior.
- **M4 release service:** prove launchd-owned startup and zero blocked events
  after lifecycle changes.

The M2 throwaway lane exercises the bridge classifier through direct foreground
launches rather than production `runtime_cli.py consent`. The M4 production
command is exercised only with an allowed stable identity. Its non-ready result
mapping therefore remains fixture-backed even after the physical matrix; do not
describe deny or pending as end-to-end production-command evidence.

### M2 Prompt-Free Staging Plan

A read-only Launch Services dump on 2026-08-10 found ten records for the
retained production bundle identifier: the intended stable app plus nine
artifact or probe copies. Throwaway registration is blocked until the retained
identity is restored to exactly one record. Cleanup must unregister only exact
non-retained paths under the known runtime-artifact or probe roots, re-dump after
every mutation, and prove that the stable URL, Team ID, CDHash, Mach-O UUID, and
bundle tree hash remain unchanged.

The prompt-free staging slice is executed in this order:

1. Add a narrow helper with dry-run, exact duplicate cleanup, evidence, stage,
   and rollback operations. It must acquire the lifecycle lock, refuse an active
   bridge service or process, reject symlinks and paths outside explicit roots,
   and never call `open`, load a launchd job, or access TCC.
2. From the pinned ALVR commit, relink four real bridge executables with
   per-lane build input and package them under never-reused bundle identifiers.
   Every lane must have a bundle identifier and Mach-O UUID distinct from the
   stable identity and every other lane. Post-build `LC_UUID` mutation is not a
   supported path; `vtool` does not provide a UUID rewrite operation.
3. Sign and verify the four bundles on the Mac16,9 build host, transfer them to
   a private `0700` M2 staging root outside application auto-registration
   directories, and revalidate SHA-256, plist identity, Team ID, CDHash, UUID,
   architecture, signature, modes, ownership, and quarantine state.
4. Register each exact throwaway app path one at a time without launching it.
   After each registration, require one exact record for that lane and re-prove
   the unchanged exact-one production record.
5. Stop before any launch. The operator-present prompt session is a separate
   slice. Rollback unregisters throwaways in reverse order, verifies zero lane
   records and one unchanged production record, and removes the staging tree
   only after those checks pass.

Expected staging evidence includes the initial and final Launch Services record
sets; stable identity and tree hashes; lane bundle IDs, UUIDs, executable hashes,
Team IDs, CDHashes, signature results, paths, modes, ownership, and quarantine
state; every exact unregister/register action; bridge process and service
absence; and cleanup status. Known failure signatures are
`consent_stage.active_service`, `consent_stage.duplicate_baseline`,
`consent_stage.path_refused`, `consent_stage.stable_changed`,
`consent_stage.identity_collision`, `consent_stage.uuid_collision`,
`consent_stage.signature_invalid`, `consent_stage.register_failed`, and
`consent_stage.residue`.

The supported per-lane relink command keeps the pinned source tree unchanged and
adds one deterministic marker section to the final executable:

```bash
printf '%s' "$lane_bundle_id" >"$lane_marker"
CARGO_TARGET_DIR="$target_root" cargo rustc \
  --manifest-path "$pinned_alvr_checkout/Cargo.toml" \
  -p alvr_macos_bridge \
  --bin alvr_macos_bridge \
  --release \
  -- \
  -C "link-arg=-Wl,-sectcreate,__TEXT,__consent_lane,$lane_marker"
```

The app packaging step copies that executable to
`Contents/MacOS/alvr_macos_bridge`, writes the lane-specific plist, and signs
the final bundle with the retained Developer ID identity, hardened runtime, and
`--timestamp=none`. The staging helper then independently validates the plist,
signature, Team ID, CDHash, UUID, executable hash, tree hash, ownership, modes,
quarantine state, and Launch Services records.

The 2026-08-10 build-host probe reproduced identical bytes and UUID when the
same marker was linked twice, while different markers produced distinct UUIDs.
Run `r20260810t142139z` prepared four signed arm64 lanes with unique IDs and
UUIDs `23436645-2C52-35E6-8A0E-EFB72C6766A2`,
`1118445C-27C0-38E7-B240-B78DC2FCA333`,
`2E94FB7B-650A-3EAB-8522-3406D33D9E12`, and
`46986F17-93E8-3443-8153-32F28FD27B44`. Every bundle verifies under Team ID
`MM5YXC7T6E`, carries the hardened-runtime flag, and has no quarantine xattr.
No bundle was launched or registered during the build-host probe.

The bridge classifier pinned at ALVR commit
`9bc309546fd1c4cdb229ec2a5f11e304154dfc3d` uses `NWBrowser` state and its
error only; it does not inspect `NWPath` or query TCC state:

- `ready` publishes `ready` with `networkAvailable: true`.
- `waiting` with DNS `kDNSServiceErr_PolicyDenied` publishes `policy-denied`
  with `networkAvailable: false`.
- Any other `waiting` state publishes `waiting` with
  `networkAvailable: null`.
- `failed` publishes `failed` with `networkAvailable: null`.
- `cancelled`, including the bridge's 120-second modal watchdog, publishes
  `cancelled` with `networkAvailable: null`.
- Browser setup failure publishes `setup-failed` with
  `networkAvailable: null`.

The initial bridge outcome is `waiting`. A Continue click before any browser
state therefore publishes `waiting`. Browser cancellation preserves an already
observed `ready` or `policy-denied` outcome, but the 120-second modal watchdog
unconditionally replaces the current outcome with `cancelled`. The operator
must press Continue after an allow or deny observation before that watchdog to
preserve the observed result.

The production command allows 150 seconds for `open -W`, leaving a 30-second
margin beyond the bridge watchdog for result publication and process exit. The
fixture contract pins that bound so the ordinary command-runner default cannot
silently shorten an operator consent session.

`policy-denied` is therefore a specific Network.framework observation, while
`waiting` intentionally combines undecided consent and other non-policy wait
conditions. The physical matrix must preserve that distinction instead of
claiming a general supported Local Network permission query.

Automation may launch the exact bridge, authenticate its process and window,
press the bridge-owned `Continue` button, collect the private `0600` result,
and observe bounded system logs. Only the operator may answer Apple's Local
Network prompt or change its System Settings switch. Automation must not press
Apple's Allow or Don't Allow controls, read the TCC database, or bypass the
sealed host-model prerequisite.

Execute the matrix in this order:

1. Add deterministic fixture coverage for every supported non-ready outcome,
   command timeout, nonzero `open` exit with a valid result, and transient-state
   cleanup or safe timeout-evidence preservation.
2. Stage M2 identity, Launch Services, helper, process, and bounded-log evidence
   without opening a privacy prompt.
3. With an operator present, use separate throwaway bundle identifiers and
   UUIDs for first-prompt allow, pending, explicit deny, and allow recovery.
   Perform deny last and never against either retained stable identity.
4. On the M4, run the production command from an exact committed installation,
   then qualify ordinary uninstall, retained-UUID update, changed-CDHash update,
   changed-UUID update, and transaction rollback.
5. After every case, prove no bridge process, launchd job, transient result, or
   duplicate Launch Services record remains. A runtime timeout retains its
   private result directory for late evidence and retry; remove it only after
   the exact bridge exits and the result is archived. Preserve the stable
   consent anchor unless the case explicitly tests a distinct throwaway app.

## Actual Evidence

Consent-matrix preparation on 2026-08-10 passes:

- all 85 runtime-start fixtures locally and on the M2 with the documented short
  `/private/tmp` fixture root, including every supported non-ready consent
  result, timeout action reporting, nonzero `open` exit with a valid result,
  completed-path cleanup, and safe late-result retry after timeout;
- source inspection of pinned ALVR commit
  `9bc309546fd1c4cdb229ec2a5f11e304154dfc3d` confirms the documented
  `NWBrowser` classifier, 120-second modal watchdog, private atomic result
  publication, and process exit behavior; and
- the M2 remains intentionally unable to create a production committed install
  because the sealed release artifact requires Mac16,9. The physical consent
  state lane uses throwaway UUIDs without weakening that prerequisite.

A read-only production `consent` admission rerun on the M2 on 2026-08-10
stopped at `preflight.missing` for the absent Freedom install root. It did not
launch the bridge, mutate the stable app, or open a privacy prompt. This confirms
that the dated post-merge observation below remains accurate; the later
`runtime.not_installed` gate is reachable only after profile preflight passes.

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
result. Clean-user behavior for the exact stable identity and persistence across
update, rollback, and uninstall remain physical follow-up gates. Explicit deny
and pending qualification must use an isolated throwaway bundle identifier and
UUID rather than the retained stable identity.

The M2 lane may qualify Launch Services and Local Network behavior and provide
secondary compatibility evidence. It does not satisfy the pinned M4 production
host contract and cannot replace M4 cadence, thermal, game, controller, or
release qualification.

## Verdict

Hardware-free and foreground-consent slices pass. Stable Launch Services
registration, steady-state removal of Xcode, and the exact-executable visible
consent path are implemented. The M2 qualifies historical denied-state evidence,
allowed recovery, and a final dev15 foreground `ready` result while preserving
one stable identity. Clean-user behavior for that stable identity, isolated
throwaway-app deny and pending states, and persistence across update, rollback,
and uninstall remain physical issue-#62 gates. Reboot and logout/login
persistence already pass on the M2 secondary lane. The M4 remains authoritative
for release qualification.
