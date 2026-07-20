# Runtime Client State And Recovery

## Objective

Extend the qualified host producer lifecycle from `waiting` into exact
`connected`, `streaming`, and bounded `recovering` states without weakening the
schema-v4 service and producer ownership boundary.

The control plane must consume current bridge-owned telemetry rather than
inferring state from console or bridge log strings. Client absence remains a
healthy `waiting` state. Client disconnect is recoverable for one bounded
window. Host service or producer identity loss remains a failure.

## Baseline

- macos-game-patches source: `236e6120ca3824c7caf7c0d0427f09d786a9bcb9`
- ALVR host source: `38b7697215efb956823e5ab70a1af2a13c83a8e1`
- qualified sealed artifact:
  `3141421da6ebf9bf710ffb1c6e8b3197e695c12d4ea0a6972b2878154a6075fb`
- owning issue: #60

## Contract

### Bridge telemetry

Append a telemetry block to shared-memory protocol version 7 while preserving
every existing ABI offset and the 4 KiB-rounded frame payload offset.

The bridge publishes, under one sequence counter:

- runtime generation nonce;
- exact bridge PID;
- bridge session ID;
- current client state: waiting, connected, or streaming;
- negotiated stream contract validity;
- monotonic stream epoch;
- successful transported-frame count;
- monotonic connect and disconnect event counts;
- a sticky stream-contract failure count.

The bridge heartbeat remains independent. The supervisor measures heartbeat
and transported-frame progress against its own monotonic clock instead of
trusting wall-clock timestamps.

Before launchd starts the bridge, the supervisor reads the manifest-declared
retained ALVR session state through a no-follow descriptor, filters out every
untrusted client, resets copied connection state to disconnected, and writes a
private generation-local `session.json`. Startup fails if no retained trusted
client exists; the source session remains user-owned and unchanged.

Every fresh bridge start zeroes the protocol header, publishes a new session
ID, binds the active runtime generation and bridge PID, and restores mode
`0600` even when the shared-memory file already exists.

### Runtime state

Schema version 5 retains the exact schema-v4 profile and producer records and
adds one required `client` field:

- `null` while the producer is launching, starting, or quiesced;
- an exact telemetry record while the producer is ready.

The client record contains status, telemetry version, runtime generation,
bridge PID, bridge session ID, stream epoch, stream-contract validity, and
transported-frame, connect-event, disconnect-event, and contract-failure
counts.

Top-level live transitions are:

```text
idle -> waiting -> connected -> streaming
                       ^             |
                       |             v
                       +---- recovering
```

- `waiting`: bridge and producer are exact; no ALVR client is connected.
- `connected`: the authenticated ALVR handshake and negotiated stream contract
  are current, but transported frames are not currently advancing.
- `streaming`: the current stream epoch is connected and transported frames
  advance inside the activity window.
- `recovering`: a previously connected client disconnected; the same bridge
  generation has one fixed monotonic recovery window.
- recovery expiry returns to `waiting`; it does not fail the host runtime.
- bridge heartbeat must advance within five monotonic seconds;
- transported-frame inactivity returns `streaming` to `connected` after two
  monotonic seconds;
- recovery expires after 30 monotonic seconds.
- bridge telemetry loss, generation/PID/session replacement, epoch regression,
  invalid stream-contract state, service loss, or producer loss fails closed.
- event counters preserve connect, transport, disconnect, and contract-failure
  evidence even when all events occur between supervisor samples.

Stop authority remains service and producer identity. Client telemetry never
authorizes a signal or cleanup mutation, and late telemetry is ignored once
cooperative stop begins.

## Implementation Sequence

1. Extend the ALVR shared-memory Rust mirror and macos-game-patches C header.
2. Publish client state and transported-frame progress from `AlvrVideoSink`.
3. Allow the native source to start with ALVR enabled while no client is
   present.
4. Add the fail-closed Python telemetry reader and monotonic state monitor.
5. Publish and validate schema-v5 control state while retaining schema-v2/3/4
   cleanup compatibility.
6. Add focused ALVR, ABI, runtime start, runtime control, and lifecycle tests.
7. Build twice, seal once, install transactionally, and repeat the physical
   connection/recovery/cleanup gate.

## Reproducible Validation

ALVR worktree:

```sh
cargo fmt --package alvr_macos_bridge -- --check
cargo test -p alvr_macos_bridge
cargo build -p alvr_macos_bridge --release
```

macos-game-patches:

```sh
python3 tools/runtime_profile_test.py
python3 tools/runtime_control_test.py
python3 tools/runtime_start_test.py
python3 tools/runtime_install_test.py
python3 tools/runtime_cli.py --help
python3 tools/build_runtime_artifact.py check
```

Physical gate:

1. Install the exact sealed candidate from a stock Freedom baseline.
2. Start with the Vision Pro client absent and require synchronized `waiting`.
3. Launch the trusted client and require `connected`, then `streaming` with a
   nonzero stream epoch and advancing transported-frame count.
4. Relaunch the client and require `recovering -> connected -> streaming` with
   a strictly newer stream epoch.
5. Leave the client absent beyond the recovery deadline and require
   `recovering -> waiting` while the exact host producer remains live.
6. Stop from every client state and prove no late event republishes live state.
7. Install and uninstall from a live stream and restore every stock/absent
   precondition with no process, service, state, generation directory,
   quarantine, undo entry, or transaction failure.

## Expected Evidence

- ALVR and macos-game-patches source commits;
- two matching unsealed build seals and one Developer ID sealed artifact;
- schema-v5 state snapshots for waiting, connected, streaming, recovering, and
  recovery expiry;
- bridge telemetry snapshots showing exact generation, PID, session ID, stream
  epoch, and transported-frame progress;
- fixture and physical transition timelines;
- exact install/uninstall transactions and final stock-baseline audit.

## Cleanup

- Cooperative `stop` must quiesce both producer groups before booting out the
  exact launchd service.
- Remove the generation directory, control socket, plist, lock, and state only
  after exact ownership and content revalidation.
- Uninstall restores all profile-owned files and removes the shared-memory
  mapping only after the bridge and producer are absent.
- Preserve diagnostic state on identity or telemetry ambiguity.

## Known Failure Signatures

- `client.telemetry_missing`: no exact version-7 mapping after bridge startup.
- `client.trust_missing`: retained ALVR state has no trusted client to seed.
- `client.trust_invalid`: retained ALVR state is unsafe, malformed, or outside
  the admitted roots.
- `client.telemetry_stale`: bridge heartbeat stopped advancing while the exact
  service remained live.
- `client.telemetry_mismatch`: generation, bridge PID, or bridge session changed.
- `client.epoch_regressed`: a client event reported an older stream epoch.
- `client.contract_failed`: ALVR negotiated a stream outside the sealed runtime
  contract.
- `state.invalid_schema`: schema-v5 client state does not match producer and
  top-level lifecycle state.
