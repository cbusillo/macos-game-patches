# Runtime Install Coordinator

## Question

Can the artifact planner and durable transaction executor be joined behind
production-shaped `install` and `uninstall` commands without permitting the
current pre-sign artifact to mutate a real CrossOver, game, or runtime path?

## Boundary

- Add one artifact-aware lifecycle coordinator over `build_plan()` and
  `TransactionExecutor` rather than resolving or executing raw operations in
  the CLI.
- Declare a retained transaction root, global lifecycle lock, active journal,
  history directory, rollback workspace, and content-namespaced stock backups
  in the dev6 manifest.
- Serialize install and uninstall through one global lock. Keep one active
  journal for both directions and archive only verified terminal journals.
- Admit a fresh transaction only after artifact/contract verification,
  readiness checks, exact stop, bounded open-file inspection, and conservative
  per-filesystem capacity checks.
- Exercise every mutation below temporary `.code/` fixture roots. Tests must
  fail if a resolved path reaches `/Applications`, a real CrossOver bottle,
  Steam game content, or the user's actual runtime state root.
- Return `artifact.sealing_required` before creating directories, stopping a
  service, writing a journal, or changing a target while the manifest uses
  separate-step signing.
- Hard cuts: no force flag, no readiness bypass, no caller-supplied journal or
  allowed-root authority, no automatic repair of a failed rollback, and no
  post-transaction signing of an installed tree.

## Planned Ownership Layout

```text
${HOME}/Library/Application Support/alvr/runtime-state/mac-alvr-runtime/runtime.lock
${HOME}/Library/Application Support/alvr/runtime-state/mac-alvr-runtime/transactions/transaction.json
${HOME}/Library/Application Support/alvr/runtime-state/mac-alvr-runtime/transactions/transaction.json.lock
${HOME}/Library/Application Support/alvr/runtime-state/mac-alvr-runtime/transactions/transaction.json.undo
${HOME}/Library/Application Support/alvr/runtime-state/mac-alvr-runtime/transactions/history/<transactionId>-<kind>-<state>.json

${HOME}/Library/Application Support/alvr/runtime-state/mac-alvr-runtime/backups/crossover_moltenvk-<stock-sha256>.dylib
${HOME}/Library/Application Support/alvr/runtime-state/mac-alvr-runtime/backups/game_openvr-<stock-sha256>.dll
```

- Runtime-owned directories use mode `0700` and the current UID.
- Locks and journals use mode `0600` and the current UID.
- The non-overridable per-user lock, journal, history, and backup namespace is
  shared by every checkout and bindings file for this runtime id. The undo
  directory is transient and is preserved only when rollback cannot complete.
- Stock backups remain retained after uninstall so their exact restoration
  evidence is available for diagnosis and a later qualified reinstall.
- Missing CrossOver, Steam, or game directories are blockers. The coordinator
  may create only declared runtime-owned container directories.

## Lifecycle Rules

1. Verify the artifact against the checked-in manifest and lock.
2. Refuse both directions without mutation when the artifact still requires a
   separate signing step.
3. Resolve both directions from `build_plan()` and use only its allowed target
   roots and operation records.
4. Acquire the global lifecycle lock and settle the active journal:
   - matching committed direction is an exact idempotent success;
   - opposite committed direction is verified and archived before proceeding;
   - incomplete or rolled-back work is recovered and archived, then the command
     returns retry-required without starting a new forward transaction;
   - failed rollback or plan drift is preserved and blocks every new mutation.
5. For a fresh install, require a passing doctor report. For either direction,
   stop exact-owned runtime state and reject open mutation targets.
6. Rebuild the live plan, reject every blocker, verify capacity on each affected
   filesystem, and execute the requested operations unchanged.

## Capacity Model

Before journal creation, reserve space for:

- retained stock backups on each backup filesystem;
- file snapshots in the transaction workspace;
- atomic file staging beside each target;
- complete tree staging beside each tree target; and
- bounded journal/metadata overhead.

The estimate may be conservative. Insufficient or uninspectable capacity must
return a stable blocker before the active journal or any target changes.

## Fixture Matrix

- current separate-step artifact gate with zero filesystem or stop actions;
- resolved plan includes exact directory-source digests and allowed roots;
- install commit and exact same-direction replay;
- install-to-uninstall transition with terminal journal archive;
- uninstall replay and full reinstall cycle;
- crash recovery returns retry-required before an opposite direction begins;
- failed rollback and semantic plan drift preserve the active journal;
- global install/uninstall lock contention;
- planner blocker, open target, and per-volume capacity refusal before journal
  creation;
