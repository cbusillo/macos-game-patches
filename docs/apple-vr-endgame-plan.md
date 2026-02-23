# Apple-Only VR Gaming System: End-to-End Plan

As of February 23, 2026.

## Executive Summary

This plan provides a pragmatic path to a working Mac (Apple Silicon) + AVP VR gaming system with hardware-accelerated rendering and encoding. Based on current evidence, **hacking CrossOver/Wine is NOT required**. The production path uses non-direct mode with DXGI desktop duplication, which already passes strict validation gates. Engineering focus should be on stabilizing source capture and bridging to native macOS VideoToolbox encoding.

---

## Current State Assessment

### What Works Today (Production Path)

- **Compositor**: `vrcompositor.exe` starts successfully under CrossOver/DXVK
- **ALVR Driver**: Loads reliably and advertises HMD to SteamVR
- **Client Decode**: AVP client successfully decodes HEVC and presents video
- **Hardware Encoding**: VideoToolbox HEVC hardware encoding verified via `hevc_gate.py`
- **Non-Direct Mode**: Passes strict validation with real decode, no synthetic fallback
  - Evidence: `temp/vr_runs/20260218-164234-live-avp-checkpoint` (pass=true)
  - Gates passed: real decode, video presenting, host frame signals, source motion

### Critical Blockers

#### 1. Direct-Mode Blocker (Not Production-Critical)

**Status**: Blocked by missing shared-handle interop in CrossOver graphics stack

**Evidence**:
- DXVK: Missing Vulkan extensions `VK_KHR_external_memory_win32`, `VK_KHR_win32_keyed_mutex`
- D3DMetal: `GetSharedHandle` fails with `0x80004001`, compositor crashes post-startup
- Location: `docs/direct-mode-blocker-dossier.md`

**Decision**: Direct-mode is R&D-only. Not required for production system.

#### 2. Non-Direct Source Capture Instability (Production-Critical)

**Status**: Intermittent desktop capture failures cause black frames or synthetic fallback

**Symptoms**:
- Flips between working capture and `host_non_direct_desktop_capture_failed`
- When capture fails, fallback to synthetic patterns or static CRC samples
- `virtual_display_component` seen but `virtual_display_present` not called consistently
- Display-redirect mode shows virtual display registered but present path inactive

**Location**: `/Users/cbusillo/Developer/ALVR/alvr/server_openvr/cpp/platform/win32/FrameRender.cpp:1212-1610`

**Root Cause Hypothesis**:
- DXGI Desktop Duplication (`IDXGIOutputDuplication`) fails intermittently under Wine/CrossOver
- Window capture fallback (GDI `BitBlt`) also unreliable for compositor window
- Virtual display redirect path registers but present callbacks not consistently invoked

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Mac Apple Silicon Host                                      │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  CrossOver/Wine (x86_64 Windows compatibility)      │   │
│  │                                                       │   │
│  │  ┌──────────────────────────────────────────────┐  │   │
│  │  │  SteamVR + VR Game                            │  │   │
│  │  │  └─> vrcompositor.exe (DXVK/D3DMetal)        │  │   │
│  │  │       └─> Renders to textures                 │  │   │
│  │  └──────────────────────────────────────────────┘  │   │
│  │           │                                          │   │
│  │           ▼                                          │   │
│  │  ┌──────────────────────────────────────────────┐  │   │
│  │  │  ALVR OpenVR Driver (driver_alvr_server.dll) │  │   │
│  │  │  - Direct mode: BLOCKED (interop failure)    │  │   │
│  │  │  - Non-direct: Desktop duplication (WORKING) │  │   │
│  │  └──────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
│           │                                                  │
│           ▼                                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Native macOS Daemon (vtbridge)                      │   │
│  │  - Receives raw frames via IPC/shared memory         │   │
│  │  - VideoToolbox HEVC hardware encode                 │   │
│  │  - Returns NALs to ALVR                              │   │
│  └─────────────────────────────────────────────────────┘   │
│           │                                                  │
│           ▼                                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  ALVR Server Network Transport                       │   │
│  │  - UDP/QUIC streaming protocol                       │   │
│  │  - Video: HEVC NALs                                  │   │
│  │  - Audio: PCM (Opus TODO)                            │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                       │
                       ▼  WiFi/Network
