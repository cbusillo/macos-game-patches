# Reproducible Runtime Artifact

This document defines the manifest-backed artifact boundary for the
owner-operated Mac ALVR runtime. GitHub issue #58 owns implementation status;
issue #62 owns the Developer ID final-sealing extension. This file records the
durable build, provenance, sealing, and ownership contract.

## Purpose

The existing physical pipeline is proven, but its binaries come from sibling
checkouts and local CrossOver, DXVK, and MoltenVK build trees. The artifact
system converts those explicit inputs into one immutable, content-addressed
runtime payload without changing the frame path or installing anything.

The artifact builder does not claim that opaque local CrossOver build trees are
reproducible. It verifies their outputs against checked-in hashes and records
the recipes and patch identities that produced them. This is the honest
reproducibility boundary until a later bootstrap issue reconstructs those source
trees automatically.

## Decisions

- Use strict JSON for the manifest, lockfile, bindings, metadata, and dry-run
  plans so the standard-library Python tool has no parser dependency.
- Keep human-reviewed intent in `runtime/manifest.json` and resolved binary
  hashes in `runtime/manifest.lock.json`.
- Treat sibling Git checkouts as source identities pinned by remote and commit.
- Classify every staged binary as a pinned-Git, repo-source, or opaque-local
  build output; pin each by content hash and link it to its build recipe.
- Produce an immutable deterministic unsealed artifact, then Developer ID sign
  only a verified private copy and publish a second immutable final artifact.
- Bind the source seal, contract hashes, pre-sign tree, final signed tree,
  Developer ID authority, Team ID, bundle identifier, CDHash, and no-timestamp
  policy into canonical final-sealing provenance.
- Publish artifacts atomically into a content-addressed directory under
  `.code/runtime-artifacts/`.
- Keep plan resolution read-only and authoritative. Issue #61 consumes those
  exact operations through a separate lifecycle coordinator. Unsealed artifacts
  remain mutation-gated; only a verified final-stage artifact can become ready.
- Never fetch or download dependencies while validating or building.
- Keep executable prerequisite commands and allowed mutation roots in trusted
  builder policy rather than allowing the manifest to widen its own authority.

## Files

```text
runtime/
  bindings.example.json
  manifest.json
  manifest.lock.json
tools/
  build_runtime_artifact.py
  runtime_control.py
  runtime_start.py
  runtime_cli.py
  runtime_install.py
  runtime_transaction.py
.code/
  runtime-bindings.json        # local, ignored
  runtime-artifacts/           # generated, ignored
```

The checked-in manifest and lock contain no machine-specific absolute paths.
Local paths are supplied through the ignored bindings file.

## Input Classes

### Git Sources

Git inputs record a repository URL, local path binding, exact commit, and clean
worktree policy. The builder verifies the current commit and tracked state
before reading any generated binary from the checkout.

The repository itself permits unrelated untracked files because each
repo-owned source file is independently hash-pinned. Sibling source checkouts
must be entirely clean.

### Repository Sources

Repo-owned build sources, protocol headers, patch files, recipes, the artifact
builder, runtime lifecycle/transaction/control tools, the unchanged production
runner, the artifact contract, and the frozen v1 scope record are tracked by
Git and pinned by SHA-256. A source edit therefore invalidates the manifest
before an artifact can be produced.

### Opaque Local Build Outputs

The following are local build products, not vendored inputs:

- patched CrossOver DXVK `d3d11.dll` and `dxgi.dll`;
- patched CrossOver MoltenVK `libMoltenVK.dylib`;
- the CrossOver Wine bridge `.dll` and Unix `.so`.

The custom OpenVR binaries are repo-source builds. The native
`alvr_macos_bridge` is a pinned-Git build from the exact ALVR host commit.

Each output has one manifest identity, one artifact destination, one normalized
format/kind/architecture contract, and one lockfile hash. PE certificate-table
state and required Mach-O signatures are checked explicitly. Missing, changed,
wrongly signed, or wrongly architected files fail closed.

### User-Supplied Prerequisites

CrossOver, Xcode, the Apple SDKs, Developer ID credentials, Steam, official game
payloads, hardware, and privacy consent are not copied into the artifact unless
an explicitly listed runtime binary is required. CrossOver version checks are
assertions against the local user-owned application.

## Artifact Layout

