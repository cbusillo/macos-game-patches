#!/usr/bin/env python3

import sys
import struct
import tempfile
import unittest
import zlib
import os
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import live_avp_checkpoint  # noqa: E402


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png_bytes(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    row = bytes(color) * width
    raw = b"".join(b"\x00" + row for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


def _png_checker_bytes(
    width: int,
    height: int,
    color_a: tuple[int, int, int, int],
    color_b: tuple[int, int, int, int],
) -> bytes:
    rows = []
    for y in range(height):
        pixels = []
        for x in range(width):
            pixels.append(bytes(color_a if (x + y) % 2 == 0 else color_b))
        rows.append(b"\x00" + b"".join(pixels))
    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


class ParseKeyOutcomeTests(unittest.TestCase):
    def test_prune_old_run_bundles_keeps_recent_and_nonmatching_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = live_avp_checkpoint.datetime.now(live_avp_checkpoint.UTC).timestamp()

            keep = root / "20260306-100000-live-avp-checkpoint"
            old_prune = root / "20260201-100000-live-avp-checkpoint"
            old_keep = root / "20260202-100000-live-avp-checkpoint"
            unrelated = root / "20260201-random-artifacts"

            for path in (keep, old_prune, old_keep, unrelated):
                path.mkdir()

            os.utime(keep, (now, now))
            old_time = now - (30 * 24 * 60 * 60)
            os.utime(old_prune, (old_time - 10, old_time - 10))
            os.utime(old_keep, (old_time - 5, old_time - 5))
            os.utime(unrelated, (old_time - 20, old_time - 20))

            pruned = live_avp_checkpoint.prune_old_run_bundles(
                root,
                keep_last=2,
                older_than_days=14,
            )

            self.assertEqual(pruned, [old_prune])
            self.assertTrue(keep.exists())
            self.assertTrue(old_keep.exists())
            self.assertFalse(old_prune.exists())
            self.assertTrue(unrelated.exists())

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

    def test_create_swap_texture_set_fallback_loop_signature_detected(self) -> None:
        dashboard_lines = []
        for index in range(8):
            dashboard_lines.append(
                "[12:00:00.{:03d} DEBUG alvr_dashboard::data_sources] Server event: "
                "{{\"timestamp\":\"12:00:00.{:03d}\",\"event_type\":{{\"id\":\"Log\",\"data\":"
                "{{\"severity\":\"Info\",\"content\":\"CreateSwapTextureSet: trying format fallback 29 -> 87 "
                "for shared texture compatibility\"}}}}}}".format(index * 10, index * 10)
            )
        dashboard_text = "\n".join(dashboard_lines)
        daemon_log = "\n".join(
            [
                f"fresh_encode sequence={index} encoded_bytes=973 sample_crc=0xc71c0011"
                for index in range(1, 25)
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log=daemon_log,
            dashboard_text=dashboard_text,
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertTrue(outcome["host_create_swap_texture_set_fallback_loop"])
        self.assertEqual(outcome["host_create_swap_texture_set_fallback_count"], 8)
        self.assertEqual(outcome["interop_signature"], "create_swap_texture_set_fallback_loop")
        self.assertTrue(outcome["interop_signature_details"]["swap_texture_set_fallback_loop"])

    def test_create_swap_texture_set_small_fallback_burst_not_loop(self) -> None:
        dashboard_text = "\n".join(
            [
                '[12:00:00.000 DEBUG alvr_dashboard::data_sources] Server event: '
                '{"timestamp":"12:00:00.000","event_type":{"id":"Log","data":'
                '{"severity":"Info","content":"CreateSwapTextureSet: trying format fallback 29 -> 87 '
                'for shared texture compatibility"}}}',
                '[12:00:00.010 DEBUG alvr_dashboard::data_sources] Server event: '
                '{"timestamp":"12:00:00.010","event_type":{"id":"Log","data":'
                '{"severity":"Info","content":"CreateSwapTextureSet: trying format fallback 29 -> 87 '
                'for shared texture compatibility"}}}',
            ]
        )
        daemon_log = "\n".join(
            [
                f"fresh_encode sequence={index} encoded_bytes=973 sample_crc=0xc71c0011"
                for index in range(1, 25)
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log=daemon_log,
            dashboard_text=dashboard_text,
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertFalse(outcome["host_create_swap_texture_set_fallback_loop"])
        self.assertEqual(outcome["host_create_swap_texture_set_fallback_count"], 2)
        self.assertNotEqual(outcome["interop_signature"], "create_swap_texture_set_fallback_loop")

    def test_create_shared_handle_success_not_misclassified_by_unrelated_notimpl(self) -> None:
        dashboard_text = "\n".join(
            [
                '[12:00:00.000 DEBUG alvr_dashboard::data_sources] Server event: '
                '{"timestamp":"12:00:00.000","event_type":{"id":"Log","data":'
                '{"severity":"Info","content":"CreateSwapTextureSet attempt CreateSharedHandle '
                'succeeded: req_fmt=29 create_fmt=29 sample=1 misc=0x900 bind=0x28 '
                'access=0x80000001 sec=0 named=0 handle=0000000000000005"}}}',
                '[12:00:00.010 DEBUG alvr_dashboard::data_sources] Server event: '
                '{"timestamp":"12:00:00.010","event_type":{"id":"Log","data":'
                '{"severity":"Error","content":"Audio record error: A backend-specific '
                'error has occurred: Not implemented. (0x80004001)"}}}',
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text=dashboard_text,
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertFalse(outcome["interop_signature_details"]["create_shared_handle_failed"])
        self.assertFalse(outcome["interop_signature_details"]["create_shared_handle_e_notimpl"])
        self.assertNotEqual(outcome["interop_signature"], "create_shared_handle_not_implemented")

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

    def test_black_red_oscillation_detected_as_synthetic_like(self) -> None:
        daemon_lines: list[str] = []
        for sequence in range(1, 241):
            if sequence % 2 == 0:
                daemon_lines.append(
                    "fresh_encode sequence={} encoded_bytes=2427 sample_crc=0xc71c0011 "
                    "spread_crc=0xc71c0011 sample_nonzero=0 sample_len=4096 "
                    "sample_min=0 sample_max=0 reason=payload_changed".format(sequence)
                )
            else:
                daemon_lines.append(
                    "fresh_encode sequence={} encoded_bytes=2435 sample_crc=0x5c2f9ff2 "
                    "spread_crc=0x5c2f9ff2 sample_nonzero=4096 sample_len=4096 "
                    "sample_min=88 sample_max=88 reason=payload_changed".format(sequence)
                )
        daemon_log = "\n".join(daemon_lines)
        alvr_text = "PROBE display_redirect_present calls=1"

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text=alvr_text,
            daemon_log=daemon_log,
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertTrue(outcome["source_black_red_oscillation_suspected"])
        self.assertEqual(outcome["source_quality_grade"], "synthetic_like")
        self.assertEqual(outcome["source_path_selected"], "virtual_display")
        self.assertGreaterEqual(outcome["source_dominant_two_crc_share"], 0.95)

    def test_spread_crc_variation_counts_as_source_motion(self) -> None:
        daemon_log = "\n".join(
            [
                "fresh_encode sequence=1 encoded_bytes=973 sample_crc=0xc71c0011 "
                "spread_crc=0xc71c0011 sample_nonzero=0 sample_len=4096 sample_min=0 sample_max=0",
                "fresh_encode sequence=2 encoded_bytes=973 sample_crc=0xc71c0011 "
                "spread_crc=0x12345678 sample_nonzero=32 sample_len=4096 sample_min=0 sample_max=255",
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log=daemon_log,
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertEqual(outcome["source_unique_fresh_sample_crcs"], ["c71c0011"])
        self.assertEqual(
            outcome["source_unique_fresh_spread_crcs"], ["12345678", "c71c0011"]
        )
        self.assertEqual(outcome["source_fresh_sample_nonzero_max"], 32)
        self.assertTrue(outcome["source_motion_seen"])

    def test_source_sample_startup_variance_still_classifies_static_black(self) -> None:
        alvr_text = "\n".join(
            [
                "VideoEncoderVtBridge: source_sample calls=1 row_pitch=17152 payload=35127296 "
                "first_bgra=0,0,0,0 sample_hash=0xdfde6ac5",
                "VideoEncoderVtBridge: source_sample calls=2 row_pitch=17152 payload=35127296 "
                "first_bgra=0,0,0,255 sample_hash=0xdfde6ac5",
                "VideoEncoderVtBridge: source_sample calls=3 row_pitch=17152 payload=35127296 "
                "first_bgra=88,88,177,255 sample_hash=0xc712dac5",
                "VideoEncoderVtBridge: source_sample calls=4 row_pitch=17152 payload=35127296 "
                "first_bgra=0,0,0,255 sample_hash=0xdfde6ac5",
                "VideoEncoderVtBridge: source_sample calls=5 row_pitch=17152 payload=35127296 "
                "first_bgra=0,0,0,255 sample_hash=0xdfde6ac5",
                "VideoEncoderVtBridge: source_sample calls=6 row_pitch=17152 payload=35127296 "
                "first_bgra=0,0,0,255 sample_hash=0xdfde6ac5",
                "VideoEncoderVtBridge: source_sample calls=7 row_pitch=17152 payload=35127296 "
                "first_bgra=0,0,0,255 sample_hash=0xdfde6ac5",
                "VideoEncoderVtBridge: source_sample calls=8 row_pitch=17152 payload=35127296 "
                "first_bgra=0,0,0,255 sample_hash=0xdfde6ac5",
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text=alvr_text,
            daemon_log="",
            dashboard_text="",
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertEqual(outcome["source_sample_observation_count"], 8)
        self.assertEqual(outcome["source_stable_source_sample_count"], 5)
        self.assertEqual(outcome["source_unique_stable_source_sample_hashes"], ["dfde6ac5"])
        self.assertEqual(outcome["source_stable_source_sample_nonblack_count"], 0)
        self.assertFalse(outcome["source_motion_seen"])
        self.assertTrue(outcome["source_static_from_source_sample"])
        self.assertTrue(outcome["source_static_suspected"])

    def test_dashboard_server_event_content_contributes_host_signals(self) -> None:
        dashboard_text = "\n".join(
            [
                '[12:00:00.000 DEBUG alvr_dashboard::data_sources] Server event: '
                '{"timestamp":"12:00:00.000","event_type":{"id":"Log","data":'
                '{"severity":"Info","content":"PROBE host_non_direct_source_enabled=1 '
                'direct_mode_disabled=1 env_disable=<unset>"}}}',
                '[12:00:00.010 DEBUG alvr_dashboard::data_sources] Server event: '
                '{"timestamp":"12:00:00.010","event_type":{"id":"Log","data":'
                '{"severity":"Info","content":"CEncoder: new_frame_ready calls=5 '
                'source=non_direct"}}}',
                '[12:00:00.020 DEBUG alvr_dashboard::data_sources] Server event: '
                '{"timestamp":"12:00:00.020","event_type":{"id":"Log","data":'
                '{"severity":"Info","content":"CEncoder: copy_to_staging calls=5 layers=0 '
                'recentering=0 target_ts=1 source=non_direct"}}}',
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text=dashboard_text,
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertTrue(outcome["host_non_direct_source_enabled"])
        self.assertTrue(outcome["host_new_frame_ready_seen"])
        self.assertTrue(outcome["host_copy_to_staging_seen"])

    def test_steam_login_window_capture_detected(self) -> None:
        dashboard_text = (
            '[12:00:00.000 DEBUG alvr_dashboard::data_sources] Server event: '
            '{"timestamp":"12:00:00.000","event_type":{"id":"Log","data":'
            '{"severity":"Info","content":"PROBE host_non_direct_frame_rendered '
            'tick=1 source=window_capture hwnd=000000000001 title=Sign in to Steam"}}}'
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text=dashboard_text,
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertTrue(outcome["source_steam_login_ui_detected"])
        self.assertTrue(outcome["source_steam_client_ui_detected"])
        self.assertIn("Sign in to Steam", outcome["source_window_capture_titles"])

    def test_source_path_tie_prefers_real_capture_over_synthetic(self) -> None:
        dashboard_text = "\n".join(
            [
                '[12:00:00.000 DEBUG alvr_dashboard::data_sources] Server event: '
                '{"timestamp":"12:00:00.000","event_type":{"id":"Log","data":'
                '{"severity":"Info","content":"PROBE host_non_direct_frame_rendered '
                'tick=1 source=window_capture hwnd=000000000001 title=VR View"}}}',
                '[12:00:00.010 DEBUG alvr_dashboard::data_sources] Server event: '
                '{"timestamp":"12:00:00.010","event_type":{"id":"Log","data":'
                '{"severity":"Info","content":"PROBE host_non_direct_frame_rendered '
                'tick=2 phase=0.250"}}}',
            ]
        )

        outcome = live_avp_checkpoint.parse_key_outcome(
            alvr_text="",
            daemon_log="",
            dashboard_text=dashboard_text,
            avp_probe_text="",
            steam_runtime_text="",
        )

        self.assertEqual(outcome["source_path_counts"], {"window_capture": 1, "synthetic_pattern": 1})
        self.assertEqual(outcome["source_path_selected"], "window_capture")
        self.assertEqual(outcome["source_quality_grade"], "real_candidate")


class AnalyzeVtBridgeDebugFramesTests(unittest.TestCase):
    def test_all_flat_frames_true_for_every_sample_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            frames_dir = Path(temp_dir)
            for i in range(2):
                (frames_dir / f"frame_{i}.png").write_text(
                    "synthetic-image",
                    encoding="utf-8",
                )

            with patch(
                "live_avp_checkpoint._parse_png_flat_rgba",
                return_value=(True, (1, 2, 3, 255)),
            ):
                outcome = live_avp_checkpoint.analyze_vtbridge_debug_frames(frames_dir)

            self.assertEqual(outcome["source_debug_frame_count"], 2)
            self.assertEqual(outcome["source_debug_flat_frame_count"], 2)
            self.assertEqual(outcome["source_debug_nonflat_frame_count"], 0)
            self.assertEqual(outcome["source_debug_unknown_frame_count"], 0)
            self.assertEqual(outcome["source_debug_all_flat"], True)

    def test_single_flat_frame_flags_all_flat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            frames_dir = Path(temp_dir)
            (frames_dir / "frame.png").write_text("synthetic-image", encoding="utf-8")

            with patch(
                "live_avp_checkpoint._parse_png_flat_rgba",
                return_value=(True, (1, 2, 3, 255)),
            ):
                outcome = live_avp_checkpoint.analyze_vtbridge_debug_frames(frames_dir)

            self.assertEqual(outcome["source_debug_frame_count"], 1)
            self.assertEqual(outcome["source_debug_flat_frame_count"], 1)
            self.assertEqual(outcome["source_debug_nonflat_frame_count"], 0)
            self.assertEqual(outcome["source_debug_unknown_frame_count"], 0)
            self.assertEqual(outcome["source_debug_all_flat"], True)

    def test_mixed_flat_and_nonflat_frames_not_all_flat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            frames_dir = Path(temp_dir)
            for i in range(2):
                (frames_dir / f"frame_{i}.png").write_text(
                    "synthetic-image",
                    encoding="utf-8",
                )

            with patch(
                "live_avp_checkpoint._parse_png_flat_rgba",
                side_effect=[(True, (1, 2, 3, 255)), (False, (0, 0, 0, 0))],
            ):
                outcome = live_avp_checkpoint.analyze_vtbridge_debug_frames(frames_dir)

            self.assertEqual(outcome["source_debug_frame_count"], 2)
            self.assertEqual(outcome["source_debug_flat_frame_count"], 1)
            self.assertEqual(outcome["source_debug_nonflat_frame_count"], 1)
            self.assertEqual(outcome["source_debug_unknown_frame_count"], 0)
            self.assertEqual(outcome["source_debug_all_flat"], False)

    def test_parse_outcome_marks_single_flat_debug_dump_as_synthetic_like(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            frames_dir = Path(temp_dir)
            (frames_dir / "frame.png").write_text("synthetic-image", encoding="utf-8")

            with patch(
                "live_avp_checkpoint._parse_png_flat_rgba",
                return_value=(True, (0, 0, 0, 255)),
            ):
                outcome = live_avp_checkpoint.parse_key_outcome(
                    alvr_text="",
                    daemon_log="",
                    dashboard_text="",
                    avp_probe_text="",
                    steam_runtime_text="",
                    debug_frames_dir=frames_dir,
                )

            self.assertTrue(outcome["source_debug_all_flat"])
            self.assertEqual(outcome["source_debug_flat_ratio"], 1.0)
            self.assertEqual(outcome["source_quality_grade"], "synthetic_like")


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


class SessionContractPatchTests(unittest.TestCase):
    def test_patch_session_contract_enables_log_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "run"
            (run_dir / "config").mkdir(parents=True, exist_ok=True)
            session_path = root / "session.json"
            session_path.write_text(
                """
{
  "client_connections": {},
  "session_settings": {
    "extra": {
      "logging": {
        "log_to_disk": false
      }
    }
  }
}
""".strip(),
                encoding="utf-8",
            )

            live_avp_checkpoint.patch_session_contract(
                session_path,
                run_dir,
                "hevc",
                "tcp",
                "auto",
                manual_client_host=None,
                manual_client_ip=None,
            )

            session = live_avp_checkpoint.read_json_retry(session_path)
            logging = session["session_settings"]["extra"]["logging"]
            self.assertTrue(logging["log_to_disk"])


class VirtualDisplayPlaceholderClassificationTests(unittest.TestCase):
    def test_placeholder_frames_do_not_grade_as_real_candidate(self) -> None:
        alvr_text = "\n".join(
            [
                *(
                    f"PROBE virtual_display_present calls={index} frame_id={index} vsync=0 mutex_held=0 copied=1 path=wait_copy_composed"
                    for index in range(1, 25)
                ),
                *(
                    "VideoEncoderVtBridge: source_sample calls={} row_pitch=17152 payload=35127296 "
                    "first_bgra=0,128,106,255 sample_hash=0xdfde6ac5".format(index)
                    for index in range(1, 31)
                ),
            ]
        )
        crc_cycle = [
            (2427, "0x2a6449c5"),
            (2801, "0x8a258aec"),
            (3148, "0x0f1ff500"),
            (3303, "0xda3fb4c3"),
        ]
        daemon_log = "\n".join(
            [
                f"fresh_encode sequence={index} encoded_bytes={size} sample_crc={crc} spread_crc=0x77d7ddb7 sample_nonzero=4096 sample_min=0 sample_max=138"
                for index, (size, crc) in enumerate(crc_cycle * 32, start=1)
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            frames_dir = Path(temp_dir)
            for index in range(5):
                (frames_dir / f"frame-{index:06d}-black.png").write_bytes(
                    _png_bytes(2, 2, (0, 0, 0, 0))
                )
            for index in range(5, 12):
                (frames_dir / f"frame-{index:06d}-nonflat.png").write_bytes(
                    _png_checker_bytes(2, 2, (0, 0, 0, 0), (0, 128, 106, 255))
                )

            outcome = live_avp_checkpoint.parse_key_outcome(
                alvr_text=alvr_text,
                daemon_log=daemon_log,
                dashboard_text="",
                avp_probe_text=(
                    "1.0 PROBE app_initialized\n"
                    "2.0 PROBE streaming_started\n"
                    "3.0 PROBE decode_success\n"
                    "3.1 PROBE video_presenting"
                ),
                steam_runtime_text="",
                debug_frames_dir=frames_dir,
            )

        self.assertTrue(outcome["source_virtual_display_placeholder_suspected"])
        self.assertEqual(outcome["source_quality_grade"], "synthetic_like")


if __name__ == "__main__":
    unittest.main()
