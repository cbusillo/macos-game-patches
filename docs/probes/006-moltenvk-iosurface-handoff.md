# MoltenVK IOSurface Handoff

## Hypothesis

CrossOver 26.2's exact x86_64 MoltenVK runtime can create an
IOSurface-backed Vulkan image, export that IOSurface, and keep it alive while a
separate native arm64 process imports the surface as a Metal texture. A known
GPU-written pixel should survive that process and architecture boundary without
full-frame CPU readback.

This is a capability probe for GitHub issue #40. It does not prove that DXVK
currently creates exportable images or that D3DMetal exposes its private Metal
textures. It answers whether the public MoltenVK, Vulkan, IOSurface, and Metal
portion of a future DXVK handoff is viable on the installed runtime.

## Environment

- Discovery plan: GitHub issue #40, child of #36; production follow-up: #53.
- Producer architecture: x86_64 under Rosetta.
- Producer Vulkan implementation:
  `/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/libMoltenVK.dylib`.
- Consumer architecture: native arm64.
- Consumer API: public IOSurface lookup plus Metal texture import and blit.
- Target source format: `VK_FORMAT_B8G8R8A8_UNORM`.
- No CrossOver bottle, game, OpenVR runtime, ALVR session, or AVP is required.

## Procedure

1. Build one universal command-line probe from
   `tools/moltenvk_iosurface_probe.mm`.
2. Run its producer mode as x86_64.
3. Dynamically load the exact MoltenVK dylib from the CrossOver application.
4. Confirm `VK_EXT_metal_objects` support.
5. Create a Vulkan image with
   `VK_EXPORT_METAL_OBJECT_TYPE_METAL_IOSURFACE_BIT_EXT` declared in the image
   creation chain.
6. Clear the image to a known color on the Vulkan queue and wait for completion.
7. Export its `IOSurfaceRef`, retain it, obtain its IOSurface ID, and confirm a
   secure IOSurface Mach-port reference can also be created.
8. Execute the same universal binary in native arm64 consumer mode with the
   IOSurface ID while the producer keeps the image alive.
9. Look up the IOSurface, import it as a Metal texture, blit one pixel to a
   shared Metal buffer, and compare that pixel with the expected value.
10. Record extension support, image/export results, producer synchronization
    cost, consumer lookup/import/read cost, architectures, and pixel values.

## Expected Proof

The probe passes only when all of the following are true:

- the installed MoltenVK advertises `VK_EXT_metal_objects`;
- Vulkan image creation and memory binding succeed with IOSurface export intent;
- `vkExportMetalObjectsEXT` returns a non-null IOSurface;
- a separate arm64 process resolves the IOSurface ID;
- Metal creates a texture from the resolved surface;
- the consumer reads the expected GPU-written pixel; and
- no full-frame CPU copy occurs between producer and consumer.

The first probe may use `vkQueueWaitIdle` as an intentionally conservative
readiness signal. A pass establishes import viability, not the final streaming
synchronization contract.

## Failure Signatures

- Missing `VK_EXT_metal_objects`: the shipped MoltenVK cannot support this path.
- Image creation or export rejection: the image flags, format, or shipped
  MoltenVK implementation does not provide IOSurface backing as requested.
- Null IOSurface lookup in the consumer: the shipped MoltenVK surface is not
  globally registered or its lifetime was not preserved.
- Metal texture creation failure: the exported surface layout is not directly
  importable with the tested descriptor.
- Pixel mismatch after queue idle: the Vulkan-to-IOSurface-to-Metal memory view
  is not coherent under the tested contract.

## Do Not Repeat

- Do not route this probe through DXGI shared handles; both D3DMetal and the
  previously tested DXVK path already failed that contract.
- Do not launch SteamVR or `vrcompositor.exe`; its shared-resource startup
  failure is unrelated to this bounded capability test.
- Do not ask for headset interpretation. This probe has a deterministic pixel
  result and no visual calibration value.
- Do not treat a pass as proof that unmodified DXVK images are exportable. DXVK
  must still declare export intent when it creates the target Vulkan image.
- Do not treat a globally discoverable IOSurface ID as the final secure IPC
  design. A production bridge should pass the IOSurface through Mach or XPC IPC.

## Evidence Log

### 2026-07-11 Cross-Architecture Capability Pass

Run: Direct Vulkan producer using CrossOver 26.2's exact x86_64
`libMoltenVK.dylib`, followed by a separately executed native arm64 consumer.

Question: Can the installed MoltenVK create an exportable IOSurface whose
GPU-written contents survive a process and architecture boundary into Metal?

Mode / build: universal `tools/moltenvk_iosurface_probe.mm`; x86_64 producer
under Rosetta; native arm64 consumer; 64x64 `VK_FORMAT_B8G8R8A8_UNORM` image.

Expected proof: `VK_EXT_metal_objects` is advertised and enabled, IOSurface
export returns a live surface and ID, the arm64 consumer imports that ID as a
Metal texture, and a one-pixel Metal blit returns BGRA `0,0,255,255`.

Artifacts captured:

- `.code/probes/006-moltenvk-iosurface/run-20260711T133245Z.log`
- `.code/probes/006-moltenvk-iosurface/repeated-20260711T134546Z.log`

Verified:

- The exact CrossOver dylib reported Vulkan `1.2.290`, MoltenVK driver version
  `0.2.2018`, and `VK_EXT_metal_objects` support on `Apple M4 Max`.
- `vkExportMetalObjectsEXT` returned a non-null IOSurface, a nonzero IOSurface
  ID, and a valid Mach-port reference.
- The producer-side Metal control read and the separate native arm64 consumer
  both returned the expected BGRA pixel.
- Five repeated process-boundary runs passed without a pixel mismatch.
- No full-frame CPU transport occurred. The consumer used a one-pixel Metal
  blit only to verify content.

Inferred: MoltenVK's public export path is sufficient for a dedicated handoff
image when export intent is declared at image creation.

Verdict: `alive`. The core Vulkan-to-IOSurface-to-arm64-Metal mechanism is
physically proven on the installed runtime. Probe 007 tests the missing D3D11
and Wine/native boundaries.

## Next Action

Probe 007 validated the missing D3D11, Wine, synchronization, and native-arm64
boundaries. In #53, carry the proven mechanism into a dedicated three-slot
handoff pool: attach viewless copy-destination textures once before first use,
import each IOSurface once on the native side, publish only after queue-ordered
fences, and gate startup with a known-pixel handle-identity self-test. Keep
public `VK_EXT_metal_objects` support as an upstream contingency if the
CrossOver DXVK/Wine device path later exposes it safely.
