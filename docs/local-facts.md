# Local Facts

As of March 22, 2026.

## Primary Host

- Model: Mac Studio M4 Max
- Role: primary runtime and development host
- Target headset: Apple Vision Pro (M2)

## CrossOver Runtime Paths

- Bottle name: `Steam`
- Bottle root:
  - `~/Library/Application Support/CrossOver/Bottles/Steam`
- SteamVR executable path (relative to bottle root):
  - `drive_c/Program Files (x86)/Steam/steamapps/common/SteamVR/bin/`
  - `win64/vrstartup.exe`
- SteamVR settings path (relative to bottle root):
  - `drive_c/Program Files (x86)/Steam/config/`
  - `steamvr.vrsettings`
- SteamVR logs path (relative to bottle root):
  - `drive_c/Program Files (x86)/Steam/logs`

## Observed Runtime Ports

- `vrlink` observed listening on:
  - `UDP 10400`
  - `TCP 10440`

## SteamVR + ALVR Bring-Up Findings

- ALVR driver now loads in SteamVR under CrossOver when runtime side-by-side
  DLLs are present in:
  - `drive_c/Program Files (x86)/Steam/steamapps/common/SteamVR/drivers/alvr_server/bin/win64`
- Required runtime DLLs observed for current `driver_alvr_server.dll` build:
  - `openvr_api.dll`
  - `libvpl.dll`
- `vrserver` now reports an active ALVR HMD when early HMD initialization is
  enabled in forked ALVR build.
- `vrcompositor` now reaches `Startup Complete` under ALVR-forced runs after
  local compatibility patches.
- Current compositor failure mode under ALVR is a runtime crash after startup:
  - `Exception c0000005`
- Direct-mode-on runs still hit ALVR texture sharing failure:
  - `CreateSwapTextureSet GetSharedHandle ... Not implemented.`
- Current SteamVR UI status under ALVR is:
  - `Headset Error (-202)`
  - `SteamVR Fail (-203)`
  - `SteamVRSystemState_NotReady`
- App Container warning remains cosmetic under CrossOver:
  - `DismissableWarning_EnableAppContainers`

## ClearXR on macOS Host

- Current host-side ClearXR live validation can reach:
  - session-management listener
  - QR pairing / authorization
  - `WAITING`
  - `MediaStreamIsReady`
- Current host-side ClearXR live validation does **not** provide a real media
  backend on this Mac:
  - `clearxr-streamer` falls back to the native macOS placeholder backend when
    the vendored CloudXR runtime is unavailable.
  - That fallback can validate pairing and control-plane flow, but real Vision
    Pro media startup is not expected to succeed on this host.
- Current observed post-pairing headset failure after QR authorization is:
  - `The operation couldn't be completed. 0x800B1004`
- Current vendor/runtime state in this repo:
  - `temp/external/clearxr-server/vendor/` does not currently contain a usable
    staged CloudXR runtime under `Server/releases/...` for the macOS-hosted
    headless run.
  - Windows-target runtime artifacts do exist under the Windows build tree, but
    they are not a usable local media backend for this Mac-hosted validation.

## Optional Windows VM

- Alias: `winders`
- VM host node: `prox-main.shiny`
- VMID: `201`
- Verified from this Mac on March 22, 2026:
  - `ssh prox-main.shiny` works
  - `qm status 201` initially reported `stopped`
  - `qm start 201` succeeded
  - `qm guest cmd 201 ping` succeeded after boot
- DNS resolution: `winders.shiny -> 192.168.1.137`
- Direct SSH from this Mac to `gaming@winders` works.
- Direct PowerShell execution over `gaming@winders` works.
- SSH from `prox-main` to `winders` was previously observed timing out and is no
  longer required for the preferred validation path.
- QEMU guest agent path is verified working from `prox-main`:
  - `qm guest cmd 201 ping`
  - `qm guest cmd 201 get-host-name` -> `WINDERS`
  - `qm guest cmd 201 network-get-interfaces` includes `192.168.1.137`
  - `qm guest exec 201 cmd.exe /c echo QGA_OK` returns output successfully
- Guest toolchain/runtime checks are verified:
  - `C:\dev\ALVR` exists (`ALVR_REPO_OK`)
  - `C:\dev\clearxr-server` now contains a minimal staged ClearXR Windows
    bundle from this workspace, rooted at:
    `C:\dev\clearxr-server\clearxr-streamer\target\x86_64-pc-windows-gnu\debug`
  - That staged path is sufficient for headless ClearXR startup, but it is not
    a full `clearxr-server` source checkout.
  - `cargo` and `rustc` were previously observed present (`C:\ProgramData\chocolatey\bin\...`)
- Current Windows ClearXR backend behavior from this workspace:
  - `python3 tools/stage_clearxr_winders.py` refreshes the staged runnable
    bundle on `gaming@winders`.
  - A direct raw launch of `clearxr-streamer.exe --clearxr-headless` currently
    fails with `failed to load ...\cloudxr.dll` / `os error 126` unless the
    CloudXR release directory is added to `PATH` first.
  - `python3 tools/smoke_clearxr_winders.py` prepends
    `...\Server\releases\6.0.4` to `PATH`, then starts a real headless
    backend successfully.
  - Verified successful smoke outcome on March 22, 2026:
    - `Using vendored CloudXR runtime from C:\dev\clearxr-server\...\debug`
    - `Created CloudXR Service`
    - session-management listener started on `192.168.1.137:55000`
- Last-known details:
  - host: `winders`
  - IP: `192.168.1.137`
  - MAC: `BC:24:11:69:E1:67`

Constraint:

- `winders` is for optional validation only and is explicitly not a fallback
  path for project success.
