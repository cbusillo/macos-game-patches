# Direct-Mode Bottom-Stack Patch Targets

Date: 2026-02-26

This note captures the concrete binaries/source files we now know are in the
critical path for SteamVR Direct Mode under CrossOver on macOS.

## Confirmed Blockers

- D3DMetal lane (current patched behavior):
  - `IDXGIResource::GetSharedHandle` now returns `S_OK`.
  - `ID3D11Device::OpenSharedResource` / `OpenSharedResource1` return `S_OK`
    cross-process.
  - But cross-process content is still black/empty (child pixel `0x00000000`
    while same-process readback sees `0xffff0000`).
  - This means API-level success does not imply real shared backing memory.

- D3DMetal lane (stock historical baseline):
  - `IDXGIResource::GetSharedHandle -> E_NOTIMPL (0x80004001)`
  - `IDXGIResource1 QI -> E_NOINTERFACE (0x80004002)`

- DXVK lane (stock):
  - Fails on `VK_KHR_EXTERNAL_MEMORY_WIN32` capability in this environment.
  - `GetSharedHandle` fails (`0x80070057`) with external-memory warnings.

- Wine token-map prototype:
  - Same-process open works.
  - Cross-process open fails by design (`E_NOTIMPL`) because pointer tokens are
    not valid across processes.

## Fresh Evidence (2026-02-26)

- Probe output:
  - `temp/probes/probe-content-now-d3dmetal.stdout`
  - `temp/probes/probe-content-now-dxvk.stdout`
  - `temp/probes/probe-content-summary-both.json`

- Diagnostic command:

```bash
python3 tools/shared_content_probe.py \
  --backends d3dmetal dxvk \
  --scenarios shared shared_keyed shared_keyed_nthandle shared_nthandle \
  --json-out temp/probes/probe-content-summary-both.json
```

- Interpretation:
  - `d3dmetal`: `diagnosis=api_success_content_not_shared`
  - `d3dmetal` keyed scenarios: `diagnosis=keyed_mutex_interface_missing`
    (`QI(IDXGIKeyedMutex) -> 0x80004002` in both parent and child)
  - `dxvk`: `diagnosis=api_share_path_unavailable`

This is the current bottom-of-stack root cause summary for direct mode.

## Phase-1 Controlled Runtime Evidence (2026-02-26)

We now have an end-to-end before/after matrix that proves the patched `wine-src`
`d3d11`/`dxgi` artifacts were exercised on the GPTK runtime lane, with archive
JSON and per-scenario logs.

- Matrix JSON:
  - `temp/probes/probe-content-phase1-gptk-matrix.json`
- Delta JSON:
  - `temp/probes/probe-content-phase1-gptk-delta.json`
- Per-run logs:
  - `temp/probes/probe-content-phase1-gptk-*.stdout`

Key runtime deltas (all scenarios):

- Map/mutex schema marker moved from `V2` to `V3`:
  - `before_map_version=2`
  - `after_map_version=3`
- Cross-process path behavior switched from surrogate creation to truthful
  rejection:
  - before: `Returning surrogate cross-process shared texture ...`
  - after: `Cross-process shared open ... rejected: backing_type=1 ...`
- Classification now surfaces this correctly as a child open-path failure:
  - before: `api_success_content_not_shared` / keyed variants
  - after: `child_open_shared_failed`

Execution helper:

```bash
python3 tools/phase1_controlled_runtime_matrix.py
```

This helper automatically:

- backs up GPTK runtime `d3d11.dll`/`dxgi.dll`,
- runs stock probe matrix,
- swaps in patched artifacts,
- runs patched matrix,
- archives stamped matrix/delta/hash JSON,
- restores stock runtime binaries in `finally`.

## Exact Patch Targets

### 1) Open-source Wine (already edited locally for prototype)

- `/Users/cbusillo/Developer/wine-src/dlls/dxgi/resource.c`
  - `dxgi_resource_GetSharedHandle`
  - `dxgi_resource_CreateSharedHandle`
  - Current prototype creates token-based handles.

- `/Users/cbusillo/Developer/wine-src/dlls/d3d11/device.c`
  - `d3d11_device_OpenSharedResource`
  - `d3d11_device_OpenSharedResource1`
  - `d3d10_device_OpenSharedResource`
  - Current prototype resolves token handles only in owner PID; cross-process
    returns `E_NOTIMPL`.

### 1a) Foundation work added 2026-02-26 (lowest-level first)

- Shared-map schema bumped to `V3` in both files:
  - `WineDxgiSharedHandleMapV3_*`
  - `WineDxgiSharedHandleMutexV3_*`
- Shared-entry metadata now carries forward compatibility fields:
  - `shared_misc_flags`
  - `shared_features` (includes keyed-mutex capability bit)
  - `backing_type` / `backing_id` (placeholder for IOSurface bridge wiring)
- Cross-process surrogate texture creation now attempts preserving
  `SHARED`/`SHARED_KEYEDMUTEX` compatibility bits before fallback stripping,
  instead of always stripping all sharing bits at first attempt.
