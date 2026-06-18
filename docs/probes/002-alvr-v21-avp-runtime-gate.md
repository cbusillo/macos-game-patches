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
  `~/Developer/_probe-alvr-v21-avp/alvr-visionos`
- Current stable source workspace:
  `~/Developer/alvr-visionos`
- `alvr-org/alvr-visionos`: `301b9285073949033727baab2d556fe9e8620612`
- `alvr-org/ALVR`: `d9f2b19d2b98b9d70411439fef83300c84ed171d`
- ALVR version: `21.0.0-dev12`
- Patch artifact:
  `patches/alvr-visionos/alvr-v21-client-core-abi.patch`
- Matching streamer release:
  `v21.0.0-dev12+nightly.2026.06.16`
- Apple Vision Pro device visible to Xcode as `Apple Vision Pro`.
- The v21 ABI patch from Probe 001 is already applied in the scratch checkout.

Device, team, signing identity, bundle ID, App Group, and local network values
are intentionally redacted in this public ledger. Use local environment values
for the placeholders shown in commands below.

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
  -destination 'platform=visionOS,id=<VISION_PRO_DESTINATION_ID>'
```

Results:

- Project exposes `ALVRClient` and `ALVREyeBroadcast` targets.
- Project exposes `ALVRClient` and `ALVREyeBroadcast` schemes.
- Xcode sees a physical `Apple Vision Pro` visionOS destination.
- Project default signing team was present, but is redacted from this public
  ledger.
- Project default bundle IDs are `alvr.client` and
  `alvr.client.ALVREyeBroadcast`.
- Entitlements include App Group `group.alvr.client.ALVR` on both targets.
- Main app entitlement also includes
  `com.apple.developer.low-latency-streaming`.

## Device Build Evidence

The v21 Rust client core and xcframework were rebuilt successfully:

```bash
cd ~/Developer/_probe-alvr-v21-avp/alvr-visionos
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
  -destination 'platform=visionOS,id=<VISION_PRO_DESTINATION_ID>'
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
  -destination 'platform=visionOS,id=<VISION_PRO_DESTINATION_ID>' \
  -allowProvisioningUpdates
```

Result:

- No account for the project-default Apple Developer team in that Xcode
  environment.
- No matching development profiles for `alvr.client` or
  `alvr.client.ALVREyeBroadcast`.

Automatic provisioning with an available local Apple Developer team and a unique
probe bundle ID advanced past account lookup, then failed on entitlements:

```bash
xcodebuild build \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'platform=visionOS,id=<VISION_PRO_DESTINATION_ID>' \
  -allowProvisioningUpdates \
  DEVELOPMENT_TEAM=<APPLE_DEVELOPMENT_TEAM_ID> \
  PRODUCT_BUNDLE_IDENTIFIER=<PROBE_APP_BUNDLE_ID>
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

Signing was unblocked by using the active local Apple Developer team, unique
probe bundle IDs, and a matching probe App Group:

- Main app bundle ID: `<PROBE_APP_BUNDLE_ID>`
- Broadcast extension bundle ID: `<PROBE_BROADCAST_BUNDLE_ID>`
- App Group: `<PROBE_APP_GROUP_ID>`
- Main app capabilities: App Groups and Low-Latency Streaming
- Broadcast extension capabilities: App Groups

The bundle IDs and capabilities were created through the App Store Connect API;
Xcode then generated development provisioning profiles for both targets during
the device build.

Device build from the stable source workspace succeeded:

```bash
cd ~/Developer/alvr-visionos
xcodebuild build \
  -project ALVRClient.xcodeproj \
  -scheme ALVRClient \
  -configuration Debug \
  -destination 'platform=visionOS,id=<VISION_PRO_DESTINATION_ID>' \
  -allowProvisioningUpdates
```

Result:

- Build succeeded for physical `xros`.
- Xcode signed with a local Apple Development identity.
- Main app and broadcast extension provisioning profiles were generated for the
  redacted probe bundle IDs.
- The build emitted a non-fatal warning that the broadcast extension
  `CFBundleVersion` (`1`) does not match the containing app (`3`).

Install and launch also succeeded:

