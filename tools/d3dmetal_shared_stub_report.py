#!/usr/bin/env python3
"""Report whether key D3DMetal shared-resource entry points are stubbed.

This inspects disassembly for known Direct Mode interop methods and checks for
an immediate `E_NOTIMPL (0x80004001)` return path.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BINARY = Path(
    "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/apple_gptk/external/"
    "D3DMetal.framework/Versions/A/D3DMetal"
)


@dataclass(frozen=True)
class Target:
    symbol: str
    short_name: str


TARGETS = [
    Target("D3D11Device::OpenSharedResource(void*, _GUID const&, void**)", "OpenSharedResource"),
    Target("D3D11Device::OpenSharedResource1(void*, _GUID const&, void**)", "OpenSharedResource1"),
    Target("DXGIResource::GetSharedHandle(void**)", "GetSharedHandle"),
]


def run_disassemble(binary: Path, symbol: str) -> str:
    cmd = [
        "lldb",
        "--batch",
        "-o",
        f"target create {binary}",
        "-o",
        f'disassemble -n "{symbol}"',
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"lldb failed for symbol '{symbol}' with exit code {completed.returncode}:\n"
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def classify_stub(disasm: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in disasm.splitlines() if line.strip()]
    has_notimpl = any("movl   $0x80004001, %eax" in line for line in lines)
    has_ret = any(re.search(r"\bretq\b", line) for line in lines)
    fast_ret = False
    for i, line in enumerate(lines):
        if "movl   $0x80004001, %eax" in line:
            window = lines[i : i + 3]
            if any("retq" in candidate for candidate in window):
                fast_ret = True
                break

    # extract the first address shown by lldb if available
    first_addr = None
    for line in lines:
        m = re.search(r"\[(0x[0-9a-fA-F]+)\]", line)
        if m:
            first_addr = m.group(1)
            break

    return {
        "stubbed_e_notimpl": bool(has_notimpl and has_ret and fast_ret),
        "has_e_notimpl_load": has_notimpl,
        "has_ret": has_ret,
        "fast_return_path": fast_ret,
        "entry_address": first_addr,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary",
        type=Path,
        default=DEFAULT_BINARY,
        help="Path to D3DMetal binary",
    )
    parser.add_argument(
        "--show-disassembly",
        action="store_true",
        help="Include raw disassembly for each symbol in output",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    binary = args.binary

    if not binary.exists():
        print(f"error: binary not found: {binary}", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "binary": str(binary),
        "targets": {},
        "all_stubbed": True,
    }

    for target in TARGETS:
        try:
            disasm = run_disassemble(binary, target.symbol)
        except RuntimeError as exc:
            report["all_stubbed"] = False
            report["targets"][target.short_name] = {
                "symbol": target.symbol,
                "error": str(exc),
            }
            continue

        classification = classify_stub(disasm)
        if not classification["stubbed_e_notimpl"]:
            report["all_stubbed"] = False

        entry: dict[str, Any] = {"symbol": target.symbol, **classification}
        if args.show_disassembly:
            entry["disassembly"] = disasm
        report["targets"][target.short_name] = entry

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
