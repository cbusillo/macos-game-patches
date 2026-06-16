# 002 - ALVR v21 AVP Runtime Gate

## Hypothesis

The patched ALVR v21 visionOS client can be built for an Apple Vision Pro,
installed on-device, paired with the matching ALVR v21 streamer, and driven far
enough to observe first video decode.

## Why This Comes Next

Probe 001 proved the v21 path is build-alive for the Rust client core and
visionOS simulator app. The durable plan now needs runtime evidence before any
CrossOver, SteamVR, D3DMetal, native VideoToolbox, or frame bridge work should
be treated as first priority.

## Current Inputs

- Scratch checkout:
  `/Users/cbusillo/Developer/_probe-alvr-v21-avp/alvr-visionos`
- Current stable source workspace:
  `/Users/cbusillo/Developer/alvr-visionos`
- `alvr-org/alvr-visionos`: `301b9285073949033727baab2d556fe9e8620612`
- `alvr-org/ALVR`: `d9f2b19d2b98b9d70411439fef83300c84ed171d`
- ALVR version: `21.0.0-dev12`
- Patch artifact:
  `patches/alvr-visionos/alvr-v21-client-core-abi.patch`
- Matching streamer release:
  `v21.0.0-dev12+nightly.2026.06.16`
- Apple Vision Pro device visible to Xcode as `Apple Vision Pro`.
- The v21 ABI patch from Probe 001 is already applied in the scratch checkout.

Device identifiers observed during the session:

- Xcode destination ID: `00008112-001108C63A78A01E`
- `devicectl` identifier: `4E8627DA-A354-5A74-93CF-61F3D17CE324`

Both refer to the same physical `Apple Vision Pro`; use the identifier format
expected by the command being run.

## Local Readiness Evidence

Commands run:

```bash
xcodebuild -list -project ALVRClient.xcodeproj
xcodebuild -showdestinations \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient
xcodebuild -showBuildSettings \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'platform=visionOS,id=00008112-001108C63A78A01E'
```

Results:

- Project exposes `ALVRClient` and `ALVREyeBroadcast` targets.
- Project exposes `ALVRClient` and `ALVREyeBroadcast` schemes.
- Xcode sees a physical `Apple Vision Pro` visionOS destination.
- Project default signing team is `A2R992S5N3`.
- Project default bundle IDs are `alvr.client` and
  `alvr.client.ALVREyeBroadcast`.
- Entitlements include App Group `group.alvr.client.ALVR` on both targets.
- Main app entitlement also includes
  `com.apple.developer.low-latency-streaming`.

## Device Build Evidence

The v21 Rust client core and xcframework were rebuilt successfully:

```bash
cd /Users/cbusillo/Developer/_probe-alvr-v21-avp/alvr-visionos
unset SDKROOT
zsh build_and_repack.sh
```

Result:

- `alvr_client_core v21.0.0-dev12` built successfully.
- `ALVRClient/ALVRClientCore.xcframework` was generated successfully.

The same source state has since been promoted from the disposable scratch path
to the stable sibling workspace documented in `docs/source-workspace.md`.

The visionOS simulator app was rebuilt after updating the patch to send the
full PSVR2 Sense input ID set with `alvr_send_active_interaction_profile`:

```bash
xcodebuild -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'generic/platform=visionOS Simulator' \
  CODE_SIGNING_ALLOWED=NO \
  build
```

Result:

- Build succeeded.
- The PSVR2 interaction profile call now matches ALVR v21's automatic button
  mapping expectations instead of reporting an empty client input set.

Device build with the project defaults failed at provisioning:

```bash
xcodebuild build \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'platform=visionOS,id=00008112-001108C63A78A01E'
```

Result:

- No profiles found for `alvr.client`.
- No profiles found for `alvr.client.ALVREyeBroadcast`.
- Xcode suggested `-allowProvisioningUpdates`.

Automatic provisioning with the project team failed because the team account is
not available in this Xcode environment:

```bash
xcodebuild build \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'platform=visionOS,id=00008112-001108C63A78A01E' \
  -allowProvisioningUpdates
```

Result:

- No account for team `A2R992S5N3`.
- No matching development profiles for `alvr.client` or
  `alvr.client.ALVREyeBroadcast`.

