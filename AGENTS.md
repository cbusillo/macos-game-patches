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

## Test Guard Rules

A test stays only if it fails when the product is broken and passes when
someone makes an intended change.

- No test may assert a literal that is defined elsewhere, such as a version,
  build number, toolchain, profile tuning value, or hash. Use distinct fixture
  values, or check agreement against the one source of truth.
- No test may assert workflow or config text. Enforce the rule where it runs:
  the workflow itself, a helper with its own unit test, or `actionlint`.
- Verification and loading code must not depend on working-tree state. Check
  live state only on the path that acts on it.
- Keep byte-exact and hash gates on real artifacts and immutable evidence.
- Wire every new `*_test.py` into `.github/workflows/ci.yml`.
