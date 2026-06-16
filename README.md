# macOS Game And VR Patch Lab

Workspace for reproducible experiments around getting PC VR and game rendering
working well on Apple hardware.

The near-term target is SteamVR through CrossOver/GPTK on macOS, paired with an
ALVR visionOS client on Apple Vision Pro. D3DMetal performance is central to the
plan; the first gate is proving that the AVP client can speak the same ALVR v21
protocol as the streamer before investing in the CrossOver-to-native frame
bridge.

## Working Style

- Define the target game, runtime, headset, macOS version, hardware, and success
  criteria before adding tools.
- Keep experiments small and reproducible: record commands, artifacts, cleanup
  steps, and failure signatures.
- Prefer focused probes over broad framework code until a path has produced real
  evidence.
- Add scripts only after a repeated command or check is worth automating.

## Patch Artifacts

Patch artifacts under `patches/` are intended for external upstream checkouts.
Each patch directory includes its own apply notes and tested upstream commits.
Use the sibling source layout in `docs/source-workspace.md` for active ALVR and
visionOS client work.

## Starting New Work

Start with the probe ledger:

```text
docs/probes/README.md
```

Include:

- goal and non-goals
- hardware and software assumptions
- first executable gate
- evidence to collect
- cleanup or rollback steps

Before live SteamVR or ALVR runs, clear stale runtime state:

```bash
python3 tools/vr_stack_cleanup.py
```

For fully sterile CrossOver/Wine probes, add `--include-wine-crossover`.
