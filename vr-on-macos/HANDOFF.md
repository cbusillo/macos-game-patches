# VR on macOS: handoff runbook

This runbook is optimized for a new contributor who wants to iterate on the
`vrclient_macos` + `openvr_api_stub` path while keeping experiments reproducible.

## What you need

- macOS host with CrossOver
- Steam + SteamVR installed inside a CrossOver bottle
- A VR title inside the same bottle (pick one canary title)
- Optional: Apple Vision Pro (AVP) client connected to ALVR

You do **not** need to wear the AVP for most dev work. Only wear it briefly to
confirm the video path (test pattern or real frames).

## Repo setup

Initialize the ALVR submodule:

```bash
git submodule update --init --recursive
```

If `git status` shows `vr-on-macos/ALVR` as modified, you have uncommitted
changes inside the submodule. A new contributor cloning the parent repo will not
receive those changes unless they are committed in the submodule repository (or
you provide them as a patch).

Recommended handoff options:

- Commit the work on a branch inside `vr-on-macos/ALVR`, push it to a fork/remote,
  then update the submodule pointer in this repo.
- If you cannot push, export a patch file for review/transfer:

```bash
git -C vr-on-macos/ALVR diff > temp/alvr-local-changes.patch
```

To apply that patch on another machine:

```bash
git -C vr-on-macos/ALVR apply ../../temp/alvr-local-changes.patch
```

## Build (golden path)

From repo root:

```bash
cd vr-on-macos
just build
```

If you don't use `just`, the equivalent is:

```bash
(cd vr-on-macos/ALVR && cargo build -p alvr_macos_bridge --release)
(cd vr-on-macos/ALVR/alvr/vrclient_macos && ./build.sh)
```

Notes:

- `vrclient_macos` cross-compiles Windows DLLs and requires `mingw-w64`.
- `alvr_macos_bridge` uses VideoToolbox and reads the active codec from ALVR's
  `session.json`.

## Run + validate (video path)

The smoke-test helper deploys DLLs into the CrossOver bottle, starts the native
bridge, and can optionally launch a target title.

1. Pick a canary title. Good starting points:
   - `steamtours` (SteamVR environments)
   - `thelab` (The Lab)
   - `aircar` (Aircar)

2. Run the smoke-test. Override the bottle path as needed:

```bash
cd vr-on-macos
just smoke steamtours '/path/to/CrossOver/Bottles/<BottleName>'
```

To auto-launch the title:

```bash
cd vr-on-macos
just smoke-launch steamtours '/path/to/CrossOver/Bottles/<BottleName>'
```

1. Confirm the bridge is alive:

- Bridge log: `/tmp/alvr_macos_bridge.log`

1. Confirm the OpenVR side loaded:

- Bottle logs (paths depend on bottle):
  - `drive_c/alvr_vrclient.log`
  - `drive_c/alvr_openvr_api.log`

1. Confirm video is flowing (with AVP connected):

- If the VR title never calls `Submit`, the bridge displays an **animated test
  pattern** instead of a frozen/green frame.
- If you see the test pattern, the transport/decode path is working and the next
  work is OpenVR compositor semantics.

## Where traces live

Keep each experiment reproducible by saving a run bundle under `temp/vr_runs/`
(gitignored). A bundle should include:

- `/tmp/alvr_macos_bridge.log`
- Bottle logs (`alvr_vrclient.log`, `alvr_openvr_api.log`)
- If present, crash artifacts (UE4 minidumps, etc.)
- A short README describing what changed + what to look for

## Common gotchas

### “missing X-ALVR header” on `:8082`

ALVR's HTTP endpoint expects an `X-ALVR` header. A normal browser request may
return “missing X-ALVR header”. Use the ALVR dashboard app, or use a request
tool that can add headers.

### AVP powered off / removed

If the AVP is removed, it may disconnect or power off. That's fine for most dev.
Only wear it briefly when you need to validate video.

### 2D mirror on macOS screen

Seeing a 2D mirror window while the AVP is blank usually means the title is
running but frames are not reaching the ALVR client (codec mismatch, no submit
loop, or bridge not connected).

## Definition of “working” for handoff

Working enough to hand off means:

- A new dev can build `alvr_macos_bridge` and `vrclient_x64.dll` from scratch.
- `vr_smoketest.sh` reliably deploys and starts the bridge.
- AVP can show the bridge's animated test pattern when no VR frames arrive.

Known blockers (expected in current state):

- Many titles initialize OpenVR but do not enter `WaitGetPoses`/`Submit` under
  our current stubs.
- Aircar still crashes early under CrossOver (minidumps captured under
  `temp/vr_runs/`).
