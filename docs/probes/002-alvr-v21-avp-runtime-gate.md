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

## Current Verdict

`partially alive` - the patched v21 Rust client core builds, the physical
Apple Vision Pro device build succeeds, the app installs, and `devicectl` can
launch it. Pairing with the matching ALVR v21 streamer and first video decode
remain untested.

## Next Action

Run the matching ALVR v21 streamer, launch the AVP client, and capture pairing
plus first video-decode evidence. Keep the signing assets above as the active
probe signing configuration unless a more permanent bundle ID strategy is
chosen.

## Runtime Evidence To Capture After Signing Is Fixed

- Successful `xcodebuild` device build log.
- `devicectl` or Xcode install result.
- ALVR nightly streamer release evidence for
  `v21.0.0-dev12+nightly.2026.06.16`.
- ALVR Dashboard screenshot showing the AVP client before/after trust.
- Device log filtered to `ALVRClient` during connection.
- Streamer log covering discovery, protocol check, stream start, video decode,
  tracking, and first failure if any.
- Final verdict: pairing and first video decode `alive`, `dead`, or `blocked`.
