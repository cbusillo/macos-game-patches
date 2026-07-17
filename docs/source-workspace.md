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

## Qualified Runtime Baseline

- Host: `cbusillo/ALVR`, branch `diagnostic/bgra-nv12-probe`, commit
  `4bd8ad054a30c3b045f2235ed94b0a4f3cd2b819`
- visionOS client: `cbusillo/alvr-visionos`, branch `main`, commit
  `171cd9dca5ef85c9dfd9f35c565c265c08e8ce82`
- visionOS client core: `cbusillo/ALVR`, branch
  `visionos-client-mdns-c5d8bd26`, commit
  `109643c88e402b36766020b8f6a99ea48aa8d55f`
- Protocol/version: `21.0.0-dev12`

These commits are the physically qualified owner-runtime baseline. The earlier
upstream commits in patch READMEs remain provenance for individual patches, not
the complete current runtime.

## Setup

```bash
cd ~/Developer
git clone https://github.com/cbusillo/alvr-visionos.git alvr-visionos
git clone https://github.com/cbusillo/ALVR.git alvr

cd ~/Developer/alvr-visionos
git checkout 171cd9dca5ef85c9dfd9f35c565c265c08e8ce82
git submodule update --init ALVR
git -C ALVR fetch \
  https://github.com/cbusillo/ALVR.git visionos-client-mdns-c5d8bd26
git -C ALVR checkout 109643c88e402b36766020b8f6a99ea48aa8d55f
git -C ALVR submodule update --init --recursive

cd ~/Developer/alvr
git checkout 4bd8ad054a30c3b045f2235ed94b0a4f3cd2b819
```

Add `alvr-org` remotes when comparing or preparing upstream work. Do not apply
the repository patch artifacts on top of these qualified fork commits; those
commits already contain the runtime changes. The patch files remain the durable
review and reconstruction record for their documented upstream bases.

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

## Runtime Artifact Bindings

The runtime artifact keeps machine-specific paths outside Git. Copy
`runtime/bindings.example.json` to the ignored
`.code/runtime-bindings.json`, then replace only the three local build-output
roots for MoltenVK, DXVK, and the OpenVR/Wine bridge outputs.

The checked-in manifest still pins every consumed byte by SHA-256. ALVR inputs
also require exact clean commits and remotes. CrossOver, DXVK, and MoltenVK
build trees remain external prerequisites rather than vendored source; their
qualified product versions, patch recipes, binary formats, architectures,
signatures, and output hashes define the honest opaque-output boundary.

Run `python3 tools/build_runtime_artifact.py validate --bindings
.code/runtime-bindings.json` before building. Unknown bindings, missing files,
dirty or wrong Git checkouts, mismatched hashes, unsupported architectures, and
signature drift fail closed.