```bash
xcrun devicectl device install app \
  --device <VISION_PRO_DEVICE_ID> \
  <DERIVED_DATA>/Build/Products/Debug-xros/ALVRClient.app

xcrun devicectl device process launch \
  --device <VISION_PRO_DEVICE_ID> \
  --terminate-existing \
  --timeout 20 \
  <PROBE_APP_BUNDLE_ID>
```

Result:

- App installed with the probe bundle ID.
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
cd ~/Developer/alvr
git submodule update --init --recursive
cargo xtask build-streamer --platform macos
```

Result:

- `alvr_server_openvr v21.0.0-dev12` built for macOS.
- `alvr_dashboard v21.0.0-dev12` built for macOS.
- The streamer output was created at
  `~/Developer/alvr/build/alvr_streamer_macos`.
- The build emitted C++ warnings, but completed successfully.

The macOS dashboard launched successfully:

```bash
~/Developer/alvr/build/alvr_streamer_macos/alvr_dashboard
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
  --device <VISION_PRO_DEVICE_ID> \
  --terminate-existing \
  --timeout 30 \
  --console \
  <PROBE_APP_BUNDLE_ID>
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
  --device <VISION_PRO_DEVICE_ID> \
  --terminate-existing \
  --timeout 60 \
  --console \
  <PROBE_APP_BUNDLE_ID>
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
  --device <VISION_PRO_DEVICE_ID> \
  --terminate-existing \
  --timeout 60 \
  --console \
  <PROBE_APP_BUNDLE_ID>
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

## Live AVP Discovery And Trust Breakthrough

A later coordinated run resolved the discovery/trust boundary that blocked the
previous attempt. The user watched the Vision Pro for local-network permission
prompts and the CrossOver ALVR dashboard for the client trust prompt while the
server was running.

The AVP client appeared in the Windows ALVR session, was trusted through the
dashboard, and reached `Streaming`:

```json
{
  "6964.client.local.alvr": {
    "display_name": "Unknown",
    "current_ip": "<AVP_LAN_IP>",
    "manual_ips": ["<AVP_LAN_IP>"],
    "trusted": true,
    "connection_state": "Streaming"
  }
}
```

Socket evidence during the same run showed ALVR control and stream transport
from CrossOver to the AVP:

```text
vrserver.exe -> <AVP_LAN_IP>:9943 (TCP ESTABLISHED)
vrserver.exe -> <AVP_LAN_IP>:9944 (UDP)
```

The user clicked Enter in the AVP client and entered a wireframe view. The ALVR
session then alternated between `Streaming`, `Disconnecting`, and
`Disconnected`, with the TCP control socket closing and then reconnecting while
UDP stream state lingered or resumed.

This establishes a new boundary: mDNS discovery, manual trust, ALVR control
transport, and ALVR stream socket setup are alive. The remaining runtime failure
is past client discovery.

## CrossOver SteamVR Compositor Failure

SteamVR's Windows compositor failed repeatedly inside the CrossOver bottle. The
live process list contained `vrserver.exe` and `vrmonitor.exe`, but no stable
`vrcompositor.exe`. `vrcompositor.txt` recorded repeated startup failures:

```text
vrcompositor 2.16.6 startup ...
VR compositor 2.16.6 ... Mixed starting up
Headset is using driver direct mode
GPU Vendor: "AMD Compatibility Mode" GPU Driver: "Unknown"
Failed to create shared frame info constant buffer!
Failed to init graphics device
Failed to initialize compositor
Failed to start compositor: VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer
```

`vrstartup.txt` also reported:

```text
VR_Init error, exiting: Shared IPC Compositor Connect Failed (306)
```

`vrserver.txt` was dominated by repeated ALVR/OpenVR event errors during the
same window:

```text
VendorSpecificEvent 111 outside of reserved range
```

ALVR's local `crash_log.txt` also emitted repeated Wine audio-capture errors:

```text
Audio record error: A backend-specific error has occurred: Not implemented. (0x80004001)
```

The audio errors are noisy but are not the primary video blocker. The compositor
failure means SteamVR is not producing stable rendered scene frames for ALVR to
encode. The AVP wireframe view is therefore best treated as a placeholder or
fallback state reached after transport setup, not proof that real SteamVR frames
are being encoded and decoded.

