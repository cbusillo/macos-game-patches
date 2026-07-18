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
- Verified recursive cleanup may add only owner write and execute permission to
  journaled, current-user directories after exact identity and tree-manifest
  validation. Recovery accepts only that normalization, so signed `0555`
  bundles can be removed without admitting changed files or foreign entries.

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

Current hardware-free coverage passes 12 descriptor, 29 transaction, 18
lifecycle, and 23 control fixture groups on macOS and Python 3.12 Linux. Python
compile, artifact check and self-test, profile check and self-test, JSON,
actionlint, Markdown, and tracked-file gitleaks gates also pass.

## Physical Qualification Result

The first physical dev8 candidate exposed one production cleanup defect after a
successful prior-version exchange. Transaction
`d4b4b62d9558492faae49e2ee7b45f36` committed every install operation, but
post-commit deletion of the displaced Developer ID bundle failed with
`transaction.cleanup_failed` because its signed directories and files were
`0555` and `0444`. The committed state remained exact, the journal preserved the
partially deleted quarantine, and the corrected descriptor cleanup resumed that
same transaction without rollback or data substitution. A verified uninstall
then restored the non-anchor baseline.

The corrected qualification used source commit
`86a13fd6ff5d76c795e0d08aee69f9ae91a0dede`, manifest SHA-256
`686bb08ca8c866057240e26a7f251942906765ff590cff2cfc63cdb710d2325f`,
and lock SHA-256
`4d1e70b6b2b4960f7fb545da735ffc8aa4c6a08595bb99fbf13e824f62edf66b`.
Two independent builds produced the same unsealed seal
`3786cf010f011b60a0da31dce10cfef945054343c17a7f5667289be3003207aa`.
Developer ID sealing produced final seal
`ced58eb9b632f3bdf9f795e1e1166c71b395e7446196010616f6828b2518342d`.
The final artifact passed 18 of 18 doctor checks on Mac16,9, arm64, macOS
27.0 build 26A5378n, Xcode 27 build 27A5194q, and CrossOver 26.2.0.39821.

The retained prior tree
`17c74ccbaefd54f9bcd64910cdc29df3548e63a9ac96fdd839cb328582c97d47`
was admitted through Developer ID qualification and atomically migrated to the
corrected tree
`d978f54d504ab1b4e48a68ab7079542901146bd7bc8446a05f92a32af61cd9cb`.
The final bundle verifies as identifier `com.alvr.macos-bridge.iosurface`, Team
ID `MM5YXC7T6E`, authority `Developer ID Application: Shiny Computers Leasing
LLC (MM5YXC7T6E)`, and CDHash
`fb419354ffb45e0983a9ebbb1c4f752f883d6dfb`.

The qualified migration and baseline restoration committed as transactions
`6c09bf69ca574e71890776396278f8c9` and
`03803a4442844f748398b36e5300f831`. Three subsequent physical cycles committed
as:

1. install `e98eab37504c46ae985a6a52310a4890`, uninstall
   `697b34fda79e48dda198f24858997086`;
2. install `ad453eb7006648909b9c43bd9bf6e278`, uninstall
   `3fcc1a5a4f304b24ac50b143d11877a7`;
3. install `7c1f0385c53c4e7b8f83f157eebd0198`, uninstall
   `93a00741446c42ceb154a8fc81d64bfe`.

Every install used plan digest
`4bc80ad2a1fe2f3a6508754b95361f3c873d9cd6c3cb760c653581f7d415cf90`;
every uninstall used
`bc32568fbd12c2c3ff4b9bf7828b46f039366308d4ab2bef7bb3f25517f135e8`.
All eight corrected transactions reported zero cleanup failures and zero
rollback failures. The final read-only plan has no install or uninstall
blockers, every non-anchor operation is ready, the retained bridge exactly
matches the sealed source tree, no transaction quarantine or undo path remains,
and runtime status is `runtime.ready` with no owner or service present.

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
- signed prior-tree cleanup lacks removable directory permissions:
  `transaction.cleanup_failed` after commit; resume only from the exact
  journaled quarantine after descriptor mode normalization;
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

Merge the cleanup correction and physical evidence, then advance #60 and #62
against this qualified installed-layout contract. The next runtime work is the
bounded `start` supervisor plus stable Launch Services and Local Network consent
validation; full game and controller acceptance remains downstream of those
control-plane and macOS lifecycle gates.
