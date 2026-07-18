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

## Runtime Control Plane

Issue #60 is extracting the proven runtime lifecycle from the research runner.
The first artifact-backed slice provides truthful prerequisite diagnostics,
live status, and exact owned teardown without changing the launchd/Mach data
plane:

```bash
python3 tools/runtime_cli.py doctor --artifact <artifact>
python3 tools/runtime_cli.py status --artifact <artifact>
python3 tools/runtime_cli.py stop
```

All commands support `--json`. `doctor` performs no mutation, `status` refuses
to infer live health from stale logs or cached PIDs, and `stop` boots out only an
exact owned launchd job. The long-running `start` supervisor remains follow-up
work; live qualification still uses the profile runner until that slice lands.

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
