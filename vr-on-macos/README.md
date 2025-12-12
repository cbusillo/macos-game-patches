### ALVR + OpenVR on macOS

If `ALVR/` is empty after cloning, initialize submodules:

```bash
git submodule update --init --recursive
```

- `ALVR/docs/MACOS.md`  
  High-level architecture of the ALVR macOS stack (CrossOver/Wine, shared memory, VideoToolbox encoder, AVP client).

- `ALVR/docs/VRCLIENT_MACOS_STATUS.md`  
  Detailed status and experiment log for the `vrclient_macos` + `openvr_api_stub` runtime used by Windows VR titles under CrossOver.

- `ALVR/docs/CURRENT_STATUS_AND_NEXT_STEPS.md`  
  One-screen summary of current behavior (The Lab never driving the compositor) and recommended next directions for the Apple-only stack.

- `ALVR/docs/VR_CLIENT_GAP_ANALYSIS.md`  
  Deep dive comparing `vrclient_macos` behavior against Proton/SteamVR/OpenVR expectations (IVRSystem/IVRCompositor/IVRSettings/IVRInput gaps).

- `ALVR/alvr/vrclient_macos/README.md`  
  Build and layout notes for the macOS-focused OpenVR client DLL.

### OpenVR macOS bridge (pre-ALVR work)

- `openvr-macos-bridge/README.md`  
  Overview of the standalone OpenVR → macOS bridge project.

- `openvr-macos-bridge/docs/DESIGN.md`  
  Design-level description of the bridge, shared memory, and client expectations.
