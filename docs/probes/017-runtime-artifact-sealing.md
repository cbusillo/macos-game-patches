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
- sealed artifact with non-fixture target authority:
  `transaction.live_path_hardening_required`;
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

## Result

The dev7 implementation passes the cross-platform artifact self-test with
unsealed admission, post-build sealing, CMS-variance, sealed planning,
relocation, re-seal refusal, signed-tree tamper, and existing artifact fixtures.
All 15 lifecycle, 15 transaction, and 23 control tests remain green, including
zero-mutation live-path hardening and unsealed doctor-readiness checks.

Qualified local validation passed on Mac16,9 with macOS 27.0, Xcode 27.0,
CrossOver 26.2, the four pinned Git trees, and all eight locked runtime inputs.
Two independent builds produced the same unsealed source seal:
`45492e7857b187f0277dd99c3dcd0708ea5321cf1ae211cdecb22aa6cb6fd93a`.

Two separate real Developer ID operations produced valid final artifacts with
distinct exact seals, `73183f11047e7d1e52a3a7555fd8ae611a1d87a52b49d0dbc30e2020728c99b6`
and `91c05efe8118d12a09f5dc1f01b4c6de9e44e88a5e1fc6456fbb50919f1c54b5`.
The embedded CMS bytes differed, so the executable and complete tree hashes
correctly received different content addresses. Both retained the same source
tree, signed attestation, CodeResources SHA-256
`c9c0fabbe38e21aaf2534774a766fc293c58aa0cd208ac051840b234dc872251`,
Developer ID authority, Team ID, bundle identifier, and CDHash
`ce46c1df418421fb3eb845a09f0cfe6d095d2ab1`.

The selected final artifact verified after relocation and resolved a read-only
plan with `artifactStage=sealed`, `requiresSealing=false`, and no planner
blockers. The real unsealed artifact returned `artifact.sealing_required`
through the lifecycle CLI before mutation. Tampering CodeResources in a
relocated copy returned `artifact.verify`. The selected sealed artifact returned
`transaction.live_path_hardening_required` before lifecycle lock creation,
service stop, journal write, or target mutation because its plan includes real
CrossOver and game roots. No Launch Services, CrossOver, game, or stable runtime
path was changed.

## Verdict

`alive`: final lifecycle identity now comes from verified post-sign bytes and a
complete exact tree rather than a manifest mode or caller assertion. Variable
CMS bytes are preserved in unique final content addresses instead of being
normalized away, while stable code identity remains independently visible.

## Next Action

Resume #61's live-enable remainder with descriptor-anchored target mutation,
then run the sealed artifact through three install/uninstall exact-restoration
cycles. In parallel, continue #62's stable bundle URL, Launch Services, Local
Network consent, reboot, and logout/login qualification. Only after both paths
pass should #60's bounded `start` supervisor consume the installed layout.
