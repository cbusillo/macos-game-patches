# Space Engineers 2 – Command-line Arguments

This document lists the **managed** command-line options parsed by
`SpaceEngineers2`'s .NET entry point (the `Keen.Game2.Program` and
`Keen.Game2.GameApp` types) in the current Game2 build.

It is based on a decompile of the managed assemblies inside the
CrossOver "Space Engineers" bottle (Game2 and the supporting VRage
render assemblies). No game binaries are modified by this document.

---

## App data & crash tagging

- `-appData:<path>`
  - **Form:** `flag:value`
  - **Parsed in:** `Keen.Game2.Program.Main`
  - **Effect:** Sets `CoreSettings.CustomUserDataPath` to `<path>`,
    overriding the default user data / logs / config location.

- `-startupTag:<string>`
  - **Form:** `flag:value`
  - **Parsed in:** `GameApp` engine setup
    (`CrashReportingSetup.StartUpTag`)
  - **Effect:** Tags crash reports / analytics for this run with the
    given string (useful for distinguishing experiment types).

---

## Projects & global content directories

- `-projectPaths:<path1;path2;...>`
  - **Form:** `flag:value` (semicolon-separated paths, quotes stripped)
  - **Parsed in:** `GameApp.GetProjects(string[] args)`
  - **Effect:** Replaces the default content project list. Each
    `<pathN>` is fed to `GameContent.GetProjectByPath` to build the
    project set.

- `-globalProjectDirs:<path1;path2;...>`
  - **Form:** `flag:value` (semicolon-separated paths, quotes stripped)
  - **Parsed in:** `GameApp.TryGetGlobalProjectDirs(string[] args)`
  - **Effect:** Adds directories to `LocalProjectLocator.GlobalSearchPaths`
    for locating projects and mods globally.

---

## World / session selection

- `-start:<idOrName>`
  - **Form:** `flag:value` (parsed via `"-start:(.+)"`)
  - **Parsed in:** `GameApp.StartPlayerExperienceAsync()`
  - **Effect:** Attempts to construct a `ContainerId` from `<idOrName>`
    and loads that save/world on startup.

- `-startContent:<nameOrId>`
  - **Form:** `flag:value` (parsed via `"-startContent:(.+)"`)
  - **Parsed in:** `StartPlayerExperienceAsync()`
  - **Effect:** Resolves a content container or default world to load
    on startup. It first tries `DefaultWorldsComponent` by name, then
    falls back to `SaveHelper.GetContentContainerIdString`.

- `-startLast`
  - **Form:** bare flag
  - **Parsed in:** `StartPlayerExperienceAsync()`
  - **Effect:** Tries to load the latest game from
    `GameOptions.LatestGame`. Logs a message if no previous save exists.

---

## Physics / simulation debug

- `-measurePhysics`
  - **Form:** bare flag
  - **Parsed in:** physics engine configuration
    (`HavokPhysicsEngineComponentObjectBuilder`)
  - **Effect:** Enables physics statistics/profiling
    (`MeasureStatistics = true`).

- `-disableLodding`
  - **Form:** bare flag
  - **Parsed in:** physics engine configuration
  - **Effect:** Disables physics LOD (`UseLodding = false`),
    increasing simulation detail at a performance cost.

---

## Windowing, display, and VSync

- `-fullscreen`
  - **Form:** bare flag
  - **Parsed in:** `GameApp.AddRender12`
  - **Effect:** Forces fullscreen mode via
    `ForcedDisplayOptionsConfigurationObjectBuilder.FullScreen = true`.
    Also sets the render display strategy to fullscreen with classic
    VSync enabled by default.

- `-windowed`
  - **Form:** bare flag
  - **Parsed in:** `AddRender12` (when `-fullscreen` is not present)
  - **Effect:** Forces windowed mode. Sets `FullScreen = false` and
    resolution from `PresetArgsParseUtils.TryGetResolutionFromArgs(_args)`
    or falls back to `1600x900`.

- `-vrr`
  - **Form:** bare flag
  - **Parsed in:** `AddRender12` (only meaningful with `-fullscreen`)
  - **Effect:** Enables variable refresh rate VSync:
    `VSync = VSyncMode.VariableRefreshRate`, adjusts display strategy
    flags to favor VRR over classic VSync.

- `-enablevsync`
  - **Form:** bare flag
  - **Parsed in:** `AddRender12` (fullscreen case)
  - **Effect:** Enables classic VSync (`VSync = VSyncMode.VSync`) and
    updates the display strategy accordingly.

- `-disablevsync`
  - **Form:** bare flag
  - **Parsed in:** `AddRender12` (fullscreen case)
  - **Effect:** Disables VSync entirely (`VSync = VSyncMode.None`) and
    clears both VRR and classic VSync flags from the display strategy.

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
