#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import live_avp_release_gate  # noqa: E402


class ReleaseGateParseTests(unittest.TestCase):
    def test_parse_run_dirs_extracts_all_entries(self) -> None:
        text = "\n".join(
            [
                "RUN_DIR[1]=/tmp/run-1",
                "noise",
                "RUN_DIR[2]=/tmp/run-2",
            ]
        )

        run_dirs = live_avp_release_gate.parse_run_dirs(text)

        self.assertEqual(run_dirs, ["/tmp/run-1", "/tmp/run-2"])

    def test_parse_report_path_returns_none_without_match(self) -> None:
        self.assertIsNone(live_avp_release_gate.parse_report_path("no report line"))

    def test_parse_report_path_extracts_value(self) -> None:
        text = "REPORT=/tmp/matrix/report.json\nother=1"

        report_path = live_avp_release_gate.parse_report_path(text)

        self.assertEqual(report_path, "/tmp/matrix/report.json")


if __name__ == "__main__":
    unittest.main()

