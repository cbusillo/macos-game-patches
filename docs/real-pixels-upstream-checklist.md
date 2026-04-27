# Real Pixels Upstream Checklist

Goal: unlock real (non-synthetic) pixels for SteamVR + ALVR on macOS/CrossOver.

This checklist is for the layers below ALVR (Wine/CrossOver/d3dmetal), where the
current blockers are observed.

## Current blocker signatures

- Shared texture opens but content stays flat/black (`OpenSharedResource` success, zero samples).
- Non-direct desktop capture fails (`StretchBlt/BitBlt` -> `ERROR_INVALID_WINDOW_HANDLE` = 1400).
- DXGI desktop duplication path unavailable (`DuplicateOutput` -> `E_NOTIMPL`).

## Lane 1: Wine open-source interop patch (source available)

Files:

- `wine-src/dlls/dxgi/resource.c`
- `wine-src/dlls/d3d11/device.c`

Function-level targets:

- `dxgi_resource_GetSharedHandle`
- `dxgi_resource_CreateSharedHandle`
- `d3d11_device_OpenSharedResource`
- `d3d11_device_OpenSharedResource1`
- `d3d10_device_OpenSharedResource`

Required behavior:

- Cross-process shared-handle reopen must map to the same backing storage (not surrogate/zero texture).
- Preserve and honor sharing flags (`D3D11_RESOURCE_MISC_SHARED`, `..._KEYEDMUTEX`) when reopening.
- If keyed mutex is present, expose working `IDXGIKeyedMutex` interface across processes.
- If keyed mutex is not present, provide deterministic sync visibility (fence/flush semantics) so reads reflect writer commits.

Acceptance criteria:

- Parent process writes a test color into shared texture; child process reads the same color.
- `OpenSharedResource*` succeeds and child readback is non-zero + matches expected CRC.
- `shared_content_probe.py` classification is no longer `api_success_content_not_shared`.

## Lane 2: CrossOver D3DMetal shared-resource implementation (closed binary)

Target binary:

- `/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/apple_gptk/external/D3DMetal.framework/Versions/A/D3DMetal`

Symbol-level targets:

- `D3D11Device::OpenSharedResource`
- `D3D11Device::OpenSharedResource1`
- `DXGIResource::GetSharedHandle`

Required behavior:

- Implement real cross-process shared-resource mapping (IOSurface-backed or equivalent), not stub/pass-through success.
- Ensure handle exchange results in a valid cross-process import with correct lifetime and synchronization.
- Ensure imported resource data is visible to consumer device/context at frame cadence.

Acceptance criteria:

- Virtual-display backbuffer probe reports non-zero samples at source.
- No persistent zero-hash/flat-frame pattern in ALVR probes.
- Streamed debug frames contain scene detail, not uniform black/red-like synthetic output.

## Lane 3: Capture fallback reliability (if shared path still blocked)

Targets:

- Wine GDI capture path (`BitBlt`, `StretchBlt`, window DC capture)
- DXGI duplication (`IDXGIOutput1::DuplicateOutput`)

Required behavior:

- GDI paths must stop failing with `1400` against virtual display contexts.
- Window capture (`VR View` and related windows) must return non-flat pixels.
- Duplication path should return supported/working status for at least one adapter/output.

Acceptance criteria:

- `host_non_direct_desktop_capture_failed` no longer dominated by `stretch_error=1400`.
- `host_non_direct_window_capture_flat_rejected` no longer continuous.
- At least one non-direct source path produces `real_candidate` quality with non-flat debug frames.

## Validation matrix (must pass)

- `tools/shared_content_probe.py` on d3dmetal and dxvk lanes.
- `tools/live_avp_checkpoint.py` strict run with:
  - `--require-real-source`
  - `--forbid-known-synthetic-source`
  - `--forbid-static-source`
  - `--require-host-frame-signals`

Pass criteria:

- `source_quality_grade = real_candidate`
- `source_debug_all_flat = false`
- no synthetic path selection (`source_path_selected != synthetic_pattern`)

## Escalation payload

Use repro bundles from failing runs (already packaged) including:

- `config/outcome.json`
- `logs/session_log.txt`
- `logs/vrserver.delta.txt`
- `logs/vrcompositor.delta.txt`
- `logs/vtbridge-daemon.log`

Include explicit signatures in report:

- `OpenSharedResource success + zero samples`
- `stretch_error=1400`
- `DuplicateOutput hr=0x80004001`
