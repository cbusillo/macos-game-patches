# GPT-5.4 Handoff Status (March 5, 2026)

## Mission

Get **real SteamVR scene/game pixels** to AVP through ALVR on macOS/CrossOver, not synthetic or flat-color fallback.

## Definition Of Done

- AVP visibly shows real scene motion/content (not solid blocks, not 2-CRC oscillation).
- Strict checkpoint passes with credible telemetry:
  - `--require-real-source`
  - `--require-source-motion`
  - `--forbid-static-source`
  - `--forbid-known-synthetic-source`
- Validation includes both telemetry and visual checks from dumped frames in `logs/vtbridge-debug-frames`.

## Non-Negotiable Run Hygiene

- Always run cleanup first:

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
```

- Do not launch/test SteamVR/ALVR if cleanup exits nonzero.

## Current State At Handoff

- I paused the in-flight run and killed it.
- Stack is currently clean (`remaining=0` from cleanup).
- Last command intentionally stopped: a `steamvr_tutorial` strict run with `ALVR_VD_DEFER_TO_WAIT=0`.

## March 5 Late Update: Native Window Fallback Proof

- A daemon-side macOS native app-window fallback slice is now proven in one live
  run bundle:
  - `temp/vr_runs/20260305-231634-live-avp-checkpoint`
- This path keeps the existing Wine driver cadence and VTBridge/VideoToolbox
  encode flow, but substitutes ScreenCaptureKit-captured app-window pixels in
  `tools/vtbridge_daemon.py` before encode.
- Exact proof markers in
  `temp/vr_runs/20260305-231634-live-avp-checkpoint/logs/vtbridge-daemon.log`:
  - `native_window_capture_debug_dump_reset reason=first_override`
  - repeated `native_window_capture_override ...`
  - repeated `native_window_capture_frame ...`
- Exact outcome proof in
  `temp/vr_runs/20260305-231634-live-avp-checkpoint/config/outcome.json`:
  - `client_video_presenting=true`
  - `client_decode_success=true`
  - `source_quality_grade="real_candidate"`
  - `source_debug_nonflat_frame_count=5`
  - `source_debug_all_flat=false`
  - `pass=true`
- Strongest visual artifact from the injected phase:
  - `temp/vr_runs/20260305-231634-live-avp-checkpoint/logs/vtbridge-debug-frames/frame-000068-nonblack-crc704d01a6.png`
- Important scope boundary:
  - this proves the native fallback can enter the existing path and reach AVP,
    not that it is the final production architecture.

## March 6 First-Gate Result

- Tutorial repeatability is now proven with two fresh sterile bundles:
  - `temp/vr_runs/20260306-061815-live-avp-checkpoint`
  - `temp/vr_runs/20260306-062643-live-avp-checkpoint`
- Both tutorial bundles show:
  - selected `steamvr_tutorial.exe` / `SteamVR Tutorial` window
  - repeated `native_window_capture_override ...`
  - non-flat injected debug frames
  - `source_debug_all_flat=false`
  - `client_video_presenting=true`
- Exact tutorial refs:
  - `20260306-061815`
    - `logs/vtbridge-daemon.log:178`
    - `logs/vtbridge-daemon.log:200`
    - `config/outcome.json:19`
    - `config/outcome.json:242`
    - `logs/vtbridge-debug-frames/frame-000076-nonblack-crc704d01a6.png`
  - `20260306-062643`
    - `logs/vtbridge-daemon.log:175`
    - `logs/vtbridge-daemon.log:197`
    - `config/outcome.json:19`
    - `config/outcome.json:277`
    - `logs/vtbridge-debug-frames/frame-000058-nonblack-crc704d01a6.png`
- First real-game native override run is also proven:
  - `temp/vr_runs/20260306-064123-live-avp-checkpoint`
- AirCar repeatability is now proven with two fresh sterile bundles:
  - `temp/vr_runs/20260306-130639-live-avp-checkpoint`
- AirCar proof highlights:
  - selected `AirCar-Win64-Shipping.exe` / `Aircar` window
  - repeated `native_window_capture_override ...`
  - `source_quality_grade="real_candidate"`
  - `source_debug_nonflat_frame_count=4` on `20260306-064123`
  - `source_debug_nonflat_frame_count=4` on `20260306-130639`
  - `source_debug_all_flat=false`
  - `client_video_presenting=true`
  - strongest artifact:
    `temp/vr_runs/20260306-064123-live-avp-checkpoint/logs/vtbridge-debug-frames/frame-001133-nonblack-crcd3a2ad7f.png`
  - repeat artifact:
    `temp/vr_runs/20260306-130639-live-avp-checkpoint/logs/vtbridge-debug-frames/frame-000799-nonblack-crcd1bde59c.png`
  - exact repeat refs:
    - `temp/vr_runs/20260306-130639-live-avp-checkpoint/logs/vtbridge-daemon.log:582`
    - `temp/vr_runs/20260306-130639-live-avp-checkpoint/logs/vtbridge-daemon.log:599`
    - `temp/vr_runs/20260306-130639-live-avp-checkpoint/config/outcome.json:19`
    - `temp/vr_runs/20260306-130639-live-avp-checkpoint/config/outcome.json:429`
    - `temp/vr_runs/20260306-130639-live-avp-checkpoint/config/outcome.json:435`
  - exact refs:
    - `logs/vtbridge-daemon.log:708`
    - `logs/vtbridge-daemon.log:724`
    - `config/outcome.json:19`
    - `config/outcome.json:494`
    - `config/outcome.json:500`

## March 6 True-VR Update: Shared-Content Probe + Redirect Routing Patch

- `tools/shared_content_probe.py` was rerun across both CrossOver backends with
  sterile cleanup and all four sharing scenarios.
- Results saved to:
  - `temp/probes/shared_content_probe-latest.json`
- Key finding:
  - `d3dmetal` successfully shares real cross-process content for plain shared
    and plain NT-handle scenarios (`diagnosis="content_shared"`).
  - `d3dmetal` fails keyed-mutex scenarios with
    `diagnosis="keyed_mutex_sync_failed"`.
  - `dxvk` still fails at the API layer in every scenario with
    `diagnosis="api_share_path_unavailable"`.
- Interpretation:
  - the current true-VR blocker is narrower than previously assumed: under
    `d3dmetal`, generic cross-process sharing is not completely broken; the
    remaining issue is more likely the exact keyed-mutex / shared-handle contract
    used by SteamVR direct mode and/or compositor routing, not plain content
    visibility.

- A true-VR routing hardening patch was applied in the adjacent ALVR repo:
  - `/Users/cbusillo/Developer/ALVR/alvr/server_openvr/cpp/alvr_server/VirtualDisplayRedirect.cpp`
  - `VirtualDisplayRedirect::Present()` now forwards to the HMD virtual-display
    path instead of logging and dropping frames.
  - `VirtualDisplayRedirect::WaitForPresent()` now forwards into the HMD
    virtual-display path before its local vsync callback.
- The patched driver builds cleanly in `winders` and was redeployed into the
  CrossOver bottle.

- Focused host-side true-VR validation bundle (no native-window fallback):
  - `temp/vr_runs/20260306-213743-live-avp-checkpoint`
- Important outcome fields:
  - `source_quality_grade="real_candidate"`
  - `source_debug_nonflat_frame_count=6`
  - `source_debug_all_flat=false`
  - `host_virtual_display_present_seen=true`
  - `pass=false` is expected here because this was a `--host-only` diagnostic,
    not an AVP proof run.
- Important log refs in `logs/vrserver.delta.txt`:
  - `display_redirect_present calls=1 frame_id=1`
  - `display_redirect_wait_for_present calls=1`
  - `virtual_display_present_dispatch calls=1`
  - `virtual_display_present calls=1 ... copied=1 path=wait_copy_composed`
- Interpretation:
  - the true-VR/non-direct virtual-display path now has a cleaner routing story
    and can produce non-flat host-side frames without leaning on the macOS
    app-window fallback.
  - next gate should focus on the exact SteamVR handle/sync contract
    (especially keyed mutex and direct-mode resource shape), not a generic
    "D3DMetal never shares content" hypothesis.

## Repos And Dirty State

- Main repo (this repo): many pre-existing dirty files; do not reset/revert broadly.
- ALVR repo (`/Users/cbusillo/Developer/ALVR`): also heavily dirty with many pre-existing edits; avoid destructive git operations.

## High-Value Changes Already Made

### 1) Color-channel interpretation fix in daemon

- File: `tools/vtbridge_daemon.py:391`
- File: `tools/vtbridge_daemon.py:497`
- Change: ffmpeg raw input switched from `bgra` to `rgba` for both encoding and debug PNG dumps.
- Why: D3D `R8G8B8A8` CPU bytes were being interpreted as BGRA, causing misleading channel swap.

### 2) Experimental redirect-forwarding suppression in ALVR (currently deployed)

- File: `/Users/cbusillo/Developer/ALVR/alvr/server_openvr/cpp/alvr_server/VirtualDisplayRedirect.cpp:97`
- Change: removed forwarding from `VirtualDisplayRedirect::Present()` into `Hmd::OnVirtualDisplayPresent()`.
- Goal: test whether duplicate present callbacks were overwriting good frames.
- Result: did not solve real-pixels issue; one run got more static/black-like.

## Build/Deploy State

- Windows VM used via Proxmox guest agent (`prox-main.shiny`, VMID `201`/`winders`).
- Rebuilt on VM: `cargo build -p alvr_server_openvr`.
- Deployed DLL hash currently in CrossOver bottle:

`/Users/cbusillo/Library/Application Support/CrossOver/Bottles/Steam/drive_c/Program Files (x86)/Steam/steamapps/common/SteamVR/drivers/alvr_server/bin/win64/driver_alvr_server.dll`

SHA256: `ea29b192a61f38f0335f2415b3cab89f17e5cdf4c03392f072c0dc9cc79110bc`

## Tooling And Fixtures Inventory

### Core scripts that matter

- `tools/vr_stack_cleanup.py`
  - Mandatory preflight/postflight process hygiene.
- `tools/live_avp_checkpoint.py`
  - Main strict harness; produces `temp/vr_runs/<timestamp>-live-avp-checkpoint` bundles.
- `tools/vtbridge_daemon.py`
  - VTBridge daemon + encode path + debug frame dumping.
- `tools/macos_window_capture_probe.swift`
  - one-shot ScreenCaptureKit probe for enumerating/capturing macOS windows.
- `tools/macos_window_capture_stream.swift`
  - continuous ScreenCaptureKit helper used by the daemon-side native fallback.
- `tools/alvr_driver_register.py`
  - Ensures ALVR driver registration each run.
- `tools/alvr_driver_deploy.py`
  - Deploys rebuilt `driver_alvr_server.dll` into CrossOver bottle.
- `tools/steamvr_smoke.py`
  - Baseline null smoke sanity checks when needed.

### Most useful run fixtures

- `temp/vr_runs/20260305-185806-live-avp-checkpoint`
  - Best forensic run for current blocker signatures.
- `temp/vr_runs/20260305-184345-live-avp-checkpoint`
  - Post-`rgba` fix reference run.
- `temp/vr_runs/20260305-190550-live-avp-checkpoint`
  - `ALVR_VD_DEFER_TO_WAIT=0` reference run.
- `temp/vr_runs/20260305-231634-live-avp-checkpoint`
  - best proof bundle for the macOS-native app-window fallback slice.
- `temp/vr_runs/20260306-061815-live-avp-checkpoint`
  - tutorial repeatability proof run 1.
- `temp/vr_runs/20260306-062643-live-avp-checkpoint`
  - tutorial repeatability proof run 2.
- `temp/vr_runs/20260306-064123-live-avp-checkpoint`
  - first AirCar native-window-capture proof run.
- `temp/vr_runs/20260306-130639-live-avp-checkpoint`
  - second AirCar native-window-capture proof run.

For each fixture, inspect at minimum:

- `config/outcome.json`
- `logs/vrserver.delta.txt`
- `logs/vrclient_TheLab.delta.txt` (or active app delta)
- `logs/vtbridge-daemon.log`
- `logs/vtbridge-debug-frames/*`

### VM/Build fixture details

- VM path assumptions used successfully:
  - host: `prox-main.shiny`
  - VMID: `201` (`winders`)
  - ALVR repo in guest: `C:\dev\ALVR`
- Build command used in guest PowerShell:
  - `Set-Location 'C:\dev\ALVR'; cargo build -p alvr_server_openvr`

## Critical Env/Mode Knobs

- `ALVR_VD_DEFER_TO_WAIT=0|1`
  - Immediate present handling vs deferred submit in `WaitForPresent()`.
- `--direct-mode off --display-redirect on --non-direct-source disable`
  - Current main path under test.
- `--steam-app-id 450390` vs `--steamvr-tool steamvr_tutorial`
  - App source choice matters; The Lab shows additional D3D11 frame-info failure signal.

## Key Runs (Recent)

| Run Dir | Key Flags | Outcome | Reality Check |
|---|---|---|---|
| `temp/vr_runs/20260305-181014-live-avp-checkpoint` | strict, non-direct disabled | `pass=true`, `source_quality_grade=real_candidate` | Visuals still looked flat/teal-black; not convincing real scene |
| `temp/vr_runs/20260305-182145-live-avp-checkpoint` | non-direct enabled + mirror on | `pass=true` | Frames were mostly solid-color style (e.g., flat green tone) |
| `temp/vr_runs/20260305-184345-live-avp-checkpoint` | after `rgba` fix | `pass=true` | Better channel correctness, still not clearly real gameplay |
| `temp/vr_runs/20260305-185806-live-avp-checkpoint` | redirect-forwarding suppressed | `pass=false`, `source_static_suspected=true` | More static/black behavior |
| `temp/vr_runs/20260305-190550-live-avp-checkpoint` | redirect-forwarding suppressed + `ALVR_VD_DEFER_TO_WAIT=0` | `pass=true` | Telemetry pass but visuals still not trustworthy real scene |

## Strong Evidence To Start From

- The Lab app-side/compositor clue (important):
  - `temp/vr_runs/20260305-185806-live-avp-checkpoint/logs/vrclient_TheLab.delta.txt:270`
  - Contains: `Failed to open frame info buffer (D3D11)!`

- Duplicate present cadence remains visible:
  - `temp/vr_runs/20260305-185806-live-avp-checkpoint/logs/vrserver.delta.txt:338`
  - `temp/vr_runs/20260305-185806-live-avp-checkpoint/logs/vrserver.delta.txt:340`
  - `virtual_display_present_dispatch calls=120` and `display_redirect_present calls=120 frame_id=120`

- Backbuffer samples still low-entropy/static-ish in problematic runs:
  - `temp/vr_runs/20260305-185806-live-avp-checkpoint/logs/vrserver.delta.txt:343`
  - `sample_hash=0x5b5085ee` (or `0xb013d8d0` in later phase), very low nonzero sample counts.

- VTBridge source sample hash often constant while “motion” inferred elsewhere:
  - `temp/vr_runs/20260305-190550-live-avp-checkpoint/logs/vrserver.delta.txt:346`
  - `VideoEncoderVtBridge: source_sample ... sample_hash=0xdfde6ac5`

## Interpretation / What Likely Remains Broken

- Color-channel mislabeling was real and is fixed, but that was not the finish-line issue.
- We still likely have a **content-source integrity problem** in virtual display import/copy path and/or app/compositor frame submission stability.
- Strict gate can still produce false confidence: `pass=true` can happen while visuals are not clearly real scene pixels.

## Recommended Next Plan For GPT-5.4

1. Treat the daemon-side native app-window override as a proven fallback slice,
   not a hypothesis.
   - Reuse `tools/live_avp_checkpoint.py` flags rather than building a second
     media path.

2. Tighten native window selection and startup behavior.
   - Current helper eventually locks onto `SteamVR Tutorial`, but initial
     `capture_window_selected none` churn still burns time before the first
     override.

3. Treat tutorial repeatability as complete and stop re-proving it.
   - Use `20260306-061815` and `20260306-062643` as the locked tutorial gate.

4. Keep acceptance visual.
   - Continue requiring `native_window_capture_override` markers plus non-flat
     dumped frames from the injected phase.

5. Treat AirCar repeatability as complete and stop re-proving tutorial.
   - AirCar is now green in `20260306-064123` and `20260306-130639`.

## Useful Commands

### Strict baseline (current pattern)

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
python3 tools/live_avp_checkpoint.py \
  --sterile-native-steam \
  --graphics-backend d3dmetal \
  --direct-mode off \
  --display-redirect on \
  --non-direct-source disable \
  --steamvr-home on \
  --stream-protocol tcp \
  --codec hevc \
  --synthetic-fallback disable \
  --host-idle-fallback disable \
  --vtbridge-debug-dump-limit 12 \
  --require-client-ready \
  --require-client-video-present \
  --forbid-synthetic-fallback \
  --forbid-host-idle-fallback \
  --require-real-decode \
  --require-source-motion \
  --require-host-frame-signals \
  --forbid-static-source \
  --forbid-known-synthetic-source \
  --require-real-source
```

### Variant with immediate present path

```bash
ALVR_VD_DEFER_TO_WAIT=0 python3 tools/live_avp_checkpoint.py <same flags>
```

### Build + deploy flow used

- Build inside VM (`winders`) via Proxmox guest agent.
- Pull `alvr_server_openvr.dll` to macOS via `nc`.
- Deploy with:

```bash
python3 tools/alvr_driver_deploy.py --dll /tmp/alvr_server_openvr_vm_patch_20260305.dll
```

## Final Notes

- Treat `temp/vr_runs/20260305-185806-live-avp-checkpoint` as the best forensic run for current blockers.
- Treat `temp/vr_runs/20260305-184345-live-avp-checkpoint` and `temp/vr_runs/20260305-190550-live-avp-checkpoint` as examples where telemetry looked okay but visual truth remained questionable.
- Do not trust “pass” alone; insist on visible, moving, non-flat real scene pixels.
- The late proof run `temp/vr_runs/20260305-231634-live-avp-checkpoint` is the
  new exception: it proves the macOS-native app-window override can enter the
  existing VTBridge/VideoToolbox path and produce non-flat injected artifacts.
- The first-gate record now includes tutorial repeatability plus two green
  tutorial bundles and two green AirCar bundles: `20260306-061815`,
  `20260306-062643`, `20260306-064123`, and `20260306-130639`.
