# DXVK D3D11 IOSurface Interop

## Hypothesis

A synthetic D3D11 texture created by CrossOver's bundled DXVK can expose its
backing `VkImage` through DXVK's Vulkan interop interfaces. If the Wine Vulkan
boundary also exposes MoltenVK's IOSurface functions, the image can be converted
to IOSurface backing before D3D11 writes it. If those host-specific functions
are absent, the same interop handles define the smallest required Wine/native
bridge.

This probe follows the direct MoltenVK capability proof in probe 006. It tests
the missing D3D11-to-Vulkan boundary without involving a game, OpenVR, ALVR, or
the AVP.

## Environment

- Discovery plan: GitHub issue #40, child of #36; production follow-up: #53.
- CrossOver: 26.2, build `26.2.0.39821`.
- CrossOver source archive:
  `https://media.codeweavers.com/pub/crossover/source/crossover-sources-26.2.0.tar.gz`,
  SHA-256 `3846ae094dd49c073467bb2b5e6e17d5bacaebcfab4b6dd2af3f132c64cad6cf`.
- Bundled DXVK: `cxaddon-1.10.3-1-25-g737aacd`.
- Bottle: `Steam`, overridden only for the probe process with
  `CX_GRAPHICS_BACKEND=dxvk`.
- Producer: `tools/d3d11_dxvk_iosurface_probe.cpp` built as a static x86_64
  Windows executable.
- Initial gate: 64x64 D3D11 texture.
- Real source contract: 3240x1800 `DXGI_FORMAT_B8G8R8A8_TYPELESS`, one mip, one
  layer, one sample, with render-target and shader-resource bindings, matching
  the submitted Freedom texture recorded by probe 005.
- Dedicated handoff contract: 3240x1800 `DXGI_FORMAT_B8G8R8A8_UNORM`, one mip,
  one layer, one sample, and no D3D bind flags or views. DXVK reduces this to
  transfer-only Vulkan usage.
- No persistent bottle graphics-setting change is permitted.

## Procedure

1. Build the Windows probe with MinGW.
2. Build the matched PE/Mach-O unixlib pair against the Wine 11.0 source in the
   exact CrossOver archive.
3. Copy the probe executable and PE bridge DLL into `C:\alvr-probes`; expose
   the paired unixlib through a temporary `WINEDLLPATH`.
4. Launch through `cxstart --env` with both `CX_GRAPHICS_BACKEND=dxvk` and the
   temporary `WINEDLLPATH`. Do not rely on inherited values because `cxstart`
   rebuilds the Wine environment.
5. Record the loaded `d3d11.dll`, `dxgi.dll`, and `vulkan-1.dll` paths and the
   DXGI adapter name.
6. Create a D3D11 device, a Freedom-compatible typeless source texture, and a
   dedicated UNORM handoff texture with no bind flags.
7. Query the texture for `IDXGIVkInteropSurface` and its owner for
   `IDXGIVkInteropDevice` and `IDXGIVkInteropDevice1`.
8. Record the `VkInstance`, `VkPhysicalDevice`, `VkDevice`, `VkImage`, current
   layout, image creation metadata, and submission queue.
9. Confirm Wine's Vulkan thunk does not expose the Apple-specific MoltenVK
   functions, then load the matched `alvr_iosurface_bridge` PE/Mach-O unixlib
   pair built against CrossOver 26.2's Wine 11.0 source.
10. Pass the raw handoff `VkImage` through the same-process unixlib, attach an
    IOSurface before any D3D view or copy uses it, and return its IOSurface ID
    plus a retained Mach-port reference.
11. Fill the typeless source with known-red data and `CopyResource` it into the
    IOSurface-backed handoff texture.
12. Flush DXVK, lock its submission queue, enqueue an empty Vulkan submission
    with a fence after the copy, release the queue, and wait for that fence.
13. Publish the IOSurface ID only after the fence signals. Import it in the
    native arm64 Metal consumer and require BGRA `0,0,255,255`.
14. Release the Mach-port reference and remove all staged bottle artifacts.

## Build Commands

Build both probes with strict warnings:

```bash
xcrun clang++ -std=c++20 -fobjc-arc \
  -Wall -Wextra -Wpedantic -Werror \
  -arch x86_64 -arch arm64 -I/opt/homebrew/include \
  tools/moltenvk_iosurface_probe.mm \
  -framework Foundation -framework CoreFoundation \
  -framework IOSurface -framework Metal \
  -o .code/probes/006-moltenvk-iosurface/moltenvk_iosurface_probe

x86_64-w64-mingw32-g++ -O2 -g -std=c++20 \
  -static -static-libgcc -static-libstdc++ \
  -Wall -Wextra -Wpedantic -Werror -I/opt/homebrew/include \
  tools/d3d11_dxvk_iosurface_probe.cpp \
  -ld3d11 -ldxgi -lole32 \
  -o .code/probes/007-dxvk-d3d11-iosurface/d3d11_dxvk_iosurface_probe.exe
```