Automatic provisioning with the available `MM5YXC7T6E` team and a unique probe
bundle ID advanced past account lookup, then failed on entitlements:

```bash
xcodebuild build \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'platform=visionOS,id=00008112-001108C63A78A01E' \
  -allowProvisioningUpdates \
  DEVELOPMENT_TEAM=MM5YXC7T6E \
  PRODUCT_BUNDLE_IDENTIFIER=com.shinycomputers.probe.alvrclient
```

Result:

- App Group `group.alvr.client.ALVR` is not available for the team.
- Wildcard team provisioning profile does not include App Groups.
- Wildcard team provisioning profile does not include Low-Latency Streaming.
- Wildcard team provisioning profile does not support
  `group.alvr.client.ALVR`.

This command intentionally tested the main app bundle override only. The
broadcast extension still needs its own probe bundle ID and matching development
profile before a complete signed device build can be expected.

Signing was unblocked by using the active Apple Developer team `MM5YXC7T6E`,
unique probe bundle IDs, and a matching probe App Group:

- Main app bundle ID: `com.shinycomputers.probe.alvrclient`
- Broadcast extension bundle ID:
  `com.shinycomputers.probe.alvrclient.broadcast`
- App Group: `group.com.shinycomputers.probe.alvrclient`
- Main app capabilities: App Groups and Low-Latency Streaming
- Broadcast extension capabilities: App Groups

The bundle IDs and capabilities were created through the App Store Connect API;
Xcode then generated development provisioning profiles for both targets during
the device build.

Device build from the stable source workspace succeeded:

```bash
cd /Users/cbusillo/Developer/alvr-visionos
xcodebuild build \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'platform=visionOS,id=00008112-001108C63A78A01E' \
  -allowProvisioningUpdates
```

Result:

- Build succeeded for physical `xros`.
- Xcode signed with `Apple Development: Chris Busillo (87ZKY9MUC6)`.
- Main app profile:
  `iOS Team Provisioning Profile: com.shinycomputers.probe.alvrclient`.
- Broadcast extension profile:
  `iOS Team Provisioning Profile: com.shinycomputers.probe.alvrclient.broadcast`.
- The build emitted a non-fatal warning that the broadcast extension
  `CFBundleVersion` (`1`) does not match the containing app (`3`).

Install and launch also succeeded:

```bash
xcrun devicectl device install app \
  --device 4E8627DA-A354-5A74-93CF-61F3D17CE324 \
  /Users/cbusillo/Library/Developer/Xcode/DerivedData/ALVRClient-auzsgqllglfixqbsyodkubmqbcnc/Build/Products/Debug-xros/ALVRClient.app

xcrun devicectl device process launch \
  --device 4E8627DA-A354-5A74-93CF-61F3D17CE324 \
  --terminate-existing \
  --timeout 20 \
  com.shinycomputers.probe.alvrclient
```

Result:

- App installed with bundle ID `com.shinycomputers.probe.alvrclient`.
- `devicectl` launched the app and reported process identifier `1001`.

## Streamer Runtime Attempt

Before the live runtime attempt, the restored cleanup preflight was run from
this repo:

```bash
python3 tools/vr_stack_cleanup.py
```

Result:

- `matched=0`
- `terminated=0`
- `remaining=0`

The standalone ALVR checkout was missing the `openvr` submodule on the first
streamer build attempt. After initializing submodules, the macOS streamer build
succeeded:

```bash
cd /Users/cbusillo/Developer/alvr
git submodule update --init --recursive
cargo xtask build-streamer --platform macos
```

Result:

- `alvr_server_openvr v21.0.0-dev12` built for macOS.
- `alvr_dashboard v21.0.0-dev12` built for macOS.
- The streamer output was created at
  `/Users/cbusillo/Developer/alvr/build/alvr_streamer_macos`.
- The build emitted C++ warnings, but completed successfully.

The macOS dashboard launched successfully:

```bash
/Users/cbusillo/Developer/alvr/build/alvr_streamer_macos/alvr_dashboard
```

Dashboard evidence:

