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

- #59 for the second-title packaged-runtime generality gate.
- #56 for parent-level runtime direction or product-boundary changes.
- #38 for submitted-frame contract facts.
- #39 for native macOS encode surface facts.
- #40 for the completed CrossOver/GPTK texture-handoff discovery record.
- #53 for production handoff-pool implementation facts.
- #41 for AVP validation and human-observation rules.
- #36 for historical GPU-resident bridge context.

If a run changes the plan, update the owning GitHub issue's `Current Status`
before starting another experiment.

Use this shape:

- Hypothesis
- Environment
- Command or procedure
- Evidence artifacts
- Verdict: `alive`, `dead`, or `blocked`
- Next action

## Probe Index

- [001 - ALVR v21 visionOS compatibility](001-alvr-v21-visionos-compatibility.md)
- [002 - ALVR v21 AVP runtime gate](002-alvr-v21-avp-runtime-gate.md)
- [003 - Real OpenVR world-locked geometry](003-real-openvr-world-locked-geometry.md)
- [004 - Windows reference VR baseline](004-windows-reference-vr-baseline.md)
- [005 - OpenVR submitted frame contract](005-openvr-submitted-frame-contract.md)
- [006 - MoltenVK IOSurface handoff](006-moltenvk-iosurface-handoff.md)
- [007 - DXVK D3D11 IOSurface interop](007-dxvk-d3d11-iosurface-interop.md)
- [008 - Real OpenVR IOSurface handoff](008-real-openvr-iosurface-handoff.md)
- [009 - Production IOSurface handoff pool](009-production-iosurface-pool.md)
- [010 - Freedom local window regression](010-freedom-local-window-regression.md)
- [011 - Open Brush controller smoke](011-open-brush-controller-smoke.md)
- [012 - SteamVR Tutorial Unity performance control](012-steamvr-tutorial-unity-performance-control.md)
- [013 - The Lab packaged-runtime qualification](013-the-lab-profile-qualification.md)
- [014 - Runtime control plane foundation](014-runtime-control-plane-foundation.md)
- [015 - Runtime transaction journal](015-runtime-transaction-journal.md)
- [016 - Runtime install coordinator](016-runtime-install-coordinator.md)
- [017 - Runtime artifact sealing](017-runtime-artifact-sealing.md)
