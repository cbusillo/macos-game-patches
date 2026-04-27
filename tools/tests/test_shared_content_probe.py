from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

SPEC = importlib.util.spec_from_file_location("shared_content_probe", TOOLS_ROOT / "shared_content_probe.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load shared_content_probe module")
shared_content_probe = importlib.util.module_from_spec(SPEC)
sys.modules["shared_content_probe"] = shared_content_probe
SPEC.loader.exec_module(shared_content_probe)


class SharedContentProbeTests(unittest.TestCase):
    def _write_temp(self, text: str) -> Path:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
        path = Path(handle.name)
        handle.close()
        path.write_text(text, encoding="utf-8")
        return path

    def test_classify_d3dmetal_api_success_content_failure(self) -> None:
        text = "\n".join(
            [
                "CreateTexture2D hr=0x00000000",
                "IDXGIResource::GetSharedHandle hr=0x00000000",
                "ID3D11Device::OpenSharedResource hr=0x00000000",
                "ID3D11Device1::OpenSharedResource1 hr=0x00000000",
                "same_process_first_pixel_bgra=0xffff0000",
                "[child] expected_bgra=0xffff0000",
                "[child] first_pixel_bgra=0x00000000",
                "[child] first_pixel_bgra=0x00000000",
                "child_openread_exit=2",
                "child_openread1_exit=2",
            ]
        )
        output = self._write_temp(text)
        result = shared_content_probe.classify_result("d3dmetal", "shared", output, exit_code=0)

        self.assertTrue(result.api_surface_success)
        self.assertFalse(result.cross_process_content_ok)
        self.assertEqual(result.diagnosis, "api_success_content_not_shared")

    def test_classify_dxvk_api_unavailable(self) -> None:
        text = "\n".join(
            [
                "CreateTexture2D hr=0x00000000",
                "IDXGIResource::GetSharedHandle hr=0x80070057",
            ]
        )
        output = self._write_temp(text)
        result = shared_content_probe.classify_result("dxvk", "shared", output, exit_code=0)

        self.assertFalse(result.api_surface_success)
        self.assertFalse(result.cross_process_content_ok)
        self.assertEqual(result.diagnosis, "api_share_path_unavailable")

    def test_keyed_mutex_sync_failure_is_distinct(self) -> None:
        text = "\n".join(
            [
                "CreateTexture2D hr=0x00000000",
                "IDXGIResource::GetSharedHandle hr=0x00000000",
                "ID3D11Device::OpenSharedResource hr=0x00000000",
                "[child] IDXGIKeyedMutex::AcquireSync hr=0x887a0001",
                "child_openread_exit=2",
            ]
        )
        output = self._write_temp(text)
        result = shared_content_probe.classify_result(
            "d3dmetal",
            "shared_keyed",
            output,
            exit_code=0,
        )

        self.assertEqual(result.diagnosis, "keyed_mutex_sync_failed")

    def test_keyed_mutex_release_failure_is_distinct(self) -> None:
        text = "\n".join(
            [
                "CreateTexture2D hr=0x00000000",
                "IDXGIResource::GetSharedHandle hr=0x00000000",
                "ID3D11Device::OpenSharedResource hr=0x00000000",
                "[child] IDXGIKeyedMutex::AcquireSync hr=0x00000000",
                "[child] IDXGIKeyedMutex::ReleaseSync hr=0x887a0001",
                "child_openread_exit=2",
            ]
        )
        output = self._write_temp(text)
        result = shared_content_probe.classify_result(
            "d3dmetal",
            "shared_keyed",
            output,
            exit_code=0,
        )

        self.assertEqual(result.diagnosis, "keyed_mutex_sync_failed")

    def test_keyed_mutex_interface_missing_is_distinct(self) -> None:
        text = "\n".join(
            [
                "CreateTexture2D hr=0x00000000",
                "IDXGIResource::GetSharedHandle hr=0x00000000",
                "ID3D11Device::OpenSharedResource hr=0x00000000",
                "QI(IDXGIKeyedMutex,parent_source) hr=0x80004002",
                "child_openread_exit=2",
            ]
        )
        output = self._write_temp(text)
        result = shared_content_probe.classify_result(
            "d3dmetal",
            "shared_keyed",
            output,
            exit_code=0,
        )

        self.assertEqual(result.diagnosis, "keyed_mutex_interface_missing")

    def test_child_open_shared_failure_is_distinct(self) -> None:
        text = "\n".join(
            [
                "CreateTexture2D hr=0x00000000",
                "IDXGIResource::GetSharedHandle hr=0x00000000",
                "ID3D11Device::OpenSharedResource hr=0x00000000",
                "[child] ID3D11Device::OpenSharedResource hr=0x80004001",
                "child_openread_exit=2",
            ]
        )
        output = self._write_temp(text)
        result = shared_content_probe.classify_result(
            "d3dmetal",
            "shared",
            output,
            exit_code=0,
        )

        self.assertEqual(result.diagnosis, "child_open_shared_failed")

    def test_shared_nthandle_ignores_keyed_mutex_markers(self) -> None:
        text = "\n".join(
            [
                "CreateTexture2D hr=0x00000000",
                "IDXGIResource::GetSharedHandle hr=0x00000000",
                "ID3D11Device::OpenSharedResource hr=0x00000000",
                "[child] ID3D11Device::OpenSharedResource hr=0x00000000",
                "[child] QI(IDXGIKeyedMutex) hr=0x80004002",
                "[child] expected_bgra=0xffff0000",
                "[child] first_pixel_bgra=0x00000000",
                "child_openread_exit=2",
            ]
        )
        output = self._write_temp(text)
        result = shared_content_probe.classify_result(
            "d3dmetal",
            "shared_nthandle",
            output,
            exit_code=0,
        )

        self.assertEqual(result.diagnosis, "api_success_content_not_shared")


if __name__ == "__main__":
    unittest.main()