┌─────────────────────────────────────────────────────────────┐
│  Apple Vision Pro                                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  ALVR visionOS Client                                │   │
│  │  - HEVC decode (hardware)                            │   │
│  │  - Video present to compositor                       │   │
│  │  - Head tracking + controller input                  │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Two-Track Plan

### Track 1: Near-Term Production Path (2 Weeks)

**Goal**: Stable working system with minimal risk, accepting current architecture limitations.

**Approach**: Non-direct mode + desktop duplication, focus on capture reliability.

**Timeline**: 2 weeks to first playable session

### Track 2: Long-Term Ideal Path (6-12 Months)

**Goal**: Optimal performance via direct-mode or better capture primitives.

**Approach**: Upstream contributions to CrossOver/DXVK/SteamVR for proper interop.

**Timeline**: Research and upstream coordination

---

## Track 1: Near-Term Production Path (Detailed)

### Phase 1: Stabilize Non-Direct Capture (Week 1, Days 1-4)

**Objective**: Eliminate intermittent desktop capture failures.

#### Gate Criteria
- [ ] 10 consecutive runs with `pass=true` on strict gates
- [ ] No `host_non_direct_desktop_capture_failed` in any run
- [ ] Source motion detected in 100% of runs (no static CRC)
- [ ] Stream startup latency < 30s (p95)

#### Tasks

**Task 1.1**: Investigate DXGI Desktop Duplication failure modes (Day 1)
- Location: `FrameRender.cpp:1212` (`DuplicateOutput` call)
- Add detailed error logging for `DuplicateOutput` HRESULT codes
- Log adapter/output enumeration sequence
- Test hypothesis: CrossOver DXGI stub may not handle multi-adapter correctly
- **Decision Point**: If DXGI duplication is fundamentally broken in Wine, pivot to window capture

**Task 1.2**: Harden window capture fallback (Day 2)
- Location: `FrameRender.cpp:1400-1430` (GDI `BitBlt` path)
- Current issue: Window capture succeeds but frames rejected as "flat"
- Add CRC histogram logging to understand "flat frame" detection false positives
- Test hypothesis: Compositor window may render black borders triggering flat detection
- Consider adaptive flat detection threshold based on window aspect ratio
- **Go/No-Go**: If window capture cannot reliably distinguish black borders from actual black frames, must use alternate approach

**Task 1.3**: Investigate virtual display redirect path (Day 3)
- Location: `VirtualDisplayRedirect.cpp:94` (`Present` callback)
- Current issue: Component registers but `virtual_display_present` not called
- Add instrumentation to understand compositor routing decision
- Check if `Prop_IsOnDesktop_Bool=false` causes compositor to skip present path
- Test hypothesis: Compositor may require both virtual display AND direct-mode components
- **Decision Point**: If virtual display path requires direct-mode interop, this is blocked

**Task 1.4**: Implement capture retry/recovery logic (Day 4)
- Add exponential backoff for `DuplicateOutput` init failures
- Add automatic reinit on `DXGI_ERROR_ACCESS_LOST`
- Add synthetic frame guard with hard timeout (fail stream rather than inject fallback)
- Update harness to detect and fail on capture init/recovery thrashing

**Success Criteria**:
- At least ONE reliable capture path (DXGI duplication OR window capture OR virtual display)
- Passing strict gates 10/10 runs
- If all paths remain unreliable, ESCALATE to Phase 1B

#### Phase 1B: Alternative Capture Strategy (Contingency)

**Trigger**: If Task 1.1-1.4 cannot achieve 80% success rate

**Option A**: Compositor render target interception
- Intercept `ID3D11DeviceContext::ResolveSubresource` or `Present` at D3D11 layer
- Requires patching CrossOver or hooking D3D11 calls from ALVR driver
- **Risk**: High complexity, may trigger anti-cheat in future

**Option B**: SteamVR mirror texture capture
- Use `IVRCompositor::GetMirrorTextureD3D11` if available under Wine
- Fallback: Capture from SteamVR's own mirror window
- **Risk**: May not be exposed properly in CrossOver environment

