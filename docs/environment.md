# Environment

This repo targets reproducible VR experiments on Apple hardware.

## Local Baseline

- macOS: 27.0.0 developer beta
- Architecture: Apple Silicon / arm64
- CrossOver: 26.2
- GPTK: 4.0

## Upstream Baseline

- ALVR stable: `v20.14.1`, published 2025-07-14
- ALVR nightly: `v21.0.0-dev12+nightly.2026.06.16`, generated from
  `alvr-org/ALVR@d9f2b19d2b98b9d70411439fef83300c84ed171d`
- ALVR visionOS repository: `alvr-org/alvr-visionos`, default branch `main`,
  last pushed 2026-04-18

## Current Assumption

ALVR v21 performed better than v20 in prior local work, but the visionOS client
may still not support the v21 streamer protocol. Prove v21 visionOS client
compatibility before investing in the CrossOver-to-native frame bridge.
