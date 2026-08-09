# Probe 029: Signed macOS Lab UI Helper

## Question

Can a narrowly scoped, Developer ID signed helper give the host-local Every Code
Lab durable Accessibility and Screen Recording authority without granting broad
assistive access to `/usr/bin/osascript` or exposing a network control surface?

## Hypothesis

A stable `.app` identity can own the two macOS privacy grants while exposing
only exact JSON commands for readiness, target-window capture, bounded button
inspection, and one product-owned button action. Human operators continue to
make macOS security decisions such as Local Network Allow or Don't Allow.

## Scope

- Bundle ID: `com.alvr.macos-game-patches.ui-helper`.
- Developer ID Team: `MM5YXC7T6E`.
- Install root:
  `~/Library/Application Support/MacOSGamePatches/UIHelper/MacOSUIHelper.app`.
- Helper-owned capture root:
  `~/Library/Application Support/MacOSGamePatches/UIHelper/Captures`.
- Helper-owned one-shot result root:
  `~/Library/Application Support/MacOSGamePatches/UIHelper/Results`.
- Target allowlist for the first slice:
  `com.alvr.macos-bridge.iosurface`, Team ID `MM5YXC7T6E`.
- Mutable button allowlist for the first slice: exact title `Continue` only.

The first slice has no listener, socket, daemon, shell execution, AppleScript,
typing, arbitrary key events, raw coordinate clicks, caller-selected output
path, or system permission decision action.

Screen Recording commands are launched through Launch Services so macOS assigns
TCC responsibility to the signed helper instead of its parent process. The
caller supplies a random 32-hex result token; the helper writes one JSON result
to its fixed private `Results` directory and exits. There is no resident broker.

## Command Contract

- `status`: report Accessibility and Screen Recording readiness plus the
  frontmost application identity without prompting.
- `request-screen-recording`: ask macOS to display its Screen Recording consent
  prompt for this signed helper identity; the operator still makes the choice.
- `request-accessibility`: ask macOS to display its Accessibility consent path
  for this signed helper identity; the operator still makes the choice.
- `windows --bundle-id <id> --pid <pid>`: list bounded target-owned window IDs,
  titles, and geometry.
- `buttons --bundle-id <id> --pid <pid>`: list only role, subrole, title,
  identifier, enabled state, and child count from a bounded target AX tree.
- `screenshot --bundle-id <id> --pid <pid> --window-id <id> --name <safe.png>`:
  capture only the validated target-owned window with ScreenCaptureKit.
- `press-button --bundle-id <id> --pid <pid> --title Continue`: require one
  exact enabled AX button, revalidate the process and tree, then perform
  `AXPress`.

Direct executable invocations emit one schema-v1 JSON object on stdout. Launch
Services invocations write that object to the token-bound private result file;
the Python client validates, deletes, and re-emits it on stdout. Diagnostic logs
go to stderr. Stable failures include invalid grammar, TCC not granted, GUI
unavailable, target identity mismatch, ambiguous or missing button, truncated
AX traversal, invalid window, unsafe output root, timeout, and unexpected
internal failure.

## Safety Contract

- Resolve the target by PID, process start token, exact bundle ID, explicit
  Developer ID certificate requirement, Security-framework signing identifier,
  and Team ID; never trust process names.
- Bind AX access to `AXUIElementCreateApplication(pid)` and never query the
  system-wide AX root.
- Limit AX traversal to five seconds, depth eight, 200 nodes, 64 emitted
  buttons, bounded child-array reads, and a 250-millisecond timeout on every
  queried element. A truncated tree is read-only and cannot authorize a press.
- Revalidate target identity, exact match count, enabled state, and `AXPress`
  support immediately before acting.
- If the validated target is not frontmost, activate only that exact signed app
  and verify focus before performing the allowlisted button action.
- Capture only a validated target window. Never capture a display or unrelated
  composite desktop in this slice.
- Create the private output root with descriptor-relative no-follow operations;
  create screenshots with `O_NOFOLLOW | O_CREAT | O_EXCL`, mode `0600`, and an
  allowlisted basename.
- `status` never requests privacy access. The explicit
  `request-screen-recording` and `request-accessibility` commands may display
  Apple's consent UI, but cannot choose an answer. The operator grants both
  permissions only to the final signed app.
