# SteamVR Compositor Status

As of February 18, 2026.

Definitive direct-mode dossier:

- `docs/direct-mode-blocker-dossier.md`

## Current State

`vrcompositor.exe` now reaches `Startup Complete` under CrossOver in ALVR-forced
runs, but direct-mode swap setup still fails and prevents real host frame
submission.

Backend behavior currently differs:

- `d3dmetal`: destabilizes after startup with repeated:
  - `Exception c0000005`
- `dxvk`: reaches `Startup Complete` but direct-mode swap creation fails with
  `CreateSwapTextureSet ... Invalid parameter`, followed by compositor assert.

SteamVR/UI behavior in the same run family:

- `Headset Error (-202)` in direct-mode-off experiments
- frequent AVP session enters decode/present but source remains static black
  when host idle fallback is disabled.

## What Is No Longer The Primary Blocker

The earlier startup-fatal failures were bypassed with compatibility patches.
These error families are no longer the first stop in current runs:

- `VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer`
- `VRInitError_Compositor_CreateMirrorTextures`
- `VRInitError_Compositor_CreateDriverDirectModeResolveTextures`

## Verified Run Evidence

Representative run bundles:

- direct mode off experiment: `temp/vr_runs/20260215-171654-steamvr-smoke`
- direct mode on + bridge required: `temp/vr_runs/20260215-172158-steamvr-smoke`

Key observations in `vrcompositor.delta.txt`:

- `Startup Complete (0.749543 seconds)`
- `Headset display is on desktop`
- `Exception c0000005`

Key observations in direct-mode-on runs:

- `CreateSwapTextureSet failed ... Invalid parameter`
- `ASSERT: "pResolved && pResolved->AsD3D11()->pTexture"`
- missing SteamVR Vulkan interop extensions in `vrclient_*` logs:
  - `VK_KHR_external_memory_win32`
  - `VK_KHR_win32_keyed_mutex`

Representative recent evidence:

- `temp/vr_runs/20260218-150723-live-avp-checkpoint`
- `temp/vr_runs/20260218-152004-live-avp-checkpoint`
- `temp/vr_runs/20260218-164708-live-avp-checkpoint` (strict direct-mode fail)
- `temp/vr_runs/20260218-162902-live-avp-checkpoint` (d3dmetal direct-mode fail)

Best production fallback evidence:

- `temp/vr_runs/20260218-164234-live-avp-checkpoint`
  (`direct-mode off`, strict pass)

## Binary Patch Set In Use

Managed by:

- `tools/steamvr_compositor_patch.py`

Additional patch added during this phase:

- `treat_create_driver_direct_mode_resolve_textures_failure_as_nonfatal`
  - file offset: `0x27333`
  - bytes: `b8 db 01 00 00` -> `31 c0 90 90 90`

Current patched SHA-256 observed after apply:

- `d30833c1f89896f00b0987573004fc6fe3334bb98d23b951d916dd06d5a4df10`

## Active Root-Cause Hypothesis

Startup blockers were removed, but direct-mode resource sharing is still
unavailable on this stack. `CreateSwapTextureSet` fails across a wide matrix of
format/misc/bind flag combinations, and compositor asserts when resolve
textures are missing.

Observed backend split remains:

- `dxvk`: strict direct-mode still fails with `CreateSwapTextureSet` invalid
  parameter and missing required Vulkan interop extensions.
- `d3dmetal`: compositor reaches startup but shared-handle interop still fails
  (`GetSharedHandle ... 0x80004001`) and render thread watchdogs/crashes.

## March 6, 2026 Update: Shared-Content Probe Narrows The D3DMetal Theory

`tools/shared_content_probe.py` was rerun with sterile cleanup across both
backends and all four sharing scenarios. Results are saved at:

- `temp/probes/shared_content_probe-latest.json`

What changed in our understanding:

- `d3dmetal` is capable of sharing real cross-process content for:
  - `shared`
  - `shared_nthandle`
- `d3dmetal` fails keyed-mutex scenarios with:
  - `diagnosis="keyed_mutex_sync_failed"`
- `dxvk` still fails at the API layer in every scenario with:
  - `diagnosis="api_share_path_unavailable"`

Implication:

- The current D3DMetal true-VR blocker is more specific than
  "no cross-process sharing." Plain shared textures can survive a cross-process
  reopen and retain content.
- The remaining likely problem is the exact SteamVR direct-mode contract:
  keyed mutex behavior, handle flavor, or compositor routing expectations.

## March 6, 2026 Update: Virtual-Display Redirect No Longer Drops Presents

A routing hardening patch was applied in the adjacent ALVR repo:

- `/Users/cbusillo/Developer/ALVR/alvr/server_openvr/cpp/alvr_server/VirtualDisplayRedirect.cpp`

Changes:

- `VirtualDisplayRedirect::Present()` now forwards into the HMD virtual-display
  path instead of logging and returning.
- `VirtualDisplayRedirect::WaitForPresent()` now forwards into the HMD
  virtual-display path before its local vsync callback.

Focused host-side validation bundle (no native-window fallback):

- `temp/vr_runs/20260306-213743-live-avp-checkpoint`

Key outcome signals from that bundle:

- `source_quality_grade="real_candidate"`
- `source_debug_nonflat_frame_count=6`
- `source_debug_all_flat=false`
- `host_virtual_display_present_seen=true`

This was a `--host-only` diagnostic, so `pass=false` is expected and should not
be read as a regression. The important result is that the non-direct
virtual-display path produced non-flat host-side content without depending on
the macOS native app-window fallback.

## Next Investigation Gate

1. Keep strict harness gates enabled (`--forbid-synthetic-fallback`,
   `--forbid-host-idle-fallback`, `--require-host-frame-signals`).
2. Treat direct-mode failures as first-class gate (`--require-direct-mode-healthy`).
3. Continue investigating non-direct-mode frame submission alternatives or
   platform backend changes required for Win32 external-memory interop.

Given current evidence, direct-mode should be treated as blocked until either
CrossOver graphics backend or SteamVR interop behavior changes.
