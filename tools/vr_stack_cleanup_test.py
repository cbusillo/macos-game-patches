"""Hardware-free fixtures for exact VR stack cleanup matching."""

from __future__ import annotations

import unittest

from vr_stack_cleanup import ProcessMatch, command_matches


class VrStackCleanupTests(unittest.TestCase):
    def test_bonjour_background_service_requires_broad_cleanup_opt_in(self) -> None:
        process = ProcessMatch(
            pid=59425,
            ppid=1,
            name="C:\\Program",
            command=(
                "C:\\Program Files "
                "C:\\Program Files\\Bonjour\\mDNSResponder.exe"
            ),
        )

        self.assertFalse(command_matches(process, False, False))
        self.assertTrue(command_matches(process, True, False))

    def test_declared_vr_target_remains_a_default_match(self) -> None:
        process = ProcessMatch(
            pid=60001,
            ppid=1,
            name="wine64-preloader",
            command=(
                "wine64-preloader "
                "C:\\Program Files (x86)\\Steam\\steamapps\\common\\The Lab"
                "\\RobotRepair\\bin\\win64\\vr.exe"
            ),
        )

        self.assertTrue(command_matches(process, False, False))


if __name__ == "__main__":
    unittest.main()
