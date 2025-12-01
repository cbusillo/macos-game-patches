# Space Engineers 2 - Technical Analysis

Reverse engineering notes for the macOS compatibility patch.

## Game Information

- **Engine**: VRage (Keen Software House proprietary)
- **Runtime**: .NET 8.0
- **Graphics API**: DirectX 12
- **Key Assemblies**: `VRage.Render.dll`, `VRage.Render12.dll`

## Root Cause Analysis

### Error Message

```
Keen.VRage.Render.Data.RenderException: No supported GPU found.
```

### Log Analysis

From `SpaceEngineers2_*_Render12.log`:

```
Support Details = SupportDetails {
  IsFeatureLevel = True;
  IsMicrosoftBasicRenderDriver = False;
  EnoughRAM = True;
  IsDoublePrecisionFloatShaderOps = False;  <-- PROBLEM
  NeedsLimitedIndirectCommands = False;
}
DeviceSupported = False
```

The GPU is detected as "Apple M4 Max" (or "AMD Compatibility Mode" under D3DMetal), but fails because:

1. `IsDoublePrecisionFloatShaderOps = False` - Apple Silicon doesn't support FP64
2. This causes `DeviceSupported = False`
3. Additionally, `HasMinimumDriverVersion = False`

## Patch 1: ForceAllAdaptersSupported

### Location

- **File**: `VRage.Render.dll`
- **Class**: `Keen.VRage.Render.CoreConfigurations.RenderConfiguration`
- **Method**: `get_ForceAllAdaptersSupported()`
- **RVA**: 0x45BF0
- **File Offset**: 0x43DF1 (after 1-byte tiny header)

### Original Code

```csharp
public bool ForceAllAdaptersSupported
{
    get { return _forceAllAdaptersSupported; }  // Returns field value (false)
}
```

```il
IL_0000: ldarg.0              ; 0x02
IL_0001: ldfld <field>        ; 0x7b 0x80 0x0a 0x00 0x04
IL_0006: ret                  ; 0x2a
```

### Patched Code

```il
IL_0000: ldc.i4.1             ; 0x17 - push true
IL_0001: nop                  ; 0x00 (x5 padding)
IL_0006: ret                  ; 0x2a - return true
```

### Bytes

| Offset | Original | Patched |
|--------|----------|---------|
| 0x43DF1 | `02 7b 80 0a 00 04 2a` | `17 00 00 00 00 00 2a` |

### Effect

When the renderer checks adapters, this property now returns `true`, causing all adapters to be marked as supported regardless of feature checks.

## Patch 2: IsSupported Bypass

### Location

- **File**: `VRage.Render12.dll`
- **Class**: `Keen.VRage.Render12.EngineComponents.Render12EngineComponent`
- **Method**: `Init(Render12ObjectBuilder ob)` (call site)
- **RVA**: 0x700BC (Init method start)
- **File Offset**: 0x6E36D (IL_00a5 within method)

### Original Code

```csharp
// In Init method:
if (!IsSupported)  // IsSupported checks DeviceSupported && HasMinimumDriverVersion
{
    _isGPUSupported = false;
    throw new RenderException("No supported GPU found.", ...);
}
```

```il
IL_00a5: ldarg.0                                    ; 0x02
IL_00a6: call instance bool ...::get_IsSupported()  ; 0x28 0x97 0x12 0x00 0x06
IL_00ab: brtrue.s IL_00e0                           ; 0x2d 0x33
IL_00ad: ... (error handling - throw exception)
IL_00e0: ... (success path)
```

### Patched Code

```il
IL_00a5: nop                  ; 0x00 - do nothing (was ldarg.0)
IL_00a6: ldc.i4.1             ; 0x17 - push true
IL_00a7: nop                  ; 0x00 (x4 padding)
IL_00ab: brtrue.s IL_00e0     ; 0x2d 0x33 - branch taken (stack has true)
```

### Bytes

| Offset | Original | Patched |
|--------|----------|---------|
| 0x6E36D | `02 28 97 12 00 06` | `00 17 00 00 00 00` |

### Stack Balance

Critical insight: The original code was:
1. `ldarg.0` - push `this` onto stack
2. `call get_IsSupported` - consume `this`, push `bool` result
3. `brtrue.s` - consume `bool`, branch if true

If we just replaced `call` with `ldc.i4.1`, `this` would remain on the stack causing `InvalidProgramException`.

Solution: NOP the `ldarg.0` as well:
1. `nop` - nothing on stack
2. `ldc.i4.1` - push `true`
3. `brtrue.s` - consume `true`, always branch to success path

## Alternative Approaches Considered

### 1. Patch get_IsSupported directly

**Problem**: Method has exception handlers (try/catch). Modifying it requires updating exception handler metadata, making the patch complex and version-sensitive.

### 2. Change brtrue.s to br.s

**Problem**: `brtrue.s` consumes a stack value, `br.s` doesn't. Would leave return value on stack.

### 3. Configuration file override

**Attempted**: Created `RenderConfigurationOverride.def` files.

**Problem**: CoreConfigurations are pushed from code before definition sets are loaded.

## Wine-Level Fix Suggestion

Instead of patching the game, Wine/CrossOver could fix this at the translation layer:

```c
// In D3DMetal or VKD3D d3d12 implementation
static HRESULT d3d12_device_CheckFeatureSupport(...)
{
    case D3D12_FEATURE_D3D12_OPTIONS:
        D3D12_FEATURE_DATA_D3D12_OPTIONS *opts = data;
        // Real hardware query...

        // Spoof FP64 for games that check but don't use it
        if (should_spoof_fp64(application_name))
            opts->DoublePrecisionFloatShaderOps = TRUE;

        return S_OK;
}
```

## Tools Used

- **ILSpy** (`ilspycmd`) - .NET decompilation
- **Python** - Binary patching and PE parsing
- **xxd/hexdump** - Binary inspection

## File Hashes (v1.5.0.3105)

For verification:

```
VRage.Render.dll (original):    [Calculate before patching]
VRage.Render12.dll (original):  [Calculate before patching]
```

## References

- [.NET IL Opcodes](https://docs.microsoft.com/en-us/dotnet/api/system.reflection.emit.opcodes)
- [PE Format](https://docs.microsoft.com/en-us/windows/win32/debug/pe-format)
- [Wine D3D12 Implementation](https://github.com/wine-mirror/wine/tree/master/dlls/d3d12)