Prepare the exact Wine source and register the unixlib once:

```bash
vendor="$PWD/.code/vendor/crossover-26.2.0"
archive="$vendor/crossover-sources-26.2.0.tar.gz"
source_url=https://media.codeweavers.com/pub/crossover/source/\
crossover-sources-26.2.0.tar.gz
wine_source="$vendor/source/sources/wine"
wine_build="$vendor/build"

mkdir -p "$vendor/source"
curl -L "$source_url" -o "$archive"
printf '%s  %s\n' \
  3846ae094dd49c073467bb2b5e6e17d5bacaebcfab4b6dd2af3f132c64cad6cf \
  "$archive" | shasum -a 256 -c -
tar -xzf "$archive" -C "$vendor/source"
rsync -a --delete tools/alvr_iosurface_bridge/ \
  "$wine_source/dlls/alvr_iosurface_bridge/"

registration='WINE_CONFIG_MAKEFILE(dlls/alvr_iosurface_bridge)'
anchor='WINE_CONFIG_MAKEFILE(dlls/adsldpc)'
if ! rg -Fq "$registration" "$wine_source/configure.ac"; then
  perl -0pi -e \
    "s@(\Q$anchor\E\n)@\$1$registration\n@" \
    "$wine_source/configure.ac"
fi
(cd "$wine_source" && autoreconf -f)
```

Configure and build the matched PE/Mach-O pair:

```bash
mkdir -p "$wine_build"
build_path=/opt/homebrew/opt/bison/bin:/usr/local/bin:/opt/homebrew/bin
(cd "$wine_build" && arch -x86_64 env \
  PATH="$build_path:/usr/bin:/bin:/usr/sbin:/sbin" \
  "$wine_source/configure" \
  --enable-win64 --disable-tests --without-freetype \
  'CC=/usr/bin/clang -arch x86_64' \
  'CXX=/usr/bin/clang++ -arch x86_64')
(cd "$wine_build" && arch -x86_64 env \
  PATH="$build_path:/usr/bin:/bin:/usr/sbin:/sbin" \
  make -j8 \
  dlls/alvr_iosurface_bridge/x86_64-windows/alvr_iosurface_bridge.dll \
  dlls/alvr_iosurface_bridge/alvr_iosurface_bridge.so)
```

Stage the matched bridge pair under one `WINEDLLPATH` root, copy the probe and
PE DLL to `C:\alvr-probes`, and launch with an explicit process-only backend:

```bash
bridge_root="$PWD/.code/probes/007-dxvk-d3d11-iosurface/bridge"
bottle_probe="$HOME/Library/Application Support/CrossOver/Bottles/Steam/drive_c/alvr-probes"
bridge_build="$wine_build/dlls/alvr_iosurface_bridge"
mkdir -p \
  "$bridge_root/x86_64-windows" \
  "$bridge_root/x86_64-unix" \
  "$bottle_probe"
cp "$bridge_build/x86_64-windows/alvr_iosurface_bridge.dll" \
  "$bridge_root/x86_64-windows/"
cp "$bridge_build/alvr_iosurface_bridge.so" \
  "$bridge_root/x86_64-unix/"
cp "$bridge_root/x86_64-windows/alvr_iosurface_bridge.dll" \
  "$bottle_probe/"
cp .code/probes/007-dxvk-d3d11-iosurface/d3d11_dxvk_iosurface_probe.exe \
  "$bottle_probe/"
cx_env="CX_GRAPHICS_BACKEND=dxvk WINEDLLPATH=$bridge_root"
/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart \
  --bottle Steam --no-update --no-gui --wait \
  --env "$cx_env DXVK_LOG_LEVEL=info" \
  'C:\alvr-probes\d3d11_dxvk_iosurface_probe.exe' \
  --wait-consumer --width 3240 --height 1800
```

Run the native arm64 consumer while the producer waits on its ready file, then
write `consumer_status=0` to the done file. Remove the probe executable, bridge
DLL, ready/done files, and generated DXVK log from `C:\alvr-probes` after the
run. Preserve only ignored evidence logs under `.code/probes`.

## Expected Proof

