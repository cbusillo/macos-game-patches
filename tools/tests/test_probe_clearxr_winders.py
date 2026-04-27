#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import probe_clearxr_winders  # type: ignore[import-not-found]  # noqa: E402


class ProbeClearXrWindersTests(unittest.TestCase):
    def test_build_probe_messages_matches_expected_wire_format(self) -> None:
        messages = probe_clearxr_winders.build_probe_messages("session-1", "client-1")

        self.assertEqual(messages["request_connection"]["Event"], "RequestConnection")
        self.assertEqual(messages["request_connection"]["SessionID"], "session-1")
        self.assertEqual(messages["request_connection"]["ClientID"], "client-1")
        self.assertEqual(messages["waiting_status"]["Status"], "WAITING")
        self.assertEqual(messages["disconnect_status"]["Status"], "DISCONNECTED")

    def test_extract_snapshot_payload_ignores_non_matching_stage(self) -> None:
        line = 'CLEARXR_HEADLESS_SNAPSHOT startup {"config":{"port":55000}}'

        self.assertEqual(
            probe_clearxr_winders.extract_snapshot_payload(line, "startup"),
            {"config": {"port": 55000}},
        )
        self.assertIsNone(probe_clearxr_winders.extract_snapshot_payload(line, "update"))

    def test_extract_remote_log_path_reads_cloudxr_redirect(self) -> None:
        line = (
            "Further logging is now being redirected to the file: "
            "`C:\\Users\\gaming\\AppData\\Local\\Temp\\cxr_streamsdk.log`"
        )

        self.assertEqual(
            probe_clearxr_winders.extract_remote_log_path(line),
            r"C:\Users\gaming\AppData\Local\Temp\cxr_streamsdk.log",
        )


if __name__ == "__main__":
    unittest.main()
