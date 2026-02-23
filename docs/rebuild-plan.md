# Rebuild Plan

## Goal

Rebuild from zero with hard constraints:

- Video must be H.265/HEVC encoded by native macOS hardware (`VideoToolbox`).
- Any software video encode path is a hard fail.
- Audio target is Opus end-to-end.
- No Windows VM fallback path for success.

## Canonical Architecture Plan

Use `docs/steamvr-alvr-plan.md` as the detailed execution plan for SteamVR +
ALVR interaction design, phase gates, and acceptance criteria.

## Entry Checklist

1. Clean stale CrossOver/Wine/SteamVR processes:
   `python3 tools/vr_stack_cleanup.py`.
2. Ensure ALVR fork source exists at `~/Developer/ALVR`.
3. Initialize ALVR submodules:
   `git -C ~/Developer/ALVR submodule update --init --recursive`.
4. Refresh ALVR lock metadata with `python3 tools/alvr_lock.py`.
5. Verify SteamVR baseline with `python3 tools/steamvr_smoke.py --mode null`.
6. Verify native HEVC gate with `python3 tools/hevc_gate.py`.
7. Verify bridge protocol gates:

   ```bash
   python3 tools/vtbridge_handshake_gate.py
   python3 tools/vtbridge_ring_conformance.py
   python3 tools/vtbridge_hw_stream_gate.py
   ```

8. Register ALVR driver intent in the bottle (pre-deploy):
   `python3 tools/alvr_driver_register.py`
9. Deploy the built ALVR driver DLL when available:
   `python3 tools/alvr_driver_deploy.py --dll <path-to-driver_alvr_server.dll>`
10. Check compositor status on both backends:

   ```bash
   python3 tools/steamvr_smoke.py --mode null --graphics-backend d3dmetal
   python3 tools/steamvr_smoke.py --mode null --graphics-backend dxvk
   ```

## Pass Conditions To Start Implementation

- SteamVR baseline run bundle is stable and reproducible.
- HEVC gate proves hardware-only encode with no software fallback.
- Bridge protocol gates pass with hardware HEVC enforcement enabled.
- SteamVR log no longer reports `forcedDriver ... ignored` once driver files are
  deployed.
- SteamVR log no longer reports missing
  `driver_alvr_server.dll` after deployment.
- SteamVR `vrcompositor` starts without
  `VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer`.
- ALVR revision is recorded in `docs/alvr-lock.json`.
