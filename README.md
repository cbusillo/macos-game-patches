# macOS Game And VR Patch Lab

Workspace for reproducible experiments around getting PC VR and game rendering
working well on Apple hardware.

The proven baseline runs unmodified Windows OpenVR games through CrossOver,
captures D3D11 frames through a custom OpenVR/DXVK IOSurface path, encodes them
with VideoToolbox, and transports them through ALVR to Apple Vision Pro with
PS VR2 Sense controls. The current workstream is turning that owner-operated
research harness into a reproducible local runtime.

The v1 boundary, exact support matrix, component ownership, frozen contracts,
and second-title decision are recorded in
`docs/reproducible-mac-alvr-runtime-v1.md`. Execution is tracked by GitHub
issue #56 and its native sub-issues. Real SteamVR compositor operation is not
part of the current accepted architecture.

## Working Style

- Define the target game, runtime, headset, macOS version, hardware, and success
  criteria before adding tools.
- Keep experiments small and reproducible: record commands, artifacts, cleanup
  steps, and failure signatures.
- Prefer focused probes over broad framework code until a path has produced real
  evidence.
- Add scripts only after a repeated command or check is worth automating.
- Treat released game binaries as fixed inputs. Do not build or maintain custom
  game forks; compatibility changes belong in the runtime, translation, bridge,
  or upstream patch/request layer.

## Patch Artifacts

Patch artifacts under `patches/` are intended for external upstream checkouts.
Each patch directory includes its own apply notes and tested upstream commits.
Use the sibling source layout in `docs/source-workspace.md` for active ALVR and
visionOS client work.

## Runtime Artifact

Issue #58 defines the first reproducible runtime artifact. The checked-in
manifest and lock enumerate the exact source commits, repository inputs, opaque
local build outputs, signatures, configuration, support matrix, and ownership
plan without vendoring third-party binaries:

```bash
python3 tools/build_runtime_artifact.py check
python3 tools/build_runtime_artifact.py self-test
```

Qualified local validation and builds use ignored bindings documented in
`runtime/bindings.example.json`. See
`docs/reproducible-runtime-artifact.md` for the deterministic payload,
provenance, sealing boundary, and read-only install/uninstall plan contract.

Issue #62 adds a second immutable publication stage for the signed bridge:

```bash
python3 tools/build_runtime_artifact.py build --bindings .code/runtime-bindings.json
python3 tools/build_runtime_artifact.py seal --artifact <unsealed-artifact>
python3 tools/build_runtime_artifact.py verify --artifact <sealed-artifact>
```

`seal` copies rather than edits its input, applies the contract-owned Developer
ID identity with timestamps disabled, verifies the complete app bundle, and
publishes a new final content address with signed source and tree provenance.

## Runtime Transactions

Issue #61 converts that read-only plan into fail-closed filesystem transactions.
The coordinator verifies planner-owned paths, serializes both
directions through one lifecycle lock, archives terminal journals, checks free
space and open targets, and recovers interrupted work before a retry. Its full
install/uninstall cycle remains hardware-free and fenced below temporary roots:

```bash
python3 tools/runtime_descriptor_test.py
python3 tools/runtime_install_test.py
python3 tools/runtime_transaction_test.py
```

Production-shaped commands are available:

```bash
python3 tools/runtime_cli.py install --artifact <artifact>
python3 tools/runtime_cli.py uninstall --artifact <artifact>
```

The dev8 contract still uses a separate signing step, but readiness now comes
from verified artifact stage rather than manifest mode alone. Unsealed artifacts
return `artifact.sealing_required` before any lifecycle mutation; a final sealed
artifact carries its exact signed tree into the transaction plan. A prior bridge
at the fixed consent-preserving URL is admitted only after marker and Developer
ID policy validation, exchanged atomically with the current signed tree, and
retained on uninstall. Other live user-path mutation still uses no-follow
descriptors, journaled target-parent identity, and exclusive sibling moves. The
corrected dev8 artifact passed a prior-version migration and three further
physical install/uninstall cycles while retaining the exact signed bridge and
restoring CrossOver, game, runtime, lock, service, and journal state. That
qualified installed layout is now the admission boundary for runtime start.

## Runtime Control Plane

Issue #60 is extracting the proven runtime lifecycle from the research runner.
The artifact-backed control plane provides truthful prerequisite diagnostics,
a bounded detached host supervisor, synchronized live status, and exact owned
teardown without changing the launchd/Mach data plane:

```bash
python3 tools/runtime_cli.py doctor --artifact <artifact>
python3 tools/runtime_cli.py start \
  --artifact <artifact> \
  --profile freedom-locomotion
python3 tools/runtime_cli.py status --artifact <artifact>
python3 tools/runtime_cli.py stop
python3 tools/runtime_profile_test.py
python3 tools/runtime_start_test.py
```

All commands support `--json`. `doctor` performs no mutation, `status` refuses
to infer live health from stale logs or cached PIDs, and `stop` boots out only an
exact owned launchd job. `start` accepts a sealed curated profile identifier,
projects the transactionally installed game tree back to its stock profile
identity, launches CrossOver in one owned process group, and reports `waiting`
only after the exact game process plus the authenticated bridge producer
handshake and startup self-tests are live. Schema-v3 stop quiesces that retained
process handle before launchd bootout and never signals serialized PIDs. The
first production-admitted profile is `freedom-locomotion`; The Lab remains a
separate multi-target lifecycle slice. Vision Pro `connected`, `streaming`, and
`recovering` transitions are still the next issue #60 slice.

## Starting New Work

Start with the active GitHub plan and the frozen runtime boundary. For new live
experiments, add or update the probe ledger before adding scripts:

```text
docs/reproducible-mac-alvr-runtime-v1.md
docs/probes/README.md
```

Include:

- goal and non-goals
- hardware and software assumptions
- first executable gate
- evidence to collect
- cleanup or rollback steps

Before live OpenVR/CrossOver or ALVR runs, clear stale runtime state:

```bash
python3 tools/vr_stack_cleanup.py
```

For fully sterile CrossOver/Wine probes, add `--include-wine-crossover`.
