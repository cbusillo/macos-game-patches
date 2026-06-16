# macOS Game And VR Patch Lab

Workspace for reproducible experiments around running and patching games on
macOS and visionOS.

## Working Style

- Define the target game, runtime, headset, macOS version, hardware, and success
  criteria before adding tools.
- Keep experiments small and reproducible: record commands, artifacts, cleanup
  steps, and failure signatures.
- Prefer focused probes over broad framework code until a path has produced real
  evidence.
- Add scripts only after a repeated command or check is worth automating.

## Starting New Work

Create a focused plan under `docs/`, for example:

```text
docs/<game-or-runtime>-plan.md
```

Include:

- goal and non-goals
- hardware and software assumptions
- first executable gate
- evidence to collect
- cleanup or rollback steps
