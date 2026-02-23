#!/usr/bin/env python3

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import live_avp_checkpoint  # noqa: E402


class ParseKeyOutcomeTests(unittest.TestCase):
    def test_client_ready_detected_from_probe_events(self) -> None:
        avp_probe_text = "\n".join(
            [
                "1.0 PROBE app_initialized",
                "2.0 PROBE streaming_started",
                "3.0 PROBE decode_success codec=1",
                "3.1 PROBE video_presenting",
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text="",
            avp_probe_text=avp_probe_text,
            steam_runtime_text="",
        )

        self.assertTrue(outcome["client_app_initialized"])
        self.assertTrue(outcome["client_streaming_started"])
        self.assertTrue(outcome["streaming_state_seen"])
        self.assertTrue(outcome["client_ready"])
        self.assertFalse(outcome["client_ui_block_suspected"])
        self.assertAlmostEqual(outcome["client_streaming_start_delay_seconds"], 1.0, places=2)
        self.assertAlmostEqual(outcome["client_ready_delay_seconds"], 2.0, places=2)
        self.assertFalse(outcome["client_streaming_start_delayed"])
        self.assertIsNone(outcome["client_ui_block_summary"])

    def test_client_ui_block_suspected_without_streaming_start(self) -> None:
        avp_probe_text = "\n".join(
            [
                "1.0 PROBE app_initialized",
                "12.0 PROBE decoder_config codec=1",
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text="",
            avp_probe_text=avp_probe_text,
            steam_runtime_text="",
        )

        self.assertTrue(outcome["client_app_initialized"])
        self.assertFalse(outcome["client_streaming_started"])
        self.assertFalse(outcome["client_ready"])
        self.assertTrue(outcome["client_ui_block_suspected"])
        self.assertIsNone(outcome["client_streaming_start_delay_seconds"])
        self.assertIsNotNone(outcome["client_ui_block_summary"])

    def test_streaming_start_delay_warning_when_delayed(self) -> None:
        avp_probe_text = "\n".join(
            [
                "1.0 PROBE app_initialized",
                "14.2 PROBE streaming_started",
                "14.4 PROBE decode_success codec=1",
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text="",
            avp_probe_text=avp_probe_text,
            steam_runtime_text="",
        )

        self.assertTrue(outcome["client_streaming_start_delayed"])
        self.assertGreater(outcome["client_streaming_start_delay_seconds"], 10.0)
        self.assertIn("delayed", outcome["client_ui_block_summary"])

    def test_extension_missing_detected_for_unavailable_line(self) -> None:
        steam_runtime_text = (
            'ASSERT: "Required vulkan device extension is unavailable: '
            'VK_KHR_external_memory_win32"'
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text=steam_runtime_text,
        )

        self.assertTrue(outcome["steamvr_external_memory_extensions_missing"])

    def test_extension_name_alone_does_not_trigger_missing_gate(self) -> None:
        steam_runtime_text = "Enabled extension: VK_KHR_external_memory_win32"

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text=steam_runtime_text,
        )

        self.assertFalse(outcome["steamvr_external_memory_extensions_missing"])

    def test_extension_missing_detected_for_not_available_line(self) -> None:
        steam_runtime_text = (
            "Required Vulkan device extension is not available: "
            "VK_KHR_WIN32_KEYED_MUTEX"
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text=steam_runtime_text,
        )

        self.assertTrue(outcome["steamvr_external_memory_extensions_missing"])

    def test_direct_mode_swap_failure_detected_from_runtime_logs(self) -> None:
        steam_runtime_text = (
            "alvr_server: CreateSwapTextureSet failed for texture 0 at CreateSharedHandle"
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text=steam_runtime_text,
        )

        self.assertTrue(outcome["host_direct_mode_swap_failed"])

    def test_bridge_connected_inferred_from_fresh_encode(self) -> None:
        daemon_log = "fresh_encode sequence=1 encoded_bytes=2435 sample_crc=0x07aa4390"

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log=daemon_log,
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertTrue(outcome["bridge_connected"])
        self.assertTrue(outcome["bridge_connected_inferred"])

    def test_non_direct_frame_probes_are_parsed(self) -> None:
        alvr_text = "\n".join(
            [
                "ALVR MGP direct-mode guard: 2026-02-17b disabled=1",
                "PROBE host_non_direct_source_enabled=1 direct_mode_disabled=1 env_disable=<unset>",
                "PROBE host_non_direct_frame_produced count=44 wake=44",
                "PROBE host_non_direct_frame_submitted count=44 wake=44",
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text=alvr_text,
            daemon_log="",
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertTrue(outcome["host_non_direct_source_enabled"])
        self.assertTrue(outcome["host_direct_mode_disabled"])
        self.assertTrue(outcome["host_non_direct_frame_produced_seen"])
        self.assertTrue(outcome["host_non_direct_frame_submitted_seen"])
        self.assertEqual(outcome["host_non_direct_frame_produced_max_count"], 44)
        self.assertEqual(outcome["host_non_direct_frame_submitted_max_count"], 44)

    def test_non_direct_source_inferred_from_non_direct_markers(self) -> None:
        alvr_text = "\n".join(
            [
                "CEncoder: new_frame_ready calls=120 source=non_direct",
                "CEncoder: copy_to_staging calls=120 layers=0 recentering=0 target_ts=1 source=non_direct",
                "PROBE host_non_direct_frame_submitted count=120 wake=120",
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text=alvr_text,
            daemon_log="",
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertTrue(outcome["host_non_direct_source_enabled"])
        self.assertTrue(outcome["host_non_direct_source_enabled_inferred"])
        self.assertTrue(outcome["host_direct_mode_disabled"])
        self.assertTrue(outcome["host_direct_mode_disabled_inferred"])

    def test_known_synthetic_source_pattern_detected(self) -> None:
        daemon_log = "\n".join(
            [
                "fresh_encode sequence=1 encoded_bytes=2435 sample_crc=0x07aa4390",
                "fresh_encode sequence=2 encoded_bytes=2434 sample_crc=0x3d20cd83",
                "fresh_encode sequence=3 encoded_bytes=2434 sample_crc=0x3f882ae0",
                "fresh_encode sequence=4 encoded_bytes=2435 sample_crc=0x68cf1f8a",
                "fresh_encode sequence=5 encoded_bytes=2434 sample_crc=0x9492a69c",
                "fresh_encode sequence=6 encoded_bytes=2434 sample_crc=0xdcf41d0c",
                "fresh_encode sequence=7 encoded_bytes=2433 sample_crc=0xe187baba",
                "fresh_encode sequence=8 encoded_bytes=2435 sample_crc=0xeabc7a17",
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log=daemon_log,
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertTrue(outcome["source_known_synthetic_pattern"])


class BuildRuntimeTextTests(unittest.TestCase):
    def test_includes_all_vrclient_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = Path(temp_dir)
            (logs_dir / "vrserver.txt").write_text("vrserver-body", encoding="utf-8")
            (logs_dir / "vrclient_alpha.txt").write_text("alpha-body", encoding="utf-8")
            (logs_dir / "vrclient_beta.txt").write_text("beta-body", encoding="utf-8")

            runtime_text = live_avp_checkpoint.build_steam_runtime_text(logs_dir)

            self.assertIn("vrserver-body", runtime_text)
            self.assertIn("alpha-body", runtime_text)
            self.assertIn("beta-body", runtime_text)

    def test_prefers_delta_and_skips_previous_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = Path(temp_dir)
            (logs_dir / "vrserver.txt").write_text("vrserver-old", encoding="utf-8")
            (logs_dir / "vrserver.delta.txt").write_text("vrserver-new", encoding="utf-8")
            (logs_dir / "vrclient_vrcompositor.txt").write_text("vc-old", encoding="utf-8")
            (logs_dir / "vrclient_vrcompositor.delta.txt").write_text("vc-new", encoding="utf-8")
            (logs_dir / "vrclient_vrcompositor.previous.txt").write_text(
                "previous-should-not-count", encoding="utf-8"
            )
            (logs_dir / "vrclient_vrcompositor.previous.delta.txt").write_text(
                "previous-delta-should-not-count", encoding="utf-8"
            )

            runtime_text = live_avp_checkpoint.build_steam_runtime_text(logs_dir)

            self.assertIn("vrserver-new", runtime_text)
            self.assertNotIn("vrserver-old", runtime_text)
            self.assertIn("vc-new", runtime_text)
            self.assertNotIn("vc-old", runtime_text)
            self.assertNotIn("previous-should-not-count", runtime_text)
            self.assertNotIn("previous-delta-should-not-count", runtime_text)


class SessionLogReadTests(unittest.TestCase):
    def test_prefers_delta_when_delta_has_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = Path(temp_dir)
            (logs_dir / "session_log.delta.txt").write_text(
                "ALVR MGP direct-mode guard: disabled=1\n", encoding="utf-8"
            )
            (logs_dir / "session_log.txt").write_text(
                "full-old-log\n", encoding="utf-8"
            )

            text, fallback_used = live_avp_checkpoint.read_alvr_session_text(logs_dir)

            self.assertIn("direct-mode guard", text)
            self.assertFalse(fallback_used)

    def test_falls_back_to_full_tail_when_delta_has_no_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = Path(temp_dir)
            (logs_dir / "session_log.delta.txt").write_text(
                "18:00:00.000 [INFO] NalParsing: SetVideoConfigNals\n", encoding="utf-8"
            )
            (logs_dir / "session_log.txt").write_text(
                "line-1\nCEncoder: new_frame_ready calls=1 source=non_direct\n",
                encoding="utf-8",
            )

            text, fallback_used = live_avp_checkpoint.read_alvr_session_text(logs_dir)

            self.assertIn("CEncoder: new_frame_ready", text)
            self.assertTrue(fallback_used)


if __name__ == "__main__":
    unittest.main()