- runtime-owned directory and lock permissions, symlink refusal, and history
  collision refusal;
- CLI text/JSON rendering and stable domain, usage, and internal exit classes;
- real-path sentinel fencing every fixture operation.

## Validation

```bash
python3 -m py_compile \
  tools/build_runtime_artifact.py \
  tools/runtime_transaction.py \
  tools/runtime_install.py \
  tools/runtime_cli.py \
  tools/runtime_install_test.py
python3 tools/runtime_install_test.py
python3 tools/runtime_transaction_test.py
python3 tools/runtime_control_test.py
python3 tools/build_runtime_artifact.py check
python3 tools/build_runtime_artifact.py self-test
```

The lifecycle fixture suite must also pass in the Linux CI environment.

## Expected Artifacts

- dev6 fixed per-user ownership, journal, and backup paths in the manifest;
- complete planner authority for allowed roots and directory-source digests;
- production-shaped lifecycle coordinator plus CLI entrypoints;
- hardware-free install, uninstall, replay, transition, recovery, locking,
  capacity, and fail-closed sealing fixtures;
- updated CI, artifact documentation, and repository validation metadata.

## Known Failure Signatures

- separate-step artifact: `artifact.sealing_required`, no lifecycle mutation;
- held global lock: `transaction.busy`;
- incomplete journal: rollback recovery, archive, and `transaction.retry_required`;
- failed rollback or journal/plan drift: preserve evidence and refuse mutation;
- planner blocker: `plan.blocked` with operation ids and reasons;
- open target: `runtime.target_busy`;
- insufficient capacity: `capacity.insufficient` before journal creation;
- unsafe ownership, mode, symlink, or path escape: fail closed before mutation.

## Deferred Live Gate

The current artifact's native bridge is signed after artifact construction.
Signing changes the exact tree digest used by transaction replay, so enabling a
real install would break the frozen artifact identity contract. Issue #62 must
define and verify the post-sign bundle identity before #61 can remove the
sealing gate. Descriptor-anchored pathname mutation and physical lifecycle
cycles also remain required before real user-path qualification.

## Result

The coordinator passes 14 lifecycle fixtures on macOS and Linux. The suite
proves install commit, same-direction replay, install-to-uninstall archive,
uninstall replay, reinstall, interrupted recovery with retry-required, recovery
refusal while a target is open, fixed-lock contention, early planner and
capacity admission, private path modes, symlink refusal, recursive bundle-file
inspection, truthful committed cleanup failure reporting, and stable CLI JSON.

The lower-level executor passes 15 fixtures, including full-digest validation of
remove-tree rollback payloads, source-path-independent plan identity for an
equivalent relocated artifact, and no-op rollback when the original file never
changed. Both suites pass in a Python 3.12 Linux container as well as the local
macOS environment. CI now retains Ubuntu coverage and adds a native macOS
lifecycle job.

The checked-in dev6 contract intentionally remains `separate-step`. A verified
artifact therefore returns `artifact.sealing_required` before creating the
lifecycle namespace, stopping runtime state, resolving live target operations,
or writing a transaction journal. No real CrossOver, game, or user runtime path
was mutated by this slice.

## Verdict

`alive`: the production-shaped coordinator is fail-closed, serialized across
checkouts, crash-recoverable inside isolated roots, and ready to consume a
future transaction-compatible signed tree. Target-binding changes still reject
an active journal and preserve it; recovery requires the original target
bindings even when an equivalent artifact is relocated.

## Next Action

Issue #62 must define the stable signed bundle URL and verified post-sign tree
identity. After that contract exists, rerun these lifecycle gates against the
qualified artifact before enabling real paths or resuming issue #60's installed
layout `start` work.

## Issue Routing

GitHub issue #61. After this coordinator lands, #62 becomes the next active
dependency for a transaction-compatible signed bundle; #60 remains blocked
until a qualified installed layout exists.

## Successor Status

Probe 017 now provides the dev7 immutable post-build Developer ID seal and a
verified exact signed-tree identity. The remaining #61 live-enable work is
descriptor-anchored target mutation plus physical install/uninstall restoration
cycles; #62 separately retains stable URL, Launch Services, and privacy-consent
qualification.
