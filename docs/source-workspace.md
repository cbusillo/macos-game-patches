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

- `alvr-org/alvr-visionos`: `301b9285073949033727baab2d556fe9e8620612`
- `alvr-org/ALVR` for v21 client-core work:
  `d9f2b19d2b98b9d70411439fef83300c84ed171d`
- ALVR version: `21.0.0-dev12`

## Setup

```bash
cd ~/Developer
git clone https://github.com/alvr-org/alvr-visionos.git alvr-visionos
git clone https://github.com/alvr-org/ALVR.git alvr

cd ~/Developer/alvr-visionos
git submodule update --init ALVR
git -C ALVR fetch --tags origin master
git -C ALVR checkout d9f2b19d2b98b9d70411439fef83300c84ed171d
git -C ALVR submodule update --init --recursive
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
