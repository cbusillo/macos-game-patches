# Fixed-Resolution Profile Qualification

## Goal

Determine whether Aircar and The Lab can render more clearly on Apple Vision
Pro with higher fixed per-eye source sizes while preserving every qualified
cadence, drop, pose, recovery, and cleanup contract.

Issue routing: #109 under compatibility tranche #59.

## Hypothesis

Both titles currently request `1152x1280` per eye and produce a `2880x1792`
target stereo surface. The owner reported smooth, usable output with softer or
lower apparent resolution in both titles. A profile-only increase to
`1280x1440` or `1440x1600` may improve source detail without changing the
runtime, game payload, encoder contract, or target surface.

The hypothesis survives only when a candidate passes the same strict gates as
the canonical profile. A clearer image does not justify lower cadence, frame or
pose loss, unstable recovery, or incomplete cleanup.

## Baseline

Aircar baseline evidence is
`aircar/real-native-encode-20260730T224010Z`: `5,400/5,400` submitted and
encoded frames, `89.979` FPS steady tail, zero producer/native drops, zero pose
generation gaps, and exact restoration.

The Lab baseline evidence includes unchanged sealed-profile passes
`the-lab/real-native-encode-20260718T030850Z` at `89.817` FPS and
`the-lab/real-native-encode-20260718T031058Z` at `90.006` FPS. Both submitted
and encoded `5,400/5,400` frames with visible-content validation, zero
producer/native drops, zero pose-generation gaps, and exact cleanup.

Run a fresh baseline beside the candidates whenever host load permits so the
comparison does not confuse resolution cost with unrelated contention.

## Candidate Matrix

| Title   | Profile   | Per-eye source | Stereo source | Target stereo |
| ------- | --------- | -------------: | ------------: | ------------: |
| Aircar  | canonical |    `1152x1280` |   `2304x1280` |   `2880x1792` |
| Aircar  | medium    |    `1280x1440` |   `2560x1440` |   `2880x1792` |
| Aircar  | high      |    `1440x1600` |   `2880x1600` |   `2880x1792` |
| The Lab | canonical |    `1152x1280` |   `2304x1280` |   `2880x1792` |
| The Lab | medium    |    `1280x1440` |   `2560x1440` |   `2880x1792` |
| The Lab | high      |    `1440x1600` |   `2880x1600` |   `2880x1792` |

The candidates remain below the existing `3240x1800` bounded stereo pool. They
change only `geometry.recommendedPerEye` and the matching
`ALVR_FAKE_RENDER_TARGET_WIDTH` and `ALVR_FAKE_RENDER_TARGET_HEIGHT` launch
environment values. The `2880x1792` target remains fixed to isolate source
detail from encoder and client-format changes.

## Automated Gates

For every candidate:

1. Validate canonical JSON and the profile schema.
2. Pass exact Steam payload and sealed-artifact preflight.
3. Pass a 300-frame smoke run.
4. Pass a 5,400-frame disconnected run with:
   - at least `89.5` FPS over the final 300 producer frames;
   - zero producer and native drops;
   - zero pose-generation gaps;
   - visible-content validation;
   - exact stock hash and owned-state restoration.
5. Record conversion average/max time, GPU conversion average/max time,
   encoded bytes/bitrate, maximum frame bytes, backpressure, pool use, host
   load, and cleanup status.

Do not treat a run under sustained unrelated host contention as a cadence
decision. Preserve it as functional or negative evidence and rerun during a
quiet window.

## Reproducible Commands

Use the final preserved-bundle dev11 artifact:

```bash
artifact=.code/runtime-aircar-preserved-dev11-a92da5d/\
mac-alvr-runtime-1.0.0-dev11-\
180f8dd0f73a1290505b89d0f9c27b4169e0c65e2804b39462d68d694b6b4e56

uv run python tools/runtime_profile.py check \
  runtime/experiments/aircar-1280x1440.json \
  runtime/experiments/aircar-1440x1600.json \
  runtime/experiments/the-lab-1280x1440.json \
  runtime/experiments/the-lab-1440x1600.json

uv run python tools/runtime_profile.py preflight \
  --profile runtime/experiments/aircar-1280x1440.json \
  --artifact "$artifact" --mode smoke
uv run python tools/runtime_profile.py probe \
  --profile runtime/experiments/aircar-1280x1440.json \
  --artifact "$artifact" --mode smoke
uv run python tools/runtime_profile.py probe \
  --profile runtime/experiments/aircar-1280x1440.json \
  --artifact "$artifact" --mode disconnected
```

Repeat the smoke and disconnected commands for the remaining candidate paths.
Run the canonical `aircar` and `the-lab` profiles in the same quiet window for
the fresh baseline.

## Expected Artifacts

Candidate probes write below:

```text
.code/probes/013-the-lab-profile-qualification/
  aircar-1280x1440/
  aircar-1440x1600/
  the-lab-1280x1440/
  the-lab-1440x1600/
```

Record each run directory, profile SHA-256, artifact seal, source and target
geometry, cadence, conversion, encode, drop, pose, host-load, and restoration
results in this document.

## Automated Results: July 31, 2026

All four experiment profiles pass canonical JSON/schema validation and exact
smoke/disconnected preflight against final dev11 seal
`180f8dd0f73a1290505b89d0f9c27b4169e0c65e2804b39462d68d694b6b4e56`.

Profile identities:

