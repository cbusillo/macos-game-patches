# AI Agent Guidelines

This document helps AI agents (Claude, Codex, Copilot, etc.) understand and contribute to this project.

## Project Overview

This repository contains binary patches that enable Windows games to run on macOS via CrossOver/Wine. Patches bypass unnecessary hardware compatibility checks that incorrectly reject Apple Silicon GPUs.

## Repository Structure

```
macos-game-patches/
├── README.md           # User-facing documentation
├── AGENTS.md           # This file - AI agent guidelines
├── LICENSE             # MIT License
└── patches/
    └── {game-name}/
        ├── patch.py    # Standalone patch script
        ├── README.md   # User documentation
        └── TECHNICAL.md # Technical analysis
```

## Common Patterns

### Why Games Fail on macOS

1. **FP64 Shader Check**: Games check for double-precision float shader support. Apple Silicon GPUs don't support FP64 in shaders (hardware limitation), but games rarely actually use FP64.

2. **Driver Version Check**: Games verify driver versions against known-good values. Wine reports different version strings that fail these checks.

3. **Feature Level Checks**: DirectX 12 feature level queries may return unexpected values through translation layers.

4. **Query Heap Creation**: D3D12 timestamp query heaps may fail creation with E_INVALIDARG.

### Patch Development Workflow

1. **Collect Logs**: Game logs typically in `AppData/Roaming/{GameName}/Logs/`

2. **Identify Error**: Look for keywords like:
   - `NoSupportedDevice`
   - `GPU not supported`
   - `IsDoublePrecisionFloatShaderOps`
   - `HasMinimumDriverVersion`

3. **Decompile**: For .NET games use ILSpy:
   ```bash
   dotnet tool install -g ilspycmd
   ilspycmd GameAssembly.dll | grep -A 20 "ErrorMethod"
   ```

4. **Find Check Location**: Search for the error string, trace back to the condition

5. **Analyze IL**: Get exact byte offsets:
   ```bash
   ilspycmd -il GameAssembly.dll | grep -B 10 "MethodName"
   ```

6. **Calculate File Offset**: Convert RVA to file offset using PE headers

7. **Create Patch**: Minimal byte changes to bypass check

### .NET IL Patching Tips

Common IL opcodes for patches:
- `0x17` (ldc.i4.1) - Push true/1
- `0x16` (ldc.i4.0) - Push false/0
- `0x2a` (ret) - Return
- `0x00` (nop) - No operation
- `0x26` (pop) - Pop value from stack
- `0x2b` (br.s) - Unconditional short branch
- `0x2d` (brtrue.s) - Branch if true (short)

Stack balance is critical:
- Method calls consume arguments AND push return value
- Branches like `brtrue.s` consume a value, `br.s` does not
- Match the original stack effect or CLR throws InvalidProgramException

### PE File Offset Calculation

```python
import struct

def rva_to_file_offset(data, rva):
    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
    coff_start = e_lfanew + 4
    num_sections = struct.unpack_from('<H', data, coff_start + 2)[0]
    opt_header_size = struct.unpack_from('<H', data, coff_start + 16)[0]
    sections_start = coff_start + 20 + opt_header_size

    for i in range(num_sections):
        section = sections_start + (i * 40)
        virtual_addr = struct.unpack_from('<I', data, section + 12)[0]
        virtual_size = struct.unpack_from('<I', data, section + 8)[0]
        raw_ptr = struct.unpack_from('<I', data, section + 20)[0]

        if virtual_addr <= rva < virtual_addr + virtual_size:
            return raw_ptr + (rva - virtual_addr)
    return None
```

### Patch Script Template

```python
#!/usr/bin/env python3
"""Game Name - macOS Compatibility Patch"""

import argparse
import shutil
import sys
from pathlib import Path

PATCHES = {
    "Assembly.dll": {
        "description": "What this patch does",
        "offset": 0x12345,
        "original": bytes([0x00, 0x00]),
        "patched": bytes([0x17, 0x2a]),
    },
}

def find_game_path():
    # Search common CrossOver bottle locations
    ...

def check_patch_status(filepath, patch_info):
    # Return "original", "patched", or "unknown"
    ...

def apply_patch(filepath, patch_info):
    # Backup and patch
    ...

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("game_path", nargs="?")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    # ... implementation
```

## Testing Patches

1. **Fresh Install**: Test on unmodified game files
2. **Idempotent**: Running patch twice should be safe
3. **Reversible**: `--restore` must fully restore originals
4. **Version Check**: Warn if bytes don't match expected

## Documentation Standards

### README.md (User-facing)
- What the patch does (non-technical)
- Requirements
- Installation steps
- Troubleshooting

### TECHNICAL.md (Developer-facing)
- Root cause analysis
- Decompilation findings
- Exact patch locations and bytes
- Why this approach was chosen

## Wine-Level Fixes

When appropriate, document how the issue could be fixed in Wine/CrossOver itself:

```c
// Example: Spoof FP64 support in d3d12
case D3D12_FEATURE_D3D12_OPTIONS:
    opts->DoublePrecisionFloatShaderOps = TRUE; // Spoof
    break;
```

This helps Wine developers implement proper fixes upstream.

## Commit Message Format

```
feat(game-name): Add macOS compatibility patch

- Bypass FP64 shader capability check
- Skip driver version validation
- Tested with version X.Y.Z
```

## Questions?

Open an issue or check existing patches for examples.