```text
mac-alvr-runtime-<version>-<full-seal>/
  payload/
    macos/
      ALVRMacOSBridge.app/
        Contents/_CodeSignature/CodeResources  # sealed stage only
        Contents/Info.plist
        Contents/MacOS/alvr_macos_bridge
        Contents/Resources/runtime-owner.json
        Contents/Resources/runtime-sealing.json # sealed stage only
      libMoltenVK.dylib
    unix/
      alvr_iosurface_bridge.so
    windows/
      alvr_iosurface_bridge.dll
      d3d11.dll
      dxgi.dll
      openvr_api.dll
      openvr_api.real.dll
  config/
    launch-agent.plist.template
    runtime-defaults.json
  plans/
    install.template.json
    uninstall.template.json
  contract/
    manifest.json
    manifest.lock.json
  provenance/
    artifact.json
    build-inputs.json
    files.sha256
    sealing.json             # sealed stage only
```

The seal is the SHA-256 of the canonical manifest hash, lock hash, and sorted
content records. Those records cover `payload/`, `config/`, `plans/`, the
canonical contract copies, and sealed build-input provenance. Only
`provenance/artifact.json` and its derived checksum list are excluded to avoid
self-reference. Absolute source paths, timestamps, usernames, and hostnames are
excluded.

An unsealed artifact is already immutable and content-addressed; its metadata
stage is `unsealed`. `seal` verifies and copies that artifact into a private
publication directory, writes a source-seal attestation inside the app bundle,
signs the copy, verifies the complete bundle, and computes a new final content
address with stage `sealed`. It never edits the source artifact or an installed
tree.

`build-inputs.json` records the declared build command, actual Git commits and
trees, tracked source hashes, prerequisite results, normalized binary metadata,
signatures, and the separate sealing policy. `verify` cross-checks that sealed
record against the contract and payload rather than trusting metadata alone. It
also rejects undeclared files or directories, generated configuration or plans
that differ from the contract, noncanonical JSON, and incorrect published
modes.

For a final artifact, `verify` additionally checks the complete app resource
envelope with `codesign --verify --strict --deep --all-architectures`, the
Mach-O architecture, exact Developer ID leaf authority, Team ID, bundle and
executable identifiers, CDHash, absent timestamp, signed source attestation,
final executable hash, and canonical final bundle-tree digest. The only
signing-created file admitted by contract is the canonical
`Contents/_CodeSignature/CodeResources` path.

Artifact files are published as `0444` or `0555` without copied timestamps. All
directories become `0555`; files and directories are flushed before atomic
publication. A repeated build may reuse an existing content-addressed directory
only when full verification succeeds.

## Mutable Boundary

The artifact itself is immutable. The manifest inventories mutable state with
an owner and `transient`, `retained`, or `restored` lifecycle. Exact install and
uninstall file mutations are also represented in paired operation templates:

- the stable signed bridge application path;
- CrossOver's live MoltenVK library;
- the game's OpenVR, DXVK, and bridge files;
- the per-user launchd plist and Mach service;
- the runtime lock and ALVR session state; and
- per-run backups, journals, logs, and evidence.

The builder never writes to those locations.

The stable bridge is a stricter paired contract. Its target is the literal
consent-preserving repository URL rather than a plan binding. The install guard
requires the signed ownership marker and `developer-id-bundle` policy, the
replacement source must be the declared sealed bundle, and the uninstall
operation is `retain_tree`. A prior version is accepted only after the lifecycle
verifies its Developer ID authority, Team ID, bundle ID, signature validity, and
no-timestamp policy. The durable transaction binds the observed prior tree,
revalidates identity at intent, and uses descriptor-backed atomic exchange so
the stable URL always names either the exact prior or exact current tree.

## Commands

### Structural Check

```bash
python3 tools/build_runtime_artifact.py check
```

Validates the manifest and lock schema, tracked repository-source hashes, safe
relative destinations, case-insensitive/path-prefix collisions, strict supply
classes, exact plan inverses, and manifest-lock identity without requiring local
runtime binaries.

When the builder, contract, or another checked repository source changes, the
read-only helper computes the exact proposed source hashes and canonical
manifest hash:

```bash
python3 tools/build_runtime_artifact.py contract-digests
```

Review and apply those values to the manifest and lock, then rerun `check`.
There is intentionally no automatic bless or write mode.

### Local Validation

```bash
python3 tools/build_runtime_artifact.py validate \
  --bindings .code/runtime-bindings.json
```

Checks Git commits and cleanliness, repo-source hashes, prerequisites, local
binary hashes, file types, and required code signatures. It performs no writes.

### Build

```bash
python3 tools/build_runtime_artifact.py build \
  --bindings .code/runtime-bindings.json \
  --output-root .code/runtime-artifacts
```

Stages into a locked temporary directory, computes the unsealed content address,
writes canonical provenance, makes the artifact read-only, and atomically
publishes it.

### Developer ID Seal

