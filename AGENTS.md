# AI Agent Guidelines

Use `.github/github.json` for non-secret repo workflow facts, validation
expectations, docs routing, and cleanup policy.

## Branch Discipline

- Do not work directly on `main` for implementation or cleanup changes.
- Create a focused task branch before editing tracked files.
- Push only task branches and open or update a PR when GitHub follow-through is
  needed.

## Experiment Hygiene

- Start new work from its owning GitHub issue (see `docs/probes/README.md` for
  routing). The issue is the plan; record a new probe's question and gates in
  `docs/probes/` before adding scripts.
- Commit reproducible commands, cleanup steps, expected artifacts, and known
  failure signatures with each experiment.
- Keep tools narrowly scoped until a path has real evidence.
- Update `.github/github.json` whenever validation commands, primary docs, or
  cleanup expectations change.

## Validation

CI (`.github/workflows/ci.yml`) runs on pull requests, `main`, and merge-train
candidates. `Repo Hygiene` is the required check: JSON, Markdown and workflow
lint, Python compile, runtime profile and artifact contract checks, the fixture
suites, and a secret scan. `Runtime Lifecycle (macOS)` runs the Swift helper and
lifecycle fixtures. Pull requests that change only Markdown, `docs/`, `LICENSE`,
or `.github/github.json` skip the fixture suites and the macOS lane. The local
commands are under `qualityGate` in `.github/github.json`; record new ones there.

`runtime/manifest.json` pins the SHA-256 of some docs as artifact inputs:
`docs/reproducible-mac-alvr-runtime-v1.md`,
`docs/reproducible-runtime-artifact.md`, and the `patches/crossover-dxvk/` and
`patches/crossover-moltenvk/` READMEs. Editing one fails
`python3 tools/build_runtime_artifact.py check` until the manifest and lock are
updated, which changes the runtime contract. Change them only as part of a
contract update.

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
