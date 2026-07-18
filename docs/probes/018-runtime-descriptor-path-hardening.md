# Runtime Descriptor Path Hardening

## Question

Can the sealed runtime transaction bind every inspected and mutated filesystem
object to no-follow directory descriptors, so concurrent symlink or rename
changes cannot redirect install, rollback, recovery, or cleanup into a foreign
CrossOver, game, bundle, or runtime-state path?

## Boundary

- Keep the manifest's canonical absolute paths and allowed-root policy, but use
  them only to select authority. Perform target inspection and mutation through
  descriptors opened one component at a time with `O_DIRECTORY` and
  `O_NOFOLLOW`.
- Hold each authority root and target parent identity for the full transaction.
  Revalidate the absolute root and root-relative parent identity before commit.
- Hash files and trees from pinned descriptors. Reject symlinks, unsupported
  entries, device-boundary changes below an authority root, and parent identity
  drift.
- Create staging and rollback entries beside their targets with exclusive
  descriptor-relative operations. Move an existing entry to a verified sibling
  rollback path before publishing the staged replacement without replacement,
  so a raced leaf is preserved rather than overwritten.
- Record the bound parent identity and forward/rollback phase in the journal so
  a new process can refuse recovery when a path now resolves to another object.
- Keep the production live-path gate until descriptor fixtures pass on macOS
  and Linux. Physical CrossOver/game mutation and the three exact-restoration
  cycles remain a separate qualified step.
- Hard cuts: no force or bypass flag, no path-based fallback when descriptor or
  exclusive/exchange rename support is unavailable, no recursive symlink
  following, and no cleanup of a payload whose identity cannot be proven.
- Threat boundary: lifecycle coordination quiesces owned runtime state and
  rejects open targets before mutation. This layer detects observable namespace
  drift but is not a sandbox against an intentionally hostile same-UID owner
  that can rewrite the journal or win the final POSIX name-to-unlink interval.

## Planned Flow

1. Open every authority root by walking from `/` with no-follow descriptors.
2. Bind operation parents beneath the deepest declared root and retain their
   device/inode identities for the transaction session.
3. Re-run preflight reads, ownership checks, and source/target hashes through
   the descriptor session.
4. Write and verify sibling staging payloads through descriptor-relative file
   and tree operations.
5. Use exclusive rename for every publication. Existing targets move first to
   a verified sibling rollback path; absent targets can never be overwritten.
6. Persist each mutation phase, verify the installed object and all authority
   identities, then remove only verified temporary payloads after commit.
7. Recover or roll back through the journaled descriptors and identities; fail
   closed with evidence preserved if rebinding is impossible or ambiguous.

## Fixture Matrix

- component and leaf symlinks before preflight, staging, commit, rollback, and
  cleanup;
- allowed root or target parent rename-and-replace before and during mutation;
- target leaf substitution between guard, staging, and publication;
- exclusive create collision and displaced-original preservation for files and
  complete app trees;
- cross-device descendant, FIFO, socket, device, and nested symlink refusal;
- crash recovery at every publication and rollback phase with matching parent
  identities;
- recovery refusal after root or parent identity drift without touching either
  the old or replacement tree;
- tampered staging, displaced-original, and journal identity records;
- three repeated fixture install/uninstall cycles with byte, mode, journal,
  lock, and owned-path restoration checks;
- macOS `renameatx_np` and Linux `renameat2` exclusive-rename checks with a stable
  unsupported-platform failure.

## Validation

```bash
python3 -m py_compile \
  tools/runtime_descriptor.py \
  tools/runtime_descriptor_test.py \
  tools/runtime_transaction.py \
  tools/runtime_transaction_test.py \
  tools/runtime_install.py \
  tools/runtime_install_test.py \
  tools/runtime_control.py \
  tools/runtime_control_test.py
python3 tools/runtime_transaction_test.py
python3 tools/runtime_install_test.py
python3 tools/runtime_control_test.py
python3 tools/build_runtime_artifact.py check
python3 tools/build_runtime_artifact.py self-test
```

The descriptor, transaction, lifecycle, and control fixture suites must also
pass under the repository's Python 3.12 Linux validation image.

## Cleanup

- Close every duplicated root, parent, source, staging, and tree descriptor on
  success, refusal, injected failure, and rollback.
