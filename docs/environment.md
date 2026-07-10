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
- ALVR current development version remains `21.0.0-dev12`.
- ALVR upstream baseline for the clean native-surface forward-port:
  `alvr-org/ALVR@e9b8e3ac62e62ad8007a7b92fb08ec33dea045ba`.
- ALVR visionOS repository: `alvr-org/alvr-visionos`, default branch `main`,
  last pushed 2026-04-18
- ALVR visionOS `main`: `301b9285073949033727baab2d556fe9e8620612`
- ALVR submodule pinned by visionOS `main`:
  `e3fd448029c795b1b2d5835c84c6588bf01bae0d` (`v20.14.1-4-ge3fd4480`)

Active tested fork baselines:

- `cbusillo/alvr-visionos@3a00da3fd572262e991b5905665f50f451464f0b`
- visionOS ALVR submodule:
  `cbusillo/ALVR@109643c88e402b36766020b8f6a99ea48aa8d55f`
- macOS bridge diagnostic checkpoint:
  `cbusillo/ALVR@4bd8ad054a30c3b045f2235ed94b0a4f3cd2b819`

## Source Workspace Layout

Use sibling source clones under `~/Developer` for active ALVR and visionOS
client work. Do not add those source trees as submodules of this repository.

```text
~/Developer/macos-game-patches/
~/Developer/alvr-visionos/
~/Developer/alvr/
```

See `docs/source-workspace.md` for the exact setup commands and current pinned
commits.

## Current Assumption

The forked visionOS client and native macOS bridge can negotiate ALVR v21,
connect to a physical AVP, and sustain a 90 FPS native IOSurface/VideoToolbox
transport session. The active product question is no longer protocol
compatibility. It is finding the best headset-grade way to move real game eye
textures from the Windows compatibility layer into native macOS GPU or encoder
surfaces without a full-frame CPU readback path.

The current CrossOver/GPTK, native surface, VideoToolbox, and ALVR stack is the
leading hypothesis, not a permanent constraint. Compare credible alternatives
against the same latency, cadence, compatibility, visual-correctness,
maintainability, and upstream-viability gates.
