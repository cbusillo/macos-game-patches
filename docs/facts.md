# Verified Facts

As of February 14, 2026.

## Platform Facts

- SteamVR store system requirements list Windows, not macOS.
  Source: <https://store.steampowered.com/app/250820/SteamVR/>
- ALVR upstream support matrix marks macOS host as unsupported.
  Source: <https://github.com/alvr-org/ALVR>
- ALVR has a separate visionOS client repository.
  Source: <https://github.com/alvr-org/alvr-visionos>
- ALVR has an App Store listing for visionOS.
  Source: <https://apps.apple.com/au/app/alvr/id6479728026>
- Steam Link App Store metadata includes Apple Vision compatibility.
  Source: <https://apps.apple.com/us/app/steam-link/id1246969117>

## Engineering Constraints

- The project requirement is hardware H.265 encode only.
- Software encoding is considered a hard fail.
- Runtime compatibility and encoding should be treated as separate subsystems.

## Notes

- App Store compatibility metadata and local availability can differ by region,
  account, or storefront behavior. Treat device-side visibility as runtime truth.
