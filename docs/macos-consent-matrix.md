# macOS Consent Matrix Helper

`tools/macos_consent_matrix.py` inspects Launch Services registrations and stages
signed apps for the consent qualification tracked in issues #62 and #141. See
[runtime readiness](probes/028-macos-runtime-readiness.md) for the physical
qualification plan. The helper does not launch apps, read or change TCC or System
Settings, install runtime artifacts, build ALVR, or rewrite Mach-O UUIDs.

## Commands

All app and allowed-root paths must be absolute. The helper prints a JSON report
with identity evidence, planned commands, results, and any refusal code. These
commands are read-only or dry runs; adding `--apply` performs the planned Launch
Services registration changes.

```sh
uv run --no-project --python 3.12 python tools/macos_consent_matrix.py inspect \
  --stable-app <absolute-stable-app>
uv run --no-project --python 3.12 python tools/macos_consent_matrix.py baseline \
  --stable-app <absolute-stable-app> --allowed-root <absolute-duplicate-root>
uv run --no-project --python 3.12 python tools/macos_consent_matrix.py stage \
  --stable-app <absolute-stable-app> --lane-app <absolute-lane-app>
uv run --no-project --python 3.12 python tools/macos_consent_matrix.py cleanup \
  --stable-app <absolute-stable-app> --lane-app <absolute-lane-app>
```

- `inspect` reports the stable identity and Launch Services state.
- `baseline` unregisters duplicate stable-app records only under explicitly
  allowed roots, preserving the retained stable app and revalidating its identity.
  Any duplicate outside those roots refuses the whole operation.
- `stage` registers already-built, signed lane apps. Their bundle identifiers and
  Mach-O UUIDs must be distinct from the stable app and each other, and their
  signing Team ID must match the stable app.
- `cleanup` unregisters the specified lane apps in reverse order and verifies
  zero remaining records for each lane. It preserves the stable registration and
  does not delete app bundles; filesystem removal is a separate operator step.
  Keep the lane bundles unchanged and validly signed on disk until
  `cleanup --apply` succeeds with zero records.

Staging and cleanup require exactly one matching stable-app registration. Resolve
duplicates with a reviewed `baseline --apply` before staging. Repeat `--lane-app`
to stage one to four apps. Each staging app must be arm64-only, signed, and in a
private operator-owned directory with mode 0700 and no group- or world-writable
ancestors. Apps must be outside `/Applications`,
`/System/Applications`, and `~/Applications`, and their bundle identifiers must
have no existing Launch Services records.

Quarantine absence is not established by the helper on macOS: its current
attribute reader uses `os.listxattr`, which is unavailable there. Inspect lane
attributes with `/usr/bin/xattr -lr <absolute-lane-app>` before staging so a
quarantine prompt does not contaminate the consent qualification. This read does
not remove attributes or grant consent.

All four commands refuse while the bridge process or service is live. Apply uses
the global lifecycle lock and revalidates the stable identity around registration
changes. Review the dry-run report before applying a change. Keep
host-specific app paths and identity evidence in the qualification record rather
than repository workflow metadata.

## Fixture Validation

```sh
uv run --no-project --python 3.12 python tools/macos_consent_matrix_test.py
```

The fixtures use temporary state and simulated commands. They do not change the
live Launch Services database or establish physical consent qualification.
