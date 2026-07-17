# CrossOver MoltenVK Patches

These patches target the MoltenVK source bundled with CrossOver 26.2.0.

`moltenvk-freedom-geometry-mesh.patch` repairs the SPIRV-Cross
geometry-to-Metal-mesh path used by Freedom's DXVK shaders. It preserves
vertex-output slots, forwards mesh-stream arguments to helper functions, and
keeps argument-buffer parameters in the generated wrapper's required order.
The patch also raises the local macOS deployment target to 12.0 for the current
Xcode toolchain.

Apply and build from an extracted CrossOver MoltenVK source tree:

```bash
patch -p1 < /path/to/macos-game-patches/patches/crossover-moltenvk/moltenvk-freedom-geometry-mesh.patch
make macos
```

Freedom must currently launch with
`MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS=0`. With argument buffers enabled,
MoltenVK produced a blue-only eye texture: every red and green byte was zero.
Disabling them restores stable full-color local and submitted frames.

The deterministic A/B evidence is recorded in
`docs/probes/010-freedom-local-window-regression.md`.