**Option C**: Native macOS compositor integration
- Route SteamVR output through macOS display pipeline
- Capture via native macOS screen recording APIs (CGDisplayStream)
- **Risk**: Requires significant architecture changes, latency concerns

**Go/No-Go Decision Point**:
- If Option A/B/C cannot be prototyped and validated within 3 days, ESCALATE to Track 2 (long-term path) or consider GPU passthrough VM approach

---

### Phase 2: Bridge and Hardware Encoding Integration (Week 1, Days 5-7)

**Objective**: Ensure captured frames flow through vtbridge to VideoToolbox encoding.

#### Gate Criteria
- [ ] Hardware HEVC encoding confirmed (no software fallback)
- [ ] End-to-end latency < 50ms (capture → encode → network send)
- [ ] Encoder handles 90 FPS sustained load without frame drops

#### Tasks

**Task 2.1**: Validate vtbridge under load (Day 5)
- Run `vtbridge_hw_stream_gate.py` with 90 FPS synthetic frames
- Measure ring slot turnover and memory pressure
- Confirm VideoToolbox session never falls back to software encoder
- **Gate**: `hevc_gate.py` must pass after 5 minutes of streaming

**Task 2.2**: Integrate encoder with non-direct capture path (Day 6)
- Location: `FrameRender.cpp` + `CEncoder.cpp`
- Wire non-direct captured texture to vtbridge ring writer
- Add frame timing instrumentation (capture timestamp → encode complete)
- **Decision Point**: If bridging adds >20ms latency, investigate zero-copy alternatives

**Task 2.3**: Optimize encoding latency (Day 7)
- Tune VideoToolbox parameters (realtime mode, max frame delay)
- Test CBR vs VBR rate control for latency
- Measure bitrate vs quality tradeoff for 90Hz target
- **Success Criteria**: 95th percentile encode time < 11ms (for 90 FPS budget)

---

### Phase 3: End-to-End Validation and Hardening (Week 2, Days 1-3)

**Objective**: First playable VR session with acceptable quality and stability.

#### Gate Criteria
- [ ] 30-minute soak test with no fatal errors
- [ ] Head tracking latency < 20ms (motion-to-photon estimate)
- [ ] Subjective image quality acceptable for seated VR experiences
- [ ] Audio working (PCM path acceptable for initial milestone)

#### Tasks

**Task 3.1**: End-to-end SteamVR app validation (Day 1)
- Test with `steamvr_room_setup` (reference app)
- Measure motion-to-photon latency with head sweep test
- Validate controller input responsiveness
- Document any visual artifacts or stuttering

**Task 3.2**: Failure mode hardening (Day 2)
- Test network disconnection recovery
- Test compositor crash recovery (does ALVR restart cleanly?)
- Test AVP sleep/wake behavior
- Add automatic retry logic for transient failures

**Task 3.3**: Performance optimization (Day 3)
- Profile CPU/GPU usage on Mac host
- Identify bottlenecks in capture/encode/send path
- Optimize frame pacing to minimize judder
- Tune network buffering for latency vs packet loss

**Success Criteria**:
- Complete 30-minute session in SteamVR Home without crash
- Subjective comfort rating 6/10 or higher
- No forced synthetic fallback activation

---

### Phase 4: Opus Audio and Input Polish (Week 2, Days 4-7)

**Objective**: Complete feature parity for production use.

#### Gate Criteria
- [ ] Opus audio working end-to-end (48kHz stereo, <40ms latency)
- [ ] Controller input working for at least 2 test titles
- [ ] Documented launch procedure (one-command start)

#### Tasks

**Task 4.1**: Opus encoder integration (Day 4)
- Add Opus codec negotiation to ALVR protocol
- Implement Opus encode on host for game audio capture
- Wire CrossOver audio output to macOS capture bridge
- **Decision Point**: If CrossOver audio routing is unreliable, may need PipeWire/PulseAudio bridge

**Task 4.2**: Opus decoder on AVP client (Day 5)
- Implement Opus decode in visionOS client
- Add jitter buffer for audio/video sync
- Measure audio latency and drift over 10 minutes
- **Gate**: Audio/video sync within 50ms over 30-minute session

**Task 4.3**: Controller and hand tracking (Day 6)
- Validate controller input for test title (e.g., Beat Saber, Superhot VR)
- Test hand tracking if supported by AVP client
- Document any mapping issues or dead zones

