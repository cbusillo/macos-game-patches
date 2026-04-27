# D3DMetal Direct-Mode Patch Runbook

Date: 2026-02-25

This runbook defines a safe, reversible workflow for D3DMetal patch experiments
targeting SteamVR Direct Mode interop.

## Preconditions

- Do not run SteamVR/CrossOver tests without cleanup preflight.
- Known baseline command sequence:

```bash
python3 tools/vr_stack_cleanup.py
python3 tools/steamvr_smoke.py --mode null
```

- Verify blocked entry points before patching:

```bash
python3 tools/d3dmetal_shared_stub_report.py
```

Expected current result: `all_stubbed: true`.

## Bundle Signature Hygiene

- Do not leave backup artifacts inside `/Applications/CrossOver.app/...`.
- Added files in the app bundle invalidate code signing and can break Rosetta
  launch with errors like:
  - `Attachment of code signature supplement failed`
- Keep backups outside the bundle (for example under
  `temp/crossover_bundle_backups/`).

## Patch Tooling

- Patch manager: `tools/d3dmetal_patch.py`
- Current patch-set: `diagnostic_s_ok`

Important: `diagnostic_s_ok` is instrumentation only. It converts immediate
`E_NOTIMPL` returns to `S_OK` and is used to discover next failure layers. It is
not a complete shared-resource implementation.

## Safe Workflow

1. Check current status (no writes):

```bash
python3 tools/d3dmetal_patch.py --check
```

1. Apply diagnostic patch set (writes backup once):

```bash
python3 tools/d3dmetal_patch.py --apply
```

1. Confirm changed status:

```bash
python3 tools/d3dmetal_patch.py --check
python3 tools/d3dmetal_shared_stub_report.py
```

1. Run sterile test cycle:

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
python3 tools/steamvr_smoke.py --mode null
```

1. Restore baseline binary:

```bash
python3 tools/d3dmetal_patch.py --restore
python3 tools/d3dmetal_patch.py --check
```

1. Re-validate stable baseline after restore:

```bash
python3 tools/vr_stack_cleanup.py
python3 tools/steamvr_smoke.py --mode null
```

## Decision Gate

Advance beyond diagnostic patching only when all are true:

- No compositor access violation in smoke runs.
- Shared-handle call path proceeds past immediate stub returns.
- Probe demonstrates valid handle/open semantics (not success-with-null).

If any fail, revert and keep direct mode in R&D status.

## Latest Diagnostic Result

- Run: `temp/vr_runs/20260225-182444-steamvr-smoke`
- Mode: `steamvr_smoke.py --mode alvr --graphics-backend d3dmetal --wait 45`
- With `diagnostic_s_ok` patch set active, direct-mode failure signature changed:
  - No immediate `Failed to open shared resolve texture` lines in the delta.
  - New compositor assertion appears instead:
    - `ASSERT: "pResolved && pResolved->AsD3D11()->pTexture"`
  - Followed by compositor crash (`Exception c0000005`).

Interpretation: forcing `S_OK` confirms code flow moves past the old stub gate,
but no valid resolved D3D11 texture object exists yet. This is expected for a
diagnostic patch and confirms the next required step is real texture-object
construction/open semantics, not return-code bypassing.

## Phase-2 Experimental Result (`pointer_handle_qi`)

- Probe run: `temp/probes/probe-pointer-qi-createhandle.stdout`
- Patch-set: `pointer_handle_qi`
- Behavior observed:
  - In-process probe path advanced:
    - `IDXGIResource::GetSharedHandle hr=0x00000000`
    - `ID3D11Device::OpenSharedResource hr=0x00000000`
  - Child-process open crashed with access violation:
    - `child_open_exit=3221225477` (`0xC0000005`)

This confirms pointer-handle semantics only work within one process and are not
valid as a cross-process direct-mode model.

- SteamVR smoke with this patch-set:
  - Run: `temp/vr_runs/20260225-183340-steamvr-smoke`
  - Result: `VRInitError_Init_HmdNotFound`, `No connected devices`

The patch-set is therefore kept as a dead-end experiment and not used as the
forward implementation lane.