```bash
python3 tools/build_runtime_artifact.py seal \
  --artifact .code/runtime-artifacts/<unsealed-artifact> \
  --output-root .code/runtime-artifacts
```

The identity, Team ID, bundle ID, output paths, and no-timestamp policy come
only from the checked-in contract; callers cannot override them. The command
requires the checked-in manifest and lock to match the source artifact, copies
the verified source under the same publication lock used by `build`, writes the
signed attestation, runs Developer ID signing, verifies the resulting bundle,
and publishes a new read-only `sealed` content address. Re-sealing a final-stage
artifact and in-place mutation are refused.

### Dry-Run Ownership Plan

```bash
python3 tools/build_runtime_artifact.py plan \
  --artifact .code/runtime-artifacts/<artifact> \
  --bindings .code/runtime-bindings.json
```

Resolves every source, target, backup, ownership marker, retention, and expected
precondition to an exact absolute path. Directory sources include a canonical
tree digest, and the output carries the complete allowed-root authority needed
by the transaction executor. It reports complete install/uninstall plans, the
resolved mutable-state inventory, readiness, and blockers, even when the current
host is not in an installable state. It rejects an artifact built from a
different external manifest or lock. The command emits JSON and performs no
mutation. An unsealed artifact reports `installReady=false` and
`uninstallReady=false` with an `artifact.sealing_required` blocker even when
every target operation is otherwise ready.

### Transactional Lifecycle Commands

```bash
python3 tools/runtime_cli.py install \
  --artifact .code/runtime-artifacts/<artifact> \
  --bindings .code/runtime-bindings.json

python3 tools/runtime_cli.py uninstall \
  --artifact .code/runtime-artifacts/<artifact> \
  --bindings .code/runtime-bindings.json
```

The coordinator accepts no caller-selected plan, journal, backup, target root,
or lifecycle-state namespace. It verifies the current manifest/lock and uses one
fixed per-user lock, journal, history, and backup root shared by every checkout
and bindings file for this runtime id. It settles the shared active journal,
stops exact-owned runtime state, rejects open targets, checks conservative
per-filesystem capacity, and passes planner operations unchanged to the durable
executor. Matching committed work is an idempotent success; interrupted work is
rolled back and archived before the operator retries the requested direction.

The dev8 manifest retains separate-step signing without weakening admission.
Both lifecycle commands return `artifact.sealing_required` before creating
directories, stopping services, writing journals, or changing targets when the
verified artifact stage is `unsealed`. A verified `sealed` artifact carries its
exact post-sign bundle tree into the transaction plan. Non-fixture target roots
now use no-follow root/parent descriptors, journaled parent identity, exact
descriptor hashing, exclusive sibling staging/original/rollback moves, and
Darwin/Linux atomic exchange for an existing stable bridge. `doctor` reports
that transaction boundary as passing after the sealed plan resolves. The
coordinator quiesces owned runtime state and rejects open targets before
mutation; descriptor hardening and before/after identity sampling fail closed on
ordinary drift but are not an authentication boundary against an intentionally
hostile same-UID owner rewriting the journal or orchestrating path swaps during
external `codesign` inspection.

### Curated Runtime Start

```bash
python3 tools/runtime_cli.py start \
  --artifact .code/runtime-artifacts/<artifact> \
  --profile freedom-locomotion \
  --bindings .code/runtime-bindings.json

python3 tools/runtime_cli.py status \
  --artifact .code/runtime-artifacts/<artifact> \
  --bindings .code/runtime-bindings.json

python3 tools/runtime_cli.py stop \
  --bindings .code/runtime-bindings.json
```

The dev10 source contract pins the profile validator, JSON Schema, and explicit
curated profiles. Public start accepts an identifier rather than an arbitrary
path, requires the exact sealed profile hash, verifies Steam app/build/depot
identity, and projects the installed game tree through the exact uninstall
substitutions and removals before launch. The current transaction plan is
Freedom-specific, so `freedom-locomotion` is admitted and The Lab fails closed
until its three targets receive separately reviewed lifecycle operations.

The detached supervisor launches the exact CrossOver `cxstart` command in one
new process group and retains the live process handle as signal authority. It
publishes schema-v3 `waiting` only after an exact in-group game executable, the
generation-owned bridge producer handshake, and all startup self-tests are
present. Stop quiesces that live group before exact launchd bootout; serialized
PIDs remain evidence only. Vision Pro connection, streaming, and recovery state
remain outside this host-only producer slice.

### Verify And Compare

