# Freedom Local Window Regression

## Goal

Identify which staged graphics component first makes the macOS Freedom window
blue-tinted or flashing. A locally corrupt game window invalidates every later
IOSurface, encoder, ALVR, and headset result.

This is a source-image gate for issue #53 under parent #36. No AVP observation
is required.

## Hypothesis

The June shared-memory launches used CrossOver's default graphics path and
showed recognizable game imagery. The production IOSurface runner instead
forces DXVK, stages custom `d3d11.dll` and `dxgi.dll`, and replaces CrossOver's
MoltenVK with a patched build. The visual regression should appear at one of
those transitions.

## Matrix

Run the same fake OpenVR runtime and submit shim through these graphics stacks,
stopping after the first corrupt result:

1. Stock CrossOver backend and stock MoltenVK.
2. CrossOver bundled DXVK with stock MoltenVK.
3. Custom DXVK DLLs with stock MoltenVK.
4. Custom DXVK DLLs with patched MoltenVK and the IOSurface handoff enabled.

Each variant captures three macOS screenshots at fixed intervals, the loaded-DLL
log, DXVK/MoltenVK logs when present, exact input hashes, and the launch command.

## Pass Gate

- Freedom's local window has stable, recognizable color.
- Consecutive screenshots do not alternate between unrelated frames or blank
  output.
- No `Invalid Resource`, device-loss, or swapchain/present failure appears.
- CrossOver MoltenVK, the game OpenVR DLL, and staged graphics DLLs return to
  their pinned pristine state after every variant.

Blue tint, channel-swapped color, flashing, or unstable presentation is a hard
failure even when IOSurface identity tests or ALVR transport counters pass.

## Cleanup

Every variant terminates Freedom and CrossOver helper processes, restores the
stock MoltenVK and OpenVR DLL by checksum, removes staged `d3d11.dll`,
`dxgi.dll`, and bridge DLLs, and preserves bounded evidence under
`.code/probes/010-freedom-local-window-regression/`.

## Current Status

`passed` on July 12, 2026 UTC. Headset testing remains paused until the corrected
settings also pass the production IOSurface runner.

### Component Matrix

- **Stock CrossOver and MoltenVK:** correct full color, including the red robot
  and neutral background.
- **Bundled DXVK and stock MoltenVK:** black, with five geometry-shader
  compilation failures.
- **Custom DXVK and stock MoltenVK:** black, with geometry failures and 257
  swapchain creations.
- **Patched stack with argument buffers:** blue-only and flashing. Red and green
  were zero in every submitted pixel, and 187 swapchains were created.
- **Corrected patched stack:** stable full color. All B, G, and R channels were
  populated, only two startup swapchains were created, and no shader failed.

The final clean artifact is
`.code/probes/010-freedom-local-window-regression/custom-dxvk-patched-mvk-20260712T031143Z`.
Three screenshots show stable full-color output. For the first six sampled
frames in both eyes, roughly 2.9 million pixels per channel were nonzero and all
channel maxima were 255. Cleanup restored the stock CrossOver MoltenVK and game
OpenVR DLL hashes and removed every staged DXVK file.

### Root Causes

Two independent upstream faults produced the reported result:

1. MoltenVK's Metal argument-buffer path corrupted Freedom's shader-resource
   bindings. The resulting D3D11 eye texture was not merely tinted: every red
   and green byte was exactly zero while blue remained populated. Launching
   with `MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS=0` restored full color in the
   local window and in direct eye-texture readback.
2. Freedom renders a 3200x1800 drawable inside a 3840x2160 macOS layer, so
   MoltenVK legitimately reports `VK_SUBOPTIMAL_KHR`. DXVK 1.10.3 treated that
   usable present result as a hard failure and rebuilt the identical swapchain
   continuously. Allowing `VK_SUBOPTIMAL_KHR` in `SynchronizePresent()` reduced
   swapchain creation from hundreds per run to the expected two startup
   creations and removed the flashing loop.

The argument-buffer boundary is proven; the lower-level MoltenVK descriptor
binding defect is not yet generalized beyond this Freedom/DXVK workload. The
runtime workaround is therefore explicit rather than presented as an upstream
MoltenVK fix.

### Reproduction

The local matrix is automated by:

```bash
bash tools/run_freedom_local_window_regression.sh stock-d3dmetal
bash tools/run_freedom_local_window_regression.sh bundled-dxvk
bash tools/run_freedom_local_window_regression.sh custom-dxvk-stock-mvk
bash tools/run_freedom_local_window_regression.sh custom-dxvk-patched-mvk
```

The corrected custom variant disables Metal argument buffers by default. Set
`FREEDOM_MVK_USE_METAL_ARGUMENT_BUFFERS=1` only to reproduce the blue-only
failure.

The source repairs are preserved under `patches/crossover-dxvk/` and
`patches/crossover-moltenvk/`. Both production IOSurface runners now disable
Metal argument buffers as part of their pinned launch contract.
