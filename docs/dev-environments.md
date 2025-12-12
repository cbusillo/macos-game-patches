# Dev Environments

This repo targets an **Apple-only** VR stack, but some parts of the workflow are
easier with access to a native Windows environment (reverse engineering, Windows
debug tools, and rapid iteration on Windows-side VR expectations).

This doc describes the *general* pattern so others can contribute.

Machine-specific details (hostnames, mounts, personal paths, credentials) should
live in `.local.md` (gitignored).

## macOS (primary)

- CrossOver/Wine used to run the Windows game binaries.
- This repo contains:
  - `vr-on-macos/` for ALVR/OpenVR runtime work
  - `patches/` + `src/macos_game_patches/` for binary patch specs and tooling

## Windows (optional but useful)

Common uses:

- Decompilers and Windows-only tooling
- Capturing/inspecting native behavior for OpenVR/SteamVR titles
- Building or running utilities that don’t behave the same under Wine

Recommended baseline:

- Keep a stable folder like `C:\dev` to hold artifacts (logs, dumps, binaries,
  scripts).
- Use `winget` to install common tools in a repeatable way.

Add your actual host connection details to `.local.md`.

