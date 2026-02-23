# Live Validation Evidence

## 2026-02-23 Revalidation Run Set

### Baseline hygiene + null smoke

Commands:

```bash
python3 tools/vr_stack_cleanup.py
python3 tools/steamvr_smoke.py --mode null
```

Run:

- `temp/vr_runs/20260223-231235-steamvr-smoke`

Smoke summary:

- `vrcompositor.txt: Startup Complete`
- `vrserver.txt: Loaded server driver`
- `vrserver.txt: Using existing HMD`
- postflight cleanup reported `remaining=0`

### Native HEVC hardware gate

Command:

```bash
python3 tools/hevc_gate.py
```

Run:

- `temp/vr_runs/20260223-231427-h0-hevc-videotoolbox`

Result:

- `pass=true` (`logs/h0-summary.json`)

### Strict non-direct production confirmation

Command:

```bash
python3 tools/live_avp_nondirect_prod.py --confirm-twice --capture-seconds 60
```

Confirmed runs:

- `temp/vr_runs/20260223-231520-live-avp-checkpoint` (`pass=true`)
- `temp/vr_runs/20260223-231843-live-avp-checkpoint` (`pass=true`)

Key outcomes (both runs):

- `client_ready=true`, `client_decode_success=true`,
  `client_video_presenting=true`
- `host_new_frame_ready_seen=true`, `host_copy_to_staging_seen=true`
- `client_synthetic_fallback_used=false`, `host_idle_fallback_used=false`
- `source_static_suspected=false`, `source_known_synthetic_pattern=false`
- second run surfaced a startup-latency advisory only:
  `client_ui_block_summary=streaming_started delayed 21.5s after`
  `app_initialized; check AVP popup/frontmost state`

### One-shot release gate artifact

Command:

```bash
python3 tools/live_avp_release_gate.py --capture-seconds 45 --steamvr-tool steamvr_overlay_viewer
```

Result:

- `RELEASE_GATE_PASS=true`
- artifact JSON:
  - `temp/pipeline_reports/20260223-232852-release-gate.json`
- artifact markdown:
  - `temp/pipeline_reports/20260223-232852-release-gate.md`

Gate-confirming run pairs in artifact:

- `temp/vr_runs/20260223-232238-live-avp-checkpoint` (`pass=true`)
- `temp/vr_runs/20260223-232545-live-avp-checkpoint` (`pass=true`)

### Direct-mode matrix refresh

Command:

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
python3 tools/live_avp_directmode_matrix.py \
  --graphics-backends dxvk d3dmetal \
  --capture-seconds 60 \
  --steamvr-tool steamvr_overlay_viewer
