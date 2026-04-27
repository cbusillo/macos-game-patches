#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import clearxr_backend_audit  # type: ignore[import-not-found]  # noqa: E402


class BuildReportTests(unittest.TestCase):
    def test_build_report_marks_vm_lane_unavailable_without_guest_repo(self) -> None:
        with (
            patch.object(
                clearxr_backend_audit,
                "local_clearxr_state",
                return_value={
                    "local_vendor_runtime_ready": False,
                    "windows_build_runtime_available": True,
                },
            ),
            patch.object(
                clearxr_backend_audit,
                "proxmox_vm_status",
                return_value={"status": "running"},
            ),
            patch.object(
                clearxr_backend_audit,
                "windows_ssh_state",
                return_value={"ssh_reachable": False},
            ),
            patch.object(
                clearxr_backend_audit,
                "guest_agent_state",
                return_value={"guest_agent_ready": True},
            ),
            patch.object(
                clearxr_backend_audit,
                "guest_clearxr_state",
                return_value={
                    "paths": {
                        "clearxrServer": False,
                        "clearxr": False,
                        "runtimeManifest": False,
                        "runtimeDll": False,
                        "windowsBuildManifest": False,
                        "windowsBuildDll": False,
                    }
                },
            ),
        ):
            report = clearxr_backend_audit.build_report(
                "prox-main.shiny",
                "201",
                "gaming@winders",
                r"C:\dev\clearxr-server",
            )

        summary = report["summary"]
        self.assertTrue(summary["macos_control_plane_only"])
        self.assertTrue(summary["local_windows_runtime_artifacts_available"])
        self.assertTrue(summary["windows_vm_running"])
        self.assertFalse(summary["windows_direct_ssh_reachable"])
        self.assertFalse(summary["windows_guest_clearxr_repo_present"])
        self.assertFalse(summary["windows_guest_runtime_ready"])

    def test_build_report_marks_guest_runtime_ready_when_repo_and_vendor_exist(self) -> None:
        with (
            patch.object(
                clearxr_backend_audit,
                "local_clearxr_state",
                return_value={
                    "local_vendor_runtime_ready": False,
                    "windows_build_runtime_available": True,
                },
            ),
            patch.object(
                clearxr_backend_audit,
                "proxmox_vm_status",
                return_value={"status": "running"},
            ),
            patch.object(
                clearxr_backend_audit,
                "windows_ssh_state",
                return_value={"ssh_reachable": False},
            ),
            patch.object(
                clearxr_backend_audit,
                "guest_agent_state",
                return_value={"guest_agent_ready": True},
            ),
            patch.object(
                clearxr_backend_audit,
                "guest_clearxr_state",
                return_value={
                    "paths": {
                        "clearxrServer": True,
                        "clearxr": False,
                        "runtimeManifest": True,
                        "runtimeDll": True,
                        "windowsBuildManifest": True,
                        "windowsBuildDll": True,
                    }
                },
            ),
        ):
            report = clearxr_backend_audit.build_report(
                "prox-main.shiny",
                "201",
                "gaming@winders",
                r"C:\dev\clearxr-server",
            )

        summary = report["summary"]
        self.assertTrue(summary["windows_guest_clearxr_repo_present"])
        self.assertTrue(summary["windows_guest_runtime_ready"])
        self.assertTrue(summary["windows_guest_build_runtime_available"])

    def test_build_report_prefers_direct_windows_ssh_when_available(self) -> None:
        with (
            patch.object(
                clearxr_backend_audit,
                "local_clearxr_state",
                return_value={
                    "local_vendor_runtime_ready": False,
                    "windows_build_runtime_available": True,
                },
            ),
            patch.object(
                clearxr_backend_audit,
                "proxmox_vm_status",
                return_value={"status": "stopped"},
            ),
            patch.object(
                clearxr_backend_audit,
                "windows_ssh_state",
                return_value={"ssh_reachable": True},
            ),
            patch.object(
                clearxr_backend_audit,
                "windows_clearxr_state_via_ssh",
                return_value={
                    "paths": {
                        "clearxrServer": True,
                        "runtimeManifest": False,
                        "runtimeDll": False,
                        "windowsBuildManifest": True,
                        "windowsBuildDll": True,
                        "streamerExe": True,
                        "openXrLoaderDll": True,
                    }
                },
            ) as windows_probe,
            patch.object(clearxr_backend_audit, "guest_agent_state") as guest_agent,
        ):
            report = clearxr_backend_audit.build_report(
                "prox-main.shiny",
                "201",
                "gaming@winders",
                r"C:\dev\clearxr-server",
            )

        summary = report["summary"]
        self.assertTrue(summary["windows_vm_running"])
        self.assertTrue(summary["windows_direct_ssh_reachable"])
        self.assertTrue(summary["windows_guest_clearxr_repo_present"])
        self.assertTrue(summary["windows_guest_build_runtime_available"])
        self.assertTrue(summary["windows_guest_streamer_executable_present"])
        windows_probe.assert_called_once_with("gaming@winders", r"C:\dev\clearxr-server")
        guest_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
