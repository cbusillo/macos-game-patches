# Probe Ledger

Each probe captures one falsifiable question before broader tooling is built.

## Evidence Gate

No live AVP, CrossOver, ALVR, OpenVR, OpenXR, or bridge run is complete until its
result is written into the relevant probe doc. Chat notes and headset impressions
are not durable evidence by themselves.

Use this vocabulary consistently:

- `verified`: backed by logs, build output, captured frames, command output, or
  a directly reproducible observation.
- `human-observed`: headset or screen feedback from a person. Useful, but not
  enough by itself to change architecture.
- `inferred`: plausible explanation based on evidence, but not directly proven.
- `unknown`: not measured or not available in the logs.
- `failed`: attempted and did not meet the expected proof.
- `do-not-repeat`: a setup, mode, target, or interpretation that previously gave
  misleading or non-actionable evidence.

Every run record must include:

```text
Run:
Question:
Mode / build:
Commands:
Expected proof:
Artifacts captured:
Logs checked:
Human observation:
Verified:
Inferred:
Failed / missing:
Unknown:
Verdict:
Do not repeat:
Next action:
Issue routing:
```

`Artifacts captured` should include exact log paths, screenshots or video names,
timestamps, frame ids, commit/branch/build identity, and whether the source was
a real app, a contract-faithful probe, a fake runtime, or a synthetic visual.

Use `Unknown` for fields that were not measured or were absent from logs. Use
`failed` only when a specific attempted proof did not meet its expected
condition.

Stop before asking for headset interpretation when the required proof logs are
missing. For the current submitted-frame contract work, the minimum proof logs
are:

```text
IVRSystem::GetProjectionRaw return ...
IVRSystem::GetEyeToHeadTransform matrix=...
IVRCompositor::WaitGetPoses contract ...
IVRCompositor::WaitGetPoses hmd_pose=...
Submit pair contract ...
encoded frame contract view_params ...
```

Route plan updates to the owning issue before moving on:

- #38 for submitted-frame contract facts.
- #39 for native macOS encode surface facts.
- #40 for CrossOver/GPTK texture handoff facts.
- #41 for AVP validation and human-observation rules.
- #36 only for parent-level recovery state or direction changes.

If a run changes the plan, update the owning GitHub issue's `Current Status`
before starting another experiment.

Use this shape:

- Hypothesis
- Environment
- Command or procedure
- Evidence artifacts
- Verdict: `alive`, `dead`, or `blocked`
- Next action

## Active Probes

- [001 - ALVR v21 visionOS compatibility](001-alvr-v21-visionos-compatibility.md)
- [002 - ALVR v21 AVP runtime gate](002-alvr-v21-avp-runtime-gate.md)
- [005 - OpenVR submitted frame contract](005-openvr-submitted-frame-contract.md)
