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

Scratch workspace: `~/Developer/_probe-alvr-v21-avp/alvr-visionos`

Commands run:

```bash
git clone --recurse-submodules https://github.com/alvr-org/alvr-visionos.git \
  ~/Developer/_probe-alvr-v21-avp/alvr-visionos
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

## Controlled v21 Bump Evidence

In the same scratch workspace, the `ALVR` submodule was moved to the ALVR commit
that produced the current nightly:

```bash
git -C ALVR fetch --tags origin master
git -C ALVR checkout d9f2b19d2b98b9d70411439fef83300c84ed171d
git -C ALVR submodule update --init --recursive
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

- `ALVR/Cargo.toml` reported `version = "21.0.0-dev12"`.
- Rust `alvr_client_core` and `ALVRClientCore.xcframework` built successfully
  against ALVR commit `d9f2b19d2b98b9d70411439fef83300c84ed171d`.
- The unmodified Swift app did not compile against the v21 generated C header.
- A small scratch-only compatibility patch let the visionOS simulator app build
  successfully.

The captured patch artifact is
`patches/alvr-visionos/alvr-v21-client-core-abi.patch`; see
`patches/alvr-visionos/README.md` for apply and build commands.

Swift changes captured in the patch:

- Rename codec constants in `ALVRClient/VideoHandler.swift` and
  `ALVRClient/EventHandler.swift` from `ALVR_CODEC_H264`, `ALVR_CODEC_HEVC`,
  and `ALVR_CODEC_AV1` to `ALVR_CODEC_TYPE_H264`, `ALVR_CODEC_TYPE_HEVC`, and
  `ALVR_CODEC_TYPE_AV1`.
- Update `AlvrClientCapabilities` construction in
  `ALVRClient/EventHandler.swift` for the v21 fields by adding `max_view_width`
  and `max_view_height`, and removing `prefer_full_range`.
- Update `alvr_send_active_interaction_profile` calls in
  `ALVRClient/WorldTracker.swift` to pass the new input ID pointer and count
  arguments.
- Replace the non-upstream `alvr_send_tracking_and_face_data` call with the v21
  `alvr_send_tracking(..., nil)` call in `ALVRClient/WorldTracker.swift`.

The last item is compile-oriented only. It drops the old face expression payload
and passes no combined eye gaze, so runtime tracking and face/eye behavior still
need a real v21 mapping before this is considered product-quality client work.

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

`alive` - upstream `alvr-visionos/main` is buildable locally, but it currently
pins a `v20.14.1` ALVR submodule. A controlled bump to the current v21 nightly
ALVR commit builds after a small Swift ABI compatibility patch, which means v21
support is probably close enough to pursue. Pairing, tracking, video decode, and
face/eye behavior are still unproven.

## Next Action

Preserve the scratch compatibility patch as the starting point for a real
`alvr-visionos` v21 port, then run the client on-device against the matching
`v21.0.0-dev12+nightly.2026.06.16` streamer. The next gate is reaching pairing
and first video decode before investing in the CrossOver/D3DMetal frame bridge.
