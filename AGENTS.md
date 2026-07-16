# AI Agent Guidelines

Use `.github/github.json` for non-secret repo workflow facts, validation
expectations, docs routing, and cleanup policy.

## Branch Discipline

- Do not work directly on `main` for implementation or cleanup changes.
- Create a focused task branch before editing tracked files.
- Push only task branches and open or update a PR when GitHub follow-through is
  needed.

## Experiment Hygiene

- Start new work with a short plan under `docs/` before adding scripts.
- Commit reproducible commands, cleanup steps, expected artifacts, and known
  failure signatures with each experiment.
- Keep tools narrowly scoped until a path has real evidence.
- Update `.github/github.json` whenever validation commands, primary docs, or
  cleanup expectations change.

## Validation

There is currently no repo-wide executable validation gate. For documentation
only changes, verify the changed Markdown and repository metadata are internally
consistent. When new tooling is added, record the relevant validation command in
`.github/github.json`.
