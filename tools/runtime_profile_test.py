"""Hardware-free fixtures for curated runtime profile admission."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import struct
import tempfile
import unittest
from unittest import mock

import runtime_profile
from runtime_profile import ProfileError


def write_pe_x86_64(path: pathlib.Path) -> None:
    payload = bytearray(128)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 64)
    payload[64:68] = b"PE\0\0"
    struct.pack_into("<H", payload, 68, 0x8664)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


class RuntimeProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture_root = os.environ.get("RUNTIME_FIXTURE_ROOT")
        if fixture_root is None and pathlib.Path("/private/tmp").is_dir():
            fixture_root = "/private/tmp"
        self.temporary = tempfile.TemporaryDirectory(dir=fixture_root)
        self.root = pathlib.Path(self.temporary.name)
        self.profile_root = self.root / "profiles"
        self.profile_root.mkdir()
        self.artifact = self.root / "artifact"
        (self.artifact / "provenance").mkdir(parents=True)
        self.profile = copy.deepcopy(runtime_profile.load_profile("freedom-locomotion").data)
        self.profile_path = self.profile_root / "freedom-locomotion.json"
        self.profile_path.write_bytes(runtime_profile.canonical_json_bytes(self.profile))
        self.profile_sha256 = runtime_profile.sha256_file(self.profile_path)
        self.manifest = {
            "sourceFiles": [
                {
                    "id": "game_profile_freedom_locomotion",
                    "path": "${REPO_ROOT}/runtime/profiles/freedom-locomotion.json",
                    "sha256": self.profile_sha256,
                    "supplyClass": "repo-source",
                }
            ]
        }
        self.write_build_inputs(self.profile_sha256)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_build_inputs(self, profile_sha256: str) -> None:
        (self.artifact / "provenance/build-inputs.json").write_text(
            json.dumps(
                {
                    "sourceFiles": [
                        {
                            "id": "game_profile_freedom_locomotion",
                            "sha256": profile_sha256,
                        }
                    ]
                }
            )
        )

    def load_curated(self) -> runtime_profile.LoadedProfile:
        with mock.patch.object(runtime_profile, "PROFILE_ROOT", self.profile_root):
            return runtime_profile.load_curated_profile(
                "freedom-locomotion",
                self.manifest,
                self.artifact,
            )

    def test_curated_profile_matches_manifest_and_artifact(self) -> None:
        loaded = self.load_curated()
        self.assertEqual(loaded.data["id"], "freedom-locomotion")
        self.assertEqual(loaded.sha256, self.profile_sha256)

    def test_unknown_profile_is_rejected(self) -> None:
        with mock.patch.object(runtime_profile, "PROFILE_ROOT", self.profile_root):
            with self.assertRaises(ProfileError) as raised:
                runtime_profile.load_curated_profile("the-lab", self.manifest, self.artifact)
        self.assertEqual(raised.exception.code, "profile.not_curated")

    def test_arbitrary_profile_path_is_rejected(self) -> None:
        with mock.patch.object(runtime_profile, "PROFILE_ROOT", self.profile_root):
            with self.assertRaises(ProfileError) as raised:
                runtime_profile.load_curated_profile(
                    str(self.profile_path),
                    self.manifest,
                    self.artifact,
                )
        self.assertEqual(raised.exception.code, "profile.invalid")

    def test_noncanonical_curated_profile_is_rejected(self) -> None:
        self.profile_path.write_text(json.dumps(self.profile))
        raw_sha256 = runtime_profile.sha256_file(self.profile_path)
        self.manifest["sourceFiles"][0]["sha256"] = raw_sha256
        self.write_build_inputs(raw_sha256)
        with self.assertRaises(ProfileError) as raised:
            self.load_curated()
        self.assertEqual(raised.exception.code, "profile.noncanonical")

    def test_manifest_profile_drift_is_rejected(self) -> None:
        self.manifest["sourceFiles"][0]["sha256"] = "0" * 64
        with self.assertRaises(ProfileError) as raised:
            self.load_curated()
        self.assertEqual(raised.exception.code, "profile.artifact_mismatch")

    def test_artifact_profile_drift_is_rejected(self) -> None:
        self.write_build_inputs("0" * 64)
        with self.assertRaises(ProfileError) as raised:
            self.load_curated()
        self.assertEqual(raised.exception.code, "profile.artifact_mismatch")

    def test_symlinked_curated_profile_is_rejected(self) -> None:
        target = self.root / "foreign.json"
        target.write_bytes(runtime_profile.canonical_json_bytes(self.profile))
        self.profile_path.unlink()
        self.profile_path.symlink_to(target)
        with self.assertRaises(ProfileError) as raised:
            self.load_curated()
        self.assertEqual(raised.exception.code, "path.symlink")

    def test_profile_timeouts_are_finitely_bounded(self) -> None:
        profile = copy.deepcopy(self.profile)
        profile["launch"]["startupTimeoutSeconds"] = 601
        with self.assertRaises(ProfileError) as raised:
            runtime_profile.validate_profile(profile)
        self.assertEqual(raised.exception.code, "profile.invalid")

    def test_projected_payload_substitutes_and_excludes_overlay_files(self) -> None:
        payload = self.root / "payload"
        payload.mkdir()
        (payload / "game.exe").write_bytes(b"game")
        (payload / "openvr_api.dll").write_bytes(b"runtime shim")
        (payload / "d3d11.dll").write_bytes(b"runtime dxvk")
        stock_openvr_hash = hashlib.sha256(b"stock openvr").hexdigest()
        count, digest = runtime_profile.payload_tree_identity(
            payload,
            substitutions={"openvr_api.dll": stock_openvr_hash},
            excluded={"d3d11.dll"},
        )
        expected = hashlib.sha256()
        expected.update(f"{hashlib.sha256(b'game').hexdigest()}  game.exe\n".encode())
        expected.update(f"{stock_openvr_hash}  openvr_api.dll\n".encode())
        self.assertEqual(count, 2)
        self.assertEqual(digest, expected.hexdigest())

    def test_projected_payload_requires_substitution_target(self) -> None:
        payload = self.root / "payload"
        payload.mkdir()
        with self.assertRaises(ProfileError) as raised:
            runtime_profile.payload_tree_identity(
                payload,
                substitutions={"openvr_api.dll": "0" * 64},
            )
        self.assertEqual(raised.exception.code, "profile.artifact_mismatch")

    def test_installed_profile_resolves_exact_steam_identity_and_targets(self) -> None:
        steam_bottle = self.root / "Steam"
        install_root = (
            steam_bottle
            / "drive_c/Program Files (x86)/Steam/steamapps/common/Freedom Locomotion VR"
        )
        target = self.profile["runtime"]["targets"][0]
        write_pe_x86_64(install_root / target["executable"])
        owned_process = self.profile["launch"]["ownedProcess"]
        assert owned_process is not None
        write_pe_x86_64(install_root / owned_process["executable"])
        write_pe_x86_64(install_root / target["openvrDirectory"] / "openvr_api.dll")
        (install_root / target["workingDirectory"]).mkdir(parents=True, exist_ok=True)
        (install_root / target["graphicsDirectory"]).mkdir(parents=True, exist_ok=True)
        source = self.profile["source"]
        app_manifest = (
            steam_bottle
            / "drive_c/Program Files (x86)/Steam/steamapps"
            / f"appmanifest_{source['appId']}.acf"
        )
        app_manifest.parent.mkdir(parents=True, exist_ok=True)
        depot = source["depots"][0]
        app_manifest.write_text(
            '"AppState" { '
            f'"appid" "{source["appId"]}" '
            f'"buildid" "{source["buildId"]}" '
            f'"SizeOnDisk" "{source["installedSizeBytes"]}" '
            '"StateFlags" "4" '
            f'"InstalledDepots" {{ "{depot["id"]}" {{ '
            f'"manifest" "{depot["manifest"]}" "size" "{depot["sizeBytes"]}" }} }} }}'
        )
        loaded = runtime_profile.LoadedProfile(
            path=self.profile_path,
            data=self.profile,
            sha256=self.profile_sha256,
        )
        installed = runtime_profile.resolve_installed_profile(
            loaded,
            {
                "HOME": str(self.root),
                "REPO_ROOT": str(self.root),
                "STEAM_BOTTLE": str(steam_bottle),
            },
        )
        self.assertEqual(installed.install_root, install_root)
        self.assertEqual(installed.entrypoint.id, "game")
        self.assertEqual(installed.entrypoint.executable, install_root / "FreedomLocomotion.exe")
        self.assertIsNotNone(installed.owned_process)
        assert installed.owned_process is not None
        self.assertEqual(
            installed.owned_process.executable,
            install_root
            / "FreedomLocomotion/Binaries/Win64/FreedomLocomotion-Win64-Shipping.exe",
        )
        self.assertEqual(installed.steam_manifest_sha256, runtime_profile.sha256_file(app_manifest))

    def test_owned_process_must_be_a_critical_file(self) -> None:
        profile = copy.deepcopy(self.profile)
        profile["launch"]["ownedProcess"]["executable"] = "unsealed.exe"
        with self.assertRaises(ProfileError) as raised:
            runtime_profile.validate_profile(profile)
        self.assertEqual(raised.exception.code, "profile.invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