```text
aircar-1280x1440
008d781783054f0c2330a061e239897128dc19a75bb484682f4fd6944a03a827

aircar-1440x1600
394775216d09511fd1fce9e4cac3abacd4c96fd018d0b6e2af06645f4a525108

the-lab-1280x1440
ae0bb2eb455bb313f8bff5524d418de7433b5906dea84135e2bf1b38be677a61

the-lab-1440x1600
6561cfb7650b90bd05e8ae27b294c59bb326eb740910768b8ff8b93e779c3af8
```

### Aircar

Both candidate smoke gates passed. The first medium smoke was retained as a
strict negative because its short final window reached `90.576` FPS, above the
`90.5` upper bound; an immediate repeat passed at `89.918` FPS with the same
zero-drop and exact-cleanup result.

Fresh 5,400-frame disconnected comparison:

- Canonical `1152x1280`
  - Run: `aircar/real-native-encode-20260731T020250Z`
  - Tail: `90.060` FPS
  - Conversion average/max: `1669/12312 us`
  - Encoded bytes/max frame: `376706770/435603`
- Medium `1280x1440`
  - Run: `aircar-1280x1440/real-native-encode-20260731T015924Z`
  - Tail: `90.006` FPS
  - Conversion average/max: `1664/27589 us`
  - Encoded bytes/max frame: `376639546/495058`
- High `1440x1600`
  - Run: `aircar-1440x1600/real-native-encode-20260731T020109Z`
  - Tail: `90.033` FPS
  - Conversion average/max: `1838/15713 us`
  - Encoded bytes/max frame: `376700762/497953`

Every run submitted and encoded `5,400/5,400` frames, reported zero
producer/native drops, zero pose-generation gaps, visible content, and
`restore_status=0`. The fixed `2880x1792` target kept encoded volume and bitrate
effectively unchanged. The high candidate increased average conversion time by
about `10%` but retained full cadence margin and is the Aircar worn A/B
finalist.

### The Lab

The high smoke passed at `90.164` FPS. Canonical and medium smoke attempts were
functionally clean but cadence-invalid under heavy unrelated host load. Their
short windows were highly variable and are not used for candidate selection.

Fresh 5,400-frame disconnected loaded-host comparison:

- Canonical `1152x1280`
  - Run: `the-lab/real-native-encode-20260731T020906Z`
  - Tail: `85.722` FPS
  - Conversion average/max: `1368/10926 us`
  - Encoded bytes/max frame: `362739730/574686`
- Medium `1280x1440`
  - Run: `the-lab-1280x1440/real-native-encode-20260731T020708Z`
  - Tail: `86.919` FPS
  - Conversion average/max: `1334/11413 us`
  - Encoded bytes/max frame: `356771741/781880`
- High `1440x1600`
  - Run: `the-lab-1440x1600/real-native-encode-20260731T020510Z`
  - Tail: `85.772` FPS
  - Conversion average/max: `1302/10945 us`
  - Encoded bytes/max frame: `356749456/812523`

All three runs still submitted and encoded `5,400/5,400` frames with visible
content, zero producer/native drops, zero pose-generation gaps, and exact
restoration. The one-minute host load remained roughly `16.7` to `22.8` across
the matrix, and the canonical profile missed cadence in the same window.
Therefore these are useful relative and functional results, not a resolution
rejection. The medium candidate produced the best loaded-host tail, while the
high candidate matched the canonical tail.

A bounded unattended retry waited for three consecutive host-load samples at
or below `12`, then ran the matrix in canonical, medium, and high order:

- Canonical `1152x1280`
  - Run: `the-lab/real-native-encode-20260731T022454Z`
  - Start/end load: `11.81/23.37`
  - Tail: `89.360` FPS; cadence fail
  - Conversion average/max: `1392/12555 us`
- Medium `1280x1440`
  - Run: `the-lab-1280x1440/real-native-encode-20260731T022632Z`
  - Start/end load: `23.37/28.99`
  - Tail: `89.979` FPS; pass
  - Conversion average/max: `1392/15584 us`
- High `1440x1600`
  - Run: `the-lab-1440x1600/real-native-encode-20260731T022817Z`
  - Start/end load: `28.99/26.73`
  - Tail: `75.735` FPS; cadence fail
  - Conversion average/max: `1461/12908 us`

Each retry still submitted and encoded `5,400/5,400` frames with visible
content, zero producer/native drops, zero pose-generation gaps, and exact
restoration. The medium profile is the only The Lab candidate with a complete
strict pass and therefore advances to the worn A/B gate. The high profile does
not advance unless the medium profile's visual improvement proves inadequate
and a later isolated quiet-host rerun establishes full cadence.

## Automated Decision

- Aircar advances `1440x1600` as its worn A/B finalist.
- The Lab advances `1280x1440` as its worn A/B finalist.
- Canonical production profiles remain unchanged until the owner completes the
  visual comparison.
- Adaptive resolution, foveation, target-surface changes, and encoder redesign
  remain separate post-v1 work.

## Cleanup

Each probe invokes the existing exact cleanup path. After any interrupted run,
execute:

```bash
uv run python tools/vr_stack_cleanup.py
uv run python tools/runtime_profile.py preflight \
  --profile runtime/experiments/aircar-1280x1440.json \
  --artifact "$artifact" --mode smoke
```

The second preflight must find the stock payload, no staged runtime files or
logs, and no owned process, service, lock, or shared-memory state.

## Human Gate

Advance at most one zero-drop candidate per title to a short worn A/B check.
The owner compares clarity, smoothness, motion response, and latency against the
canonical profile. Keep `1152x1280` when the automated cost is material or the
visual improvement is not meaningful.

Adaptive resolution, foveation, target-surface changes, or encoder redesign are
outside this experiment and remain post-v1 work.
