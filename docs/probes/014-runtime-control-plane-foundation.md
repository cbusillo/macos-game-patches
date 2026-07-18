# Runtime Control Plane Foundation

## Question

Can the sealed runtime contract support truthful `doctor`, `status`, and `stop`
commands without changing the proven launchd, Mach service, frame handoff,
encoder, or transport data plane?

## Boundary

- First slice: artifact-aware `doctor`, live-identity `status`, idempotent
  `stop`, human output, JSON output, and hardware-free fixtures.
- Follow-up slice: the long-running `start` supervisor, bounded startup,
  Vision Pro waiting/connection transitions, recovery, and game-process
  ownership.
- Reused contracts: `runtime/manifest.json`, the sealed artifact verifier,
  manifest bindings, the per-user launchd service, `native-probe.lock`, and the
  installed bridge bundle ownership marker.
- Hard cuts: no data-plane rewrite, broad PID killing, cached-PID trust, silent
  self-healing from `doctor`, GUI, installer, updater, or telemetry service.

## State Contract

The control plane reports exactly one of these states:

- `stopped`: no owned launchd job, runtime lock, or synchronized state exists.
- `installed`: a sealed artifact verifies, but one or more readiness checks do
  not pass.
- `ready`: the artifact and every required host prerequisite pass while the
  runtime is stopped.
- `waiting`: a future live supervisor owns the service and is waiting for the
  Vision Pro client.
- `connected`: the authenticated client is connected but no active stream is
  being delivered.
- `streaming`: the authenticated client and producer are actively streaming.
- `recovering`: the live supervisor is inside a bounded host/client recovery
  transition.
- `failed`: ownership, live identity, synchronized state, artifact, or
  prerequisite evidence is stale, foreign, contradictory, or unreadable.

The first slice derives `stopped`, `installed`, `ready`, and `failed` directly.
It may report the four live states only when a schema-versioned state record
matches the current owner PID and process start time, launchd PID, service
label, verified artifact path and seal, installed ownership marker, bridge
signature, and generation. It must not infer a healthy live state from old
logs, PID liveness alone, or cached identities.

## Command Contract

### `doctor`

- Requires a sealed artifact path.
- Verifies the artifact seal and exact checked-in manifest/lock identity.
- Resolves plan-phase bindings without mutation.
- Evaluates every manifest prerequisite as `pass`, `fail`, or `unknown` with a
  stable code and actionable remediation.
- Checks the control-plane tools and runtime-state path without creating them.
- Exits successfully only when every required check passes.

### `status`

- Reads launchd by `gui/<uid>/com.alvr.macos-bridge.iosurface`.
- Verifies the registered plist, program path, live executable, and code-sign
  identity before reporting an owned service.
- Requires every mutable path to remain inside the manifest's allowed roots
  without symlink components.
- Reads `native-probe.lock` only as evidence; a live PID is not trusted as a
  stop target.
- Treats stale locks, foreign jobs, and service/state disagreement as `failed`.
- Reports `ready` only when an artifact is supplied and the same doctor checks
  pass; otherwise a valid artifact is `installed` and no artifact is `stopped`.

### `stop`

- Is successful when the runtime is already stopped.
- Refuses to mutate a foreign or identity-mismatched launchd job.
- Boots out an owned job through its live registered launchd path rather than
  signaling a cached PID.
- Removes only a validated owned plist, stale dead-owner lock, and synchronized
  runtime state after the service is absent.
- Refuses to remove a lock whose owner PID is still alive until the future
  supervisor supplies a stronger owner identity contract.

## Fixture Matrix

- passing, mismatched, unavailable, and indeterminate command prerequisites;
- passing, missing, mismatched, and invalid plist prerequisites;
- stopped runtime and repeated idempotent stop;
- stale dead-owner lock cleanup;
- live-owner lock refusal;
- owner-PID reuse rejected by the recorded process start time;
- foreign launchd path/program refusal;
- missing owner marker and live code-sign failure refusal;
- owned launchd bootout by registered plist path;
- service and synchronized-state disagreement;
- ready, installed, and invalid-artifact status separation;
- human and JSON rendering with stable exit behavior.

## Validation

