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
python3 tools/vr_stack_cleanup.py
python3 tools/steamvr_smoke.py
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
  --stream-protocol udp \
  --direct-mode off \
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
  --require-pass
```

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