An isolated Option A recheck was started by copying the `Steam` CrossOver bottle
to a temporary `SteamVRProbe` bottle before rerunning SteamVR. The copy consumed
the remaining local disk space during CrossOver bottle finalization, leaving the
probe bottle unable to save registry state or load `kernel32.dll` reliably:

```text
wineserver: could not save registry branch to system.reg : No space left on device
wine: could not load kernel32.dll, status c0000135
```

The failed temporary bottle was deleted. This attempt does not change the
compositor verdict; the next isolated recheck should first ensure substantially
more free disk space than the source bottle size, or use a slimmer SteamVR-only
bottle.

A second isolated Option A recheck used external storage for the probe bottle:

```text
~/Library/Application Support/CrossOver/Bottles/SteamVRProbe
  -> $CROSSOVER_BOTTLE_OUT/SteamVRProbe
```

The `Steam` bottle was copied with `rsync -aHAX`, CrossOver recognized the
symlinked bottle as `Status=uptodate`, and Wine booted successfully in the
external bottle:

```text
Microsoft Windows 10.0.19045
```

SteamVR then launched from the external `SteamVRProbe` bottle. `vrserver.exe`
and `vrmonitor.exe` stayed alive long enough to collect fresh logs, but
`vrcompositor.exe` repeatedly exited with the same D3DMetal shared-frame
failure:

```text
vrcompositor 2.16.6 startup ...
CGraphicsDevice Init...
Headset is using driver direct mode
GPU Vendor: "AMD Compatibility Mode" GPU Driver: "Unknown"
Creating constant buffers
Failed to create shared frame info constant buffer!
Failed to init graphics device
Failed to initialize compositor
Failed to start compositor: VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer
```

`vrstartup.txt` again reported:

```text
VR_Init error, exiting: Shared IPC Compositor Connect Failed (306)
```

This validates that the current CrossOver 26.2 / D3DMetal / GPTK 4.0 probe path
still reaches the same SteamVR compositor boundary when isolated from the
primary Steam bottle. The external probe bottle remains useful for follow-up
backend or SteamVR-version experiments.

The external `SteamVRProbe` bottle was then used for a backend comparison:

- `CX_GRAPHICS_BACKEND=d3dmetal`: `vrcompositor.exe` created a device and
  shaders, then failed at `Failed to create shared frame info constant buffer!`.
- `CX_GRAPHICS_BACKEND=dxvk`: `vrcompositor.exe` reported
  `GPU Vendor: "Apple M4 Max" GPU Driver: "35.0.10.1000"`, hit a GPU
  measurement MSAA texture allocation warning, then failed at the same shared
  frame-info constant buffer.
- no explicit `CX_GRAPHICS_BACKEND`: the fallback path again reported
  `GPU Vendor: "AMD Compatibility Mode" GPU Driver: "Unknown"` and failed at
  the same shared frame-info constant buffer.

The backend comparison makes the failure look like a SteamVR direct-mode
compositor shared-resource requirement rather than a simple D3DMetal toggle or
ALVR transport issue. DXVK changes adapter identity and an earlier texture
allocation warning, but it does not get past the shared constant-buffer step.

A follow-up control forced SteamVR's built-in `null` driver in the external
`SteamVRProbe` bottle. This made the active HMD `null.Null Serial Number` and
blocked ALVR's HMD because it did not match the temporary `forcedDriver`
setting:

```text
null: driver_null: Render Target: 1512 1680
Active HMD set to null.Null Serial Number
Can't add device alvr_server.1WMHH000X00000: Does not match user setting
  "forcedDriver" for the HMD
```

The compositor still started and failed at the same point, but the log changed
from ALVR direct mode to the null driver's desktop-display path:

```text
Forcing debug mode for null driver because of Prop_DisplayDebugMode_Bool.
CGraphicsDevice Init...
Headset display is on desktop
Creating swap chain - Format=28
Creating constant buffers
Failed to create shared frame info constant buffer!
Failed to init graphics device
Failed to start compositor: VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer
```

This control strongly reduces the likelihood that the compositor failure is
caused by ALVR's v21 OpenVR driver contract. The same SteamVR compositor
shared-resource failure appears with SteamVR's own null HMD.

