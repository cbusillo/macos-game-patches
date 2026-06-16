# Environment

This repo targets reproducible VR experiments on Apple hardware.

## Local Baseline

- macOS: 27.0.0 developer beta
- Architecture: Apple Silicon / arm64
- CrossOver: 26.2
- GPTK: 4.0
- Xcode: 27.0 build 27A5194q
- visionOS SDK: 27.0
- Metal Toolchain: 27A5194o

## Upstream Baseline

- ALVR stable: `v20.14.1`, published 2025-07-14
- ALVR nightly: `v21.0.0-dev12+nightly.2026.06.16`, generated from
  `alvr-org/ALVR@d9f2b19d2b98b9d70411439fef83300c84ed171d`
- ALVR visionOS repository: `alvr-org/alvr-visionos`, default branch `main`,
  last pushed 2026-04-18
- ALVR visionOS `main`: `301b9285073949033727baab2d556fe9e8620612`
- ALVR submodule pinned by visionOS `main`:
  `e3fd448029c795b1b2d5835c84c6588bf01bae0d` (`v20.14.1-4-ge3fd4480`)

## Current Assumption

ALVR v21 performed better than v20 in prior local work, but the visionOS client
may still not support the v21 streamer protocol. Prove v21 visionOS client
compatibility before investing in the CrossOver-to-native frame bridge.