- The dashboard selected the Metal backend on `Apple M4 Max`.
- The dashboard loaded the local ALVR session.
- Existing trusted AVP client entries remained in
  `~/Library/Application Support/alvr/session.json`.
- No streamer server socket or client connection-state transition was observed.
- `~/Library/Application Support/alvr/logs` was created, but no server log was
  written during the dashboard-only run.

The AVP client was launched again with console output attached:

```bash
xcrun devicectl device process launch \
  --device 4E8627DA-A354-5A74-93CF-61F3D17CE324 \
  --terminate-existing \
  --timeout 30 \
  --console \
  com.shinycomputers.probe.alvrclient
```

Result:

- The app launched and stayed alive until the bounded `devicectl` console
  timeout.
- Console output included ALVR worker startup, tracking worker startup,
  `initializeAr`, `Reset playspace`, and `Initialize ALVR`.
- The ALVR dashboard session still reported the trusted AVP client entries as
  `Disconnected`.

This established a sharper boundary: the patched v21 AVP client can launch, and
the v21 macOS dashboard can build and run, but the dashboard alone does not
start the streaming server. ALVR expects SteamVR/`vrserver` to load the ALVR
OpenVR driver. On this machine, native macOS OpenVR files were not registered:

```text
~/.config/openvr/openvrpaths.vrpath: missing
~/Library/Application Support/Steam/config/steamvr.vrsettings: missing
```

The only SteamVR runtime found during this probe was inside the CrossOver Steam
bottle:

```text
~/Library/Application Support/CrossOver/Bottles/Steam/drive_c/Program Files (x86)/Steam/steamapps/common/SteamVR/bin/win64/vrserver.exe
~/Library/Application Support/CrossOver/Bottles/Steam/drive_c/Program Files (x86)/Steam/steamapps/common/SteamVR/bin/win64/vrstartup.exe
```

## CrossOver Windows Streamer Attempt

The next runtime attempt used the matching ALVR v21 Windows streamer artifact
inside the existing CrossOver `Steam` bottle.

Sterile cleanup was run first, including native Steam helper cleanup and Wine /
CrossOver process cleanup:

```bash
python3 tools/vr_stack_cleanup.py \
  --include-wine-crossover \
  --sterile-native-steam
```

Result:

- `matched=0`
- `terminated=0`
- `remaining=0`

The matching upstream nightly release was available from
`alvr-org/ALVR-nightly`:

```text
v21.0.0-dev12+nightly.2026.06.16
alvr_streamer_windows.zip
sha256:79953b0c200dec3a1fe1e2438663d69aec0267d620420f6461644a104c93ceea
```

The Windows streamer was extracted into the CrossOver bottle at:

```text
C:\ALVR\v21.0.0-dev12-nightly.2026.06.16
```

Key files were present after extraction:

```text
C:\ALVR\v21.0.0-dev12-nightly.2026.06.16\ALVR Dashboard.exe
C:\ALVR\v21.0.0-dev12-nightly.2026.06.16\driver.vrdrivermanifest
C:\ALVR\v21.0.0-dev12-nightly.2026.06.16\bin\win64\driver_alvr_server.dll
C:\ALVR\v21.0.0-dev12-nightly.2026.06.16\bin\win64\openvr_api.dll
```

The ALVR OpenVR driver was registered with the SteamVR runtime inside the same
CrossOver bottle:

```bash
STEAMVR='C:\Program Files (x86)\Steam\steamapps\common\SteamVR'

/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart \
  --bottle Steam \
  --no-gui \
  "${STEAMVR}\bin\win64\vrpathreg.exe" \
  adddriver 'C:\ALVR\v21.0.0-dev12-nightly.2026.06.16'
```

`vrpathreg show` then reported both the pre-existing iVRy external driver and
the new ALVR driver:

```text
Runtime path = C:\Program Files (x86)\Steam\steamapps\common\SteamVR
Config path = C:\Program Files (x86)\Steam\config
Log path = C:\Program Files (x86)\Steam\logs
External Drivers:
  ivry : C:\Program Files (x86)\Steam\steamapps\common\iVRy
  alvr_server : C:\ALVR\v21.0.0-dev12-nightly.2026.06.16
```