**Task 4.4**: Launch automation and runbook (Day 7)
- Create single-command startup script (e.g., `start_vr_session.py`)
- Document troubleshooting steps for common issues
- Create shutdown/cleanup procedure
- Write user-facing quick-start guide

**Success Criteria**:
- Play a seated VR game (e.g., Moss, I Expect You To Die) for 30 minutes with audio
- Controller input responsive enough for casual gameplay
- New user can follow runbook to start session in < 5 minutes

---

### Phase 5: Production Hardening and Issue Matrix (Week 3+)

**Objective**: Stabilize for daily use and document known limitations.

#### Tasks

**Task 5.1**: Soak testing and telemetry
- Run 10x 60-minute sessions with instrumentation
- Collect metrics: frame drops, network retries, encode stalls, capture failures
- Build reliability dashboard from run artifacts

**Task 5.2**: Multi-game compatibility matrix
- Test 5-10 popular VR titles
- Document per-game quirks, performance settings, compatibility issues
- Identify any game-specific patches needed

**Task 5.3**: Performance tuning
- Optimize for battery life on AVP
- Reduce Mac host CPU usage (target <30% on M4 Max)
- Test lower bitrates for improved battery/thermals

**Task 5.4**: Failure documentation
- Document all known issues with workarounds
- Create issue tracker with severity classification
- Define red lines (e.g., "will not work with anti-cheat games")

---

## Track 2: Long-Term Ideal Path (6-12 Months)

**Goal**: Achieve production-quality direct-mode support or equivalent performance primitive.

### Option A: Upstream CrossOver/DXVK Contributions

**Target**: Add `VK_KHR_external_memory_win32` emulation layer in DXVK/MoltenVK

**Approach**:
1. Engage with CrossOver/CodeWeavers team on shared texture interop priorities
2. Prototype `VK_KHR_external_memory_win32` → Metal shared texture bridge
3. Contribute patches upstream to DXVK and MoltenVK projects
4. Work with SteamVR team to validate shared texture workflow on macOS

**Timeline**: 6-12 months (dependent on upstream review cycles)

**Risk**: Low chance of acceptance if macOS VR is not strategic priority for Wine/DXVK

### Option B: GPU Passthrough VM

**Target**: Use UTM/QEMU with GPU passthrough to run Windows natively

**Approach**:
1. Research Apple Silicon VM GPU passthrough status (currently limited)
2. Test performance of SteamVR under x86_64 Windows VM with GPU virtualization
3. If viable, route ALVR driver through VM with native DirectX support