- The helper does not press macOS Allow or Don't Allow buttons. Those remain
  human-observed issue-#62 evidence.

## Reproducible Commands

Planned deterministic validation:

```bash
swift test --package-path tools/macos_ui_helper
python3 tools/macos_ui_helper_client_test.py
python3 tools/build_macos_ui_helper.py check
python3 tools/build_macos_ui_helper.py build
python3 tools/build_macos_ui_helper.py install
python3 tools/macos_ui_helper_client.py status
```

Planned physical validation on the M2:

```bash
python3 tools/macos_ui_helper_client.py status
python3 tools/macos_ui_helper_client.py request-accessibility
python3 tools/macos_ui_helper_client.py request-screen-recording
python3 tools/macos_ui_helper_client.py windows --bundle-id ... --pid ...
python3 tools/macos_ui_helper_client.py buttons --bundle-id ... --pid ...
python3 tools/macos_ui_helper_client.py screenshot \
  --bundle-id ... --pid ... --window-id ... --name ...
python3 tools/macos_ui_helper_client.py press-button \
  --bundle-id ... --pid ... --title Continue
```

## Expected Artifacts

- Swift unit-test output for command grammar, target validation, traversal
  limits, ambiguity refusal, and path hardening.
- Signed app identity, designated requirement, Team ID, bundle ID, CDHash, and
  Mach-O UUID.
- Status JSON before and after physical TCC grants.
- A target-window-only PNG with mode `0600` and no unrelated window content.
- Button inspection and exact `Continue` action JSON.
- Final evidence showing no helper listener, daemon, bridge service, or stale
  process.

## Known Failure Signatures

- `permission.accessibility.not_granted`
- `permission.screen_recording.not_granted`
- `session.gui.unavailable`
- `target.identity_mismatch`
- `target.window_invalid`
- `ax.tree_truncated`
- `ax.button.not_found`
- `ax.button.ambiguous`
- `ax.button.not_pressable`
- `output.root_unsafe`
- `output.name_invalid`
- `operation.timeout`
- `client.launch_timeout`

## Physical Evidence

The M2 secondary lane (`Mac14,6`) passed the signed-helper path from an Every
Code `0.6.116` app server launched inside the logged-in Aqua session:

- The final arm64 helper build has bundle ID
  `com.alvr.macos-game-patches.ui-helper`, Team ID `MM5YXC7T6E`, CDHash
  `1d5ddc5ec50e229977ee10bfb164a6dd6420669c`, and Mach-O UUID
  `3EBB7977-9F29-335F-BC0A-7462920DA0D9`.
- Launch Services execution reported both Accessibility and Screen Recording
  granted to the stable helper identity. Direct child execution is not the
  supported transport because macOS may attribute Screen Recording to the
  parent process.
- The helper resolved the live Developer ID bridge by PID, bundle ID, and live
  code signature; bounded AX traversal visited 88 nodes and returned exactly
  one enabled, pressable `Continue` button.
- ScreenCaptureKit returned only bridge window `2724`: a 260 by 266 PNG of
  36,656 bytes, created with mode `0600`. Visual review showed only the ALVR
  consent window and no surrounding desktop content.
- One final-build app-server turn launched foreground consent, revalidated PID
  `22930`,
  observed the exact button, activated only that signed target, and completed
  `AXPress`. The operator answered Apple's subsequent Local Network prompt.
- The bridge wrote schema-v1 outcome `ready` with `networkAvailable: true` and
  exited; no helper daemon, listener, bridge process, or service remained.

The owner chose to retain existing iTerm and Every Code Accessibility grants on
this dedicated test host for future unrestricted lab automation. The helper's
bounded contract is qualified, but exclusive helper-only host authority is not
claimed.

## Cleanup

- Stop any test helper process; the normal helper is one-shot and should not
  remain resident.
- Remove captured PNGs after durable evidence is recorded.
- Remove the helper from Accessibility and Screen Recording before deleting the
  installed app when the experiment is retired.
- Do not change `/usr/bin/osascript` privacy permissions.

## Verdict

`pass`: the signed one-shot helper, Launch Services client transport, exact
target validation, bounded window capture, bounded AX inspection, and exact
product-owned action passed on the M2. Issue #62 still owns the broader Local
Network persistence, update, rollback, and clean-user matrix; the M4 remains the
release-authoritative performance host.