- Cross-process open path now routes through a dedicated backing dispatcher:
  - `NONE`/`CPU_STAGING`: current surrogate behavior
  - `IOSURFACE`: explicit `E_NOTIMPL` placeholder with targeted logging
  - this is the insertion point for real IOSurface-backed reopen work.

Build sanity checks run:

```bash
make -C /Users/cbusillo/Developer/wine-src \
  dlls/dxgi/x86_64-windows/resource.o \
  dlls/d3d11/x86_64-windows/device.o

make -C /Users/cbusillo/Developer/wine-src \
  dlls/dxgi/x86_64-windows/dxgi.dll \
  dlls/d3d11/x86_64-windows/d3d11.dll
```

Result: both touched objects compile.

### 2) Closed CrossOver binary (hard blocker)

- `/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/apple_gptk/external/D3DMetal.framework/Versions/A/D3DMetal`
  - `D3D11Device::OpenSharedResource` (stub behavior)
  - `D3D11Device::OpenSharedResource1` (stub behavior)
  - `DXGIResource::GetSharedHandle` (stub behavior)

Historical disassembly showed these routines as immediate `E_NOTIMPL` stubs.
Current local diagnostic patching can bypass that return path, but the content
probe proves cross-process texture bytes still do not propagate.

Runtime lane detail that matters for patch deployment:

- Primary GPTK DLL path exercised by D3DMetal lane:
  - `/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/apple_gptk/wine/x86_64-windows/d3d11.dll`
  - `/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/apple_gptk/wine/x86_64-windows/dxgi.dll`

This path takes precedence over the generic CrossOver runtime path for these
tests:

- `/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib/wine/x86_64-windows/*.dll`

### 3) Optional deep path if we avoid D3DMetal stubs

- `/Users/cbusillo/Developer/wine-src/dlls/win32u/d3dkmt.c`
- `/Users/cbusillo/Developer/wine-src/dlls/win32u/vulkan.c`
- `/Users/cbusillo/Developer/wine-src/server/d3dkmt.c`

These are relevant if we attempt a serialized/open-by-reconstruction transport
for cross-process resource metadata and synchronization.

### 4) CrossOver ABI coupling (operational blocker)

- Runtime unix module:
  - `/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib/wine/x86_64-unix/win32u.so`

Experiment summary (2026-02-25):

- Patched `wine-src/dlls/win32u/vulkan.c` to force external-memory capability
  exposure (`use_external_memory() -> TRUE`), then rebuilt `win32u.so`.
- Replacing CrossOver's `win32u.so` with the upstream-built module caused
  immediate process start failure (`exit 66`) before normal app launch.
- Conclusion: this lane is ABI-coupled to the CrossOver runtime build and is
  not safely swappable as a single upstream module.

Implication:

- Any `win32u`/`ntdll` Vulkan extension work must be integrated against the
  matching CrossOver build set (or a full compatible runtime set), not via
  one-off `win32u.so` replacement.

## Practical Bottom-Up Order

1. Keep Wine prototype as safe harness (done):
   same-process success and safe cross-process failure (`E_NOTIMPL`), no crash.
2. Establish D3DMetal intervention strategy:
   either binary patching of stubbed methods or replacement shim at the
   D3DMetal boundary.
3. If D3DMetal change is unavailable, pivot to non-direct mode as production
   path and treat direct mode as blocked by closed-source backend limits.

## New Local Tooling

- `tools/d3dmetal_shared_stub_report.py`
  - confirms whether key entry points are still immediate `E_NOTIMPL` stubs.
- `tools/d3dmetal_patch.py`
  - reversible patch manager for D3DMetal diagnostic patch sets.
- `tools/d3dmetal_texture_layout_probe.py`
  - LLDB attachment helper to dump live `DXGIResource` object-field pointers
    at `GetSharedHandle` breakpoint for IOSurface/Metal-object reconnaissance.
  - `--probe-iosurface` now attempts best-effort `IOSurfaceID` extraction for
    Objective-C candidate fields.
- `tools/win_shared_content_probe.c`
  - C probe that verifies whether cross-process `OpenSharedResource*` returns
    usable content, not just `S_OK`.
- `tools/shared_content_probe.py`
  - Build/run wrapper with mandatory cleanup preflight and JSON classification
    (`api_success_content_not_shared`, `api_share_path_unavailable`, etc.).
- `docs/d3dmetal-directmode-patch-runbook.md`
  - guarded workflow for apply/check/restore and sterile validation cycles.

Quick probe example:

```bash
python3 tools/d3dmetal_texture_layout_probe.py \
  --process-pattern 'vrcompositor\.exe' \
  --symbol '__ZN12DXGIResource15GetSharedHandleEPPv' \
  --probe-iosurface
```

## Current State Snapshot

- Runtime CrossOver `dxgi.dll` and `d3d11.dll` are currently restored to stock.
- Runtime CrossOver `win32u.so` is restored to stock
  (`win32u.so.mgp.bak` == active hash).
- Backup artifacts were moved out of the CrossOver bundle to
  `temp/crossover_bundle_backups/` to keep app code-signing valid.
- Local Wine prototype edits exist only in `wine-src` files listed above.
- VR stack cleanup baseline currently passes (`remaining=0`).
