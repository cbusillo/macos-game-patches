# VR on macOS – Tooling

This repo is currently scratch-only, but we keep a short list of tools that are
useful for SE2 and other Windows-on-macOS patch work.

This repo also includes VR runtime work under `vr-on-macos/`; keep VR-specific
tools here when they become repeat-use.

## Binary / IL inspection

- `dnfile` (Python): quick IL + metadata inspection without Windows. Install:
  `python3 -m pip install dnfile`.
- `ilspycmd` (dotnet global tool): CLI decompiler. Install:
  `dotnet tool install -g ilspycmd`.
- `dotnet-ildasm` (dotnet global tool): dump IL. Install:
  `dotnet tool install -g dotnet-ildasm`.
- `strings` / `xxd` (macOS): fast sanity checks of offsets/bytes.

## Wine/CrossOver runtime

- `uv run run --help` — launch via CrossOver
- `uv run clean-run --help` — clear caches, then launch
- `uv run clean-patch-run --help` — clear caches, reapply a patch spec,
  then launch
- `uv run clean --help` — clear caches and kill processes (no patch or launch)

Defaults target `space-engineers-2`; customize with `--patch-target`, `--bottle`,
`--game-folder`, etc.

If you add other repeat-use tools, list them here so future sessions can find
them quickly.

## VR runtime smoke + tracing

- `just` (optional): run common VR workflows from `vr-on-macos/`:
  - `cd vr-on-macos && just --list`
- `vr-on-macos/ALVR/alvr/vrclient_macos/build/vr_smoketest.sh` — deploy the
  OpenVR client DLLs into common Steam/game locations and launch a test title.
- ALVR web UI: the server runs an HTTP endpoint on `:8082`, but it expects an
  `X-ALVR` header (so a regular browser may show a “missing X-ALVR header” error).
  Prefer launching the ALVR dashboard app, or temporarily enable untrusted HTTP
  in ALVR settings when debugging locally.
  - Quick check:
    - `curl -H 'X-ALVR: 1' http://127.0.0.1:8082/`
- Run bundles: store per-run logs and counters under `temp/vr_runs/`
  (gitignored) so traces are reproducible without committing proprietary assets.

If a title produces a UE4 minidump under CrossOver, a quick way to summarize it
on macOS is:

```bash
cargo install --locked minidump-stackwalk
minidump-stackwalk path/to/UE4Minidump.dmp
```

## Windows dev environment

- `winget` (Windows): install tools repeatably on a native Windows VM/host.
- Sysinternals `PsExec`: launch SteamVR/apps in the active console session when
  SSH-launched processes behave differently.