- Remove only descriptor-verified transaction temporaries after a committed or
  exactly rolled-back operation.
- Preserve the active journal and rollback payloads after identity drift,
  ambiguous recovery, or failed rollback.
- Do not modify real CrossOver, game, Launch Services, or stable runtime paths
  while developing and reviewing the descriptor layer.

## Known Failure Signatures

- unavailable descriptor or atomic rename capability:
  `transaction.descriptor_unsupported`;
- symlink or unsupported entry: `path.symlink` or
  `transaction.tree_unsupported`;
- authority root, parent, or device identity drift:
  `transaction.path_identity_changed`;
- exclusive publication collision: `transaction.target_changed`;
- staged, displaced, installed, or rollback payload mismatch: the existing
  `transaction.write_mismatch`, `transaction.target_foreign`, or
  `transaction.rollback_failed` contract;
- legacy schema-v1 journal: `transaction.journal_invalid`; preserve it for
  diagnosis rather than attempting an unauthenticated in-place migration.

## Expected Artifacts

- a small descriptor filesystem module shared by transaction preflight,
  execution, rollback, recovery, and cleanup;
- journaled authority identities and atomic publication phases;
- adversarial rename, symlink, leaf-race, and crash-recovery fixtures;
- updated lifecycle admission, validation metadata, and operator docs;
- physical-cycle commands and expected evidence for the qualified Mac.

## Issue Routing

GitHub issue #61. Completion unblocks #62's stable installed URL, Launch
Services, Local Network, reboot, and update matrix and #60's bounded installed
layout `start` supervisor.

## Result

The descriptor layer now walks every declared authority root from `/` with
`O_DIRECTORY` and `O_NOFOLLOW`, pins root and parent descriptors, rejects device
changes below a root, hashes files and trees from descriptors, and invalidates
cached descendants when a directory entry moves. Darwin uses
`renameatx_np(RENAME_EXCL)` and Linux uses `renameat2(RENAME_NOREPLACE)`; there
is no pathname or overwrite fallback.

Transaction journal schema v2 records target-parent device/inode/type identity
for every mutation. File and tree replacement stage beside the target, move the
current target to an exclusive sibling, verify the displaced payload, publish
the replacement exclusively, and journal each forward and rollback boundary.
Recovery refuses a replacement parent identity, while a leaf substituted after
intent is moved aside, detected, restored to its name, and never overwritten.
Commit cleanup validates every displaced or staged payload, journals its exact
quarantine identity plus a per-entry identity/content manifest before deletion,
and resumes interrupted recursive cleanup only for the proven remaining subset.
The owner-operated v1 lifecycle quiesces ordinary game/runtime concurrency first;
hostile same-UID namespace manipulation remains outside the authentication
boundary documented by probe 015.

Runtime-owned directory creation and both lifecycle locks now use the same
no-follow descriptor rules. A final sealed plan is no longer rejected merely
because it declares real roots; `doctor` reports the descriptor transaction
boundary as passing after the plan resolves safely. Unsealed artifacts remain
blocked before lifecycle mutation.

The implementation passes 10 descriptor, 22 transaction, 15 lifecycle, and 23
control fixture groups on macOS. The descriptor, transaction, and lifecycle
suites also pass under Python 3.12 Linux using a native container filesystem;
the control suite passes in the same Linux image. The tests cover root/parent
replacement, cached-descendant invalidation, leaf substitution, symlinks,
unsupported entries, exclusive collisions, crash recovery, journal identity
drift, exact rollback, lock contention, and non-fixture declared authority.

No real CrossOver, game, Launch Services, or stable runtime path was modified
while implementing this slice.

## Verdict

`alive`: sealed lifecycle plans now reach a descriptor-anchored transaction
instead of the former `transaction.live_path_hardening_required` blanket gate.
The remaining #61 release evidence is physical, not architectural: three real
install/uninstall cycles must prove exact restoration on the qualified Mac.

## Next Action

Build and seal the current artifact, capture pristine CrossOver/game/runtime
state, then run three install/uninstall cycles with exact content, mode,
signature, service, process, lock, journal, and owned-path comparisons. Stop
before game launch if any restoration or signature evidence differs.
