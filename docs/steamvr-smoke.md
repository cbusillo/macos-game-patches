# SteamVR Smoke Harness

## Purpose

Provide a minimal, repeatable SteamVR launch test under CrossOver and capture a
deterministic run bundle.

Script:

- `tools/steamvr_smoke.py`
- `tools/vr_stack_cleanup.py`
- `tools/live_avp_checkpoint.py`
- `tools/live_avp_nondirect_prod.py`
- `tools/live_avp_directmode_matrix.py`
- `tools/live_avp_release_gate.py`
- `tools/package_crossover_repro_bundle.py`

## Basic Usage

```bash
python3 tools/steamvr_smoke.py
```

`tools/steamvr_smoke.py` now runs `tools/vr_stack_cleanup.py` as a required
preflight by default and aborts if cleanup exits nonzero.

For fully sterile preflight (including native Steam helper cleanup):

```bash
python3 tools/steamvr_smoke.py --sterile-native-steam
```

To bypass preflight cleanup (not recommended):

```bash
python3 tools/steamvr_smoke.py --skip-preflight-cleanup
```

## Process Hygiene

If you see stale CrossOver icons, Wine debuggers (`winedbg.exe`), or wrapper
processes (`winetemp`, `conhost.exe`, `explorer.exe`) after a run, use:

```bash
python3 tools/vr_stack_cleanup.py
```

For fully sterile runs (including native macOS Steam helper `ipcserver`):

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
```

This uses the same process matcher as `tools/steamvr_smoke.py` and exits nonzero
when matching processes still remain.

## Useful Modes

- Keep current SteamVR settings:

```bash
python3 tools/steamvr_smoke.py --mode unchanged
```

- Force null driver (deterministic runtime sanity):

```bash
python3 tools/steamvr_smoke.py --mode null
```

- Force vrlink driver (transport-facing test):

```bash
python3 tools/steamvr_smoke.py --mode vrlink
```

- Force backend for reproducibility (useful when comparing `d3dmetal` vs `dxvk`):

```bash
python3 tools/steamvr_smoke.py --mode null --graphics-backend dxvk
```

Notes:

- Default backend is now `dxvk` because recent `d3dmetal` runs consistently
  reached `Startup Complete` and then crashed `vrcompositor` with
  `Exception c0000005`.
- Use `--graphics-backend d3dmetal` only for targeted regression checks.

## Output

Run bundles are created under:

- `temp/vr_runs/<timestamp>-steamvr-smoke/`

Each bundle includes:

- copied SteamVR logs (`vrserver.txt`, `vrmonitor.txt`, etc., when present)
- per-run deltas for each copied log (`*.delta.txt`) so old log history does
  not pollute conclusions
- `logs/smoke-summary.txt` with key pattern matches
- `logs/ports.txt` snapshot for `UDP 10400` and `TCP 10440`
- `logs/processes.txt` process snapshot
- optional before/after `steamvr.vrsettings` when mode is changed

## Strict Live AVP Checkpoint

Use this when you want a single bounded run with one AVP prompt and automatic
teardown:

```bash
python3 tools/live_avp_checkpoint.py --sterile-native-steam --require-pass
```

Strict real-decode validation (no client synthetic fallback, no host idle-frame
fallback injection, and required host frame submission probes):

```bash
python3 tools/live_avp_checkpoint.py \
  --sterile-native-steam \
  --host-only \
  --codec hevc \
  --stream-protocol tcp \
  --foveated-encoding off \
  --direct-mode off \
  --steamvr-home on \
  --mirror-view on \
  --steamvr-tool steamvr_monitor \
  --synthetic-fallback disable \
  --forbid-synthetic-fallback \
  --host-idle-fallback disable \
  --forbid-host-idle-fallback \
  --require-real-decode \
  --require-client-video-present \
  --require-host-frame-signals \
  --require-source-motion \
  --forbid-static-source \
  --require-real-source \
  --require-pass
