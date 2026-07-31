# Runtime Profile Experiments

Files in this directory are explicit-path experiment inputs, not
production-admitted curated profiles. They are excluded from the default
`runtime_profile.py check` discovery, are not embedded in the sealed runtime
artifact, and must not be passed to the production `start` command.

Once a profile has generated evidence, its exact bytes and SHA-256 are part of
that evidence. Do not reformat it after a probe; create a new experiment
identity and rerun the gates instead.

Use them only with the reproducible probe commands in the matching probe
document. Promote a result by updating the canonical profile after its
automated and human acceptance gates pass; do not retain parallel production
profiles for minor tuning differences.

Current experiment:

- `docs/probes/026-fixed-resolution-profile-qualification.md`