A small local-only D3D11 probe was then compiled with MinGW and run from
external storage inside the same `SteamVRProbe` bottle. Under the default
D3DMetal path, D3D11 device creation and resource creation succeeded, including
resources created with `D3D11_RESOURCE_MISC_SHARED` and
`D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX`. Exporting a shared handle failed:

```text
D3D11CreateDevice                          OK hr=0x00000000
Feature level: 0xb100
constant buffer + SHARED                   OK hr=0x00000000
  QueryInterface IDXGIResource             OK hr=0x00000000
  GetSharedHandle                          FAIL hr=0x80004001
  QueryInterface IDXGIResource1            FAIL hr=0x80004002
constant buffer + SHARED_NTHANDLE          OK hr=0x00000000
  QueryInterface IDXGIResource             OK hr=0x00000000
  GetSharedHandle                          FAIL hr=0x80004001
  QueryInterface IDXGIResource1            FAIL hr=0x80004002
Texture2D + SHARED                         OK hr=0x00000000
  QueryInterface IDXGIResource             OK hr=0x00000000
  GetSharedHandle                          FAIL hr=0x80004001
  QueryInterface IDXGIResource1            FAIL hr=0x80004002
Texture2D + SHARED_KEYEDMUTEX              OK hr=0x00000000
  QueryInterface IDXGIResource             OK hr=0x00000000
  GetSharedHandle                          FAIL hr=0x80004001
  QueryInterface IDXGIResource1            FAIL hr=0x80004002
Texture2D + SHARED_NTHANDLE                OK hr=0x00000000
  QueryInterface IDXGIResource             OK hr=0x00000000
  GetSharedHandle                          FAIL hr=0x80004001
  QueryInterface IDXGIResource1            FAIL hr=0x80004002
```

`0x80004001` is `E_NOTIMPL`, and `0x80004002` is `E_NOINTERFACE`. The same
probe produced the same shared-handle failures when launched with
`CX_GRAPHICS_BACKEND=dxvk` in this bottle. That means the simple backend toggle
does not currently restore the shared-handle export path SteamVR appears to
need. The evidence now points more specifically at missing or unavailable D3D11
cross-process shared-handle support, not ordinary D3D11 resource creation.

The remaining nuance was that a modern DXVK path may prefer
`D3D11_RESOURCE_MISC_SHARED_NTHANDLE` plus `IDXGIResource1::CreateSharedHandle`
instead of the older `IDXGIResource::GetSharedHandle` path. The local probe did
exercise `D3D11_RESOURCE_MISC_SHARED_NTHANDLE`, but `IDXGIResource1` was not
available in the currently exercised CrossOver path.

That nuance was checked with an official DXVK 2.7.1 release package. The
downloaded `dxvk-2.7.1.tar.gz` matched GitHub's release digest:

```text
d85ce7c79f57ecd765aaa1b9e7007cb875e6fde9f6d331df799bce73d513ce87
```

The x64 `d3d11.dll` and `dxgi.dll` files were staged next to the probe executable
on external storage and again inside `C:\dxvk-decider` in the `SteamVRProbe`
bottle. The probe was then updated to print loaded module paths, and the final
run with `WINEDLLOVERRIDES='d3d11,dxgi=n'` confirmed those colocated DLLs were
active:

```text
Loaded d3d11.dll                        C:\dxvk-decider\d3d11.dll
Loaded dxgi.dll                         C:\dxvk-decider\dxgi.dll
```

That verified-DXVK run still produced the same results: `CreateBuffer` and
`CreateTexture2D` succeeded, `GetSharedHandle` returned `E_NOTIMPL`, and
`IDXGIResource1` remained unavailable even for
`D3D11_RESOURCE_MISC_SHARED_NTHANDLE` resources. CrossOver's own
`CX_GRAPHICS_BACKEND=dxvk` SteamVR path had also changed adapter identity to
`Apple M4 Max` and still failed at the same shared frame-info constant buffer.

The practical conclusion is that every currently available local CrossOver path
remains red for the shared-handle requirement. Reviving Option A now depends on
proving and enabling working DXGI external-memory support in the translation
layer itself, not on ALVR v21 source changes or ordinary SteamVR settings.

## ALVR v21 Shared-Memory Encoder Recheck

