# Local Facts

As of February 17, 2026.

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

## Optional Windows VM

- Alias: `winders`
- VM host node: `prox-main.shiny`
- VMID: `201` (`qm list` shows `winders` as running)
- DNS resolution: `winders.shiny -> 192.168.1.137`
- Direct SSH from this Mac to `winders` currently times out.
- SSH from `prox-main` to `winders` currently times out.
- QEMU guest agent path is verified working from `prox-main`:
  - `qm guest cmd 201 ping`
  - `qm guest cmd 201 get-host-name` -> `WINDERS`
  - `qm guest cmd 201 network-get-interfaces` includes `192.168.1.137`
  - `qm guest exec 201 cmd.exe /c echo QGA_OK` returns output successfully
- Guest toolchain/repo checks via QGA are verified:
  - `C:\dev\ALVR` exists (`ALVR_REPO_OK`)
  - `cargo` and `rustc` are present (`C:\ProgramData\chocolatey\bin\...`)
- Last-known details:
  - host: `winders`
  - IP: `192.168.1.137`
  - MAC: `BC:24:11:69:E1:67`

Constraint:

- `winders` is for optional validation only and is explicitly not a fallback
  path for project success.
