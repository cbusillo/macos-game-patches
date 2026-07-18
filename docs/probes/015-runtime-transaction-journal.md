# Runtime Transaction Journal

## Question

Can the resolved runtime install/uninstall operation model execute inside an
isolated filesystem with durable journaling, reverse-order rollback, crash
recovery, and idempotence before any production path or sealed manifest changes
are enabled?

## Boundary

- First slice: a generic filesystem transaction executor and hardware-free
  fixtures under temporary `.code/` roots.
- Inputs: already-resolved absolute operations shaped like the artifact
  install/uninstall plan records. The executor enriches directory-source
  records with a canonical `sourceTreeSha256` before semantic plan identity is
  calculated; the sealed planner itself remains unchanged in this fixture slice.
- Outputs: an atomic JSON journal, transaction-owned undo payloads, committed or
  rolled-back filesystem state, and stable failure codes.
- Deferred: production `install`/`uninstall` CLI commands, a declared persistent
  journal path in the sealed manifest, app signing/Launch Services changes,
  physical lifecycle cycles, any mutation of user CrossOver/game paths, and an
  authenticated journal for a hostile same-user rewrite threat model.
- Hard cuts: no best-effort deletion, no following symlinks, no target outside
  allowed roots, no undeclared action, no continuation after plan/journal drift,
  and no dev5 manifest edit merely to expose this fixture slice.

## Transaction Contract

Each journal records:

- schema version, transaction id, install/uninstall kind, plan digest, state,
  created/updated timestamps, and current step;
- every operation id, action, target, status, and durable undo description;
- the original failure and any rollback failures;
- one of `prepared`, `running`, `rolling-back`, `committed`, `rolled-back`, or
  `failed`.

The journal is written atomically before and after every mutation boundary.
File snapshots live in the transaction root. Rename-based file/tree payloads
use deterministic hidden siblings in the target parent so atomic swaps remain
on the target filesystem; those paths are part of the validated journal and
are removed after commit or complete rollback. A new process encountering
`running` or `rolling-back` recovers by rolling back all prepared/applied
mutations in reverse order rather than guessing forward progress.

## Supported Fixture Actions

- assertions: `assert_sha256`, `assert_absent`, `assert_absent_or_owned`;
- installation: `backup`, `replace_file`, `create_file`, `replace_tree`;
- uninstallation: `restore`, `remove`, `remove_tree`;
- no-op ownership declarations: `retain`.

All file and tree comparisons use SHA-256 and exact ownership markers. The
resolved `replace_tree` record inherits its marker contract from the preceding
matching `assert_absent_or_owned` record, mirroring the artifact planner's
actual output. Canonical tree digests cover relative paths, entry types, modes,
sizes, and file hashes for source staging, committed replay, and exact rollback.
Atomic staging occurs in the target parent, and the executor rejects symlink
path components before preflight or mutation.

## Execution Rules

1. Validate the complete plan, action schema, unique ids, resources, transaction
   kind, guard relationships, allowed roots, sources, targets, backups,
   markers, and journal location without target mutation.
2. Persist the prepared journal and transaction-local undo directory.
3. Recheck each operation immediately before applying it.
4. Persist undo intent, apply atomically, then persist `applied`.
5. On an ordinary failure, record the failure and roll back every applied step
   in reverse order before returning failure.
6. On simulated process death, leave the durable `running` journal. A later
   recovery invocation rolls back from recorded undo intent.
7. A committed journal with the same semantic plan digest is an idempotent
   success without live forward preflight. Volatile planner observations such
   as `ready`, `exists`, and live hashes do not affect identity; a changed
   execution field, canonical tree source digest, or transaction kind fails
   closed.

## Fixture Matrix

- successful mixed install transaction;
- successful restore/remove uninstall transaction;
- assertion failure before the first mutation;
- injected failure after each mutating step with exact rollback;
- simulated crash followed by new-process recovery;
- committed transaction replay without additional mutation;
- mismatched plan digest and transaction kind refusal;
- target escape, parent symlink, source symlink, journal symlink, duplicate id,
  unknown action, malformed marker, and foreign ownership marker refusal;
- backup mismatch, deterministic undo-path and terminal-state inconsistency
  refusal, rollback crash resumption, and terminal rollback-failure journaling.

## Validation

```bash
python3 -m py_compile \
  tools/runtime_transaction.py \
  tools/runtime_transaction_test.py
python3 tools/runtime_transaction_test.py
```

The tests must also pass in a Linux Python container because CI is hosted on
Ubuntu. No fixture may access `/Applications`, a real CrossOver bottle, Steam,
or the user runtime state root.

## Expected Artifacts

- reusable transaction module with no production CLI entrypoint;
- 15 deterministic install, uninstall, rollback, recovery, path-safety,
  journal-integrity, and idempotence fixtures;
- journal examples under ignored temporary paths during tests;
- updated CI and repository validation metadata;
- one focused PR that leaves #61 open for manifest/dev6 and production command
  integration.

## Known Failure Signatures

- plan digest or kind differs from an existing journal: refuse and preserve it;
- committed journal effects disagree with live files: refuse before cleanup;
- target or journal escapes allowed roots: refuse before mutation;
- any symlink component in a mutable path: refuse before mutation;
- source/target/backup hash mismatch: preflight failure, no mutation;
- ordinary injected failure: `rolled-back` only if every undo succeeds;
- simulated crash: journal remains `running` until explicit recovery completes;
- rollback failure: `failed`, preserve journal and undo payloads, and refuse a
  new transaction on that path.

## Result

The fixture implementation passes all 15 tests on macOS and Linux. It commits
every resolved action shape, restores byte-for-byte target snapshots after
injected failures, recovers crashes at intent/mutation/applied and rolling-back
boundaries, ignores volatile planner observations for committed replay, rejects
semantic plan or kind drift, and validates every journal-owned cleanup/undo path
before use. Committed file and tree effects are revalidated against persisted
content digests before cleanup or idempotent replay succeeds. Equivalent
artifact relocation preserves plan identity through content hashes, changed
source content still produces semantic drift, modified remove-tree rollback
payloads fail closed, and an unchanged original file no longer needs a second
write during rollback.

The JSON journal is not an authentication boundary against an owner who can
rewrite both the journal and target files. This slice detects structural drift,
unsafe path substitution, invalid transitions, and terminal-state/filesystem
inconsistency; production integration must either retain that owner-operated
threat model or add an external integrity anchor.

## Verdict

`alive`: every fixture either commits exactly, restores its byte-for-byte
pre-transaction state, or preserves a truthful terminal failure journal and
undo payload when rollback is intentionally sabotaged.

## Next Action

Implement the isolated executor and failure matrix, review the journal schema,
then decide the dev6 manifest entries required for production journal and
backup ownership.

## Issue Routing

GitHub issue #61.
