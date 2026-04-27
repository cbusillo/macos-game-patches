#!/usr/bin/env python3

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

SPEC = importlib.util.spec_from_file_location("d3dmetal_patch", TOOLS_ROOT / "d3dmetal_patch.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load d3dmetal_patch module")
d3dmetal_patch = importlib.util.module_from_spec(SPEC)
sys.modules["d3dmetal_patch"] = d3dmetal_patch
SPEC.loader.exec_module(d3dmetal_patch)


class D3DMetalPatchTests(unittest.TestCase):
    def test_patch_status_transitions(self) -> None:
        patch = d3dmetal_patch.patch_set_diagnostic_s_ok()[0]
        blob = bytearray(patch.offset + len(patch.original) + 1)
        blob[patch.offset : patch.offset + len(patch.original)] = patch.original

        self.assertEqual(d3dmetal_patch.patch_status(bytes(blob), patch), "original")

        blob[patch.offset : patch.offset + len(patch.patched)] = patch.patched
        self.assertEqual(d3dmetal_patch.patch_status(bytes(blob), patch), "patched")

        blob[patch.offset : patch.offset + len(patch.original)] = b"\x90" * len(patch.original)
        self.assertEqual(d3dmetal_patch.patch_status(bytes(blob), patch), "unknown")

    def test_apply_and_restore_round_trip(self) -> None:
        patches = d3dmetal_patch.patch_set_diagnostic_s_ok()
        max_end = max(p.offset + len(p.original) for p in patches)
        original = bytearray(max_end + 16)
        for patch in patches:
            original[patch.offset : patch.offset + len(patch.original)] = patch.original

        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "D3DMetal"
            binary.write_bytes(bytes(original))

            # backup/restore use on-disk paths
            d3dmetal_patch.ensure_backup(binary, binary.read_bytes())

            exit_code, patched = d3dmetal_patch.apply_patches(binary.read_bytes(), patches)
            self.assertEqual(exit_code, 0)
            binary.write_bytes(patched)

            for patch in patches:
                self.assertEqual(d3dmetal_patch.patch_status(binary.read_bytes(), patch), "patched")

            restore_code = d3dmetal_patch.restore_backup(binary)
            self.assertEqual(restore_code, 0)

            restored = binary.read_bytes()
            self.assertEqual(restored, bytes(original))

    def test_all_patch_sets_have_consistent_byte_lengths(self) -> None:
        for name, builder in d3dmetal_patch.PATCH_SET_BUILDERS.items():
            patches = builder()
            self.assertTrue(patches, msg=f"patch set '{name}' is empty")
            for patch in patches:
                self.assertEqual(
                    len(patch.original),
                    len(patch.patched),
                    msg=f"byte length mismatch for patch '{patch.name}' in set '{name}'",
                )


if __name__ == "__main__":
    unittest.main()