The D3D11/Vulkan boundary is alive when:

- the loaded D3D11 implementation is CrossOver's DXVK;
- the texture exposes `IDXGIVkInteropSurface`;
- its device exposes at least `IDXGIVkInteropDevice`;
- valid Vulkan handles and image metadata are returned; and
- the dedicated handoff texture is viewless and transfer-only; and
- DXVK rendering can be explicitly flushed before direct queue use.

The no-DXVK-patch compatibility path is alive when the matched unixlib can call
the host MoltenVK function in the same Rosetta process, return a live IOSurface
transport handle, and the arm64 consumer reads the D3D11-written pixel after the
queue-ordered fence signals.

## Failure Signatures

- Adapter remains `AMD Compatibility Mode`: D3DMetal was selected instead of
  DXVK, so the run is invalid.
- `IDXGIVkInteropSurface` returns `E_NOINTERFACE`: the bundled DXVK does not
  expose the required image interface.
- `IDXGIVkInteropDevice1` is absent but the base interface is present: wrapping
  a separately created exportable Vulkan image may require a DXVK update or
  patch, but an existing-image native bridge remains testable.
- Apple-specific Vulkan function pointers are null: Wine's thunk does not expose
  them; use a same-process unixlib bridge rather than retrying DXGI handles.
- D3D copy, marker submit, or fence wait fails: do not publish or consume the
  surface; fix the queue-ordering contract first.

## Do Not Repeat

- Do not test `IDXGIResource::GetSharedHandle` or
  `IDXGIResource1::CreateSharedHandle`; those paths already failed under both
  D3DMetal and verified DXVK.
- Do not modify the bottle's persistent graphics backend for this probe.
- Do not launch SteamVR or the game.
- Do not treat a raw host IOSurface pointer as cross-process transport. The
  native side must convert it to an IOSurface ID, Mach right, or XPC object.
- Do not use a render-target clear as the write proof after retrofitting an
  existing DXVK image. That run produced a valid but zero-filled IOSurface,
  consistent with a stale cached render view. `CopyResource` is the proven
  handoff operation.
- Do not retrofit the game's submitted texture. Create a dedicated handoff
  texture before any view or copy uses it, then copy the game texture into it.
- Do not give the handoff texture render-target or shader-resource bind flags.
  It is a copy destination only; the game's submitted texture remains the
  untouched source.
- Do not assume the PE-side non-dispatchable `VkImage` token will always equal
  the host MoltenVK handle. Current Wine/CrossOver passes it through unchanged,
  and the pixel proof validates that invariant for this build only.

## Proven Handle And Sync Contract

```text
Freedom-compatible typeless D3D11 texture
    -> ID3D11DeviceContext::CopyResource
dedicated UNORM DXVK handoff texture, BindFlags=0, no views
    -> IDXGIVkInteropSurface::GetVulkanImageInfo
VkImage in the Wine/DXVK process
    -> matched Wine unixlib -> vkUseIOSurfaceMVK
IOSurface ID + retained Mach-port reference
    -> DXVK FlushRenderingCommands
    -> queue-ordered empty vkQueueSubmit + VkFence
    -> publish only after vkWaitForFences succeeds
native arm64 IOSurfaceLookup -> Metal texture
```

The marker submission is ordered after DXVK's flushed copy on the same Vulkan
queue. Waiting on its fence proves the copy completed without requiring
`vkQueueWaitIdle`. The current probe waits on the producer thread; production
must move that wait and publication to a worker so the OpenVR submit thread can
return after the copy, flush, and marker enqueue.

The current global IOSurface ID is acceptable only for the bounded unsandboxed
probe. Production must transfer the retained Mach right or an XPC IOSurface
object and must not expose globally guessable frame IDs.

## Evidence Log

### 2026-07-11 DXVK Interop And Wine Boundary

Verified:

- CrossOver selected bundled DXVK `cxaddon-1.10.3-1-25-g737aacd`; the adapter
  was `Apple M4 Max`, not D3DMetal's `AMD Compatibility Mode`.
- `IDXGIVkInteropSurface`, `IDXGIVkInteropDevice`, and
  `IDXGIVkInteropDevice1` were all available.
- DXVK returned valid instance, device, queue, image, layout, format, extent,
  and usage metadata.
- Wine exposed standard queue and fence functions but did not expose
  `vkUseIOSurfaceMVK`, `vkGetIOSurfaceMVK`, or `vkExportMetalObjectsEXT`.
- A matched PE/Mach-O unixlib built against CrossOver 26.2's Wine 11.0 source
  passed a fixed-width scalar ABI smoke test and attached the IOSurface.