A source-level v21 encoder-boundary patch was then tested to answer one last
Option A question: whether CrossOver SteamVR can reach ALVR's encoder path if
ALVR bypasses Windows hardware encoders and hands frames to a native macOS
VideoToolbox bridge.

The patched ALVR source branch was built from macOS with `cargo-xwin` and staged
as a separate CrossOver driver folder:

```text
C:\ALVR\v21.0.0-dev12-macos-shm-local.2026.06.17\bin\win64\driver_alvr_server.dll
```

SteamVR was registered to use only that patched `alvr_server` driver. The
native macOS bridge was started in `shared-memory` mode, then CrossOver SteamVR
was launched with:

```text
ALVR_MACOS_SHM_ENCODER=1
```

The first run loaded the patched driver but returned `Hmd Not Found` before the
encoder path. The branch was adjusted so `ALVR_MACOS_SHM_ENCODER=1` also forces
ALVR's early HMD registration path. The second run confirmed this got SteamVR
past HMD discovery:

```text
Driver 'alvr_server' started activation of tracked device with serial number '1WMHH000X00000'
Loaded server driver alvr_server ... C:\ALVR\v21.0.0-dev12-macos-shm-local.2026.06.17\bin\win64\driver_alvr_server.dll
Active HMD set to alvr_server.1WMHH000X00000
Starting vrcompositor process: C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrcompositor.exe
```

That was the useful positive result: the patched source path can make ALVR the
active SteamVR HMD inside CrossOver without launching a separate Windows ALVR
Dashboard owner.

The negative result is decisive for Option A. `vrcompositor.exe` still failed
before any frame reached ALVR's new shared-memory encoder boundary:

```text
Creating constant buffers
Failed to create shared frame info constant buffer!
Failed to init graphics device
Failed to initialize compositor
Failed to start compositor: VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer
```

At the same time, the native bridge remained at:

```text
still waiting for shared-memory producer config
```

So the shared-memory ALVR patch removed the HMD-registration ambiguity but did
not get real Windows frames. The remaining failure is still SteamVR compositor
initialization inside CrossOver, before `VideoEncoderSharedMem::Transmit` can
publish producer config or BGRA frames to `/tmp/alvr_frame_buffer.shm`.

## D3D11 Readback Cost Probe

Because Apple Silicon uses unified physical memory for CPU and GPU access, the
next question was whether a pre-compositor Option B path could afford a D3D11
texture readback under CrossOver/D3DMetal and then hand BGRA frames to the
native macOS ALVR bridge.

A small D3D11 probe was added at `tools/d3d11_readback_probe.cpp`, built as a
static Windows executable with MinGW, staged into the CrossOver `Steam` bottle,
and run through `cxstart`:

```bash
x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
  -static-libstdc++ tools/d3d11_readback_probe.cpp \
  -ld3d11 -ldxgi -lole32 \
  -o $PROBE_OUT/d3d11_readback_probe.exe

/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart \
  --bottle Steam \
  --no-gui \
  'C:\alvr-probes\d3d11_readback_probe.exe' \
  --width 4096 \
  --height 2048 \
  --frames 150 \
  --warmup 30 \
  --out 'C:\alvr-probes\readback_4096x2048.csv'
```

The probe created a BGRA8 D3D11 render target, copied it to a CPU-readable
staging texture, mapped the staging texture, and copied the full mapped frame
row-by-row. The default mode copies into a heap buffer; `--no-heap-copy` copies
the same full frame into a reusable scratch buffer so it models a direct
full-frame write path without per-frame heap allocation. The CrossOver adapter
reported by D3D11 was:

```text
Adapter: AMD Compatibility Mode
FeatureLevel=0xb100
DedicatedVideoMemory=115448725504 SharedSystemMemory=115448725504
```

Measured results:

- `640x360`, 0.92 MB, heap copy: total mean 0.674 ms, p99 1.073 ms,
  max 1.095 ms, effective 1482.58 FPS.
- `1280x720`, 3.69 MB, heap copy: total mean 1.026 ms, p99 1.320 ms,
  max 1.735 ms, effective 974.86 FPS.
- `2144x2048`, 17.56 MB, heap copy: total mean 2.928 ms, p99 3.764 ms,
  max 3.922 ms, effective 341.57 FPS.
