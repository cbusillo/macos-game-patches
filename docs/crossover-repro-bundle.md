# CrossOver Repro Bundle

As of February 23, 2026.

Use this when escalating a persistent interop blocker to external projects
(CodeWeavers, DXVK, ALVR upstream).

## Goal

Produce a small, deterministic bundle that proves failure with concrete
signatures and minimal noise.

## Preflight

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
```

## Canonical strict repro

```bash
python3 tools/live_avp_checkpoint.py \
  --sterile-native-steam \
  --host-only \
  --codec hevc \
  --stream-protocol udp \
  --direct-mode on \
  --graphics-backend d3dmetal \
  --display-redirect on \
  --non-direct-source disable \
  --synthetic-fallback disable \
  --host-idle-fallback disable \
  --steamvr-home on \
  --steamvr-tool steamvr_room_setup \
  --require-direct-mode-healthy \
  --require-host-frame-signals \
  --forbid-static-source \
  --require-pass
```

If you need matrix evidence, also run:

```bash
python3 tools/live_avp_directmode_matrix.py
```

## Required artifacts

- Run directory under `temp/vr_runs/<stamp>-live-avp-checkpoint`
- `config/outcome.json`
- `logs/session_log.txt` and `.delta.txt`
- `logs/vrserver.delta.txt`
- `logs/vrcompositor.delta.txt`
- `logs/vtbridge-daemon.log`
- `config/meta.json`
- `config/steamvr.vrsettings.before.json`
- `config/steamvr.vrsettings.after.json`

## Required signatures to highlight

- Shared-handle / interop failures (`GetSharedHandle`, `CreateSharedHandle`, HRESULT)
- Missing required Vulkan extensions (if present)
- Missing frame-signal probes (`host_new_frame_ready_*`, `host_copy_to_staging_*`)
- Static source markers (`source_static_suspected`, repeated `sample_crc`)

## Bundle packaging

Use:

```bash
python3 tools/package_crossover_repro_bundle.py --run-dir <RUN_DIR>
```

Default output: `<RUN_DIR>/repro-bundle.zip`.

## Issue template

- Environment:
  - macOS version
  - CrossOver version
  - SteamVR build id
  - Backend (`dxvk` or `d3dmetal`)
- Expected behavior:
  - direct-mode healthy, host frame signals, non-static source
- Actual behavior:
  - copy exact `gate_failures` from `config/outcome.json`
- Attachments:
  - repro-bundle zip