```

Use `--require-real-source` when validating actual game imagery. This fails
runs that only pass due non-direct synthetic fallback frames.

Use `--mirror-view on` for non-direct runs that need window-capture friendly
SteamVR mirror surfaces (`VR View` / `Legacy Mirror`).

Experimental native app-window override:

- Use this when Wine-side mirror capture is known-bad but a real CrossOver app
  window is visible on macOS.
- This keeps the existing VTBridge/VideoToolbox encode flow and only swaps the
  pixel payload inside `tools/vtbridge_daemon.py`.
- Proof bundle:
  - `temp/vr_runs/20260305-231634-live-avp-checkpoint`

Focused proof command:

```bash
python3 tools/live_avp_checkpoint.py \
  --sterile-native-steam \
  --graphics-backend d3dmetal \
  --direct-mode off \
  --display-redirect on \
  --non-direct-source enable \
  --mirror-view on \
  --minimize-crossover-windows off \
  --steamvr-home on \
  --steamvr-tool steamvr_tutorial \
  --stream-protocol tcp \
  --codec hevc \
  --foveated-encoding off \
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
  --native-window-capture-title-contains "SteamVR Tutorial" \
  --native-window-capture-owner-contains steamvr_tutorial.exe \
  --native-window-capture-fps 10
```

Current proof status (2026-03-05):

- daemon log shows `native_window_capture_debug_dump_reset` plus repeated
  `native_window_capture_override ...`
- `config/outcome.json` reports:
  - `source_quality_grade = real_candidate`
  - `source_debug_nonflat_frame_count = 5`
  - `source_debug_all_flat = false`
  - `client_video_presenting = true`
- strongest injected-phase artifact:
  - `logs/vtbridge-debug-frames/frame-000068-nonblack-crc704d01a6.png`

Current first-gate status (2026-03-06):

- tutorial repeatability proven with:
  - `temp/vr_runs/20260306-061815-live-avp-checkpoint`
  - `temp/vr_runs/20260306-062643-live-avp-checkpoint`
- exact tutorial refs:
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
- first real-game AirCar native override proven with:
  - `temp/vr_runs/20260306-064123-live-avp-checkpoint`
- AirCar repeatability proven with:
  - `temp/vr_runs/20260306-130639-live-avp-checkpoint`
  - `temp/vr_runs/20260306-132336-live-avp-checkpoint`
- strongest AirCar artifact:
  - `logs/vtbridge-debug-frames/frame-001133-nonblack-crcd3a2ad7f.png`
- repeat AirCar artifact:
  - `logs/vtbridge-debug-frames/frame-000799-nonblack-crcd1bde59c.png`
- second repeat AirCar artifact:
  - `logs/vtbridge-debug-frames/frame-000300-nonblack-crcd9051352.png`
- exact AirCar refs:
  - `logs/vtbridge-daemon.log:708`
  - `logs/vtbridge-daemon.log:724`
  - `config/outcome.json:19`
  - `config/outcome.json:494`
  - `config/outcome.json:500`
  - repeat bundle refs:
    - `logs/vtbridge-daemon.log:582`
    - `logs/vtbridge-daemon.log:599`
    - `config/outcome.json:19`
    - `config/outcome.json:429`
    - `config/outcome.json:435`
  - second repeat bundle refs:
    - `logs/vtbridge-daemon.log:527`
    - `logs/vtbridge-daemon.log:545`
    - `config/outcome.json:19`
    - `config/outcome.json:454`
    - `config/outcome.json:460`

Temporary diagnostic note: if foveated encoding is disabled during source
isolation, re-enable it after baseline real-source imagery is confirmed.

Current empirical status (2026-02-28): strict non-direct runs pass reliably
with `--foveated-encoding off`; forcing `--foveated-encoding on` can regress
to static-black source signatures on this stack.

Behavior:

- required cleanup preflight and cleanup postflight
- defaults to `dxvk` backend for launch stability (override with
  `--graphics-backend d3dmetal`)
- rewrites SteamVR + ALVR session config to a deterministic contract
- explicitly aligns SteamVR `directMode` with `--direct-mode` and suppresses
  recurring crash/changelog popups (`blocked_by_safe_mode=false`, high
  `lastVersionNotice`) to reduce false "stuck connecting" states
- clears `Documents/alvr_probe.log` at run start to prevent stale probe results
- pushes unattended-safe AVP client defaults each run
  (`autoEnterOnConnect=1`, `keepAwakeWhileActive=1`)
- emits per-run outcome JSON at `config/outcome.json` with key booleans:
  `bridge_connected`, `frame_ready_seen`, `decoder_fatal`, `connection_timeout`,
  `client_decoder_config_seen`, `client_decode_success`,
  `client_video_presenting`, `host_idle_fallback_*`, and host frame-signal
  probes (`host_new_frame_ready_*`, `host_copy_to_staging_*`) plus non-direct
  direct-mode-off probes (`host_non_direct_source_enabled`,
  `host_non_direct_frame_produced_*`, `host_non_direct_frame_submitted_*`)
  and source/interop diagnostics (`source_path_selected`,
  `source_quality_grade`, `interop_signature`).
- optional `--wine-debug-channels` writes `logs/wine-d3d-trace.log` and stores
  trace-line count in `config/meta.json`.

Direct-mode diagnostics:

- `--require-direct-mode-healthy` fails the run when:
  - `CreateSwapTextureSet` fails in host logs, or
  - SteamVR logs report missing external-memory interop extensions
    (`VK_KHR_external_memory_win32`, `VK_KHR_win32_keyed_mutex`).

See `docs/direct-mode-blocker-dossier.md` for current strict direct-mode
evidence and blocker status.

## Best Production Path

Current best production path is strict `direct-mode off` with both fallbacks
disabled, enforced by the same strict gates used for direct-mode attempts.

Locked production command (single run):

```bash
python3 tools/live_avp_nondirect_prod.py
```

Locked production command (two-run confirmation):

```bash
python3 tools/live_avp_nondirect_prod.py --confirm-twice
```

Known passing evidence:

- `temp/vr_runs/20260218-164234-live-avp-checkpoint`
- `temp/vr_runs/20260223-231520-live-avp-checkpoint`
- `temp/vr_runs/20260223-231843-live-avp-checkpoint`
- release-gate artifact:
  - `temp/pipeline_reports/20260223-232852-release-gate.json`
  - `temp/pipeline_reports/20260223-232852-release-gate.md`

If SteamVR enters safe mode and blocks `alvr_server`, run a null smoke first to
clear crash-safe-mode state, then rerun the strict checkpoint:

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
python3 tools/steamvr_smoke.py --mode null --graphics-backend dxvk
```

