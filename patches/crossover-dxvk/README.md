# CrossOver DXVK Patches

These patches target the DXVK 1.10.3 source bundled with CrossOver 26.2.0.

`dxvk-1.10.3-freedom-macos.patch` contains two required repairs:

- Adapt upstream DXVK commit `d93568f1` so geometry-shader position inputs and
  outputs compile through MoltenVK's Metal mesh-shader path.
- Treat `VK_SUBOPTIMAL_KHR` from `vkQueuePresentKHR` as usable, matching the
  existing acquire behavior. Freedom renders at 3200x1800 inside a 3840x2160
  macOS layer; rebuilding the identical swapchain on every suboptimal result
  caused visible flashing.

Apply and build from an extracted CrossOver DXVK source tree:

```bash
patch -p1 < /path/to/macos-game-patches/patches/crossover-dxvk/dxvk-1.10.3-freedom-macos.patch
meson setup /path/to/dxvk-build --cross-file build-win64.txt
ninja -C /path/to/dxvk-build src/d3d11/d3d11.dll src/dxgi/dxgi.dll
```

The local-window validation command is documented in
`docs/probes/010-freedom-local-window-regression.md`.
