# Space Engineers 2 – Command-line Arguments

This document lists the **managed** command-line options parsed by
`SpaceEngineers2`'s .NET entry point (the `Keen.Game2.Program` and
`Keen.Game2.GameApp` types) in the current Game2 build.

It is based on a decompile of the managed assemblies inside the
CrossOver "Space Engineers" bottle (Game2 and the supporting VRage
render assemblies). No game binaries are modified by this document.

---

## Command-line arguments (current build)

Below are the managed CLI switches found in the current build (2025-12-09, Space Engineers 2 v2.0.2.39) after re-decompiling the shipped assemblies. Only switches that are still present are listed.

### App data & crash tagging

- `-appData:<path>` — parsed in `Program.Main`; sets `CoreSettings.CustomUserDataPath`.
- `-startupTag:<string>` — parsed in `CrashReportingSetup`; tags crash reports/analytics.

### Projects & global content directories

- `-projectPaths:<p1;...>` — parsed in `GameApp.GetProjects`; replaces default content project list.
- `-globalProjectDirs:<p1;...>` — parsed in `GameApp.TryGetGlobalProjectDirs`; adds global search paths for mods/content.

### World / session selection

- `-start:<idOrName>` — parsed in `StartPlayerExperienceAsync`; loads the specified save/world.
- `-startContent:<nameOrId>` — parsed in `StartPlayerExperienceAsync`; loads a content container/default world.
- `-startLast` — parsed in `StartPlayerExperienceAsync`; loads the latest game if present.

### Physics / simulation debug

- `-measurePhysics` — parsed in Havok physics config; enables physics profiling.
- `-disableLodding` — parsed in physics config; disables physics LOD.

### Windowing, display, and VSync

- `-fullscreen` — parsed in render setup; forces fullscreen.
- `-windowed` — parsed in render setup; forces windowed (defaults to 1600x900 if no resolution flag).
- `-vrr` — parsed in render setup; enables VRR VSync (fullscreen only).
- `-enablevsync` — parsed in render setup; classic VSync on.
- `-disablevsync` — parsed in render setup; VSync off.
- `-resolution:<W>x<H>` — parsed in render setup; sets resolution (windowed default 1600x900).

### Render overrides

- `-forceAllAdaptersSupported` — ArgSwitch in `RenderConfiguration`; forces all adapters treated as supported (suppresses GPU gate on Wine/Metal).

### Asset journal / content

- `-noAssetJournal` — ArgSwitch in `AssetJournalComponentCoreConfiguration`; disables asset journal.

### Game core startup

- `-noNextFrameHotfix` — ArgSwitch in `GameCoreConfiguration`; disables the next-frame hotfix.
- `-dumpJobs` — ArgSwitch in `GameCoreConfiguration`; enables job dumping.
- `-defaultWorld` — ArgSwitch in `GameCoreConfiguration`; starts default world.
- `-startWithSpectator` — ArgSwitch in `GameCoreConfiguration`; spawns spectator on start.
- `-noPillars` — ArgSwitch in `GameCoreConfiguration`; disables pillar spawning.

### UI / client presentation

- `-hideVersion` — ArgSwitch in `Game2.Client` UI; hides version string in UI overlays (two aliases to same flag).

---

## Platform / UI behavior

- `-hiddenWindow`
  - **Form:** bare flag
  - **Parsed in:** platform configuration
    (`PlatformObjectBuilder.HiddenWindow`)
  - **Effect:** Creates the game window in a hidden state. Primarily
    useful for automated / headless runs.

- `-nosplash`
  - **Form:** bare flag
  - **Parsed in:** platform configuration
    (`PlatformObjectBuilder.ShowSplashScreen`)
  - **Effect:** Disables the splash screen when launching, unless the
    app is in crash-report-only mode.

---

## Scripting, batch runs, and instance index

- `-loadScripts`
  - **Form:** bare flag
  - **Parsed in:** main engine setup in `GameApp`
  - **Effect:** Enables the scripting pipeline by calling
    `AddScripting(engineBuilder)`, which wires up script/mod
    code providers and whitelists.

- `-batchIndex:<n>`
  - **Form:** `flag:value` (where `<n>` is a non-negative integer)
  - **Parsed in:** `GameApp.TryGetBatchIndexFromArgs`
  - **Effect:** Sets `GameAppComponent.InstanceIndex` for batched or
    multi-instance scenarios. Invalid values trigger an assertion.

---

## Replay, recording, and automation

- `-enableRecording`
  - **Form:** bare flag
  - **Parsed in:** `GameApp.PostEngineInit`
  - **Effect:** Starts the game with the replay recorder enabled
    (`ReplayRecorderStartupState.Enabled`) and shows a warning dialog
    advising not to manually save while recording.

- `-automatedReplay`
  - **Form:** bare flag
  - **Parsed in:** `PostEngineInit`
  - **Effect:** Starts in automated replay mode
    (`ReplayRecorderStartupState.Automated`), intended for QA /
    scripted replay runs.

---

## Render presets, resolution, and quality (PresetArgsParseUtils)

These options are parsed by `Keen.VRage.Render.Utils.PresetArgsParseUtils`
and consumed from `GameApp.AddRender12` when configuring
`ForcedDisplayOptionsConfigurationObjectBuilder` and the
`Render12ObjectBuilder.ForcedSettings`.

- `-resolution:<width>x<height>`
  - **Form:** `flag:value` where `<width>` and `<height>` are integers,
    e.g. `-resolution:1920x1080`.
  - **Parsed in:**
    - `PresetArgsParseUtils.TryGetResolutionFromArgs(string[] args)`
    - Used by `GameApp.AddRender12` to set
      `ForcedDisplayOptionsConfigurationObjectBuilder.Resolution`.
  - **Effect:** Overrides the display resolution used by the game. In
    windowed mode, if this flag is not provided, the game defaults to
    `1600x900`.

- `-textureQuality:<Low|Medium|High>`
  - **Form:** `flag:value` where the value is one of the
    `RenderOptions.Texture` enum names (`Low`, `Medium`, `High`).
  - **Parsed in:**
    - `PresetArgsParseUtils.TryGetTextureQualityFromArgs(string[] args)`
    - Result assigned to
      `ForcedSettings.TextureQuality` in `GameApp.AddRender12`.
  - **Effect:** Forces the texture quality preset independent of the
    main render preset.

- `-renderPreset:<Low|Medium|High|Extreme|Custom>`
  - **Form:** `flag:value` where the value is one of the
    `RenderOptions.Preset` enum names (`Low`, `Medium`, `High`,
    `Extreme`, `Custom`).
  - **Parsed in:**
    - `PresetArgsParseUtils.TryGetRenderPresetFromArgs(string[] args)`
    - Result assigned to `ForcedSettings.Preset` in
      `GameApp.AddRender12`.
  - **Effect:** Selects the overall render preset that controls a
    bundle of options (AA mode, shadows, terrain detail, etc.).

- `-visualPreference:<Performance|Quality>`
  - **Form:** `flag:value` where the value is one of the
    `RenderOptions.VisualPreference` enum names (`Performance`,
    `Quality`).
  - **Parsed in:**
    - `PresetArgsParseUtils.TryGetVisualPreferenceFromArgs(string[] args)`
    - Result assigned to `ForcedSettings.VisualPreference` in
      `GameApp.AddRender12`.
  - **Effect:** Biases the renderer toward either higher performance or
    higher visual quality, influencing how some presets are applied.