The Windows ALVR Dashboard launched in CrossOver and stayed alive. Its launch
log contained D3DMetal `BeginEvent` / `EndEvent` unsupported API warnings, but
no fatal startup error was observed.

The first direct SteamVR launch did not reach `vrserver.exe`. Steam's
`gameprocess_log.txt` showed `vrstartup.exe` exiting with `-1073741515`, and a
Wine loader trace identified the missing runtime dependency as
`vcruntime140.dll`. Copying SteamVR's own bundled VC runtime DLLs from
`bin\vrwebhelper\win64` to `bin\win64` unblocked SteamVR startup for this
local bottle:

```text
vcruntime140.dll
vcruntime140_1.dll
msvcp140.dll
vccorlib140.dll
```

After the runtime DLL copy, CrossOver SteamVR launched far enough for
`vrserver.exe` to load the ALVR server driver:

```text
Loaded server driver alvr_server (IServerTrackedDeviceProvider_004) from C:\ALVR\v21.0.0-dev12-nightly.2026.06.16\bin\win64\driver_alvr_server.dll
Active HMD set to alvr_server.1WMHH000X00000
```

`vrmonitor.txt` also reported:

```text
VR_Init successful
CQVRController::CheckHmdDriverName: ActualTrackingSystemName: alvr_server (0)
[Status Warning Added 1WMHH000X00000 Headset(0)] Searching...
```

The signed AVP client was launched while CrossOver SteamVR and the Windows ALVR
Dashboard were running:

```bash
xcrun devicectl device process launch \
  --device 4E8627DA-A354-5A74-93CF-61F3D17CE324 \
  --terminate-existing \
  --timeout 60 \
  --console \
  com.shinycomputers.probe.alvrclient
```

Result:

- The app launched and stayed alive until the bounded `devicectl` console
  timeout.
- Console output again showed ALVR worker startup, tracking worker startup,
  `initializeAr`, `Reset playspace`, and `Initialize ALVR`.
- The Windows ALVR `session.json` remained at
  `C:\ALVR\v21.0.0-dev12-nightly.2026.06.16\session.json` with an empty
  `client_connections` object.
- No headset trust prompt, client entry, protocol check, stream start, encoder
  start, or first decode evidence was observed during this run.

This is a meaningful boundary improvement over the macOS dashboard-only
attempt: the ALVR Windows OpenVR driver loads inside CrossOver SteamVR and
SteamVR accepts it as the active HMD. The next boundary is not driver ABI load;
it is getting the AVP client discovered by, trusted by, and connected to the
Windows ALVR streamer session.

## CrossOver Client Discovery Attempt

A follow-up discovery probe found that the cleanup tool was not matching some
Wine-hosted Windows VR processes because their macOS process names were
truncated, for example `C:\ALVR\v21.0.0-` and `C:\Program Files`. The cleanup
tool was updated to match relevant Wine-hosted ALVR and SteamVR command-line
paths. After that update, the cleanup dry run caught stale `vrserver.exe`,
`vrmonitor.exe`, and duplicate `ALVR Dashboard.exe` processes, and the real
cleanup removed them before the next runtime attempt.

The fresh CrossOver launch again reached the ALVR-loaded SteamVR state:

```text
C:\ALVR\v21.0.0-dev12-nightly.2026.06.16\ALVR Dashboard.exe
C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrserver.exe -waitformonitor
C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrmonitor.exe ...
```

ALVR's web server was reachable from macOS through the CrossOver Wine process:

```text
http://127.0.0.1:8082/api/version
21.0.0-dev12+nightly.2026.06.16
```

Plain requests without the ALVR dashboard header were rejected with
`missing X-ALVR header`, which matches ALVR v21's server API behavior. The live
session before the AVP launch still had discovery enabled and an empty client
list:

```json
{
  "server_version": "21.0.0-dev12+nightly.2026.06.16",
  "client_connections": {},
  "connection": {
    "client_discovery": {
      "enabled": true,
      "content": {
        "auto_trust_clients": false
      }
    },
    "stream_port": 9944,
    "web_server_port": 8082
  }
}
```

Source review changed the discovery assumption for ALVR v21. The older wiki
describes UDP discovery on port `9943`, but the active v21 source path uses
mDNS/Bonjour service discovery:

- ALVR server browses `_alvr._tcp.local.`, requires the `protocol` TXT key,
  and uses `device_id` when present before falling back to the mDNS hostname.
- The client listens for the server's TCP control connection on port `9943`.
- The visionOS client uses `NWListener` to publish `_alvr._tcp` with service
  name `ALVR Apple Vision Pro`.
- The visionOS client source prints `mDNS listener is ready` and
  `mDNS registration updated:` when publication succeeds.

The AVP client was launched while `dns-sd` browsed for `_alvr._tcp` services:

```bash
dns-sd -B _alvr._tcp local

xcrun devicectl device process launch \
  --device 4E8627DA-A354-5A74-93CF-61F3D17CE324 \
  --terminate-existing \
  --timeout 60 \
  --console \
  com.shinycomputers.probe.alvrclient
```

Result:

- The AVP app launched and again printed ALVR worker startup,
  `initializeAr`, `Reset playspace`, and `Initialize ALVR`.
- No `mDNS listener is ready`, `mDNS registration updated:`, or mDNS failure
  message appeared in the bounded console output.
- `dns-sd -B _alvr._tcp local` saw no ALVR service during the 60 second run.
- The Windows ALVR `session.json` still had an empty `client_connections`
  object after the AVP launch.
- `lsof` showed the CrossOver ALVR server/driver listening on TCP `8082` and
  mDNS `5353`, but no visible client-side TCP `9943` listener from the AVP.

This shifts the live blocker again: the Windows streamer is alive and browsing
for ALVR v21 clients, but the visionOS client is not visibly publishing the
Bonjour service needed for discovery.

## Current Verdict

`blocked` - the build/install/launch leg is alive, and the CrossOver Windows
streamer path now reaches an ALVR-loaded SteamVR state. The patched v21 Rust
client core builds, the physical Apple Vision Pro device build succeeds, the app
installs, `devicectl` can launch it, the v21 macOS dashboard builds and runs,
and the matching v21 Windows ALVR server driver loads inside CrossOver SteamVR.
The runtime probe remains blocked because the signed AVP client did not appear
in the Windows ALVR streamer session, so pairing and first video decode have not
started. The most recent evidence points to the visionOS client discovery
publisher rather than CrossOver's OpenVR driver load path: no `_alvr._tcp`
Bonjour service was visible from macOS while the AVP app was running, and the
app console did not print its expected mDNS listener readiness or registration
messages.

## Next Action

Keep using the matching ALVR v21 Windows streamer inside the CrossOver Steam
bottle, but focus the next attempt on the visionOS mDNS publisher:

- add bounded logging around `handleMdnsBroadcasts()`, `NWListener.start`,
  `stateUpdateHandler`, and `serviceRegistrationUpdateHandler` in the AVP
  client;
- confirm the signed app bundle includes `NSBonjourServices` for `_alvr._tcp`
  and add `NSLocalNetworkUsageDescription` if visionOS requires the prompt to
  publish Bonjour services;
- relaunch the AVP client while browsing with `dns-sd -B _alvr._tcp local` and
  record whether `ALVR Apple Vision Pro` appears;
- once the service appears, confirm the Windows ALVR session records the client
  as untrusted, trust it through the Windows streamer session, and rerun the
  AVP launch;
- if client connection starts, capture the first encoder/compositor/decode
  failure and only then decide whether the native macOS encoder shim is the
  right next implementation target.

Keep the signing assets above as the active probe signing configuration unless a
more permanent bundle ID strategy is chosen.

## Runtime Evidence To Capture Next

- Successful `xcodebuild` device build log.
- `devicectl` or Xcode install result.
- ALVR nightly streamer release evidence for
  `v21.0.0-dev12+nightly.2026.06.16`.
- Bonjour discovery evidence for `_alvr._tcp` while the AVP client is running.
- Windows ALVR Dashboard, API, or `session.json` evidence showing the AVP client
  before/after trust.
- Device log filtered to `ALVRClient` during connection.
- Streamer log covering discovery, protocol check, stream start, video decode,
  tracking, and first failure if any.
- Final verdict: pairing and first video decode `alive`, `dead`, or `blocked`.
