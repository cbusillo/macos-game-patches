# Runtime Stable Bundle Lifecycle

## Question

Can the transactional runtime update the Developer ID signed bridge at its one
consent-preserving app URL, reject foreign bundles, and leave that stable anchor
installed across runtime uninstall while every mutable patch, service, process,
lock, journal, and staged payload still restores exactly?

## Boundary

- Keep the bridge URL fixed at
  `.code/state/alvr-macos-bridge/ALVRMacOSBridge.app` for the owner-operated v1
  runtime.
- Admit a prior bundle only when the ownership marker, bundle identifier,
  Developer ID authority/team, signature validity, and target path match the
  declared stable-bundle contract.
- Replace an admitted prior bundle through the descriptor transaction at the
  same URL. Preserve its exact tree for rollback until the update commits.
- Treat the current artifact bundle as a retained consent anchor during
  uninstall. Uninstall still removes every mutable runtime effect outside this
  explicit retained resource.
- Establish the physical-cycle baseline after one qualified prior-version
  migration. Each of the following three install/uninstall cycles must retain
  the exact current artifact bundle and restore every other owned path exactly.
- Hard cuts: no manual deletion, no marker-only ownership, no alternate app URL,
  no Team ID or bundle ID drift, no unregister/re-register shortcut, and no
  bypass for an invalid or uninspectable signature.

## Implemented Flow

1. Inspect the stable target without mutation and classify it as absent, exact
   current artifact, verified prior runtime bundle, or foreign.
2. Keep generic artifact planning deterministic and cross-platform. The generic
   plan labels a marker-matched prior tree as requiring qualification; the
   lifecycle adds injected platform identity evidence before any stop, lock,
   journal, or target mutation when no active journal needs recovery.
3. For a verified prior bundle, bind its exact tree digest into the install
   transaction and exchange it atomically with the staged current bundle at the
   same parent and URL using `renameatx_np(RENAME_SWAP)` on macOS or
   `renameat2(RENAME_EXCHANGE)` on Linux.
4. On failure or crash, restore the exact displaced prior tree through the
   existing descriptor journal.
5. On commit, retain the exact current artifact bundle at the stable URL. On
   uninstall, require that exact tree to exist and retain it while removing all
   non-anchor runtime effects.
6. Re-plan after every direction change and fail closed if target identity or
   signature evidence changed after admission.

## Fixture Matrix

- fresh install with no stable bundle;
- exact current bundle replay;
- verified prior signed bundle update at the same URL;
- wrong marker, bundle ID, Team ID, authority, signature, path, or tree refusal;
- prior bundle substitution between admission, intent, displacement, and
  publication;
- crash and rollback at every tree publication boundary;
- uninstall with current bundle retained and all other effects restored;
- install/uninstall/reinstall after retained-bundle replay;
- Linux fixtures with an injected identity inspector and no macOS tool
  dependency;
- three post-migration fixture cycles with exact retained-anchor and pristine
  non-anchor state comparisons.

## Implementation Result

- The dev8 manifest removes `NATIVE_BRIDGE_BUNDLE` as a caller-selectable
  binding and declares the stable URL literally in the guard, replacement,
  retained mutable state, Launch Services state, and uninstall operation.
- Structural validation requires exactly one Developer ID stable-bundle guard,
  one matching atomic replacement sourced from `sealing.bundlePath`, one
  matching `retain_tree`, the signed owner marker, and retained mutable state.
- Generic planning distinguishes absent, exact current, qualification-required,
  and foreign targets. Lifecycle admission verifies a prior bundle's signature,
  authority, Team ID, bundle ID, CDHash, and timestamp policy, then rechecks its
  exact tree digest.
- Transaction intent revalidates the admitted tree through the injected
  inspector. The qualified tree digest and signature evidence are observational
  and do not alter journal plan identity; the ownership and retention policies
  remain semantic and do alter it.
- Existing stable bundles use descriptor-backed atomic exchange. Crashes before
  or after forward or rollback exchange leave the stable URL present with the
  exact prior or exact current tree, and recovery restores the prior tree.
- `retain_tree` is a verified non-mutating uninstall action. Missing, modified,
  unmarked, or foreign trees fail closed rather than silently succeeding or
  deleting the path.

The v1 runtime remains owner-operated. Tree hashes before and after external
`codesign` inspection detect ordinary drift, but the JSON journal, final POSIX
unlink interval, and orchestrated same-UID path swaps remain outside the
authentication boundary against an intentionally hostile owner process.

## Validation

```bash
python3 -m py_compile \
  tools/runtime_descriptor.py \
  tools/build_runtime_artifact.py \
  tools/runtime_transaction.py \
  tools/runtime_install.py \
  tools/runtime_control.py \
  tools/runtime_transaction_test.py \
  tools/runtime_install_test.py \
  tools/runtime_control_test.py
python3 tools/runtime_transaction_test.py
python3 tools/runtime_install_test.py
python3 tools/runtime_control_test.py
python3 tools/runtime_descriptor_test.py
python3 tools/build_runtime_artifact.py check
python3 tools/build_runtime_artifact.py self-test
```

The transaction, lifecycle, and control fixtures must also pass under the
repository's Python 3.12 Linux validation image.

Current hardware-free coverage passes 11 descriptor, 28 transaction, 18
lifecycle, and 23 control fixture groups on macOS and Python 3.12 Linux. Python
compile, artifact check and self-test, profile check and self-test, JSON,
actionlint, Markdown, and tracked-file gitleaks gates also pass.

## Cleanup

- Preserve the pre-update stable bundle until transaction commit or exact
  rollback.
- Never unregister or manually delete the stable URL during this probe.
- Remove temporary fixture bundles, identity evidence, journals, and output
  roots after validation.
- Do not modify real CrossOver, game, Launch Services, or stable runtime paths
  until the implementation and review gates pass.

## Known Failure Signatures

- prior bundle cannot be authenticated: fail before lifecycle mutation with the
  exact identity reason;
- admitted target changes before displacement: `transaction.target_changed` or
  `transaction.path_identity_changed`;
- rollback cannot restore the admitted prior tree:
  `transaction.rollback_failed` with the evidence preserved;
- retained bundle differs during uninstall: fail closed rather than deleting or
  replacing it.

## Issue Routing

GitHub issue #61 owns the transactional migration and exact-restoration gate.
Issue #62 remains blocked until the retained stable URL is available for Launch
Services, Local Network, reboot, logout/login, update, and rollback validation.

## Expected Artifacts

- a declarative stable-bundle ownership and retention contract;
- lifecycle admission evidence for prior Developer ID bundles;
- deterministic upgrade/retain operations bound into journaled transactions;
- cross-platform fixtures for update, rollback, replay, and exact restoration;
- a fresh sealed artifact and three physical post-migration restoration cycles.

## Next Action

Complete independent review and cross-platform gates, publish and verify a fresh
sealed dev8 artifact, then perform one qualified migration followed by three
physical install/uninstall cycles. Each cycle must retain the exact dev8 bridge
and restore every non-anchor path before game launch is enabled.
