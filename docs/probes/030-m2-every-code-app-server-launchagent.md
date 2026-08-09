# Probe 030: M2 Every Code App Server LaunchAgent

## Question

Can the secondary M2 test host run its Every Code app server as a durable,
per-user Aqua LaunchAgent instead of an iTerm-owned manual process, while
retaining loopback-only access, exact managed-update rollback, and a fail-closed
first migration?

## Hypothesis

A fixed per-user LaunchAgent can start the exact `~/.local/bin/code` binary only
inside the logged-in Aqua session, bind only `127.0.0.1:8765`, restart after a
crash, and return after login without granting a network-facing control surface.
The existing SSH loopback tunnel and controller should reconnect after the
manual process is replaced.

## Baseline

The M2 baseline on August 9, 2026 is:

- Host: `chris-mbp`, user `cbusillo`, UID `501`.
- macOS `27.0` build `26A5388g` with an active Aqua login session.
- Every Code `0.6.116` at `~/.local/bin/code`, SHA-256
  `a039bb8bc43df69818849c51388c4059e33a8c74896af3c3a86dfaf1100c9b56`.
- Manual app server PID `14998`, owned by an iTerm login shell, listening only
  on `127.0.0.1:8765`.
- Local controller tunnel PID `17857` with control socket
  `/tmp/m2-code-lab.sock` and local forwarding to the same loopback port.
- No existing matching LaunchAgent file or loaded launchd label.

## Service Contract

- Label: `com.cbusillo.every-code-lab.app-server`.
- Plist: `~/Library/LaunchAgents/com.cbusillo.every-code-lab.app-server.plist`.
- Program arguments:
  `~/.local/bin/code app-server --listen ws://127.0.0.1:8765`.
- Working directory: the clean M2 repository checkout.
- Session boundary: `LimitLoadToSessionType = Aqua`.
- Lifecycle: `RunAtLoad = true`, crash-only
  `KeepAlive = { SuccessfulExit = false; }`, five-second throttle.
- Standard input: `/dev/null`.
- Private logs under `~/Library/Logs/EveryCode/` with mode `0600`.
- Private code-hash and working-directory contract plus operation lock under
  `~/.code/run/`.
- No public listener, remote bind, shell wrapper, root daemon, or system-domain
  service.

## Safety Contract

- Confirm remote hostname, user, UID, GUI session, exact code path, version,
  checksum, and working directory before writing.
- Reject a foreign listener on port `8765`; only replace the exact current
  `code app-server` command owned by the current user.
- Render with `plistlib`, lint with `plutil`, and publish through an atomic
  same-directory replacement.
- Load only into `gui/<uid>` and verify the loaded service's PID, command,
  working directory, and loopback listener.
- Treat loopback as a trusted-local-user boundary, not authentication. Move the
  SSH control socket from `/tmp` to owner-only `~/.code/run/` state.
- Stop the exact manual listener before bootstrapping. Never start `code`
  directly from SSH as a fallback.
- Serialize writes with an owner-only advisory lock and preserve the installed
  code hash plus working-directory identity in a private contract file.
- Preserve any previous plist bytes for rollback during an update.
- On failure, restore the previous plist and service when one existed. For the
  first migration, boot out the candidate and move its plist outside
  `LaunchAgents` into the private log root for diagnosis; never fall back to an
  SSH-owned app server.
- Do not perform logout or reboot until installation, app-server protocol, and
  controller reconnection all pass in the current Aqua session.

## Reproducible Commands

```bash
python3 tools/macos_app_server_launch_agent.py check \
  --working-directory ~/Developer/macos-game-patches-m2
python3 tools/macos_app_server_launch_agent.py install \
  --working-directory ~/Developer/macos-game-patches-m2
python3 tools/macos_app_server_launch_agent.py status
python3 tools/macos_app_server_launch_agent_test.py
```

Host-local launchd inspection:

```bash
launchctl print gui/501/com.cbusillo.every-code-lab.app-server
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

## Expected Artifacts

- Deterministic plist and command-admission fixture output.
- Atomic installed plist with owner-only writable parent state.
- `launchctl print` evidence for one loaded Aqua service and one live PID.
- One exact loopback listener with no wildcard or non-loopback address.
- App-server initialize, conversation creation, and prompt response through the
  existing SSH tunnel after process replacement.
- Clean rollback or uninstall evidence with no stale service or listener.

## Known Failure Signatures

- `host.identity_mismatch`
- `session.aqua_unavailable`
- `code.identity_mismatch`
- `working_directory.invalid`
- `listener.foreign`
- `plist.invalid`
- `launchd.bootstrap_failed`
- `launchd.service_not_ready`
- `controller.reconnect_failed`
- `migration.manual_recovery_required`

## Current-Session Evidence

The M2 transition passed on August 9, 2026:

- Read-only admission confirmed the exact Every Code `0.6.116` binary and
  SHA-256, one manual PID `14998`, one IPv4 loopback listener, the clean M2
  checkout, UID `501`, and the active Aqua launchd domain.
- The installer atomically published the owner-only plist, private code-hash
  contract, advisory lock, and `0600` logs, terminated only manual PID `14998`,
  and bootstrapped service PID `24729` with parent PID `1`.
- A second install returned `changed: false` and preserved PID `24729`, proving
  the exact healthy configuration is idempotent.
- The local SSH master moved from the shared `/tmp` socket to owner-only
  `~/.code/run/m2-code-lab.sock`; the old socket is absent and tunnel PID
  `13812` owns the local forward.
- The controller rejected its stale conversation after process replacement,
  created a new conversation, and returned exact LaunchAgent and signed-helper
  status. Accessibility and Screen Recording both remained granted.
- An exact `launchctl kill SIGKILL` crash test replaced PID `24729` with PID
  `25125`, restored the loopback listener, and passed status. The controller
  again created a fresh conversation and the signed helper remained ready.
- No logout or reboot was performed. Return-after-login remains the next
  physical gate because automatic login is disabled and requires the operator
  to establish a new Aqua session.

## Rollback

```bash
python3 tools/macos_app_server_launch_agent.py uninstall
```

Uninstall boots out only the exact per-user label and removes only its exact
plist. Logs remain for diagnosis unless the operator explicitly removes them.
If the LaunchAgent experiment is retired, the operator may restart the app
server manually from iTerm inside the logged-in Aqua session.

Managed updates restore the previous exact plist and loaded state on failure.
The first manual-to-LaunchAgent cutover cannot safely recreate an iTerm-owned
process from SSH: failure removes the candidate from `LaunchAgents`, preserves
its plist in the private log root, and returns the exact local-Aqua recovery
command for the operator.

## Cleanup

- Keep the LaunchAgent installed when the verdict passes; persistence is the
  feature under test.
- Remove temporary plist staging files and transient controller prompt files.
- Preserve no listener other than the exact loopback app server.
- Do not change the signed UI helper or its TCC grants.

## Verdict

`partial-pass`: installation, exact status, idempotency, crash restart,
controller reconnection, owner-only tunnel state, and signed-helper TCC
continuity pass in the current Aqua session. Logout/login and reboot persistence
remain unclaimed until the operator establishes a new graphical login.
