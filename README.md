# VR on macOS: Fresh Start

This repository was intentionally reset to a docs-first baseline.

Current objective:

- Run a Windows VR game on Apple Silicon via CrossOver.
- Use Apple Vision Pro as the headset.
- Require H.265 hardware encoding on macOS hardware.
- Reject any software-encode path.

## What Is Kept

- `README.md`: project intent and reset status.
- `docs/facts.md`: verified external facts and links.
- `docs/local-facts.md`: current machine and network facts for this workspace.
- `docs/alvr-source.md`: where ALVR runtime code lives and how it is managed.
- `docs/rebuild-plan.md`: practical rebuild sequence from zero.
- `docs/steamvr-alvr-plan.md`: interaction and phase-gate plan for HEVC + Opus.
- `docs/steamvr-compositor-blocker.md`: current compositor startup blocker,
  hashes, and patch direction.
- `docs/external-projects-map.md`: external projects and escalation channels
  mapped to current blockers.
- `docs/crossover-repro-bundle.md`: deterministic repro bundle checklist and
  escalation template.
- `docs/bridge-ipc-v1.md`: mirrored snapshot of bridge protocol v1.
- `docs/steamvr-smoke.md`: minimal SteamVR runtime smoke harness.
- `tools/hevc_gate.py`: H.265 VideoToolbox hardware gate.
- `tools/steamvr_smoke.py`: deterministic SteamVR launch/log capture harness.
  Captures per-run `*.delta.txt` log slices and supports
  `--graphics-backend {default,d3dmetal,dxvk}`. Default backend is `dxvk`
  because current `d3dmetal` runs crash `vrcompositor` with `Exception c0000005`.
- `tools/vr_stack_cleanup.py`: explicit preflight cleanup for stale CrossOver,
  Wine, SteamVR, and `winetemp` wrapper processes. Supports
  `--sterile-native-steam` to also terminate native Steam `ipcserver` for fully
  sterile runs.
- `tools/live_avp_checkpoint.py`: strict one-shot live AVP harness with
  mandatory preflight/postflight cleanup, `dxvk`-first backend defaults, and
  per-run outcome gating (`bridge_connected`, `frame_ready_seen`, decoder
  probes, client present probes, host frame-signal probes).
- `tools/live_avp_nondirect_prod.py`: locked strict non-direct profile runner
  for the current production fallback path (single command with fixed strict
  gates).
- `tools/live_avp_directmode_matrix.py`: strict direct-mode research runner
  that executes DXVK + D3DMetal and writes a blocker report.
- `tools/live_avp_release_gate.py`: one-shot CI/operations gate that runs the
  strict non-direct pipeline twice and emits artifact summaries.
- `tools/alvr_lock.py`: writes the currently referenced ALVR commit lock file.
- `tools/alvr_driver_register.py`: forces SteamVR to prefer `alvr_server` and
  registers external ALVR driver paths in the CrossOver bottle.
- `tools/alvr_driver_deploy.py`: deploys `driver_alvr_server.dll` and
  manifest into the SteamVR driver folder in the CrossOver bottle, and verifies
  runtime DLL presence for imported `openvr_api.dll` / `libvpl.dll`.
- `tools/vtbridge_protocol.py`: bridge protocol constants and framing helpers.
- `tools/vtbridge_daemon.py`: local daemon skeleton with ring-state handling.
- `tools/vtbridge_probe.py`: local probe client for handshake verification.
- `tools/vtbridge_handshake_gate.py`: deterministic local handshake gate runner.
- `tools/vtbridge_ring_conformance.py`: ring slot state transition conformance test.
- `tools/vtbridge_hw_stream_gate.py`: strict hardware-HEVC stream gate for
  the bridge path.
- `tools/package_crossover_repro_bundle.py`: package a reproducible escalation
  zip from a run directory.
- `LICENSE`.

## Current Status

- No runtime code is considered production-ready.
- Previous experiments were removed from versioned source.
- We are rebuilding only from verified constraints.

## Next Step

Start with `docs/facts.md`, then `docs/local-facts.md`, then execute
`docs/rebuild-plan.md` top-down.

First executable gate:

```bash
python3 tools/hevc_gate.py
```

First runtime smoke:

```bash
python3 tools/vr_stack_cleanup.py
python3 tools/steamvr_smoke.py --mode null
```

Strict live checkpoint (minimal AVP attention window):

```bash
python3 tools/live_avp_checkpoint.py --sterile-native-steam --require-pass
```

Strict real-source validation (fails if client synthetic fallback or host idle
fallback is enabled/used):

```bash
python3 tools/live_avp_checkpoint.py \
  --sterile-native-steam \
  --host-only \
  --codec hevc \
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

Current direct-mode status and strict repro evidence:

- `docs/direct-mode-blocker-dossier.md`

Current best production path:

- strict `direct-mode off` validation (see `docs/steamvr-smoke.md`)

One-command strict non-direct production run:

```bash
python3 tools/live_avp_nondirect_prod.py
```

Two-run strict confirmation (recommended before calling the path stable):

```bash
python3 tools/live_avp_nondirect_prod.py --confirm-twice
```

Direct-mode research matrix (strict, evidence-first):

```bash
python3 tools/live_avp_directmode_matrix.py
```

One-shot release gate with artifacts:

```bash
python3 tools/live_avp_release_gate.py
```

ALVR reference lock update:

```bash
python3 tools/alvr_lock.py
```
