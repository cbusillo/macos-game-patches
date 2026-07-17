# Environment

This repo targets reproducible VR experiments on Apple hardware.

## Qualified Local Baseline

- Host: Mac Studio `Mac16,9`, Apple M4 Max, 128 GB
- macOS: 27.0 beta, build `26A5378n`
- Architecture: Apple Silicon / arm64
- CrossOver: 26.2.0 build `39821`
- GPTK: 4.0
- Xcode: 27.0 build 27A5194q
- visionOS SDK: 27.0
- Metal Toolchain: 27A5194o
- Physical client: Apple Vision Pro on visionOS 27.0 beta, build `24M5316k`

## Qualified Source Baseline

- ALVR protocol/version: `21.0.0-dev12`
- Host fork: `cbusillo/ALVR@229e8ced76be9b62307fe79690229c5e6bc020d5`
  on `diagnostic/bgra-nv12-probe`
- visionOS client fork:
  `cbusillo/alvr-visionos@171cd9dca5ef85c9dfd9f35c565c265c08e8ce82`
- visionOS client-core fork:
  `cbusillo/ALVR@109643c88e402b36766020b8f6a99ea48aa8d55f`
  on `visionos-client-mdns-c5d8bd26`
- Upstream v21 ancestry includes
  `alvr-org/ALVR@d9f2b19d2b98b9d70411439fef83300c84ed171d`
  and
  `alvr-org/ALVR@c5d8bd2652cf0642e63aac817e3777db21506514`.

## Source Workspace Layout

Use sibling source clones under `~/Developer` for active ALVR and visionOS
client work. Do not add those source trees as submodules of this repository.

```text
~/Developer/macos-game-patches/
~/Developer/alvr-visionos/
~/Developer/alvr/
```

See `docs/source-workspace.md` for the exact setup commands and pinned commits.

## Current Direction

The ALVR v21 client, GPU-resident CrossOver-to-native bridge, hardware encode,
transport, physical Vision Pro rendering, PS VR2 controls, recovery, and exact
cleanup are proven on the qualified baseline. The active workstream packages
that path as the owner-operated runtime defined in
`docs/reproducible-mac-alvr-runtime-v1.md`.

These are exact qualification pins, not minimum-version promises. Any host,
operating-system, dependency, game-build, signing, or stable-bundle change must
be requalified before inheriting the existing support claim.
