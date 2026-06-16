# 001 - ALVR v21 visionOS Compatibility

## Hypothesis

The current ALVR visionOS client can be built and used with the current ALVR v21
streamer/nightly protocol.

## Why This Comes First

Prior local work found ALVR v21 performed better than v20, but the visionOS
client did not work correctly against v21. If that is still true, the first
milestone is updating or pinning the AVP client before CrossOver, SteamVR,
D3DMetal, or native VideoToolbox bridge work can produce a useful end-to-end
result.

## Current Upstream Facts

- ALVR stable release is `v20.14.1`.
- ALVR nightly release is `v21.0.0-dev12+nightly.2026.06.16`.
- `alvr-org/alvr-visionos` has no GitHub releases or tags.
- `alvr-org/alvr-visionos` `main` is
  `301b9285073949033727baab2d556fe9e8620612`.
- `alvr-org/alvr-visionos` pins its `ALVR` submodule to
  `e3fd448029c795b1b2d5835c84c6588bf01bae0d`, which reports
  `version = "20.14.1"` and `v20.14.1-4-ge3fd4480`.
- `alvr-org/alvr-visionos` issue 165, "ALVR Nightly Support", is open as of
  2026-06-16.

## Local Build Evidence

Scratch workspace: `/Users/cbusillo/Developer/_probe-alvr-v21-avp/alvr-visionos`

Commands run:

```bash
git clone --recurse-submodules https://github.com/alvr-org/alvr-visionos.git \
  /Users/cbusillo/Developer/_probe-alvr-v21-avp/alvr-visionos
rustup target add aarch64-apple-ios
xcodebuild -downloadComponent MetalToolchain
unset SDKROOT
zsh build_and_repack.sh
xcodebuild build \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'generic/platform=visionOS Simulator' \
  CODE_SIGNING_ALLOWED=NO
```

Results:

- Rust `alvr_client_core` distribution build succeeded for the pinned
  `v20.14.1` ALVR submodule.
- `ALVRClient/ALVRClientCore.xcframework` was generated successfully.
- The first Xcode app build failed because the local Xcode install lacked the
  Metal Toolchain component.
- After `xcodebuild -downloadComponent MetalToolchain`, the no-signing
  `generic/platform=visionOS Simulator` build succeeded.
- This proves the current upstream visionOS client is buildable locally, but it
  builds against ALVR `20.14.1`, not ALVR v21.

## Procedure

1. Clone or update `alvr-org/ALVR` and `alvr-org/alvr-visionos` into a local
   workspace outside this repo.
2. Build the ALVR visionOS client for a generic visionOS destination.
3. Pair the client with the current ALVR v21 nightly streamer.
4. Record whether connection, protocol negotiation, tracking, and video decode
   reach a usable state.

## Evidence To Capture

- ALVR commit SHA and release/nightly tag
- `alvr-visionos` commit SHA
- Xcode build command and result
- headset/client logs
- streamer logs
- first failing protocol or runtime symptom, if any

## Verdict

`blocked` - upstream `alvr-visionos/main` is buildable locally, but it currently
pins a `v20.14.1` ALVR submodule. Current v21/nightly visionOS support is not
present in the upstream pin and remains unresolved by open issue 165.

## Next Action

Attempt a controlled v21 submodule bump to the current ALVR nightly source
commit and rebuild `alvr_client_core` plus the visionOS app. If the build fails,
the next workstream is porting `alvr-visionos` to the current v21 client-core C
ABI before CrossOver/D3DMetal bridge work can produce a useful end-to-end result.