Escalation bundle packaging for a failing run:

```bash
python3 tools/package_crossover_repro_bundle.py --run-dir <RUN_DIR>
```

Prompt note:

- The visionOS client has `autoEnterOnConnect` enabled by default.
- During live checkpoints, keep ALVR frontmost; only tap `Enter` if the button
  appears.

Client readiness diagnostics:

- Use `--require-client-ready` to enforce `app_initialized`,
  `streaming_started`, and first decoded/presented frame.
- When app init is seen but stream start is missing or significantly delayed,
  `tools/live_avp_checkpoint.py` now emits `CLIENT_UI_DIAG:` and stores
  `client_ui_block_summary` plus timing fields in `config/outcome.json`.

## Direct-Mode Research Path

Use this command for repeatable strict direct-mode blocker capture across DXVK
and D3DMetal:

```bash
python3 tools/live_avp_directmode_matrix.py
```

Behavior:

- runs strict `direct-mode on` with both fallbacks disabled
- applies all strict gates including `--require-direct-mode-healthy` and
  `--require-pass`
- executes both backends in sequence and writes a compact blocker report to
  `temp/vr_runs/<timestamp>-directmode-matrix/report.json`

Current backend status:

- `dxvk` direct-mode is contract-blocked on this stack because required Win32
  external-memory Vulkan extensions are unavailable.
- `d3dmetal` remains the active direct-mode research backend.

## One-Shot Release Gate

Use this command as the CI/operations gate for the currently supported
production pipeline:

```bash
python3 tools/live_avp_release_gate.py
```

Behavior:

- runs strict non-direct validation with `--confirm-twice`
- writes artifacts to `temp/pipeline_reports/`
  - `<timestamp>-release-gate.json`
  - `<timestamp>-release-gate.md`
  - `latest-release-gate.json`
  - `latest-release-gate.md`
- exits `0` only when both strict non-direct runs pass
- exits `2` when any strict release gate fails

Optional R&D capture in the same pass:

```bash
python3 tools/live_avp_release_gate.py --include-directmode-matrix
```