```bash
python3 tools/build_runtime_artifact.py verify \
  --artifact .code/runtime-artifacts/<artifact>

python3 tools/build_runtime_artifact.py compare \
  .code/runtime-build-a/<artifact> \
  .code/runtime-build-b/<artifact>
```

`verify` recalculates all content records and the seal, then verifies final
Developer ID evidence when the stage is `sealed`. `compare` requires two
independent artifacts at the same stage to have identical seals and file
records. Unsealed builds are deterministic and must compare equal. Developer ID
CMS bytes can vary between valid no-timestamp signing operations even when the
source seal, signed attestation, CodeResources, and CDHash remain identical, so
each final tree receives its own exact content address and an exact sealed-stage
`compare` intentionally reports those byte-level differences.

### Self-Test

```bash
python3 tools/build_runtime_artifact.py self-test
```

Creates bounded temporary fixtures and covers valid, missing, hash-mismatched,
wrong-type, unsafe-destination, symlink, wrong-commit, tracked/untracked dirty
worktree, atomic-build, read-only publication, exact-plan, contract mismatch,
exact modes, undeclared directories, tamper detection, and two-build-equivalence
behavior. Deterministic test doubles also cover the unsealed gate, post-build
seal, final-stage plan readiness, CMS byte variance with stable code identity,
stale publication cleanup, relocation, re-seal refusal, and signed-tree tamper
detection. It does not use CrossOver or Apple tooling and runs in CI.

## Binding Rules

Bindings use `${NAME}` references. The built-ins are `${REPO_ROOT}` and
`${HOME}`. Unknown names, recursive references, non-string values, and unknown
binding keys are rejected.

Input bindings resolve to regular non-symlink files or Git directories. Plan
targets resolve under one of the explicitly allowed roots in the manifest.
Existing symlinks cannot escape those roots.

The builder accepts only the project-owned target-root templates and exact
prerequisite command/plist locations compiled into its trusted policy. A
manifest cannot add commands or widen the target sandbox. Local binding values
remain an explicit operator trust boundary.

`runtime/bindings.example.json` documents the qualified local shape. Copy it to
`.code/runtime-bindings.json` and replace only the local build-output roots.

## Error Contract

Failures are emitted as JSON with a stable symbolic code and context. Important
classes include:

- `manifest.invalid` and `lock.invalid`;
- `binding.missing` and `binding.unknown`;
- `path.unsafe` and `path.symlink`;
- `git.revision`, `git.remote`, and `git.dirty`;
- `source.hash`, `source.untracked`, `input.missing`, `input.hash`, and
  `input.type`;
- `input.signature` and `prerequisite.mismatch`;
- `sealing.identity`, `sealing.signature`, and `sealing.source_mismatch`;
- `artifact.publish`, `artifact.verify`, and `artifact.compare`;
- `plan.unresolved` for incomplete or unsafe dry-run plans; and
- `artifact.sealing_required`, `transaction.descriptor_unsupported`,
  `transaction.path_identity_changed`, `transaction.target_changed`,
  `transaction.busy`, `transaction.retry_required`, `plan.blocked`,
  `runtime.target_busy`, and `capacity.insufficient` for lifecycle admission or
  descriptor-transaction failures; and
- `internal.error` for unexpected failures that are still returned as JSON.

There is no continue-anyway override for integrity, identity, architecture, or
path failures.

## Validation Gates

Issue #58 is complete when:

1. `check` and `self-test` pass in CI.
2. `validate` passes against the qualified local bindings.
3. Two independent local builds compare equal.
4. The dry-run plan reports every owned install and uninstall path without
   modifying the host.
5. The existing probe runner and frame-path sources remain unchanged.

Issue #62's final-sealing slice additionally requires:

1. two independently signed copies to preserve the source seal, signed
   attestation, CodeResources, and CDHash; varying CMS bytes must produce
   distinct exact final tree and artifact seal values rather than being
   normalized away;
2. an unsealed artifact to remain lifecycle-gated before every mutation;
3. a final artifact to verify after relocation and enter planning as `sealed`;
4. source-seal, signed-attestation, CodeResources, executable, identity, and
   final-tree tampering to fail closed; and
5. the stable installed bundle URL and privacy-consent behavior to remain a
   separate physical qualification gate.

## Non-Goals

- rebuilding or downloading CrossOver, DXVK, or MoltenVK source trees;
- registering, notarizing, or publicly distributing the bridge application;
- enabling real CrossOver or game mutation before a transaction-compatible
  signed artifact passes the lifecycle gate;
- starting ALVR, a game, or the Vision Pro client;
- defining the reusable per-game profile schema;
- adding GUI, notarization, updates, or telemetry; and
- changing the proven frame, pose, transport, controller, or lifecycle path.
