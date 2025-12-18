# VR on macOS

Start here:

- `HANDOFF.md` — single-page runbook to build, deploy, and validate the current
  ALVR + `vrclient_macos` stack.

## ALVR + OpenVR on macOS

## Quick commands (Justfile)

If you have `just` installed, the fastest way to run common VR iteration tasks
is:

```bash
cd vr-on-macos
just --list
```

Common recipes:

- `just build`
- `just smoke <GAME> <BOTTLE_PATH>`
- `just smoke-launch <GAME> <BOTTLE_PATH>`
- `just logs <BOTTLE_PATH>`

If you don't have `just`, install it with Homebrew (`brew install just`) or
Cargo (`cargo install just`).

If `ALVR/` is empty after cloning, initialize submodules:

```bash
git submodule update --init --recursive
```

- `ALVR/docs/MACOS.md`  
  High-level architecture of the ALVR macOS stack (CrossOver/Wine, shared
  memory, VideoToolbox encoder, AVP client).

- `ALVR/docs/VRCLIENT_MACOS_STATUS.md`  
  Detailed status and experiment log for the `vrclient_macos` +
  `openvr_api_stub` runtime used by Windows VR titles under CrossOver.

- `ALVR/docs/CURRENT_STATUS_AND_NEXT_STEPS.md`  
  One-screen summary of current behavior (The Lab never driving the compositor)
  and recommended next directions for the Apple-only stack.

- `ALVR/docs/VR_CLIENT_GAP_ANALYSIS.md`  
  Deep dive comparing `vrclient_macos` behavior against Proton/SteamVR/OpenVR
  expectations (IVRSystem/IVRCompositor/IVRSettings/IVRInput gaps).

- `ALVR/alvr/vrclient_macos/README.md`  
  Build and layout notes for the macOS-focused OpenVR client DLL.

## Run bundles (recommended)

When iterating on `vrclient_macos`, keep each experiment reproducible by saving
a run bundle under `temp/vr_runs/` (gitignored). A bundle typically includes:

- `alvr_vrclient.log` + `alvr_openvr_api.log`
- Any shared-memory counter snapshots (if used)
- A short one-paragraph README describing what changed and what to look for

## Picking a canary VR title

When iterating on `vrclient_macos`, pick one "canary" title to keep the loop
tight. Good canaries:

- Launch quickly and work without anti-cheat
- Use OpenVR/SteamVR directly (not multiple runtime layers)
- Exercise compositor + input (so we see both `WaitGetPoses` and `Submit`)

Current canary:

- **Aircar** (Steam) — small, fast to iterate, and a good signal for whether
  we're satisfying the minimum SteamVR/OpenVR readiness contract.

Known non-canary:

- **VRChat** — blocked by anti-cheat under CrossOver in most configurations.

## Windows ground truth (SteamVR null driver)

If a title fails under CrossOver, capturing a native Windows trace often helps
identify which OpenVR calls + return values the title expects before it enters
the compositor loop.

Using the SteamVR **null driver** is a useful way to get logs even with no HMD
attached. It won't necessarily render correctly, but it typically produces
`vrserver`/`vrclient` logs that show the application's contract expectations.

## OpenVR macOS bridge (pre-ALVR work)

- `openvr-macos-bridge/README.md`  
  Overview of the standalone OpenVR → macOS bridge project.

- `openvr-macos-bridge/docs/DESIGN.md`  
  Design-level description of the bridge, shared memory, and client
  expectations.
