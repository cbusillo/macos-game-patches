# ALVR visionOS Patch Artifacts

Patch artifacts in this directory are meant for scratch checkouts of
`alvr-org/alvr-visionos`, not for this repository itself.

## ALVR v21 Client-Core ABI

`alvr-v21-client-core-abi.patch` is the compile-first compatibility patch
captured during Probe 001.

Tested upstream state:

- `alvr-org/alvr-visionos`: `301b9285073949033727baab2d556fe9e8620612`
- `alvr-org/ALVR`: `d9f2b19d2b98b9d70411439fef83300c84ed171d`
- ALVR version: `21.0.0-dev12`
- Local SDK: Xcode 27.0, visionOS SDK 27.0, Metal Toolchain 27A5194o

Apply from an `alvr-visionos` checkout after moving its `ALVR` submodule to the
matching v21 commit:

```bash
git -C ALVR fetch --tags origin master
git -C ALVR checkout d9f2b19d2b98b9d70411439fef83300c84ed171d
git -C ALVR submodule update --init --recursive
git apply --unidiff-zero ~/Developer/macos-game-patches/patches/alvr-visionos/alvr-v21-client-core-abi.patch
```

Build check:

```bash
unset SDKROOT
zsh build_and_repack.sh
xcodebuild build \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'generic/platform=visionOS Simulator' \
  CODE_SIGNING_ALLOWED=NO
```

Status:

- Builds the v21 Rust `alvr_client_core` and the visionOS simulator app.
- Updates Swift call sites for the generated v21 C ABI.
- Reports the full PSVR2 Sense input ID set when activating the PSVR2
  interaction profile, preserving ALVR v21 automatic controller button mapping.
- Does not prove pairing, tracking, video decode, device signing, or runtime
  streaming.
- Temporarily drops the old non-upstream face expression path and passes no
  combined eye gaze to `alvr_send_tracking`; this needs a real v21 tracking
  mapping before device/runtime work is considered complete.

## Open Brush Startup Liveness

`alvr-open-brush-startup-liveness.patch` fixes the pre-immersive tracking state
used by the connected Open Brush probe and adds bounded decoder diagnostics.

Tested state:

- `cbusillo/alvr-visionos`: `3a00da3fd572262e991b5905665f50f451464f0b`
- ALVR version: `21.0.0-dev12`
- visionOS SDK: `27.0`

Apply after the v21 client-core ABI patch:

```bash
git apply \
  ~/Developer/macos-game-patches/patches/alvr-visionos/alvr-open-brush-startup-liveness.patch
```

Build and device-check:

```bash
unset SDKROOT
zsh build_and_repack.sh
xcodebuild \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'generic/platform=visionOS' \
  build
```

The patch replaces the invalid zero-quaternion, zero-IPD fake tracking with an
identity HMD pose and a `64 mm` stereo pair. It also reports bounded packet and
decode cadence plus previously suppressed VideoToolbox errors. Connected
artifact `real-native-encode-20260715T225109Z` transports `900/900` frames with
`899` exact poses and no decoder errors after this change.
