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
- `alvr-org/alvr-visionos` issue 165, "ALVR Nightly Support", is open as of
  2026-06-16.

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

`blocked` - upstream evidence suggests v21/nightly visionOS support may still be
unfinished, but this has not been validated locally on current hardware.

## Next Action

Run the build and pairing procedure against the current v21 nightly. If v21 does
not work, decide whether to finish `alvr-visionos` v21 support first or pin this
project back to the newest compatible v20 stream/client pair for bridge probes.
