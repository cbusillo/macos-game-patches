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
git apply /path/to/macos-game-patches/patches/alvr-visionos/alvr-v21-client-core-abi.patch
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
- Does not prove pairing, tracking, video decode, device signing, or runtime
  streaming.
- Temporarily drops the old non-upstream face expression path and passes no
  combined eye gaze to `alvr_send_tracking`; this needs a real v21 tracking
  mapping before device/runtime work is considered complete.
