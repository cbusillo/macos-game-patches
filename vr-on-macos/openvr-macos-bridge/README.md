
# OpenVR macOS bridge

This directory contains early work on an **OpenVR-compatible runtime surface**
implemented on macOS, intended to let Windows OpenVR titles run under
CrossOver/Wine while the “real” VR work (tracking, compositor timing, video
encode + transport) happens on Apple hardware.

Today, most active work is happening in the ALVR-based stack under
`vr-on-macos/ALVR/`. This bridge is kept as a place for design notes and
experiments that don’t belong inside ALVR yet.

## What this is (high level)

- A macOS-side runtime that can satisfy OpenVR client expectations
- A shared-memory or IPC path between:
  - a Windows-side OpenVR client DLL (loaded by the game under Wine)
  - a macOS-side server that can drive poses + compositor submissions

## Docs

- `docs/DESIGN.md` — design notes, message flow, and responsibilities
