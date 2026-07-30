# Aperture Hand Lab Index-Input Compatibility Classification

## Goal

Classify the official Aperture Hand Lab Steam payload against the shared Mac
ALVR runtime, identifying the earliest exact graphics, OpenVR, compositor,
render-model, skeletal/capacitive-input, haptic, or ownership boundary without
misrepresenting Valve Index behavior as ordinary PS VR2 Sense input.

Issue routing: #103, under compatibility tranche #59.

## Pinned Public Metadata

Recorded from Valve app info on July 30, 2026:

- Steam app: `868020` (`Aperture Hand Lab`)
- public build: `23437678`
- content depot: `868021`
- depot manifest: `634480059032772972`
- depot size: `793828171` bytes
- compressed download: `451032976` bytes
- public branch update: May 27, 2026
- install directory: `Knux`
- launch executable: `Knux.exe`
- platform: Windows x86-64
- runtime: OpenVR
- declared controller family: SteamVR
- declared play area: room-scale, `2.0 x 1.5` meters
- declared action manifest: `actions.json`
- Steam compatibility test: build `23437678`, recorded July 29, 2026,
  `UnsupportedGraphicsPerformance`

The cached authorized Steam session installed that exact public build. The
installed app manifest records `StateFlags=4`, depot `868021`, manifest
`634480059032772972`, and `793828171` bytes on disk.

## Installed Payload Identity

- installed files: `231`
- installed tree SHA-256:
  `15803f2161db3dd09870c4d1f41a55b915259cec12b572fc04b00f5781ef4ff4`
- `Knux.exe` SHA-256:
  `c0409ae483405c2852fa329b01ce2f199128263ad186f94758481d5a52ccea47`
- `UnityPlayer.dll` SHA-256:
  `b3751f312a7bea66d832e8a5999d8c7629aa76eb20b3328410a69cb6f7ec0a67`
- `Knux_Data/Managed/Assembly-CSharp.dll` SHA-256:
  `062aabfa5302be84579ddd3135e4afc5f9bc588cd1d8debdb28e39b88c0c62b0`
- stock `Knux_Data/Plugins/openvr_api.dll` SHA-256:
  `74eeb0c989d10e8aaf20da5ae2bc1fa7dd34e74fd3c54f9cce40544878e4e157`
- `actions.json` SHA-256:
  `7a2d65ceaa141e9a837866452fbf8e35c319393c1f8feca955435d109dd91f5d`
- `bindings_knuckles.json` SHA-256:
  `38fba14ccaed0be87096a17611f3b2d0786f996db6ba911f9ccde41ac172f2d5`
- engine: Unity `2018.2.10f1`, Windows x86-64
- launch/process candidate: `Knux.exe`
- stock OpenVR DLL location: `Knux_Data/Plugins/openvr_api.dll`

## Plan

1. Install the exact official public payload through the cached Steam session.
2. Record exact file count, tree hash, executables, PE imports, and runtime DLLs.
3. Inventory OpenVR interface revisions, the action manifest, controller
   bindings, render models, skeletal/capacitive inputs, haptics, overlays,
   process ownership, and generated state.
4. Decide whether the current D3D11 profile/runtime contracts can represent the
   title without false Index-to-Sense mappings or title-specific aliases.
5. Run only the narrowest local or disconnected probe justified by the static
   evidence, and retain the earliest exact failure signature.
6. Restore the stock payload and route shared capability gaps to the owning
   post-v1 workstreams.

## Reproducible Commands

Refresh public metadata:

```bash
steamcmd="$repo/.code/tools/steamcmd/steamcmd.sh"
"$steamcmd" \
  +@sSteamCmdForcePlatformType windows \
  +login anonymous \
  +app_info_update 1 \
  +app_info_print 868020 \
  +quit
```

Install through the cached authorized Steam session:

```bash
cxstart \
  --bottle Steam \
  --no-wait \
  --no-gui \
  steam://install/868020
```

Record the canonical tree identity and critical hashes:

```bash
steam_root="$HOME/Library/Application Support/CrossOver/Bottles/Steam"
game_root="$steam_root/drive_c/Program Files (x86)/Steam/steamapps/common/Knux"

GAME_ROOT="$game_root" python3 -c '
import json, os
from pathlib import Path
from tools.runtime_profile import payload_tree_identity
print(json.dumps(payload_tree_identity(Path(os.environ["GAME_ROOT"]))))
'

(
  cd "$game_root"
  shasum -a 256 \
    Knux.exe \
    UnityPlayer.dll \
    Knux_Data/Managed/Assembly-CSharp.dll \
    Knux_Data/Plugins/openvr_api.dll \
    actions.json \
    bindings_knuckles.json \
    unityProject.vrmanifest
)
```

Extract the managed OpenVR interface table and input contract:

```bash
monodis --userstrings \
  "$game_root/Knux_Data/Managed/Assembly-CSharp.dll" \
  | rg 'FnTable:IVR[A-Za-z]+_[0-9]+'

jq '{actions, default_bindings}' "$game_root/actions.json"
jq '{controller_type, bindings}' "$game_root/bindings_knuckles.json"
```

## Expected Artifacts

- `.code/probes/025-aperture-hand-lab/appinfo-*.txt`
- `.code/probes/025-aperture-hand-lab/static-inventory-*.txt`
- official payload under the existing Steam bottle
- exact payload inventory and tree identity recorded in this document
- retained local/disconnected logs only when static admission succeeds