Artifacts:

- `.code/probes/007-dxvk-d3d11-iosurface/console-20260711T141723Z.log`
- `.code/probes/007-dxvk-d3d11-iosurface/console-bridge-20260711T143130Z.log`

Verdict: DXVK image access is `alive`; the Apple-specific host call is a narrow
same-process Wine unixlib boundary, not a missing DXGI shared handle.

### 2026-07-11 Full-Size Cross-Process Pixel Pass

Run: Five repeated 3240x1800 producer/consumer runs, a contract-faithful
typeless confirmation, a post-review rebuild, and five strengthened copy-only
handoff runs.

Expected proof: a D3D11 `CopyResource` writes a known pixel into the dedicated
handoff texture; the queue-ordered fence signals; a separate native arm64 Metal
process imports the IOSurface and reads the same pixel.

Artifacts:

- `.code/probes/007-dxvk-d3d11-iosurface/fullsize-repeated-20260711T144051Z.log`
- `.code/probes/007-dxvk-d3d11-iosurface/typeless-fullsize-producer-20260711T144315Z.log`
- `.code/probes/007-dxvk-d3d11-iosurface/typeless-fullsize-consumer-20260711T144315Z.log`
- `.code/probes/007-dxvk-d3d11-iosurface/review-rerun-dxvk-producer-20260711T145457Z.log`
- `.code/probes/007-dxvk-d3d11-iosurface/review-rerun-dxvk-consumer-20260711T145457Z.log`
- `.code/probes/007-dxvk-d3d11-iosurface/copy-only-producer-20260711T150618Z.log`
- `.code/probes/007-dxvk-d3d11-iosurface/copy-only-consumer-20260711T150618Z.log`
- `.code/probes/007-dxvk-d3d11-iosurface/copy-only-repeated-20260711T150653Z.log`

Verified:

- All original and strengthened repeated full-size runs returned the expected
  BGRA `0,0,255,255` in the native arm64 consumer.
- The real-compatible source remained `DXGI_FORMAT_B8G8R8A8_TYPELESS` with
  render-target and shader-resource binds.
- The strengthened handoff texture used `DXGI_FORMAT_B8G8R8A8_UNORM`, no bind
  flags, no views, Vulkan layout `GENERAL`, and transfer-only usage `0x3`.
- The post-review rebuild passed fixed-width bridge ABI checks, strict warning
  builds, and queue-fence synchronization.
- No full-frame CPU readback or CPU frame transport occurred. The synthetic
  source uses one diagnostic CPU upload; transport into the handoff surface is
  a D3D11 GPU copy, and the consumer reads one pixel only for verification.

Measured costs across the five strengthened copy-only runs:

| Stage                    |  Minimum |      p50 |  Maximum |
| ------------------------ | -------: | -------: | -------: |
| D3D copy plus DXVK flush | 1.612 ms | 1.766 ms | 1.870 ms |
| Queue marker submission  | 0.041 ms | 0.045 ms | 0.068 ms |
| Fence completion wait    | 1.877 ms | 2.319 ms | 3.229 ms |
| IOSurface lookup         | 4.685 ms | 4.945 ms | 5.279 ms |
| Metal texture import     | 0.578 ms | 0.621 ms | 0.639 ms |

The IOSurface lookup and Metal texture import are one-time pool setup costs in a
production ring, not per-frame transport costs. Cold Metal device setup and the
one-pixel verification blit are intentionally excluded from the handoff cost.

Inferred: a three-slot dedicated texture pool can amortize import and lookup,
while a producer worker waits on per-slot fence markers and publishes completed
frames without blocking the game submit thread.

Verdict: `alive`, with maintenance constraints. This is the first physically
proven CrossOver D3D11-to-native-arm64 GPU surface handoff. The current
production candidate is the dedicated, viewless, copy-only texture attached
once before first use. It keeps the deprecated MoltenVK call isolated, but still
requires a startup handle-identity self-test and a vendor-update gate.

## Next Action

In #53, build a three-slot pool of dedicated copy-only handoff textures. Attach
each slot once before first use, import each retained IOSurface once on the
native side, move fence waits and publication to a worker, and transfer Mach
rights through local IPC instead of publishing IOSurface IDs. Add a throwaway
startup pixel self-test that disables the path if Wine no longer preserves the
host `VkImage` token. Then integrate the real Freedom `Submit` texture as the
copy source. Treat a standard `VK_EXT_metal_objects` DXVK/Wine path as an
upstream contingency, not an assumption for the first production
implementation.