```bash
python3 -m py_compile \
  tools/runtime_control.py \
  tools/runtime_cli.py \
  tools/runtime_control_test.py
python3 tools/runtime_control_test.py
python3 tools/runtime_cli.py --help
```

The first physical lifecycle replay remains deferred until `start` exists. The
new commands must not alter the dev5 artifact or the cadence and cleanup gates
recorded by probe 013.

## Expected Artifacts

- maintainable runtime-control module and thin CLI;
- deterministic fixture output for missing, stale, foreign, and idempotent
  lifecycle states;
- updated repository validation metadata and primary runtime documentation;
- one focused PR that leaves issue #60 open for `start` and recovery.

## Known Failure Signatures

- artifact seal or contract digest mismatch: `failed`; replace the artifact,
  never bypass verification;
- launchd path, program, executable, or signature mismatch: `failed`; perform
  no stop mutation;
- dead or malformed runtime lock: `failed` in `status`, removable by `stop`;
- live runtime-lock PID without a verified supervisor identity: `failed` and
  non-removable by this slice;
- absent Vision Pro: not a doctor failure; the future live supervisor reports
  `waiting`;
- missing synchronized live-state record: do not guess `connected` or
  `streaming` from logs.

## Verdict

`alive` if fixtures prove truthful diagnosis, live identity refusal, and
idempotent owned teardown without touching the data plane.

## Next Action

Implement the first slice, then review its ownership and failure behavior before
extracting `start` from the research runner.

## Issue Routing

GitHub issue #60.

## Runs

### Foundation Fixture And Live-Host Validation

Run:
`.code/runtime-control-doctor.json`, `.code/runtime-control-status.json`,
`.code/runtime-control-stop-a.json`, and `.code/runtime-control-stop-b.json`.

Question:
Does the first control-plane slice report prerequisite and lifecycle truth,
refuse foreign identity, and tear down exact owned stale state idempotently?

Mode / build:
Hardware-free fixtures on macOS 27.0 and Linux Python 3.13, plus read-only
doctor/status and idempotent stop against sealed dev5 artifact
`50999eee45412dbc2272159759ad5c046e3264914d7e7728aac87099972b649f`.

Commands:
`python3 tools/runtime_control_test.py`; the probe 014 validation commands;
artifact/profile contract checks and self-tests; repository Markdown and
workflow lint; sealed dev5 `doctor`, `status`, and two consecutive `stop`
commands.

Expected proof:
Stable human and JSON output; exact artifact and host diagnostics; `ready` on a
valid stopped host; repeated stopped teardown success; and fixture refusal for
foreign services, symlinked roots, signature failure, PID reuse, live owner
locks, malformed state, and launchd query errors.

Artifacts captured:
Four JSON command reports under `.code/`; 21 named fixture results on macOS and
inside `python:3.13-slim`; compile, contract, lint, and review output.

Logs checked:
Fixture output, doctor check list, status JSON, both stop JSON reports, artifact
and profile self-test output, actionlint, markdownlint, and final Gemini review.

Human observation:
Not applicable; this slice intentionally requires no headset or controller
window.

Verified:
All 21 fixtures passed on both macOS and Linux. The sealed artifact passed all
16 doctor checks, status reported `ready`, and both stop invocations reported
`stopped` without mutation. Runtime artifact check/self-test, profile
check/self-test, Python compile, JSON validation, actionlint, markdownlint, and
diff checks passed. The final focused Gemini security review reported no
remaining findings.

Inferred:
The ownership and diagnostic foundation is ready for the separate `start`
supervisor slice without changing or resealing dev5.

Failed / missing:
`start`, live Vision Pro states, recovery, and physical lifecycle replay are not
implemented by this slice.

Unknown:
Behavior under a future long-running supervisor until its state writer and
shutdown channel are implemented.

Verdict:
`alive`.

Do not repeat:
Do not edit the frozen runtime-v1 scope record merely to document control-plane
progress; it is sealed into dev5 and would invalidate the artifact contract.

Next action:
Merge this foundation, then implement the bounded `start` supervisor and its
owner identity/state writer as the next issue #60 slice.

Issue routing:
`#60`.
