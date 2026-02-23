# External Projects Map

As of February 23, 2026.

This map tracks external projects that can accelerate our Apple-only VR goal
(Mac Apple Silicon host + AVP headset, hardware-accelerated 3D and HEVC).

## Highest Leverage

| Project | Why It Helps | Use Now | Owner/Link |
|---|---|---|---|
| ALVR upstream + visionOS client | Core protocol/client behavior, AVP-specific fixes, telemetry patterns | Mirror upstream fixes, avoid private-only divergence | <https://github.com/alvr-org/ALVR>, <https://github.com/alvr-org/alvr-visionos> |
| DXVK | D3D11->Vulkan translation behavior under Wine/CrossOver; directly affects SteamVR compositor path | Use for blocker diffs and extension behavior comparisons | <https://github.com/doitsujin/dxvk> |
| VKD3D-Proton | D3D12 title compatibility path and frame behavior under Wine stacks | Use for D3D12 title compatibility triage | <https://github.com/HansKristian-Work/vkd3d-proton> |
| Monado | OpenXR runtime architecture reference for long-term SteamVR independence | Reference design and instrumentation ideas | <https://monado.freedesktop.org/> |

## Medium Leverage

| Project | Why It Helps | Use Now | Owner/Link |
|---|---|---|---|
| OpenComposite | OpenVR->OpenXR translation for some titles; potential SteamVR bypass path | Run selective title experiments, not default path | <https://github.com/QuestCraftPlusPlus/OpenComposite> |
| WiVRn | Strong wireless VR streaming architecture/telemetry examples | Borrow quality gates and stream diagnostics patterns | <https://github.com/WiVRn/WiVRn> |

## Escalation Channels

| Channel | Use Condition | Output We Should Provide |
|---|---|---|
| CodeWeavers support | Reproducible CrossOver/Wine interop blocker persists after our ALVR-side fixes | Minimal repro executable + precise logs + run bundle |
| ALVR upstream issues/PRs | Fix is generic and likely useful outside our fork | Small patch, strict before/after evidence, no private assumptions |

## Decision Rules

- Do not start deep CrossOver patching until our short feasibility matrix still
  shows the same interop signatures after instrumentation hardening.
- Prefer upstream-compatible fixes in ALVR whenever they do not compromise our
  strict hardware-encode and source-quality gates.
- Keep a per-project action list in run notes: what was tested, what changed,
  and what remains blocked.