**Timeline**: 3-6 months (blocked on Apple's VM graphics API maturity)

**Risk**: May not achieve acceptable performance due to virtualization overhead

### Option C: Native macOS SteamVR Client

**Target**: Port SteamVR runtime to native macOS (similar to SteamVR Linux)

**Approach**:
1. Reverse-engineer SteamVR compositor protocol
2. Build native macOS OpenVR runtime that translates to Metal
3. Coordinate with Valve on official macOS support

**Timeline**: 12+ months (very high effort)

**Risk**: Valve has shown no interest in macOS VR support; likely to remain unsupported

---

## Go/No-Go Decision Gates

### Gate 1: End of Phase 1 (Day 4)

**Question**: Can we achieve reliable non-direct capture?

**Success Criteria**: 8/10 runs pass strict gates with source motion detected

**If NO**:
- Escalate to Phase 1B (alternative capture)
- If Phase 1B also fails, PIVOT to Track 2 or consider project infeasible with current stack

**If YES**: Proceed to Phase 2

### Gate 2: End of Phase 2 (Day 7)

**Question**: Is hardware encoding latency acceptable for 90 FPS VR?

**Success Criteria**: 95th percentile encode latency < 15ms

**If NO**:
- Investigate encoder tuning or lower frame rate target (72 FPS)
- Consider if image quality/latency tradeoff is acceptable

**If YES**: Proceed to Phase 3

### Gate 3: End of Phase 3 (Week 2, Day 3)

**Question**: Is the subjective VR experience acceptable for production?

**Success Criteria**:
- 30-minute session completes without crash
- Tester comfort rating ≥ 6/10
- No major visual artifacts or judder

**If NO**:
- Identify specific comfort/quality issues
- Determine if issues are fixable in Phase 4-5 or architectural

**If YES**: Proceed to Phase 4 (production polish)

### Gate 4: End of Phase 4 (Week 2, Day 7)

**Question**: Is the system ready for daily use by target user?

**Success Criteria**:
- Audio working
- Controller input functional for 2+ test games
- Launch procedure documented and repeatable

**If NO**: Extend Phase 4 or descope audio/input for initial release

**If YES**: Declare MVP complete, proceed to Phase 5 (hardening)

---

## Next 2 Weeks: Prioritized Task List

### Week 1

**Priority 1 (Must-Have)**:
1. **Day 1**: Debug DXGI Desktop Duplication failure modes (`FrameRender.cpp:1212`)
2. **Day 2**: Harden window capture fallback (flat frame detection tuning)
3. **Day 3**: Investigate virtual display redirect present path
4. **Day 4**: Implement capture retry/recovery logic
5. **Day 4 EOD**: **GATE 1 DECISION** - Proceed or pivot to Phase 1B

**Priority 2 (Should-Have)**:
6. **Day 5**: Validate vtbridge under 90 FPS load
7. **Day 6**: Integrate encoder with non-direct capture path
8. **Day 7**: Optimize encoding latency for 90 FPS target
9. **Day 7 EOD**: **GATE 2 DECISION** - Latency acceptable?

### Week 2

**Priority 1 (Must-Have)**:
10. **Day 1**: End-to-end validation with `steamvr_room_setup`
11. **Day 2**: Failure mode hardening (network, compositor crash recovery)
12. **Day 3**: Performance optimization and 30-min soak test
13. **Day 3 EOD**: **GATE 3 DECISION** - Subjective quality acceptable?

**Priority 2 (Should-Have)**:
14. **Day 4**: Opus encoder integration (host-side)
15. **Day 5**: Opus decoder integration (AVP client-side)
16. **Day 6**: Controller input validation
17. **Day 7**: Launch automation and runbook documentation
18. **Day 7 EOD**: **GATE 4 DECISION** - MVP complete?

---

## Engineering Focus Areas

### Critical Path (Block Everything If These Fail)

1. **Non-direct capture reliability** (FrameRender.cpp)
   - DXGI Desktop Duplication OR window capture OR virtual display
   - Must achieve 80%+ success rate before proceeding

2. **Hardware encoding latency** (vtbridge + VideoToolbox)
   - Must stay under 15ms p95 for acceptable VR experience

3. **AVP client decode/present** (already working, maintain)
   - Guard against regressions in ALVR client updates

### High-Value Optimizations (After Critical Path)

1. **Capture stability improvements**
   - Retry logic, error recovery, adaptive fallback

2. **Encoding tuning**
   - Bitrate, keyframe interval, rate control mode

3. **Network optimization**
   - Packet loss handling, jitter buffer tuning

### Nice-to-Have (Post-MVP)

1. **Direct-mode support** (Track 2, long-term)
2. **Multi-game compatibility** (Phase 5)
3. **Performance dashboard** (Phase 5)

---

## Known Failure Modes and Mitigations

### Failure Mode 1: Desktop Capture Hangs/Fails

**Symptoms**: `host_non_direct_desktop_capture_failed`, static CRC frames

**Current Hypothesis**: DXGI Desktop Duplication not fully implemented in Wine/CrossOver

**Mitigation Strategy**:
- Primary: Fix DXGI duplication reliability (Phase 1, Task 1.1)
- Fallback: Window capture with improved flat detection (Phase 1, Task 1.2)
- Last Resort: Virtual display redirect or compositor interception (Phase 1B)

### Failure Mode 2: Intermittent Black Frames

**Symptoms**: Video present works but displays black/wireframe/colored squares

**Current Hypothesis**: Race condition between capture and compositor render

**Mitigation Strategy**:
- Add frame synchronization barriers in capture path
- Increase staging texture pool size to avoid read-while-write
- Log compositor frame timing to detect timing mismatches

### Failure Mode 3: Direct-Mode Required by Game

**Symptoms**: Specific game requires direct-mode and refuses to render in non-direct

**Current Status**: Not yet observed, but possible

**Mitigation Strategy**:
- Document as known limitation
- If critical game, escalate to Track 2 (upstream contributions)
- Alternative: Run game in non-VR mode with VorpX-style injection (out of scope)

### Failure Mode 4: Audio Desync

**Symptoms**: Audio drifts from video over time

**Mitigation Strategy**:
- Implement timestamp-based A/V sync (Phase 4, Task 4.2)
- Add jitter buffer with adaptive sizing
- Monitor drift and resync if exceeds 100ms threshold

---

## Success Metrics

### MVP Success (End of Week 2)

- [ ] 30-minute VR session completes without fatal error
- [ ] Video quality subjectively acceptable (6/10 rating)
- [ ] Head tracking latency < 20ms
- [ ] Hardware HEVC encoding confirmed (no software fallback)
- [ ] Audio working (PCM or Opus)
- [ ] Controller input functional

### Production Success (End of Week 4)

- [ ] 10 consecutive 60-minute sessions without crash
- [ ] 5+ games tested and documented
- [ ] Launch procedure documented and validated by second user
- [ ] Known issues documented with workarounds
- [ ] Telemetry dashboard for reliability monitoring

### Long-Term Success (6-12 Months)

- [ ] Direct-mode support or equivalent performance
- [ ] Upstream contributions accepted (if applicable)
- [ ] Multi-game compatibility matrix published
- [ ] Community runbook and support available

---

## Immediate Next Actions (Monday Morning)

1. **Priority 1**: Run 5 consecutive non-direct strict validation runs to establish baseline reliability
   ```bash
   python3 tools/vr_stack_cleanup.py --sterile-native-steam
   python3 tools/live_avp_nondirect_prod.py --confirm-twice --capture-seconds 60
   ```

2. **Priority 2**: Instrument `FrameRender.cpp:1212` with detailed DXGI duplication logging
   - Add HRESULT code logging
   - Add adapter/output enumeration logging
   - Rebuild and test

3. **Priority 3**: Review `host_non_direct_desktop_capture_failed` logs from recent runs
   - Identify error patterns
   - Correlate with compositor state (DXVK vs D3DMetal, SteamVR version)

4. **Priority 4**: Document current vtbridge status
   - Verify `vtbridge_hw_stream_gate.py` passes
   - Measure baseline encode latency with synthetic frames

5. **Priority 5**: Schedule Gate 1 decision checkpoint (end of Day 4)
   - Criteria: 8/10 runs passing strict gates
   - Prepare Phase 1B options if needed

---

## Appendix: Recurring Symptom Patterns

### Pattern A: Connecting → Wireframe/Colored Squares

**Interpretation**: Client connected, video pipeline initialized, but receiving invalid/corrupt frames

**Root Cause**: Likely capture failure → synthetic fallback or uninitialized texture data

**Fix Target**: Phase 1 (stabilize capture)

### Pattern B: Connecting → Black Screen

**Interpretation**: Video present working but source is black (static CRC)

**Root Cause**: Compositor rendering black OR capture timing issue OR idle fallback active

**Fix Target**: Phase 1 (capture timing) or Phase 3 (compositor state)

### Pattern C: Works Then Becomes Unstable

**Interpretation**: Initial frames good, then degrades to black/wireframe

**Root Cause**: Resource exhaustion (staging texture pool), duplication lost, or encoder stall

**Fix Target**: Phase 2 (encoder recovery) and Phase 3 (failure hardening)

---

## Conclusion

**Do you need to hack CrossOver?** → **NO** (for near-term production path)

The production path uses non-direct mode with desktop duplication, which is already functional and passes strict validation gates. Engineering effort should focus on:

1. **Week 1**: Stabilizing non-direct capture reliability (Phase 1-2)
2. **Week 2**: End-to-end validation and production polish (Phase 3-4)

Direct-mode support is a long-term optimization (Track 2) but not required for a working system. The critical path is capture reliability and encoding latency, both of which are solvable within the current architecture.

**Fastest route to working system**: Execute Track 1, Phases 1-4 over 2 weeks, with strict go/no-go gates at Days 4, 7, and Week 2 Day 3.