Captured read-only evidence:

- `.code/probes/025-aperture-hand-lab/appinfo-20260730T064502Z.txt`
  (SHA-256
  `10c496d9a198ed64389367f1ce034a6615223ede6277a933d1f435552399e327`)
- `.code/probes/025-aperture-hand-lab/static-inventory-20260730T161344Z.txt`
  (SHA-256
  `ada2f4181545ec108af6b0d21d2d5de2e74dbb0427edb4af23a79ca0fa58f242`)

## Cleanup

Do not alter game binaries during inventory. Any later runtime probe must use
the normal profile runner or an explicitly documented bounded research path and
finish with exact stock hashes, no staged runtime DLLs, no owned process, and no
generated runtime state below the game tree.

The classification stopped at read-only static inventory. It did not launch
`Knux.exe`, stage a runtime DLL, or create title-owned save/log state. The stock
OpenVR DLL remained at SHA-256
`74eeb0c989d10e8aaf20da5ae2bc1fa7dd34e74fd3c54f9cce40544878e4e157`,
and the repeated payload identity remained 231 files with tree SHA-256
`15803f2161db3dd09870c4d1f41a55b915259cec12b572fc04b00f5781ef4ff4`.
Steam-created desktop and Start Menu URL shortcuts are retained with the
official installation for any future post-v1 probe.

## Known Failure Signatures

- the public payload no longer contains the expected action manifest or Index
  bindings;
- the title requests OpenVR interface revisions not exposed by the shared
  runtime;
- graphics submission is outside the D3D11/IOSurface contract;
- required Index skeletal or capacitive channels cannot be represented by PS
  VR2 Sense without deceptive behavior;
- controller render-model or overlay dependencies fail before frame submission;
- process or generated-state ownership cannot be expressed with exact cleanup.

## Current Evidence

Valve still publishes a free Windows/OpenVR build with an explicit
`actions.json` contract. The public branch was refreshed on May 27, 2026, and
the latest Steam compatibility record independently marks the same build as
graphics-performance unsupported on its tested reference platform.

The payload is a Unity `2018.2.10f1` application. `boot.config` enables VR and
selects `OpenVR`. `UnityPlayer.dll` contains both Direct3D 11 and Vulkan paths,
so static binaries alone do not establish the active graphics backend. The
classification stops at an earlier exact input boundary and therefore does not
invent a `d3d11` profile or claim that the frame path was exercised.

The managed Valve OpenVR binding names these function tables:

```text
IVRSystem_019
IVRChaperone_003
IVRChaperoneSetup_005
IVRCompositor_022
IVROverlay_018
IVRRenderModels_006
IVRExtendedDisplay_001
IVRSettings_002
IVRApplications_006
IVRScreenshots_001
IVRTrackedCamera_003
IVRInput_004
IVRSpatialAnchors_001
```

The normal `SteamVR.CreateInstance` path first obtains compositor 022 and
overlay 018, then calls `IdentifyApplication()`. That call unconditionally
obtains `FnTable:IVRApplications_006`. The shared runtime exposes the current
applications table 007 plus legacy tables 004 and 005, but not 006, so this is
the earliest exact ABI failure and returns
`VRInitError_Init_InterfaceNotFound`.

If applications 006 were added correctly, the same startup path next calls
`SteamVR_Input.IdentifyActionsFile()`, which unconditionally obtains
`FnTable:IVRInput_004` before loading `actions.json`. The shared runtime exposes
legacy input tables 005, 006, and 007, but not 004. The managed binding also
contains paths for tracked camera, extended display, and spatial anchors that
do not all have exact shared-runtime dispatch, but those optional paths are not
needed to establish this verdict.

Adding an 18-slot `IVRInput_004` table would remove only the first ABI failure.
It would not provide the title's required semantics:

- `actions.json` declares 16 actions: seven boolean, one pose, two skeleton,
  three vector1, two vector2, and one vibration action.
- Its only default binding is `controller_type=knuckles`.
- The binding has left/right skeleton inputs and four `force_sensor` sources
  supplying grip and thumb pressure.
- `CHGHand` reads the `GripPressure` and `ThumbPressure` axes directly, while
  `CHGSkeleton` consumes per-bone positions and rotations every update.
- The title explicitly warns when the reported controller type contains
  neither `knuckles` nor `index`.

The shared controller transport carries pose, linear/angular velocity, pressed
and touched button masks, and five two-axis values. It has no per-finger bones,
finger curls, capacitive force, or thumb/grip pressure fields. The current
skeletal methods report zero bones or `VRInputError_NoData`; the pressure and
thumb-specific action names resolve to `Unknown` and return zero; and the
controller identity reports `vive_controller`. Haptic calls return success,
but that does not compensate for absent hand state.

Verdict: `unsupported-current-v1`. The earliest exact failure is the missing
`FnTable:IVRApplications_006` ABI, followed independently by the missing
18-slot `FnTable:IVRInput_004` ABI. The decisive product boundary is deeper:
this title is an Index hand-interaction demonstration whose gameplay consumes
skeletal and force-sensor state that the PS VR2 Sense transport does not
represent. Do not add title-specific ABI aliases, spoof an Index controller
identity, collapse all fingers into trigger/grip, or claim support from a
warning-screen launch. Legacy OpenVR interface work belongs to #96;
skeletal/capacitive controller representation and compatibility policy belong
to #99. A future probe is justified only after those shared contracts define an
honest degraded-input or full hand-state capability.