- `4096x2048`, 33.55 MB, heap copy: map mean 8.191 ms, read mean
  1.129 ms, total mean 9.323 ms, p99 21.284 ms, max 24.196 ms,
  effective 107.26 FPS.
- `4096x2048`, 33.55 MB, direct-write model: map mean 4.918 ms,
  read mean 0.797 ms, total mean 5.718 ms, p99 9.009 ms, max 9.345 ms,
  effective 174.88 FPS.
- `4288x2048`, 35.13 MB, heap-copy stress case outside the current
  4096x2048 shared-memory ABI limit: total mean 5.760 ms, p99 6.695 ms,
  max 7.232 ms.

Interpretation:

- The expensive part is `Map`, which is where queued GPU work, transfer, and
  synchronization collapse on the immediate D3D11 context. The separate `clear`
  and `copy` timings only measure command submission, not completed GPU work.
- At the current 4096x2048 shared-memory ABI limit, the full heap-copy path
  averaged below the 90 Hz 11.1 ms frame interval, but p99 and max exceeded it:
  9.32 ms mean, 21.28 ms p99, 24.20 ms max. That consumes too much of a 90 Hz
  frame budget once game rendering, conversion, encode, network, and AVP decode
  are included.
- Avoiding an intermediate heap buffer and reusing the destination storage is
  important. The direct-write model still copies the full frame row-by-row, but
  improved 4096x2048 timing to 5.72 ms mean, 9.01 ms p99, and 9.35 ms max.
  This is the result closest to a shared-memory writer that copies mapped D3D
  rows directly into the ring slot.
- This does not prove a real game path is fast enough after game rendering,
  capture interposition, color conversion, VideoToolbox encode, network, and AVP
  decode are included. A real game will also contend with active rendering and
  can suffer pipeline stalls from synchronous `Map`. It does prove that
  CrossOver/D3DMetal D3D11 staging readback is not obviously disqualifying on
  this Apple Silicon machine.
- The probe uses one staging texture and fully synchronous readback, so a
  double-buffered bridge may improve scheduling, but that needs a real-frame
  end-to-end test rather than more synthetic extrapolation.

This keeps the Option B CPU-readback bridge worth prototyping before spending
time on a much harder GPU external-memory or IOSurface path.

## Current Verdict

`alive` - the v21 AVP client can be signed, installed, launched,
discovered, trusted, driven into ALVR `Streaming`, and fed VideoToolbox HEVC from
a native macOS bridge. Option A remains blocked: Windows SteamVR's compositor
still fails inside CrossOver with
`VRInitError_Compositor_CreateSharedFrameInfoConstantBuffer`, so the direct
Windows SteamVR compositor-to-ALVR encoder path should stay parked unless the
translation layer changes.

The productive path is now Option B. A CrossOver-side app-local OpenVR `Submit`
shim captured real SteamVR Tutorial D3D11 eye textures, wrote paired BGRA frames
to `/tmp/alvr_frame_buffer.shm`, and the native macOS bridge delivered those
frames to AVP. Protocol-v2 timing showed that D3D readback is not currently the
largest problem; the native bridge's scalar BGRA-to-I420 conversion measured
around 36-37 ms/frame and is now the main bottleneck.

## Next Action

Continue the native v21 bridge track and replace or bypass the scalar
BGRA-to-I420 conversion before more stereo-comfort tuning. Preferred first spike:
use Accelerate/vImage to convert BGRA to NV12 and feed VideoToolbox through a
pooled `CVPixelBuffer` path. If that is still too slow or too jittery, test
direct 32BGRA VideoToolbox input or a Metal conversion pass.

Keep the local signing assets represented by the redacted placeholders as the
active probe signing configuration unless a more permanent bundle ID strategy is
chosen. Do not commit the concrete Apple account, device, bundle, or App Group
values to this public repository.

## Runtime Evidence To Capture Next

- Lower-latency native bridge pixel path benchmark for the current 2560x720
  side-by-side SteamVR Tutorial smoke.
- Bridge timing logs for read, conversion, encode submit/output, and stream
  cadence after the pixel-path change.
- AVP display confirmation after the conversion path changes.
- Updated verdict on whether the Option B frame path can plausibly fit the
  rough <=20 ms comfort target for this stage.
