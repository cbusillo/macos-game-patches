# AI Agent Guidelines

This repository tracks reproducible steps for getting SteamVR + ALVR working on
macOS hardware.
Use `.github/github-repo-workflow.json` for non-secret repo workflow facts,
validation commands, GitHub signal availability, docs routing, important
workflows, and cleanup policy.

## Mandatory Startup Hygiene

Before any SteamVR, CrossOver, Wine, or ALVR test run, execute:

```bash
python3 tools/vr_stack_cleanup.py
```

Rules:

- Treat cleanup as required preflight, not optional.
- If cleanup exits nonzero, do not continue with launch/testing until the stack
  is clean.
- Repeat cleanup between major test attempts when process state is uncertain.
- For fully sterile live runs, include native Steam helper cleanup:

```bash
python3 tools/vr_stack_cleanup.py --sterile-native-steam
```

## Minimal Test Sequence

```bash
python3 tools/vr_stack_cleanup.py
python3 tools/steamvr_smoke.py --mode null
```

Use this baseline before introducing ALVR, VTBridge, or headset-specific
variables.
