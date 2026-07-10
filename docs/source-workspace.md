# Source Workspace

This repository is the planning, probe, and patch ledger. Keep active upstream
source work in sibling clones under `~/Developer` instead of vendoring source or
adding submodules here.

## Canonical Layout

```text
~/Developer/
  macos-game-patches/   # this repo: docs, probes, patch artifacts
  alvr-visionos/        # source checkout for the Apple Vision Pro client
  alvr/                 # optional standalone ALVR checkout for streamer work
```

Use `alvr-visionos/ALVR` for the client-core submodule when building the
visionOS app. Use the standalone `alvr/` checkout when working on the streamer,
server, dashboard, or CrossOver bridge experiments that need a full ALVR source
tree outside the visionOS client checkout.

## Current Baseline

- `cbusillo/alvr-visionos` active AVP client:
  `3a00da3fd572262e991b5905665f50f451464f0b`
- visionOS checkout's `ALVR` submodule:
  `109643c88e402b36766020b8f6a99ea48aa8d55f`
- `cbusillo/ALVR` tested diagnostic checkpoint:
  `4bd8ad054a30c3b045f2235ed94b0a4f3cd2b819`
- `alvr-org/ALVR` clean forward-port baseline:
  `e9b8e3ac62e62ad8007a7b92fb08ec33dea045ba`
- ALVR version: `21.0.0-dev12`

## Setup

```bash
cd ~/Developer
git clone https://github.com/cbusillo/alvr-visionos.git alvr-visionos
git clone https://github.com/cbusillo/ALVR.git alvr

cd ~/Developer/alvr-visionos
git submodule update --init ALVR
git -C ALVR remote add fork \
  https://github.com/cbusillo/ALVR.git 2>/dev/null || true
git -C ALVR fetch fork visionos-client-mdns-c5d8bd26
git -C ALVR checkout 109643c88e402b36766020b8f6a99ea48aa8d55f
git -C ALVR submodule update --init --recursive

cd ~/Developer/alvr
git remote add upstream https://github.com/alvr-org/ALVR.git 2>/dev/null || true
git fetch origin diagnostic/bgra-nv12-probe
git fetch upstream master
```

Apply patch artifacts from this repo to the sibling source checkout:

```bash
cd ~/Developer/alvr-visionos
git apply ~/Developer/macos-game-patches/patches/alvr-visionos/alvr-v21-client-core-abi.patch
```

## Why Not Submodules Here

- `alvr-visionos` already contains an `ALVR` submodule, and v21 work requires
  moving that nested submodule away from the upstream v20 pin.
- Builds create large Rust and Xcode outputs that should stay outside this
  documentation repo.
- Active source branches, bisects, runtime probes, and upstream PRs are easier
  from ordinary sibling clones.
- This repo's durable output is evidence and reproducible patches, not vendored
  source.

If CI eventually needs to reproduce a full source tree, prefer a manifest and
setup script before adding submodules to this repository.
