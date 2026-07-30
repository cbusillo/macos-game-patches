# Google Earth VR Legacy Compatibility Classification

## Goal

Classify the official Google Earth VR Steam payload against the shared
OpenVR/D3D11 Mac ALVR runtime while separating local launch, rendering, input,
and cleanup behavior from any retired or unavailable external imagery service.

Issue routing: #102, under compatibility tranche #59.

## Pinned Public Metadata

Recorded from Valve app info on July 30, 2026:

- Steam app: `348250` (`Google Earth VR`)
- public build: `2483525`
- content depot: `348251`
- depot manifest: `7991951760450053422`
- depot size: `2996347031` bytes
- compressed download: `2013973520` bytes
- shared Windows redistributable depot: `228986` from app `228980`
- install directory: `EarthVR`
- launch executable: `Earth.exe`
- platform: Windows x86-64
- runtime: OpenVR
- declared controller families: SteamVR and Oculus
- declared play areas: standing and seated
- installed files: `906`
- installed tree SHA-256:
  `59470ea2ecfcae6b52a9228cf2ab4373cb9d63f7ec70021cbce0d10fd1bdddc1`
- `Earth.exe` SHA-256:
  `b07109d7a394ba011bd7aa5d27bf05f41b9954346e11ac8de2073fbd326a1da7`
- stock `openvr_api.dll` SHA-256:
  `8768bb31597f7d10af8b2a68fe543ac111032e8d91c46e57dd90bd5c7bfe12de`

## Plan

1. Download the official public payload into an isolated ignored directory.
2. Record exact file count, tree hash, executables, PE imports, and runtime DLLs.
3. Inventory OpenVR interfaces, controller bindings, graphics backend, network
   endpoints, generated state, and likely process ownership.
4. Decide whether the existing profile contract can describe the title without
   a title-specific runtime branch.
5. Run artifact/profile preflight and the narrowest local or disconnected probe
   before any physical Vision Pro session.
6. Classify external-service failure separately from local runtime support and
   restore the exact official payload after every probe.

## Reproducible Commands

Refresh public app metadata without touching the CrossOver bottle:

```bash
steamcmd="$repo/.code/tools/steamcmd/steamcmd.sh"
"$steamcmd" \
  +@sSteamCmdForcePlatformType windows \
  +login anonymous \
  +app_info_update 1 \
  +app_info_print 348250 \
  +quit
```

Download the public payload into an isolated directory:

```bash
install_root="$PWD/.code/vendor/google-earth-vr"
"$steamcmd" \
  +@sSteamCmdForcePlatformType windows \
  +force_install_dir "$install_root" \
  +login anonymous \
  +app_update 348250 validate \
  +quit
```

Anonymous SteamCMD metadata access succeeded, but payload download returned
`No subscription`. The cached authorized CrossOver Steam session then installed
the same public build from `steam://install/348250`; its content log confirmed
depot `348251` and manifest `7991951760450053422` before commit.

## Expected Artifacts

- `.code/probes/024-google-earth-vr/appinfo-*.txt`
- `.code/vendor/google-earth-vr/`
- exact payload inventory and tree identity recorded in this document
- retained local/disconnected probe logs if the title reaches the supported
  frame path

## Cleanup

The initial download is isolated and must not modify the CrossOver Steam
library. If that path succeeds, remove only the owned ignored payload when it
is no longer needed:

```bash
rm -rf .code/vendor/google-earth-vr
```

Any later bottle-backed probe must use the normal profile runner and finish
with exact stock hashes, no staged runtime DLLs, no owned process, and no
generated runtime logs below the game tree.

The official uninstall script additionally owns these generated directories:

```text
%LOCALAPPDATA%\Google\VR\Earth
%ALLUSERSPROFILE%\Google\VR\Earth
```

## Known Failure Signatures

- anonymous Steam login cannot acquire the free app license;
- the shared redistributable depot is absent from an isolated download;
- the public build launches locally but external imagery/API requests fail;
- the title requests legacy OpenVR compositor or controller interfaces not yet
  implemented by the shared runtime;
- a native Oculus path is selected instead of the declared OpenVR launch;
- process or generated-state ownership cannot be expressed without weakening
  exact cleanup.

## Current Evidence

Valve still publishes one public Windows/OpenVR build, unchanged since 2018.
The cached Steam account installed that exact build into the existing bottle
without modifying its payload.

Static inventory identifies two independent boundaries before external imagery
or service behavior can be tested:

- `Earth.exe` imports `OPENGL32.dll` and `openvr_api.dll`; it does not import
  D3D11 or DXGI. The current curated profile validator rejects any
  `graphicsApi` other than `d3d11`, so this title cannot enter the v1 frame path
  through profile data alone.
- The executable requests legacy `IVRSystem_017`, `IVRCompositor_021`,
  `IVROverlay_016`, `IVRRenderModels_005`, and `IVRApplications_006`. The shared
  runtime has no dispatch entries for those exact revisions. Adding aliases
  without slot/ABI validation would be unsafe.

The payload contains no SteamVR Input action or binding manifest. Binary
strings identify direct Vive-era touchpad, application-button, grip, trigger,
controller-model, tooltip, and haptic behavior. That makes controller support a
legacy polling/model problem rather than a profile-only action remap.

The client embeds active Google endpoints including `kh.google.com`,
`earth.google.com`, `maps.googleapis.com`, `geo0.ggpht.com`, and Google Earth
RPC search/config/reveal routes. Their live availability remains untested
because the local graphics and OpenVR interface boundaries occur first.

Verdict: `unsupported-current-v1`. This is an evidence-backed architecture
classification, not a service-retirement claim. OpenGL submission belongs to
issue #98, missing legacy OpenVR revisions to #96, and direct Vive-era
controller semantics to #99. Do not add Google Earth-specific aliases or a
D3D11 fiction
inside the pre-release tranche.
