#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import live_clearxr_avp  # type: ignore[import-not-found]  # noqa: E402


class RuntimeBackendWarningTests(unittest.TestCase):
    def test_build_runtime_backend_warning_detects_native_placeholder_detail(self) -> None:
        startup_snapshot = {
            "cloudxr": {
                "detail": (
                    "Native macOS control-plane backend is ready. Pairing will advertise "
                    "fingerprint bb328bab...cd572ad5 while the streaming runtime is replaced on this host."
                )
            }
        }

        warning = live_clearxr_avp.build_runtime_backend_warning(startup_snapshot)

        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertIn("placeholder backend", warning)

    def test_build_runtime_backend_warning_detects_runtime_failure_note(self) -> None:
        startup_snapshot = {
            "notes": [
                (
                    "CloudXR runtime loading failed on this macOS host, so Clear XR is using "
                    "a native backend instead: missing runtime files"
                )
            ]
        }

        warning = live_clearxr_avp.build_runtime_backend_warning(startup_snapshot)

        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertIn("real media streaming is not expected to succeed", warning)

    def test_build_runtime_backend_warning_ignores_real_runtime_snapshot(self) -> None:
        startup_snapshot = {
            "cloudxr": {
                "detail": (
                    "CloudXR Runtime API is ready. Runtime 6.0.4 will use TLS fingerprint "
                    "bb328bab...cd572ad5 on port 48322."
                )
            },
            "notes": [],
        }

        warning = live_clearxr_avp.build_runtime_backend_warning(startup_snapshot)

        self.assertIsNone(warning)


if __name__ == "__main__":
    unittest.main()
