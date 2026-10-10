# Security Policy

## Supported Versions

Security fixes target the current `main` branch. There are no separate
supported release lines.

## Reporting a Vulnerability

Report suspected vulnerabilities privately through GitHub's
[Report a vulnerability](https://github.com/cbusillo/macos-game-patches/security/advisories/new)
form. Do not open a public issue for a vulnerability.

Include the commit, the macOS version and hardware, the component, the
impact, and the smallest steps that reproduce it.

Do not send game files, license keys, Steam or other account credentials,
device identifiers, network addresses, or other personal data. Use redacted or
made-up values.

This is a single-maintainer project. Reports are handled on a best-effort
basis, and I aim to reply within seven days.

## Scope

Relevant reports include:

- the patched runtime or streaming path being reachable by devices other
  than the paired headset;
- scripts changing macOS security settings or system files beyond what the
  docs describe;
- unsafe handling of downloaded tools, patches, or build artifacts; and
- dependency or GitHub Actions supply-chain problems.

Problems in ALVR, DXVK, CrossOver, SteamVR, or a game should go to that
project unless a patch here makes them worse.
