# Direct-Mode Blocker Dossier

As of February 18, 2026.

## Goal

Prove or disprove strict `direct-mode on` end-to-end viability under current
CrossOver + SteamVR + ALVR stack with real decode and strict gates enabled.

## Strict Repro Command

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
python3 tools/live_avp_checkpoint.py \
  --sterile-native-steam \
  --host-only \
  --codec hevc \
  --stream-protocol udp \
  --direct-mode on \
  --steamvr-home on \
  --steamvr-tool steamvr_room_setup \
  --synthetic-fallback disable \
  --forbid-synthetic-fallback \
  --host-idle-fallback disable \
  --forbid-host-idle-fallback \
  --require-real-decode \
  --require-client-video-present \
  --require-host-frame-signals \
  --require-source-motion \
  --forbid-static-source \
  --require-direct-mode-healthy \
  --require-pass
```

## Repro Evidence

### Run: `temp/vr_runs/20260218-164708-live-avp-checkpoint` (DXVK, strict direct-mode)

- Outcome: `pass=false`
- Gate failures:
  - `host_direct_mode_swap_failed`
  - `steamvr_external_memory_extensions_missing`
  - `host_new_frame_ready_missing`
  - `host_copy_to_staging_missing`
  - `source_motion_missing`
  - `source_static_suspected`
- Key lines:
  - `ALVR MGP direct-mode guard ... disabled=0`
  - `CreateSwapTextureSet failed ... Last HRESULT ... Invalid parameter`
  - `Required vulkan device extension is unavailable: VK_KHR_external_memory_win32`
  - `Required vulkan device extension is unavailable: VK_KHR_win32_keyed_mutex`
  - daemon repeats single CRC source samples (`sample_crc=0xc71c0011`)

### Run: `temp/vr_runs/20260218-162902-live-avp-checkpoint` (D3DMetal, strict direct-mode)

- Outcome: `pass=false`
- Key lines:
  - `ALVR MGP direct-mode guard ... disabled=0`
  - `CreateSwapTextureSet attempt GetSharedHandle failed ... hr=0x80004001`
  - `Headset is using driver direct mode`
  - `Startup Complete`
  - `Failed Watchdog timeout ... Aborting`
  - `Exception c0000005`

Interpretation:

- DXVK path fails direct-mode texture-sharing contract.
- D3DMetal path reaches compositor startup but still fails at shared-handle
  interop / stability.
- Both paths fail strict direct-mode health before reliable host frame
  submission from compositor reaches encoder.

## 2026-02-18 Recovery Sprint (Two New Hypotheses)

Goal of this sprint: use a different direct-mode interop strategy (not harness
changes) with two substantive driver patches, then rerun strict sterile
`direct-mode on` on both DXVK and D3DMetal after each patch.

### Hypothesis 1: Handle-Creation Ordering / Access Mask Is The Blocker

Patch set (driver code):

- `OvrDirectModeComponent.cpp`
  - reorder shared misc candidates to prefer legacy shared-handle flags first
    (`SHARED_KEYEDMUTEX`, `SHARED`) before NT-handle variants.
  - expand NT-handle `CreateSharedHandle` access variants (`READ|WRITE`,
    `GENERIC_ALL`, `0`).

Validation runs:

- DXVK strict run: `temp/vr_runs/20260218-195303-live-avp-checkpoint`
  - still fails strict direct-mode gates.
  - key lines:
    - `CreateSwapTextureSet attempt CreateSharedHandle failed ... access=0x80000001 ... hr=0x80070057`
    - `CreateSwapTextureSet attempt CreateSharedHandle failed ... access=0x10000000 ... hr=0x80070057`
    - `CreateSwapTextureSet failed for texture 0 ... Last HRESULT ... Invalid parameter`
    - missing required SteamVR Vulkan interop extensions in
      `vrclient_vrcompositor.delta.txt`:
      - `VK_KHR_external_memory_win32`
      - `VK_KHR_win32_keyed_mutex`
- D3DMetal strict run: `temp/vr_runs/20260218-195734-live-avp-checkpoint`
  - still fails, no stable streaming.
  - key lines:
    - `CreateSwapTextureSet attempt GetSharedHandle failed ... hr=0x80004001`
    - `Exception c0000005`

Result: hypothesis disproven.

### Hypothesis 2: NT Handle Security/Name + OpenSharedResource1 Compatibility

Patch set (driver code):

- `OvrDirectModeComponent.cpp`
  - for NT-handle candidates, expand `CreateSharedHandle` matrix across:
    - access flags (`READ|WRITE`, `GENERIC_ALL`, `0`)
    - security attrs (`nullptr`, explicit `SECURITY_ATTRIBUTES`)
    - name (`nullptr`, explicit generated name)
  - add probe fields `sec` and `named` to failure/success logs.
- `shared/d3drender.cpp`
  - add `ID3D11Device1::OpenSharedResource1` fallback when
    `OpenSharedResource` fails.

Validation runs:

- DXVK strict run: `temp/vr_runs/20260218-201027-live-avp-checkpoint`
  - still fails strict direct-mode gates.
  - key lines prove full matrix was attempted and still rejected:
    - `CreateSwapTextureSet attempt CreateSharedHandle failed ... access=0x80000001 sec=0 named=0 hr=0x80070057`
    - `CreateSwapTextureSet attempt CreateSharedHandle failed ... access=0x10000000 sec=1 named=1 hr=0x80070057`
    - `CreateSwapTextureSet attempt CreateSharedHandle failed ... access=0x0 sec=1 named=1 hr=0x80070057`
    - SteamVR still asserts missing:
      - `VK_KHR_external_memory_win32`
      - `VK_KHR_win32_keyed_mutex`
- D3DMetal strict run: `temp/vr_runs/20260218-201454-live-avp-checkpoint`
  - still fails, compositor not stable enough for direct-mode-health pass.
  - key lines:
    - `CreateSwapTextureSet attempt GetSharedHandle failed ... hr=0x80004001`
    - `Startup Complete`
    - `Failed Watchdog timeout ... Aborting`
    - `Exception c0000005`

Result: hypothesis disproven.

### Ranked Next Patches (Post-Sprint)

1. Add direct-mode interop probe depth around `IDXGIResource1` path:
   log `QueryInterface(IDXGIResource1)` success/failure as `Info`, include
   exact HRESULT and candidate tuple before compositor abort, so D3DMetal
   failures can distinguish `GetSharedHandle` vs `CreateSharedHandle` path.
2. Add explicit `CreateSharedHandle`-first branch for NTHANDLE candidates
   (skip legacy `GetSharedHandle` dependence) and gate-harden on whether
   returned handles can be reopened via `OpenSharedResource1`.
3. Treat missing Vulkan Win32 external-memory extension support as a platform
   dependency blocker for DXVK direct mode; track upstream backend/runtime work
   required to expose `VK_KHR_external_memory_win32` and
   `VK_KHR_win32_keyed_mutex`.

## Capability Conclusion

Under current platform/runtime behavior, strict `direct-mode on` is not
production-viable. The blocker is not client decode; it is compositor
interop/driver direct-mode frame sharing on this stack.

## Hardened Production Fallback

Use strict non-direct path (`direct-mode off`) with both fallbacks disabled and
host non-direct frame source enabled.

Validated pass run:

- `temp/vr_runs/20260218-164234-live-avp-checkpoint`

This run proves:

- real decode + present,
- no client synthetic fallback,
- no host idle fallback injection,
- host frame-signal telemetry present,
- source motion/non-static gates passing,
- overall `pass=true`.