```

Report:

- `temp/vr_runs/20260223-234145-directmode-matrix/report.json`

Result summary:

- DXVK path is explicitly blocked by contract and reports missing Win32
  external-memory Vulkan support (`VK_KHR_external_memory_win32`,
  `VK_KHR_win32_keyed_mutex`).
- D3DMetal direct-mode run (`temp/vr_runs/20260223-234146-live-avp-checkpoint`)
  still fails strict gates with `host_direct_mode_recovery_used`.
- Blocker class remains unchanged: non-direct remains production path while
  direct-mode requires restored real compositor submissions without recovery
  source fallback.

### Current interpretation (2026-02-23)

- strict non-direct production path remains stable under repeated gates
- SteamVR Win32 external-memory extension absence remains visible in outcomes
  (`steamvr_external_memory_extensions_missing=true`) but does not block
  current non-direct production acceptance criteria

## 2026-02-18 Run Set

### Run: `temp/vr_runs/20260218-032035-live-avp-checkpoint` (pass)

- Goal: verify decode + present with strict client gates before disabling
  host idle fallback.
- Outcome: `pass=true`.
- Key outcome fields:
  - `client_decoder_config_seen=true`
  - `client_decode_success=true`
  - `client_video_presenting=true`
  - `client_synthetic_fallback_enabled=false`
  - `client_synthetic_fallback_used=false`
- Key probe lines:
  - `PROBE streaming_started`
  - `PROBE decoder_config codec=1`
  - `PROBE decode_success codec=1`
  - `PROBE video_presenting`

### Run: `temp/vr_runs/20260218-033304-live-avp-checkpoint` (expected fail)

- Goal: disable host idle fallback and fail when source is static.
- Outcome: `pass=false`.
- Gate failures:
  - `source_motion_missing`
  - `source_static_suspected`
- Key outcome fields:
  - `host_idle_fallback_enabled=false`
  - `host_idle_fallback_used=false`
  - `source_unique_fresh_sample_crcs=["c71c0011"]`
  - `source_unique_fresh_encoded_sizes=[2427]`
- Key probe lines:
  - `PROBE host_idle_fallback_enabled=0 env_disable=1`
  - `CreateSwapTextureSet ... CreateSharedHandle failed ... hr=0x80070057`

### Run: `temp/vr_runs/20260218-034208-live-avp-checkpoint` (expected fail)

- Goal: require host frame submission telemetry (`new_frame_ready`,
  `copy_to_staging`) in addition to strict decode gates.
- Outcome: `pass=false`.
- Gate failures:
  - `host_new_frame_ready_missing`
  - `host_copy_to_staging_missing`
  - `source_motion_missing`
  - `source_static_suspected`
- Key outcome fields:
  - `host_new_frame_ready_seen=false`
  - `host_copy_to_staging_seen=false`
  - `client_decoder_config_seen=true`
  - `client_decode_success=true`
  - `client_video_presenting=true`

## Current Interpretation

- Client decode/present path is healthy with synthetic client fallback disabled.
- Host idle fallback can now be forced off and detected by the harness.
- With host idle fallback disabled, stream remains static and host frame
  submission probes are absent.
- The immediate blocker is upstream of encode/decode, in host frame production/submission.

### Run: `temp/vr_runs/20260218-152004-live-avp-checkpoint` (direct-mode gate fail)

- Goal: classify direct-mode health with explicit gates.
- Outcome: `pass=false`.
- Gate failures:
  - `host_direct_mode_swap_failed`
  - `steamvr_external_memory_extensions_missing`
- Key outcome fields:
  - `host_direct_mode_swap_failed=true`
  - `steamvr_external_memory_extensions_missing=true`
  - `host_new_frame_ready_seen=false`
  - `host_copy_to_staging_seen=false`
  - `source_static_suspected=true`

Key diagnostics in logs:

- `CreateSwapTextureSet failed ... Last HRESULT ... Invalid parameter`
- SteamVR client asserts missing Vulkan interop extensions:
  - `VK_KHR_external_memory_win32`
  - `VK_KHR_win32_keyed_mutex`

### Run: `temp/vr_runs/20260218-160900-live-avp-checkpoint`

Pass: non-direct host source.

- Goal: validate a minimal direct-mode-off host frame source with both
  client synthetic fallback and host idle fallback disabled.
- Outcome: `pass=true` with strict real-decode + host frame-signal gates.
- Key outcome fields:
  - `client_synthetic_fallback_enabled=false`
  - `client_synthetic_fallback_used=false`
  - `host_idle_fallback_enabled=false`
  - `host_idle_fallback_used=false`
  - `client_decoder_config_seen=true`
  - `client_decode_success=true`
  - `client_video_presenting=true`
  - `host_non_direct_source_enabled=true`
  - `host_non_direct_frame_produced_seen=true`
  - `host_non_direct_frame_submitted_seen=true`
  - `source_fresh_encode_count=5`
  - `source_unique_fresh_sample_crcs=["07aa4390", "3d20cd83", "e187baba"]`
  - `source_static_suspected=false`

Key probe lines:

- `ALVR MGP direct-mode guard: 2026-02-17b disabled=1`
- `PROBE host_idle_fallback_enabled=0 env_disable=1`
- `PROBE host_non_direct_source_enabled=1 direct_mode_disabled=1 env_disable=<unset>`
- `PROBE host_non_direct_frame_produced count=1 wake=1`
- `PROBE host_non_direct_frame_submitted count=1 wake=1`
- `CEncoder: new_frame_ready calls=1 source=non_direct`
- `CEncoder: copy_to_staging calls=1 ... source=non_direct`
- `PROBE synthetic_fallback_enabled=0`
- `PROBE decoder_config codec=1`
- `PROBE decode_success codec=1`
- `PROBE video_presenting`

### Run: `temp/vr_runs/20260218-164708-live-avp-checkpoint`

Strict direct-mode fail.

- Goal: prove strict `direct-mode on` path with direct-mode-health gate.
- Outcome: `pass=false`.
- Gate failures:
  - `host_direct_mode_swap_failed`
  - `steamvr_external_memory_extensions_missing`
  - `host_new_frame_ready_missing`
  - `host_copy_to_staging_missing`
  - `source_motion_missing`
  - `source_static_suspected`
- Key diagnostics:
  - `CreateSwapTextureSet failed ... Invalid parameter`
  - `Required vulkan device extension is unavailable: VK_KHR_external_memory_win32`
  - `Required vulkan device extension is unavailable: VK_KHR_win32_keyed_mutex`
  - source CRC remains constant (`c71c0011`) across fresh encodes.

### Run: `temp/vr_runs/20260218-164234-live-avp-checkpoint` (strict non-direct pass)

- Goal: hardened production fallback under strict gates (`direct-mode off`).
- Outcome: `pass=true`.
- Key outcome fields:
  - `client_synthetic_fallback_enabled=false`
  - `client_synthetic_fallback_used=false`
  - `host_idle_fallback_enabled=false`
  - `host_idle_fallback_used=false`
  - `client_decoder_config_seen=true`
  - `client_decode_success=true`
  - `client_video_presenting=true`
  - `host_non_direct_source_enabled=true`
  - `host_non_direct_frame_produced_seen=true`
  - `host_non_direct_frame_submitted_seen=true`
  - `source_static_suspected=false`

## 2026-02-18 Direct-Mode Recovery Sprint

Two direct-mode interop patch hypotheses were tested with strict sterile runs
on both DXVK and D3DMetal after each patch.

### Hypothesis 1 Runs

- DXVK: `temp/vr_runs/20260218-195303-live-avp-checkpoint`
  - Outcome: `pass=false`
  - Gate failures:
    - `host_new_frame_ready_missing`
    - `host_copy_to_staging_missing`
    - `host_direct_mode_swap_failed`
    - `steamvr_external_memory_extensions_missing`
    - `source_motion_missing`
    - `source_static_suspected`
  - Key lines:
    - `CreateSwapTextureSet attempt CreateSharedHandle failed ... hr=0x80070057`
    - `CreateSwapTextureSet failed for texture 0 ... Invalid parameter`
- D3DMetal: `temp/vr_runs/20260218-195734-live-avp-checkpoint`
  - Outcome: `pass=false`
  - Key lines:
    - `CreateSwapTextureSet attempt GetSharedHandle failed ... hr=0x80004001`
    - `Exception c0000005`

### Hypothesis 2 Runs

- DXVK: `temp/vr_runs/20260218-201027-live-avp-checkpoint`
  - Outcome: `pass=false`
  - Gate failures:
    - `host_new_frame_ready_missing`
    - `host_copy_to_staging_missing`
    - `host_direct_mode_swap_failed`
    - `steamvr_external_memory_extensions_missing`
    - `source_motion_missing`
    - `source_static_suspected`
  - Key lines:
    - `CreateSwapTextureSet attempt CreateSharedHandle failed ... hr=0x80070057`
    - `CreateSharedHandle matrix attempts still fail (access/security/name variants)`
    - `CreateSharedHandle fallback attempts still fail (legacy + named handles)`
    - `Required vulkan device extension is unavailable: VK_KHR_external_memory_win32`
    - `Required vulkan device extension is unavailable: VK_KHR_win32_keyed_mutex`
- D3DMetal: `temp/vr_runs/20260218-201454-live-avp-checkpoint`
  - Outcome: `pass=false`
  - Key lines:
    - `CreateSwapTextureSet attempt GetSharedHandle failed ... hr=0x80004001`
    - `Startup Complete`
    - `Failed Watchdog timeout ... Aborting`
    - `Exception c0000005`

## 2026-02-18 Operations Split (Ship vs R&D)

### Ship Path: strict non-direct with two-run confirmation

Command:

```bash
python3 tools/live_avp_nondirect_prod.py --confirm-twice --capture-seconds 60
```

Confirmed runs:

- `temp/vr_runs/20260218-230728-live-avp-checkpoint` (`pass=true`)
- `temp/vr_runs/20260218-230926-live-avp-checkpoint` (`pass=true`)

Notes:

- both runs report client decode + video presenting and no fallback usage.
- second run demonstrated delta-log probe loss tolerance via
  `host_idle_fallback_enabled_inferred=true` while still passing strict gates.
- both runs surfaced `client_ui_block_summary` delay warnings, indicating AVP
  UI/frontmost friction can still add startup latency even when pass criteria
  are met.

### R&D Path: strict direct-mode matrix

Command:

```bash
python3 tools/live_avp_directmode_matrix.py --capture-seconds 60
```

Matrix report:

- `temp/vr_runs/20260218-231126-directmode-matrix/report.json`

Result summary:

- DXVK run `temp/vr_runs/20260218-231126-live-avp-checkpoint` failed strict
  gates with `steamvr_external_memory_extensions_missing` plus missing host
  frame signals and static source.
- D3DMetal run `temp/vr_runs/20260218-231322-live-avp-checkpoint` failed strict
  gates with missing host frame signals and static source.
- ranked next patches in report prioritize backend capability validation
  (Win32 external-memory Vulkan support) and a hardened ALVR shared-resource
  fallback path when shared-handle open is unavailable.

### One-Shot Release Gate Artifact

Command:

```bash
python3 tools/live_avp_release_gate.py --capture-seconds 60
```

Result:

- `RELEASE_GATE_PASS=true`
- artifact JSON:
  - `temp/pipeline_reports/20260218-233422-release-gate.json`
- artifact markdown:
  - `temp/pipeline_reports/20260218-233422-release-gate.md`

Gate-confirming run pairs in artifact:

- `temp/vr_runs/20260218-233028-live-avp-checkpoint` (`pass=true`)
- `temp/vr_runs/20260218-233225-live-avp-checkpoint` (`pass=true`)

### Burn-In: 3 Consecutive Gate Cycles

Command pattern per cycle:

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
python3 tools/live_avp_release_gate.py --capture-seconds 45
```

Summary artifact:

- `temp/pipeline_reports/20260218-235522-burnin-summary.jsonl`

Aggregates:

- cycle pass rate: `3/3` (`100%`)
- strict run pass rate: `6/6` (`100%`)
- average client stream-start delay: `23.9s`
- max client stream-start delay: `51.6s`
- idle-fallback-disabled inferred from launch contract
  (delta probe missing): `3/6` runs

### New R&D Matrix Cycle

Command:

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
python3 tools/live_avp_directmode_matrix.py --capture-seconds 60
```

Report:

- `temp/vr_runs/20260219-000613-directmode-matrix/report.json`

Findings:

- DXVK remains blocked on identical strict gate failures, including
  `steamvr_external_memory_extensions_missing` and static-source signatures.
- D3DMetal remains blocked on missing host frame signals plus static source.
- Compared with prior matrix baseline (`20260218-231126`), blocker class is
  unchanged; no new pass signal was observed.
