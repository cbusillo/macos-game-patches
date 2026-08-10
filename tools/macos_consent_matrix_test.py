from __future__ import annotations

import contextlib
import io
import json
import pathlib
import plistlib
import tempfile
import unittest
from typing import Any, Callable, Sequence
from unittest import mock

import macos_consent_matrix as matrix
import runtime_control


class FakeRunner:
    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.apps: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, ...]] = []
        self.fail_unregister: set[str] = set()
        self.fail_register: set[str] = set()
        self.process_output = ""
        self.service_active = False
        self.mutate_stable_after_unregister = False
        self.stable_mutation: Callable[[], object] | None = None

    def add_app(
        self,
        app: pathlib.Path,
        *,
        bundle_id: str,
        uuid: str,
        cd_hash: str,
        team_id: str = "TEAM123456",
        architectures: str = "arm64",
    ) -> None:
        self.apps[str(app)] = {
            "bundleId": bundle_id,
            "uuid": uuid,
            "cdHash": cd_hash,
            "teamId": team_id,
            "architectures": architectures,
        }

    def add_record(self, app: pathlib.Path, *, path: pathlib.Path | None = None) -> None:
        identity = self.apps[str(app)]
        record: dict[str, Any] = {
            "identifier": identity["bundleId"],
            "path": str(path or app),
            "teamId": identity["teamId"],
            "cdHashes": [identity["cdHash"]],
        }
        self.records.setdefault(identity["bundleId"], []).append(record)

    def _dump(self) -> str:
        separator = "-" * 80
        blocks = []
        for bundle_id in sorted(self.records):
            for record in self.records[bundle_id]:
                blocks.append(
                    "\n".join(
                        (
                            "bundle id:                  Fixture",
                            f"path:                       {record['path']} (0x1234)",
                            f"teamID:                     {record['teamId']}",
                            f"identifier:                 {record['identifier']}",
                            f"trustedCodeSignatures:      {record['cdHashes'][0]}",
                        )
                    )
                )
        return f"\n{separator}\n".join(blocks)

    def run(self, argv: Sequence[str], *, timeout: float = 10.0) -> runtime_control.CommandResult:
        command = tuple(str(item) for item in argv)
        self.calls.append(command)
        if command == matrix.PROCESS_LIST_COMMAND:
            return runtime_control.CommandResult(command, 0, stdout=self.process_output)
        if command[:2] == ("/bin/launchctl", "print"):
            if self.service_active:
                return runtime_control.CommandResult(command, 0, stdout="state = running\n")
            return runtime_control.CommandResult(command, 1, stderr="Could not find service")
        if command == (str(runtime_control.LSREGISTER_PATH), "-dump"):
            return runtime_control.CommandResult(command, 0, stdout=self._dump())
        if command[:2] == (str(runtime_control.LSREGISTER_PATH), "-u"):
            path = command[2]
            if path in self.fail_unregister:
                return runtime_control.CommandResult(command, 1, stderr="fixture unregister failure")
            for bundle_id, records in self.records.items():
                self.records[bundle_id] = [record for record in records if record["path"] != path]
            if self.mutate_stable_after_unregister and self.stable_mutation is not None:
                self.stable_mutation()
            return runtime_control.CommandResult(command, 0)
        if command[:2] == (str(runtime_control.LSREGISTER_PATH), "-f"):
            path = command[2]
            if path in self.fail_register:
                return runtime_control.CommandResult(command, 1, stderr="fixture register failure")
            identity = self.apps[path]
            self.records.setdefault(identity["bundleId"], []).append(
                {
                    "identifier": identity["bundleId"],
                    "path": path,
                    "teamId": identity["teamId"],
                    "cdHashes": [identity["cdHash"]],
                }
            )
            return runtime_control.CommandResult(command, 0)
        if command[:2] == ("/usr/bin/codesign", "--verify"):
            return runtime_control.CommandResult(command, 0)
        if command[:3] == ("/usr/bin/codesign", "-dv", "--verbose=4"):
            identity = self.apps[command[3]]
            return runtime_control.CommandResult(
                command,
                0,
                stderr=(
                    f"Identifier={identity['bundleId']}\n"
                    f"TeamIdentifier={identity['teamId']}\n"
                    f"CDHash={identity['cdHash']}\n"
                ),
            )
        if command[:2] == ("/usr/bin/otool", "-l"):
            identity = self.apps[str(pathlib.Path(command[2]).parent.parent.parent)]
            return runtime_control.CommandResult(
                command,
                0,
                stdout=f"cmd LC_UUID\ncmdsize 24\nuuid {identity['uuid']}\n",
            )
        if command[:2] == ("/usr/bin/lipo", "-archs"):
            identity = self.apps[str(pathlib.Path(command[2]).parent.parent.parent)]
            return runtime_control.CommandResult(command, 0, stdout=f"{identity['architectures']}\n")
        raise AssertionError(f"unexpected command: {command}")


class ConsentMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="consent-matrix-")
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.fixture_root = self.root / "fixtures"
        self.fixture_root.mkdir(mode=0o700)
        self.lock_path = self.root / "locks" / "runtime.lock"
        self.runner = FakeRunner()
        self.stable = self.make_app(
            "stable.app",
            bundle_id=matrix.PRODUCTION_BUNDLE_ID,
            uuid="11111111-1111-1111-1111-111111111111",
            cd_hash="1111111111111111111111111111111111111111",
        )
        self.runner.add_record(self.stable)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_app(self, name: str, *, bundle_id: str, uuid: str, cd_hash: str, team_id: str = "TEAM123456") -> pathlib.Path:
        app = self.fixture_root / name
        executable = "alvr_macos_bridge"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        with (app / "Contents" / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleExecutable": executable}, stream)
        (app / "Contents" / "MacOS" / executable).write_bytes(b"signed fixture executable")
        (app / "Contents" / "Resources" / "marker.txt").write_text(name)
        self.runner.add_app(app, bundle_id=bundle_id, uuid=uuid, cd_hash=cd_hash, team_id=team_id)
        return app

    def test_baseline_dry_run_does_not_unregister(self) -> None:
        duplicate = self.make_app(
            "duplicate.app",
            bundle_id=matrix.PRODUCTION_BUNDLE_ID,
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        self.runner.add_record(duplicate)
        report = matrix.run_matrix(
            "baseline",
            stable_app=self.stable,
            allowed_roots=[self.fixture_root],
            runner=self.runner,
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["outcome"], "dry-run")
        self.assertEqual(len(self.runner.records[matrix.PRODUCTION_BUNDLE_ID]), 2)
        self.assertFalse(any(call[1:] == ("-u", str(duplicate)) for call in self.runner.calls))
        self.assertEqual(report["commands"][-1]["argv"], [str(runtime_control.LSREGISTER_PATH), "-u", str(duplicate)])

    def test_duplicate_stable_record_is_not_selected_for_cleanup(self) -> None:
        self.runner.records[matrix.PRODUCTION_BUNDLE_ID].append(
            dict(self.runner.records[matrix.PRODUCTION_BUNDLE_ID][0])
        )
        report = matrix.run_matrix(
            "baseline",
            stable_app=self.stable,
            allowed_roots=[self.fixture_root],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["errors"][0]["code"], "consent_stage.duplicate_baseline")
        self.assertFalse(any(call[1:] == ("-u", str(self.stable)) for call in self.runner.calls))

    def test_baseline_apply_selects_only_exact_duplicate_under_root(self) -> None:
        duplicate = self.make_app(
            "allowed/duplicate.app",
            bundle_id=matrix.PRODUCTION_BUNDLE_ID,
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        outside = self.make_app(
            "outside.app",
            bundle_id=matrix.PRODUCTION_BUNDLE_ID,
            uuid="33333333-3333-3333-3333-333333333333",
            cd_hash="3333333333333333333333333333333333333333",
        )
        allowed = duplicate.parent
        self.runner.add_record(duplicate)
        self.runner.add_record(outside)
        report = matrix.run_matrix(
            "baseline",
            stable_app=self.stable,
            allowed_roots=[allowed],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["errors"][0]["code"], "consent_stage.path_refused")
        self.assertEqual({record["path"] for record in self.runner.records[matrix.PRODUCTION_BUNDLE_ID]}, {str(self.stable), str(duplicate), str(outside)})

        self.runner.records[matrix.PRODUCTION_BUNDLE_ID].remove(next(record for record in self.runner.records[matrix.PRODUCTION_BUNDLE_ID] if record["path"] == str(outside)))
        report = matrix.run_matrix(
            "baseline",
            stable_app=self.stable,
            allowed_roots=[allowed],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.assertTrue(report["ok"])
        self.assertEqual(
            [record["path"] for record in self.runner.records[matrix.PRODUCTION_BUNDLE_ID]],
            [str(self.stable)],
        )

    def test_baseline_apply_removes_multiple_exact_duplicates(self) -> None:
        duplicates = [
            self.make_app(
                f"allowed/duplicate-{index}.app",
                bundle_id=matrix.PRODUCTION_BUNDLE_ID,
                uuid=f"22222222-2222-2222-2222-{index:012d}",
                cd_hash=f"{index:040x}",
            )
            for index in (1, 2)
        ]
        for duplicate in duplicates:
            self.runner.add_record(duplicate)
        report = matrix.run_matrix(
            "baseline",
            stable_app=self.stable,
            allowed_roots=[duplicates[0].parent],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.assertTrue(report["ok"])
        self.assertEqual(
            [record["path"] for record in self.runner.records[matrix.PRODUCTION_BUNDLE_ID]],
            [str(self.stable)],
        )
        self.assertEqual(
            [call for call in self.runner.calls if call[1:2] == ("-u",)],
            [
                (str(runtime_control.LSREGISTER_PATH), "-u", str(duplicate))
                for duplicate in duplicates
            ],
        )

    def test_path_root_and_symlink_refusals(self) -> None:
        lane = self.make_app(
            "lane.app",
            bundle_id="com.example.lane",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        link = self.root / "lane-link.app"
        link.symlink_to(lane, target_is_directory=True)
        for candidate in (link, pathlib.Path("relative.app")):
            report = matrix.run_matrix("inspect", stable_app=candidate, runner=self.runner)
            self.assertFalse(report["ok"])
            self.assertEqual(report["errors"][0]["code"], "consent_stage.path_refused")

    def test_invalid_plist_value_still_emits_json(self) -> None:
        plist = self.stable / "Contents" / "Info.plist"
        with plist.open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleIdentifier": matrix.PRODUCTION_BUNDLE_ID,
                    "CFBundleExecutable": b"not-json-serializable",
                },
                stream,
            )
        report = matrix.run_matrix("inspect", stable_app=self.stable, runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.bundle_invalid")
        json.dumps(report)

    def test_active_process_and_service_are_refused(self) -> None:
        long_path = "/Users/example/Library/Application Support/" + "x" * 100 + "/alvr_macos_bridge"
        self.runner.process_output = f"42 {long_path}\n"
        report = matrix.run_matrix("inspect", stable_app=self.stable, runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.active_process")
        self.assertIn("-ww", matrix.PROCESS_LIST_COMMAND)

        self.runner.process_output = ""
        self.runner.service_active = True
        report = matrix.run_matrix("inspect", stable_app=self.stable, runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.active_service")

    def test_lane_quarantine_and_world_writable_tree_are_refused(self) -> None:
        lane = self.make_app(
            "lane.app",
            bundle_id="com.example.lane",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        with mock.patch.object(matrix.os, "listxattr", return_value=[matrix.QUARANTINE_XATTR], create=True):
            report = matrix.run_matrix("stage", stable_app=self.stable, lane_apps=[lane], runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.path_refused")

        (lane / "Contents" / "Resources" / "marker.txt").chmod(0o666)
        report = matrix.run_matrix("stage", stable_app=self.stable, lane_apps=[lane], runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.tree_unsafe")

    def test_lane_parent_must_be_private(self) -> None:
        lane = self.make_app(
            "public/lane.app",
            bundle_id="com.example.lane",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        lane.parent.chmod(0o755)
        report = matrix.run_matrix("stage", stable_app=self.stable, lane_apps=[lane], runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.path_refused")
        self.assertEqual(report["errors"][0]["details"]["reason"], "parent_not_private")

    def test_stable_mutation_after_unregister_is_detected(self) -> None:
        duplicate = self.make_app(
            "duplicate.app",
            bundle_id=matrix.PRODUCTION_BUNDLE_ID,
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        self.runner.add_record(duplicate)
        self.runner.mutate_stable_after_unregister = True
        self.runner.stable_mutation = lambda: (self.stable / "Contents" / "Resources" / "marker.txt").write_text("mutated")
        report = matrix.run_matrix(
            "baseline",
            stable_app=self.stable,
            allowed_roots=[self.fixture_root],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["errors"][0]["code"], "consent_stage.stable_changed")

    def test_stage_rejects_identity_and_uuid_collisions(self) -> None:
        production_lane = self.make_app(
            "production-lane.app",
            bundle_id=matrix.PRODUCTION_BUNDLE_ID,
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        report = matrix.run_matrix("stage", stable_app=self.stable, lane_apps=[production_lane], runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.identity_collision")

        uuid_lane = self.make_app(
            "uuid-lane.app",
            bundle_id="com.example.uuid-lane",
            uuid="11111111-1111-1111-1111-111111111111",
            cd_hash="3333333333333333333333333333333333333333",
        )
        report = matrix.run_matrix("stage", stable_app=self.stable, lane_apps=[uuid_lane], runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.uuid_collision")

    def test_stage_refuses_duplicate_production_baseline(self) -> None:
        duplicate = self.make_app(
            "duplicate.app",
            bundle_id=matrix.PRODUCTION_BUNDLE_ID,
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        lane = self.make_app(
            "lane.app",
            bundle_id="com.example.lane",
            uuid="33333333-3333-3333-3333-333333333333",
            cd_hash="3333333333333333333333333333333333333333",
        )
        self.runner.add_record(duplicate)
        report = matrix.run_matrix("stage", stable_app=self.stable, lane_apps=[lane], runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.duplicate_baseline")

    def test_stage_requires_single_arm64_slice(self) -> None:
        lane = self.make_app(
            "lane.app",
            bundle_id="com.example.lane",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        self.runner.apps[str(lane)]["architectures"] = "arm64 x86_64"
        report = matrix.run_matrix("stage", stable_app=self.stable, lane_apps=[lane], runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.bundle_invalid")

    def test_stage_and_cleanup_ordering(self) -> None:
        first = self.make_app(
            "first.app",
            bundle_id="com.example.first",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        second = self.make_app(
            "second.app",
            bundle_id="com.example.second",
            uuid="33333333-3333-3333-3333-333333333333",
            cd_hash="3333333333333333333333333333333333333333",
        )
        report = matrix.run_matrix(
            "stage",
            stable_app=self.stable,
            lane_apps=[first, second],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.assertTrue(report["ok"])
        register_calls = [call for call in self.runner.calls if len(call) > 1 and call[1] == "-f"]
        self.assertEqual([call[2] for call in register_calls], [str(first), str(second)])
        report = matrix.run_matrix(
            "cleanup",
            stable_app=self.stable,
            lane_apps=[first, second],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.assertTrue(report["ok"])
        unregister_calls = [call for call in self.runner.calls if len(call) > 1 and call[1] == "-u"]
        self.assertEqual([call[2] for call in unregister_calls[-2:]], [str(second), str(first)])
        self.assertEqual(self.runner.records.get("com.example.first", []), [])
        self.assertEqual(self.runner.records.get("com.example.second", []), [])

    def test_registration_failure_preserves_previous_lanes(self) -> None:
        first = self.make_app(
            "first.app",
            bundle_id="com.example.first",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        second = self.make_app(
            "second.app",
            bundle_id="com.example.second",
            uuid="33333333-3333-3333-3333-333333333333",
            cd_hash="3333333333333333333333333333333333333333",
        )
        self.runner.fail_register.add(str(second))
        report = matrix.run_matrix(
            "stage",
            stable_app=self.stable,
            lane_apps=[first, second],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["errors"][0]["code"], "consent_stage.register_failed")
        self.assertEqual(report["evidence"]["staged"], [str(first)])
        self.assertEqual(len(self.runner.records["com.example.first"]), 1)
        self.assertNotIn("-u", [part for call in self.runner.calls for part in call])

    def test_cleanup_failure_preserves_unprocessed_lanes(self) -> None:
        first = self.make_app(
            "first.app",
            bundle_id="com.example.first",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        second = self.make_app(
            "second.app",
            bundle_id="com.example.second",
            uuid="33333333-3333-3333-3333-333333333333",
            cd_hash="3333333333333333333333333333333333333333",
        )
        matrix.run_matrix(
            "stage",
            stable_app=self.stable,
            lane_apps=[first, second],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.runner.fail_unregister.add(str(first))
        report = matrix.run_matrix(
            "cleanup",
            stable_app=self.stable,
            lane_apps=[first, second],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=self.lock_path,
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["errors"][0]["code"], "consent_stage.residue")
        self.assertEqual(report["evidence"]["removed"], [str(second)])
        self.assertEqual(len(self.runner.records["com.example.first"]), 1)
        self.assertEqual(self.runner.records["com.example.second"], [])

    def test_apply_requires_lifecycle_lock(self) -> None:
        lane = self.make_app(
            "lane.app",
            bundle_id="com.example.lane",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        report = matrix.run_matrix(
            "stage",
            stable_app=self.stable,
            lane_apps=[lane],
            apply=True,
            runner=self.runner,
        )
        self.assertEqual(report["errors"][0]["code"], "consent_stage.lock_failed")

    def test_root_execution_is_refused(self) -> None:
        with mock.patch.object(matrix.os, "geteuid", return_value=0):
            report = matrix.run_matrix("inspect", stable_app=self.stable, runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.path_refused")
        self.assertEqual(report["errors"][0]["details"]["reason"], "root_forbidden")
        self.assertEqual(self.runner.calls, [])

    def test_dry_run_never_uses_launch_or_tcc_commands(self) -> None:
        lane = self.make_app(
            "lane.app",
            bundle_id="com.example.lane",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        report = matrix.run_matrix("stage", stable_app=self.stable, lane_apps=[lane], runner=self.runner)
        self.assertTrue(report["ok"])
        forbidden = {"open", "tccutil", "xcodebuild", "devicectl", "System Settings", "bootout"}
        command_text = " ".join(" ".join(call) for call in self.runner.calls)
        self.assertTrue(forbidden.isdisjoint(command_text.split()))
        self.assertFalse(any(call[1:] == ("-f", str(lane)) for call in self.runner.calls))

    def test_launch_services_dump_text_is_not_embedded_in_report(self) -> None:
        payload = f"{self.runner._dump()}\nPRIVATE-LS-DUMP-SENTINEL"
        with mock.patch.object(self.runner, "_dump", return_value=payload):
            report = matrix.run_matrix("inspect", stable_app=self.stable, runner=self.runner)
        self.assertNotIn("PRIVATE-LS-DUMP-SENTINEL", json.dumps(report))

    def test_apply_uses_private_lifecycle_lock_when_supplied(self) -> None:
        lane = self.make_app(
            "lane.app",
            bundle_id="com.example.lane",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
        )
        lock = self.root / "locks" / "consent.lock"
        report = matrix.run_matrix(
            "stage",
            stable_app=self.stable,
            lane_apps=[lane],
            apply=True,
            runner=self.runner,
            lifecycle_lock_path=lock,
        )
        self.assertTrue(report["ok"])
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_signature_mismatch_is_reported(self) -> None:
        lane = self.make_app(
            "lane.app",
            bundle_id="com.example.lane",
            uuid="22222222-2222-2222-2222-222222222222",
            cd_hash="2222222222222222222222222222222222222222",
            team_id="OTHERTEAM",
        )
        report = matrix.run_matrix("stage", stable_app=self.stable, lane_apps=[lane], runner=self.runner)
        self.assertEqual(report["errors"][0]["code"], "consent_stage.signature_invalid")

    def test_json_main_shape(self) -> None:
        expected = {"schemaVersion": 1, "ok": True, "action": "inspect", "outcome": "dry-run", "evidence": {}, "commands": [], "errors": []}
        with mock.patch.object(matrix, "run_matrix", return_value=expected), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(matrix.main(["inspect", "--stable-app", str(self.stable)]), 0)
        self.assertEqual(json.loads(output.getvalue()), expected)


if __name__ == "__main__":
    unittest.main()
