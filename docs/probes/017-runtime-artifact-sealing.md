# Runtime Artifact Sealing

## Question

Can the deterministic runtime artifact be sealed as a Developer ID signed,
content-addressed tree before lifecycle planning, while preserving the stable
bridge bundle identity and refusing every unsigned, stale, or substituted tree?

## Boundary

- Keep deterministic payload construction separate from keychain-backed
  Developer ID signing.
- Produce a new immutable sealed artifact rather than signing an installed tree
  or modifying an existing content-addressed artifact in place.
- Bind the final bundle tree digest, code-signing identifier, Team ID, CDHash,
  and source artifact seal into canonical provenance covered by the final seal.
- Verify the complete final bundle before `build_plan()` can report lifecycle
  readiness.
- Preserve the existing bundle identifier and stable installed URL; this slice
  does not register Launch Services or request Local Network consent.
- Hard cuts: no force or bypass flag, no caller-authored signature metadata, no
  trust in directory names alone, no mutation after final verification, and no
  live CrossOver or game-path changes during fixture validation.

## Planned Flow

1. Build and verify the deterministic unsealed artifact.
2. Copy it into a locked temporary publication directory.
3. Sign the declared bridge bundle with the manifest identity and no timestamp.
4. Verify the bundle and executable identity from the signed bytes.
5. Record canonical sealing provenance and the final bundle tree digest.
6. Recompute the artifact records and publish a new immutable content address.
7. Allow lifecycle planning only for that verified final stage.

## Fixture Matrix

- successful post-build sealing and relocated verification;
- unsigned and unsealed artifact remains mutation-gated;
- wrong identifier, Team ID, signature kind, or missing signature refusal;
- payload, CodeResources, sealing provenance, and source-seal tampering;
- undeclared signing output, symlink, path escape, and in-place sealing refusal;
- stale or substituted bundle rejection before lifecycle mutation;
- equivalent sealed artifacts compare identically when signing output is
  deterministic, or report distinct final seals without weakening verification;
- CLI JSON/text contracts and stable failure codes.

## Validation

```bash
python3 -m py_compile \
  tools/build_runtime_artifact.py \
  tools/runtime_transaction.py \
  tools/runtime_install.py \
  tools/runtime_cli.py
python3 tools/build_runtime_artifact.py check
python3 tools/build_runtime_artifact.py self-test
python3 tools/runtime_transaction_test.py
python3 tools/runtime_install_test.py
python3 tools/runtime_control_test.py
```

Native macOS sealing fixtures must exercise the real `codesign` verifier. Linux
coverage may use deterministic test doubles only for the signing operation; it
must still exercise canonical provenance, path safety, and lifecycle gating.

## Expected Artifacts

- dev7 post-build sealing contract and canonical provenance;
- `seal` CLI command that publishes a new immutable final artifact;
- final-stage artifact verification and lifecycle readiness;
- hardware-free tamper, identity, relocation, and gate fixtures;
- updated artifact documentation, CI, and repository validation metadata.

## Cleanup

- Remove temporary signing directories and lock files after success or failure.
- Preserve an already published immutable artifact; never rewrite it in place.
- Remove local fixture keychains, identities, and generated artifacts after the
  test run.
- Do not unregister or delete the stable user bridge bundle in this slice.

## Known Failure Signatures

- unsealed artifact: `artifact.sealing_required`;
- unavailable or ambiguous identity: `sealing.identity`;
- failed signature verification or identity mismatch: `sealing.signature`;
- changed source artifact: `sealing.source_mismatch`;
- changed final tree or provenance: `artifact.verify`;
- existing final content address with different bytes: `artifact.publish`;
- unsafe output, symlink, or undeclared signing file: fail closed before publish.

## Issue Routing

GitHub issue #62. A verified final artifact unblocks #61's live install and
exact-restoration qualification; #60 remains blocked until that installed layout
exists.
