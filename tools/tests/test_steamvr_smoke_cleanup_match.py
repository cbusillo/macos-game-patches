#!/usr/bin/env python3

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

SPEC = importlib.util.spec_from_file_location("steamvr_smoke", TOOLS_ROOT / "steamvr_smoke.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load steamvr_smoke module")
steamvr_smoke = importlib.util.module_from_spec(SPEC)
sys.modules["steamvr_smoke"] = steamvr_smoke
SPEC.loader.exec_module(steamvr_smoke)


class SteamVrSmokeCleanupMatchTests(unittest.TestCase):
    def test_wine_service_helpers_are_matched(self) -> None:
        pattern = steamvr_smoke.smoke_process_pattern()

        self.assertTrue(
            steamvr_smoke.is_smoke_process(
                r"C:\windows\system32\plugplay.exe",
                pattern,
            )
        )
        self.assertTrue(
            steamvr_smoke.is_smoke_process(
                r"C:\windows\system32\svchost.exe -k LocalServiceNetworkRestricted",
                pattern,
            )
        )
        self.assertTrue(
            steamvr_smoke.is_smoke_process(
                r"C:\windows\system32\rpcss.exe",
                pattern,
            )
        )

    def test_steam_helper_binaries_are_matched(self) -> None:
        pattern = steamvr_smoke.smoke_process_pattern()

        self.assertTrue(
            steamvr_smoke.is_smoke_process(
                r"C:\Program Files (x86)\Steam\bin\cef\cef.win64\steamwebhelper.exe --type=renderer",
                pattern,
            )
        )
        self.assertTrue(
            steamvr_smoke.is_smoke_process(
                r"C:\Program Files (x86)\Steam\gameoverlayui64.exe -pid 1234",
                pattern,
            )
        )

    def test_unrelated_process_is_not_matched(self) -> None:
        pattern = steamvr_smoke.smoke_process_pattern()

        self.assertFalse(
            steamvr_smoke.is_smoke_process(
                "/usr/bin/python3 some_script.py",
                pattern,
            )
        )


if __name__ == "__main__":
    unittest.main()

