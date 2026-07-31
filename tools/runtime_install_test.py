"""Hardware-free fixtures for artifact-aware runtime lifecycle coordination."""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from typing import Any, cast
from unittest import mock

import build_runtime_artifact as artifact_contract
import runtime_cli
import runtime_install
import runtime_profile
import runtime_transaction
from runtime_control import (
    CheckResult,
    CommandResult,
    DoctorReport,
    RuntimeContext,
    StatusReport,
    StopReport,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CODE_ROOT = pathlib.Path(os.environ.get("RUNTIME_FIXTURE_ROOT", REPO_ROOT / ".code"))
MARKER = "Contents/Resources/runtime-owner.json"


class SimulatedCrash(BaseException):
    pass


class ClosedTargetRunner:
    def run(self, argv: Any, *, timeout: float = 10.0) -> CommandResult:
        command = tuple(str(item) for item in argv)
        if command and command[0] == "/usr/sbin/lsof":
            return CommandResult(command, 1)
        raise AssertionError(f"unexpected command: {command}")


class BusyTargetRunner(ClosedTargetRunner):
    def run(self, argv: Any, *, timeout: float = 10.0) -> CommandResult:
        command = tuple(str(item) for item in argv)
        if command and command[0] == "/usr/sbin/lsof":
            return CommandResult(command, 0, stdout="p4242\n")
        return super().run(argv, timeout=timeout)


class RecordingClosedTargetRunner(ClosedTargetRunner):
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: Any, *, timeout: float = 10.0) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.calls.append(command)
        return super().run(command, timeout=timeout)


class LifecycleFixture:
    def __init__(self) -> None:
        CODE_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="runtime-install-", dir=CODE_ROOT)
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.artifact_root = self.root / "artifact"
        self.targets_root = self.root / "targets"
        self.runtime_state = self.root / "runtime-state"
        self.bridge_root = self.root / "bridge-state"
        self.bridge_bundle = self.bridge_root / "ALVRMacOSBridge.app"
        self.backup_root = self.root / "backups"
        self.lifecycle_root = self.root
        self.transaction_root = self.root / "transactions"
        self.history_root = self.transaction_root / "history"
        self.journal = self.transaction_root / "transaction.json"
        self.global_lock_root = self.root
        self.global_lock = self.global_lock_root / "runtime.lock"
        self.journal_lock = self.transaction_root / "transaction.json.lock"
        self.undo_root = self.transaction_root / "transaction.json.undo"
        self.stock = self.targets_root / "stock.bin"
        self.backup = self.backup_root / "stock-original.bin"
        self.created = self.runtime_state / "bridge" / "created.bin"
        self.artifact_root.mkdir()
        self.targets_root.mkdir()
        self.stock.write_bytes(b"stock payload")
        self.patched_source = self._artifact_file("payload/patched.bin", b"patched payload")
        self.created_source = self._artifact_file("payload/created.bin", b"created payload")
        self.source_tree = self._tree(self.artifact_root / "payload/ALVRMacOSBridge.app")
        self.marker_sha256 = artifact_contract.sha256_file(self.source_tree / MARKER)
        self.tree_sha256 = artifact_contract.canonical_tree_sha256(self.source_tree)
        self.stock_sha256 = artifact_contract.sha256_bytes(b"stock payload")
        self.patched_sha256 = artifact_contract.sha256_file(self.patched_source)

    def cleanup(self) -> None:
        self.temporary.cleanup()

    def _artifact_file(self, relative: str, payload: bytes) -> pathlib.Path:
        path = self.artifact_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    @staticmethod
    def _tree(root: pathlib.Path) -> pathlib.Path:
        marker = root / MARKER
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b'{"owner":"fixture"}\n')
        (root / "Contents/Resources/data.bin").write_bytes(b"tree payload")
        return root

    @staticmethod
    def _file_hash(path: pathlib.Path) -> str | None:
        return artifact_contract.sha256_file(path) if path.is_file() else None

    def _tree_owned(self) -> bool:
        return (
            self.bridge_bundle.is_dir()
            and artifact_contract.canonical_tree_sha256(self.bridge_bundle) == self.tree_sha256
        )

    def plan(self) -> dict[str, Any]:
        stock_hash = self._file_hash(self.stock)
        backup_hash = self._file_hash(self.backup)
        created_hash = self._file_hash(self.created)
        stock_ready = stock_hash == self.stock_sha256
        backup_ready = stock_hash is not None and (
            backup_hash is None or backup_hash == stock_hash
        )
        created_absent = not self.created.exists()
        bridge_exists = self.bridge_bundle.exists()
        bridge_marker_hash = (
            artifact_contract.sha256_file(self.bridge_bundle / MARKER)
            if (self.bridge_bundle / MARKER).is_file()
            else None
        )
        bridge_tree_hash = (
            artifact_contract.canonical_tree_sha256(self.bridge_bundle)
            if self.bridge_bundle.is_dir()
            else None
        )
        if not bridge_exists:
            tree_ready = True
            tree_ownership = "absent"
        elif bridge_marker_hash != self.marker_sha256:
            tree_ready = False
            tree_ownership = "foreign"
        elif bridge_tree_hash == self.tree_sha256:
            tree_ready = True
            tree_ownership = "artifact-owned"
        else:
            tree_ready = False
            tree_ownership = "qualification-required"
        install: list[dict[str, Any]] = [
            {
                "id": "assert_stock",
                "resource": "stock",
                "action": "assert_sha256",
                "target": str(self.stock),
                "expectedSha256": self.stock_sha256,
                "actualSha256": stock_hash,
                "ready": stock_ready,
                **({} if stock_ready else {"blockedReason": "stock target is not pristine"}),
            },
            {
                "id": "backup_stock",
                "resource": "stock",
                "action": "backup",
                "target": str(self.stock),
                "backup": str(self.backup),
                "targetSha256": stock_hash,
                "backupSha256": backup_hash,
                "ready": backup_ready,
                **({} if backup_ready else {"blockedReason": "backup does not match target"}),
            },
            {
                "id": "replace_stock",
                "resource": "stock",
                "action": "replace_file",
                "atomic": True,
                "source": str(self.patched_source),
                "sourceSha256": self.patched_sha256,
                "target": str(self.stock),
                "ready": stock_ready,
                **({} if stock_ready else {"blockedReason": "stock target is not pristine"}),
            },
            {
                "id": "assert_created_absent",
                "resource": "created",
                "action": "assert_absent",
                "target": str(self.created),
                "exists": self.created.exists(),
                "ready": created_absent,
                **({} if created_absent else {"blockedReason": "created target exists"}),
            },
            {
                "id": "create_runtime_file",
                "resource": "created",
                "action": "create_file",
                "atomic": True,
                "source": str(self.created_source),
                "sourceSha256": artifact_contract.sha256_file(self.created_source),
                "target": str(self.created),
                "ready": created_absent,
                **({} if created_absent else {"blockedReason": "created target exists"}),
            },
            {
                "id": "assert_bridge_owned",
                "resource": "bridge",
                "action": "assert_absent_or_owned",
                "source": str(self.source_tree),
                "sourceTreeSha256": self.tree_sha256,
                "marker": MARKER,
                "ownershipPolicy": "developer-id-bundle",
                "sourceMarkerSha256": self.marker_sha256,
                "target": str(self.bridge_bundle),
                "exists": bridge_exists,
                "ownership": tree_ownership,
                **(
                    {"targetMarkerSha256": bridge_marker_hash}
                    if bridge_marker_hash is not None
                    else {}
                ),
                **(
                    {"targetTreeSha256": bridge_tree_hash}
                    if bridge_tree_hash is not None
                    else {}
                ),
                "ready": tree_ready,
                **(
                    {}
                    if tree_ready
                    else {"blockedReason": "bridge tree requires lifecycle qualification"}
                ),
            },
            {
                "id": "replace_bridge",
                "resource": "bridge",
                "action": "replace_tree",
                "atomic": True,
                "retainOnUninstall": True,
                "source": str(self.source_tree),
                "sourceTreeSha256": self.tree_sha256,
                "target": str(self.bridge_bundle),
                "ready": True,
            },
        ]
        restore_ready = stock_hash == self.stock_sha256 or (
            stock_hash == self.patched_sha256 and backup_hash == self.stock_sha256
        )
        remove_ready = created_hash in {None, artifact_contract.sha256_file(self.created_source)}
        uninstall_tree_ready = self._tree_owned()
        uninstall: list[dict[str, Any]] = [
            {
                "id": "restore_stock",
                "resource": "stock",
                "action": "restore",
                "atomic": True,
                "source": str(self.patched_source),
                "sourceSha256": self.patched_sha256,
                "target": str(self.stock),
                "backup": str(self.backup),
                "expectedSha256": self.stock_sha256,
                "ready": restore_ready,
                **({} if restore_ready else {"blockedReason": "stock restoration is unproven"}),
            },
            {
                "id": "remove_runtime_file",
                "resource": "created",
                "action": "remove",
                "source": str(self.created_source),
                "sourceSha256": artifact_contract.sha256_file(self.created_source),
                "target": str(self.created),
                "ready": remove_ready,
                **({} if remove_ready else {"blockedReason": "runtime file is modified"}),
            },
            {
                "id": "retain_bridge",
                "resource": "bridge",
                "action": "retain_tree",
                "source": str(self.source_tree),
                "sourceTreeSha256": self.tree_sha256,
                "marker": MARKER,
                "sourceMarkerSha256": self.marker_sha256,
                "target": str(self.bridge_bundle),
                "ready": uninstall_tree_ready,
                **({} if uninstall_tree_ready else {"blockedReason": "bridge tree is modified"}),
            },
        ]
        mutable_state = [
            self._state("backup_root", "directory", self.backup_root),
            self._state("bridge_bundle_root", "directory", self.bridge_root),
            self._state("lifecycle_root", "directory", self.lifecycle_root),
            self._state("runtime_state", "directory", self.runtime_state),
            self._state("transaction_history", "directory", self.history_root),
            self._state("transaction_journal", "file", self.journal),
            self._state("transaction_journal_lock", "file", self.journal_lock),
            self._state("transaction_lock", "file", self.global_lock),
            self._state("transaction_root", "directory", self.transaction_root),
            self._state("transaction_undo", "directory", self.undo_root),
        ]
        install_blockers = [item["id"] for item in install if not item["ready"]]
        uninstall_blockers = [item["id"] for item in uninstall if not item["ready"]]
        plan = {
            "schemaVersion": 1,
            "artifact": str(self.artifact_root),
            "sealId": "a" * 64,
            "requiresSealing": False,
            "fixtureRoot": str(CODE_ROOT),
            "allowedTargetRoots": [str(self.root)],
            "mutableState": mutable_state,
            "install": install,
            "uninstall": uninstall,
            "installReady": not install_blockers,
            "uninstallReady": not uninstall_blockers,
            "installBlockers": install_blockers,
            "uninstallBlockers": uninstall_blockers,
        }
        self.assert_plan_fenced(plan)
        return plan

    @staticmethod
    def _state(item_id: str, kind: str, path: pathlib.Path) -> dict[str, Any]:
        return {
            "id": item_id,
            "kind": kind,
            "owner": "runtime",
            "lifecycle": "retained",
            "location": str(path),
            "exists": path.exists(),
        }

    def assert_plan_fenced(self, plan: dict[str, Any]) -> None:
        for operation in [*plan["install"], *plan["uninstall"]]:
            for field in ("target", "backup"):
                raw = operation.get(field)
                if raw is not None:
                    self._assert_under_root(pathlib.Path(raw))
        for record in plan["mutableState"]:
            self._assert_under_root(pathlib.Path(record["location"]))

    def _assert_under_root(self, path: pathlib.Path) -> None:
        self.assert_common_path(path)

    def assert_common_path(self, path: pathlib.Path) -> None:
        if os.path.commonpath([str(path), str(self.root)]) != str(self.root):
            raise AssertionError(f"fixture path escaped root: {path}")


class ProfilePlanFixture:
    def __init__(self, lifecycle: LifecycleFixture) -> None:
        self.lifecycle = lifecycle
        self.sources = {
            "game_openvr": lifecycle._artifact_file(
                "payload/windows/openvr_api.dll",
                b"profile openvr shim",
            ),
            "custom_openvr_runtime": lifecycle._artifact_file(
                "payload/windows/openvr_api.real.dll",
                b"profile fake runtime",
            ),
            "dxvk_d3d11": lifecycle._artifact_file(
                "payload/windows/d3d11.dll",
                b"profile d3d11",
            ),
            "dxvk_dxgi": lifecycle._artifact_file(
                "payload/windows/dxgi.dll",
                b"profile dxgi",
            ),
            "game_wine_bridge_windows": lifecycle._artifact_file(
                "payload/windows/alvr_iosurface_bridge.dll",
                b"profile wine bridge",
            ),
        }
        targets: list[runtime_profile.ResolvedProfileTarget] = []
        self.stock_payloads: dict[str, bytes] = {}
        for target_id in ("hub", "secret-shop", "robot-repair"):
            target_root = lifecycle.targets_root / target_id
            openvr_directory = target_root / "openvr"
            graphics_directory = target_root / "graphics"
            openvr_directory.mkdir(parents=True)
            graphics_directory.mkdir(parents=True)
            stock_payload = f"stock {target_id}".encode()
            (openvr_directory / "openvr_api.dll").write_bytes(stock_payload)
            stock_sha256 = artifact_contract.sha256_bytes(stock_payload)
            self.stock_payloads[target_id] = stock_payload
            targets.append(
                runtime_profile.ResolvedProfileTarget(
                    id=target_id,
                    role="hub" if target_id == "hub" else "experience",
                    executable=target_root / f"{target_id}.exe",
                    working_directory=target_root,
                    openvr_directory=openvr_directory,
                    graphics_directory=graphics_directory,
                    stock_openvr_sha256=stock_sha256,
                    process_pattern=f"[{target_id[0]}]{target_id[1:]}",
                )
            )
        self.profile = runtime_install.ProfilePlanBinding(
            id="the-lab",
            sha256="f" * 64,
            install_root=lifecycle.targets_root,
            targets=tuple(targets),
        )
        legacy_openvr = lifecycle.targets_root / "legacy/openvr"
        legacy_graphics = lifecycle.targets_root / "legacy/graphics"
        legacy_openvr.mkdir(parents=True)
        legacy_graphics.mkdir(parents=True)
        (legacy_openvr / "openvr_api.dll").write_bytes(b"legacy stock")
        legacy_backup = lifecycle.backup_root / "legacy-openvr.dll"
        self.manifest: dict[str, list[dict[str, Any]]] = {
            "installPlan": [
                {
                    "id": "retain_shared_install",
                    "resource": "shared_runtime",
                    "action": "retain",
                    "target": str(lifecycle.stock),
                },
                {
                    "id": "verify_stock_openvr",
                    "resource": "game_openvr",
                    "action": "assert_sha256",
                    "expectedSha256": artifact_contract.sha256_bytes(b"legacy stock"),
                    "target": str(legacy_openvr / "openvr_api.dll"),
                },
                {
                    "id": "backup_stock_openvr",
                    "resource": "game_openvr",
                    "action": "backup",
                    "backup": str(legacy_backup),
                    "target": str(legacy_openvr / "openvr_api.dll"),
                },
                {
                    "id": "install_openvr_shim",
                    "resource": "game_openvr",
                    "action": "replace_file",
                    "atomic": True,
                    "source": "payload/windows/openvr_api.dll",
                    "target": str(legacy_openvr / "openvr_api.dll"),
                },
                *self._create_templates(
                    "custom_openvr_runtime",
                    "fake_runtime",
                    legacy_openvr / "openvr_api.real.dll",
                    "payload/windows/openvr_api.real.dll",
                ),
                *self._create_templates(
                    "dxvk_d3d11",
                    "d3d11",
                    legacy_graphics / "d3d11.dll",
                    "payload/windows/d3d11.dll",
                ),
                *self._create_templates(
                    "dxvk_dxgi",
                    "dxgi",
                    legacy_graphics / "dxgi.dll",
                    "payload/windows/dxgi.dll",
                ),
                *self._create_templates(
                    "game_wine_bridge_windows",
                    "game_wine_bridge_windows",
                    legacy_graphics / "alvr_iosurface_bridge.dll",
                    "payload/windows/alvr_iosurface_bridge.dll",
                ),
                {
                    "id": "retain_stock_openvr_backup",
                    "resource": "game_openvr",
                    "action": "retain",
                    "target": str(legacy_backup),
                },
            ],
            "uninstallPlan": [
                {
                    "id": "restore_stock_openvr",
                    "resource": "game_openvr",
                    "action": "restore",
                    "atomic": True,
                    "source": "payload/windows/openvr_api.dll",
                    "target": str(legacy_openvr / "openvr_api.dll"),
                    "backup": str(legacy_backup),
                    "expectedSha256": artifact_contract.sha256_bytes(b"legacy stock"),
                },
                *self._remove_templates(
                    "custom_openvr_runtime",
                    "fake_runtime",
                    legacy_openvr / "openvr_api.real.dll",
                    "payload/windows/openvr_api.real.dll",
                ),
                *self._remove_templates(
                    "dxvk_d3d11",
                    "d3d11",
                    legacy_graphics / "d3d11.dll",
                    "payload/windows/d3d11.dll",
                ),
                *self._remove_templates(
                    "dxvk_dxgi",
                    "dxgi",
                    legacy_graphics / "dxgi.dll",
                    "payload/windows/dxgi.dll",
                ),
                *self._remove_templates(
                    "game_wine_bridge_windows",
                    "game_wine_bridge_windows",
                    legacy_graphics / "alvr_iosurface_bridge.dll",
                    "payload/windows/alvr_iosurface_bridge.dll",
                ),
                {
                    "id": "retain_stock_openvr_backup_after_restore",
                    "resource": "game_openvr",
                    "action": "retain",
                    "target": str(legacy_backup),
                },
                {
                    "id": "retain_shared_uninstall",
                    "resource": "shared_runtime",
                    "action": "retain",
                    "target": str(lifecycle.stock),
                },
            ],
        }
        base_plan = lifecycle.plan()
        allowed_roots = [lifecycle.root]
        base_plan["install"] = [
            artifact_contract.resolve_plan_operation(
                item,
                lifecycle.artifact_root,
                {},
                allowed_roots,
            )
            for item in self.manifest["installPlan"]
        ]
        base_plan["uninstall"] = [
            artifact_contract.resolve_plan_operation(
                item,
                lifecycle.artifact_root,
                {},
                allowed_roots,
            )
            for item in self.manifest["uninstallPlan"]
        ]
        self.plan = runtime_install._materialize_profile_plan(
            base_plan,
            self.manifest,
            lifecycle.artifact_root,
            self.profile,
            {},
        )

    @staticmethod
    def _create_templates(
        resource: str,
        identifier: str,
        target: pathlib.Path,
        source: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": f"verify_{identifier}_absent",
                "resource": resource,
                "action": "assert_absent",
                "target": str(target),
            },
            {
                "id": f"install_{identifier}",
                "resource": resource,
                "action": "create_file",
                "atomic": True,
                "source": source,
                "target": str(target),
            },
        ]

    @staticmethod
    def _remove_templates(
        resource: str,
        identifier: str,
        target: pathlib.Path,
        source: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": f"remove_{identifier}",
                "resource": resource,
                "action": "remove",
                "source": source,
                "target": str(target),
            }
        ]

    def prepare_executor(self, kind: runtime_install.MutationKind) -> runtime_transaction.TransactionExecutor:
        paths = runtime_install.lifecycle_paths(self.plan)
        runtime_install.ensure_private_directory(
            paths.transaction_root,
            paths.allowed_roots,
            "transaction_root",
        )
        if kind == "install":
            runtime_install._provision_install_directories(self.plan, paths)
        return runtime_install._executor(kind, self.plan, paths)


@contextlib.contextmanager
def lifecycle_fixture() -> Iterator[LifecycleFixture]:
    fixture = LifecycleFixture()
    try:
        yield fixture
    finally:
        fixture.cleanup()


@contextlib.contextmanager
def patched_lifecycle(
    fixture: LifecycleFixture,
    *,
    artifact_stage: str = "sealed",
) -> Iterator[tuple[RuntimeContext, mock.Mock, mock.Mock]]:
    context = RuntimeContext(
        bindings_path=fixture.root / "bindings.json",
        lifecycle_lock_path=fixture.global_lock,
        runner=ClosedTargetRunner(),
    )
    manifest = {"sealing": {"mode": "separate-step"}}
    artifact = {
        "path": str(fixture.artifact_root),
        "id": "fixture-runtime",
        "version": "1.0.0-dev6",
        "sealId": "a" * 64,
        "stage": artifact_stage,
    }
    doctor = DoctorReport(
        (
            CheckResult(
                "fixture.ready",
                "pass",
                "fixture prerequisites are ready",
                "none",
            ),
        ),
        artifact,
    )
    stop = StopReport(
        True,
        "stopped",
        "runtime.stopped",
        "fixture runtime is stopped",
        ("already-stopped",),
        {"present": False},
        {"alive": False},
    )
    status = StatusReport(
        "stopped",
        "runtime.stopped",
        "fixture runtime is stopped",
        artifact,
        {"present": False},
        {"alive": False},
        {"exists": False},
    )
    with (
        mock.patch.object(
            runtime_install,
            "load_runtime_contract",
            return_value=(manifest, {}, "b" * 64, "c" * 64),
        ),
        mock.patch.object(runtime_install, "verify_artifact_reference", return_value=artifact),
        mock.patch.object(runtime_install, "_build_plan", side_effect=lambda *args: fixture.plan()) as plan,
        mock.patch.object(
            runtime_install,
            "_inspect_stable_bundle",
            return_value={
                "kind": "developer-id",
                "identifier": "example.fixture",
                "teamIdentifier": "FIXTURETEAM",
                "cdhash": "d" * 40,
                "authority": "Developer ID Application: Fixture",
                "timestamp": False,
            },
        ),
        mock.patch.object(runtime_install, "doctor_runtime", return_value=doctor) as doctor_mock,
        mock.patch.object(runtime_install, "status_runtime", return_value=status),
        mock.patch.object(runtime_install, "stop_runtime", return_value=stop) as stop_mock,
    ):
        yield context, doctor_mock, stop_mock


class RuntimeInstallTests(unittest.TestCase):
    def test_live_supervisor_stops_before_both_lifecycle_directions(self) -> None:
        states = {2: "idle", 3: "waiting", 4: "waiting", 5: "streaming"}
        for schema_version, state in states.items():
            with self.subTest(schema_version=schema_version), lifecycle_fixture() as fixture:
                lock_held = False
                original_global_lock = runtime_install.global_lifecycle_lock

                @contextlib.contextmanager
                def tracked_global_lock(*args: Any, **kwargs: Any) -> Iterator[None]:
                    nonlocal lock_held
                    with original_global_lock(*args, **kwargs):
                        lock_held = True
                        try:
                            yield
                        finally:
                            lock_held = False

                live = StatusReport(
                    state,
                    f"runtime.{state}",
                    "fixture supervisor is live",
                    {"sealId": "a" * 64},
                    {"present": True},
                    {"alive": True},
                    {"record": {"schemaVersion": schema_version}},
                )
                with (
                    patched_lifecycle(fixture) as (context, _, stop_mock),
                    mock.patch.object(
                        runtime_install,
                        "status_runtime",
                        return_value=live,
                    ),
                    mock.patch.object(
                        runtime_install,
                        "global_lifecycle_lock",
                        new=tracked_global_lock,
                    ),
                ):
                    stopped_report = stop_mock.return_value
                    stop_lock_states: list[bool] = []

                    def tracked_stop(_: RuntimeContext) -> StopReport:
                        stop_lock_states.append(lock_held)
                        return stopped_report

                    stop_mock.side_effect = tracked_stop
                    install = runtime_install.install_runtime(context, fixture.artifact_root)
                    install_replay = runtime_install.install_runtime(
                        context, fixture.artifact_root
                    )
                    uninstall = runtime_install.uninstall_runtime(context, fixture.artifact_root)
                self.assertTrue(install.ok)
                self.assertTrue(install_replay.ok)
                self.assertTrue(uninstall.ok)
                self.assertEqual(stop_mock.call_count, 6)
                self.assertEqual(
                    stop_lock_states,
                    [False, True, False, True, False, True],
                )
                self.assertEqual(install.stop_actions.count("already-stopped"), 2)
                self.assertEqual(
                    install_replay.stop_actions.count("already-stopped"), 2
                )
                self.assertEqual(uninstall.stop_actions.count("already-stopped"), 2)

    def test_sealing_gate_has_zero_lifecycle_mutation(self) -> None:
        with lifecycle_fixture() as fixture:
            with patched_lifecycle(fixture, artifact_stage="unsealed") as (
                context,
                doctor_mock,
                stop_mock,
            ):
                for command in (runtime_install.install_runtime, runtime_install.uninstall_runtime):
                    report = command(context, fixture.artifact_root)
                    self.assertFalse(report.ok)
                    self.assertEqual(report.reason_code, "artifact.sealing_required")
            doctor_mock.assert_not_called()
            stop_mock.assert_not_called()
            self.assertFalse(fixture.transaction_root.exists())
            self.assertEqual(fixture.stock.read_bytes(), b"stock payload")

    def test_sealed_declared_roots_use_descriptor_transactions(self) -> None:
        with lifecycle_fixture() as fixture:
            with patched_lifecycle(fixture) as (context, doctor_mock, stop_mock):
                plan_mock = cast(mock.Mock, runtime_install._build_plan)

                def live_plan(*_: Any) -> dict[str, Any]:
                    plan = fixture.plan()
                    plan["allowedTargetRoots"] = [
                        *plan["allowedTargetRoots"],
                        str(pathlib.Path.home()),
                    ]
                    return plan

                plan_mock.side_effect = live_plan
                report = runtime_install.install_runtime(context, fixture.artifact_root)
                self.assertTrue(report.ok)
                self.assertEqual(report.reason_code, "transaction.committed")
            doctor_mock.assert_called_once()
            stop_mock.assert_called_once()
            self.assertEqual(fixture.stock.read_bytes(), b"patched payload")

    def test_install_uninstall_replay_and_reinstall_cycle(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            _,
        ):
            installed = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertTrue(installed.ok)
            self.assertEqual(installed.state, "committed")
            self.assertEqual(fixture.stock.read_bytes(), b"patched payload")
            self.assertEqual(fixture.backup.read_bytes(), b"stock payload")
            self.assertEqual(fixture.created.read_bytes(), b"created payload")
            self.assertTrue(fixture.bridge_bundle.is_dir())
            self.assertEqual(fixture.transaction_root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(fixture.global_lock.stat().st_mode & 0o777, 0o600)
            self.assertEqual(fixture.journal.stat().st_mode & 0o777, 0o600)

            replay = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertTrue(replay.ok)
            self.assertEqual(replay.state, "already-committed")
            self.assertEqual(replay.transaction_id, installed.transaction_id)
            self.assertEqual(replay.stop_actions, ("already-stopped",))

            uninstalled = runtime_install.uninstall_runtime(context, fixture.artifact_root)
            self.assertTrue(uninstalled.ok)
            self.assertEqual(fixture.stock.read_bytes(), b"stock payload")
            self.assertFalse(fixture.created.exists())
            self.assertTrue(fixture._tree_owned())
            self.assertTrue(fixture.backup.is_file())
            self.assertIsNotNone(uninstalled.archived_journal)

            uninstall_replay = runtime_install.uninstall_runtime(context, fixture.artifact_root)
            self.assertTrue(uninstall_replay.ok)
            self.assertEqual(uninstall_replay.state, "already-committed")
            self.assertEqual(uninstall_replay.stop_actions, ("already-stopped",))

            reinstalled = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertTrue(reinstalled.ok)
            self.assertNotEqual(reinstalled.transaction_id, installed.transaction_id)
            self.assertEqual(len(list(fixture.history_root.glob("*.json"))), 2)

    def test_committed_replay_surfaces_stopped_state_failures(self) -> None:
        failure_codes = (
            "producer.orphaned",
            "producer.identity_unavailable",
            "service.foreign",
            "runtime.cleanup_failed",
        )
        commands = {
            "install": (
                runtime_install.install_runtime,
                b"patched payload",
                0,
            ),
            "uninstall": (
                runtime_install.uninstall_runtime,
                b"stock payload",
                1,
            ),
        }
        for command, (
            replay_command,
            expected_payload,
            expected_history_count,
        ) in commands.items():
            for failure_code in failure_codes:
                with (
                    self.subTest(command=command, failure_code=failure_code),
                    lifecycle_fixture() as fixture,
                ):
                    with patched_lifecycle(fixture) as (context, _, stop_mock):
                        installed = runtime_install.install_runtime(
                            context,
                            fixture.artifact_root,
                        )
                        self.assertTrue(installed.ok)
                        committed = installed
                        if command == "uninstall":
                            committed = runtime_install.uninstall_runtime(
                                context,
                                fixture.artifact_root,
                            )
                            self.assertTrue(committed.ok)
                        failed_status = StatusReport(
                            "failed",
                            failure_code,
                            "fixture stale runtime state requires exact cleanup",
                            committed.artifact,
                            {"present": False},
                            {"alive": False},
                            {"record": {"schemaVersion": 5}},
                        )
                        failed_stop = StopReport(
                            False,
                            "failed",
                            failure_code,
                            "fixture stopped-state verification failed",
                            (f"preserve {failure_code}",),
                            {"present": False},
                            {"alive": False},
                        )
                        stop_mock.reset_mock()
                        lock_held = False
                        stop_lock_states: list[bool] = []
                        original_global_lock = runtime_install.global_lifecycle_lock

                        @contextlib.contextmanager
                        def tracked_global_lock(
                            *args: Any,
                            **kwargs: Any,
                        ) -> Iterator[None]:
                            nonlocal lock_held
                            with original_global_lock(*args, **kwargs):
                                lock_held = True
                                try:
                                    yield
                                finally:
                                    lock_held = False

                        def failed_stopped_state(_: RuntimeContext) -> StopReport:
                            stop_lock_states.append(lock_held)
                            return failed_stop

                        stop_mock.side_effect = failed_stopped_state
                        with (
                            mock.patch.object(
                                runtime_install,
                                "status_runtime",
                                return_value=failed_status,
                            ),
                            mock.patch.object(
                                runtime_install,
                                "global_lifecycle_lock",
                                new=tracked_global_lock,
                            ),
                        ):
                            replay = replay_command(
                                context,
                                fixture.artifact_root,
                            )

                    self.assertFalse(replay.ok)
                    self.assertEqual(replay.state, "blocked")
                    self.assertEqual(replay.reason_code, failure_code)
                    self.assertIsNone(replay.transaction_id)
                    self.assertEqual(replay.stop_actions, (f"preserve {failure_code}",))
                    stop_mock.assert_called_once_with(context)
                    self.assertEqual(stop_lock_states, [True])
                    journal = json.loads(fixture.journal.read_text())
                    self.assertEqual(journal["kind"], command)
                    self.assertEqual(journal["state"], "committed")
                    self.assertEqual(
                        len(list(fixture.history_root.glob("*.json"))),
                        expected_history_count,
                    )
                    self.assertEqual(fixture.stock.read_bytes(), expected_payload)

    def test_prior_committed_plan_journal_archives_before_new_install(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            _,
        ):
            installed = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertTrue(installed.ok)
            uninstalled = runtime_install.uninstall_runtime(context, fixture.artifact_root)
            self.assertTrue(uninstalled.ok)
            previous_transaction_id = uninstalled.transaction_id

            fixture.patched_source.write_bytes(b"patched payload v2")
            fixture.patched_sha256 = artifact_contract.sha256_file(fixture.patched_source)
            upgraded = runtime_install.install_runtime(context, fixture.artifact_root)

            self.assertTrue(upgraded.ok, upgraded.to_dict())
            self.assertEqual(upgraded.state, "committed")
            self.assertIsNotNone(upgraded.archived_journal)
            assert upgraded.archived_journal is not None
            self.assertIn(previous_transaction_id or "", upgraded.archived_journal.name)
            self.assertEqual(fixture.stock.read_bytes(), b"patched payload v2")
            self.assertEqual(len(list(fixture.history_root.glob("*.json"))), 2)

    def test_verified_prior_bundle_migrates_and_is_retained(self) -> None:
        with lifecycle_fixture() as fixture:
            fixture._tree(fixture.bridge_bundle)
            fixture.bridge_root.chmod(0o700)
            prior_payload = fixture.bridge_bundle / "Contents/Resources/data.bin"
            prior_payload.write_bytes(b"prior signed tree payload")
            prior_tree = artifact_contract.canonical_tree_sha256(fixture.bridge_bundle)
            self.assertNotEqual(prior_tree, fixture.tree_sha256)

            with patched_lifecycle(fixture) as (context, _, stop_mock):
                installed = runtime_install.install_runtime(context, fixture.artifact_root)
                self.assertTrue(installed.ok, installed.to_dict())
                self.assertTrue(fixture._tree_owned())

                uninstalled = runtime_install.uninstall_runtime(context, fixture.artifact_root)
                self.assertTrue(uninstalled.ok)
                self.assertTrue(fixture._tree_owned())
                self.assertEqual(fixture.stock.read_bytes(), b"stock payload")
                self.assertFalse(fixture.created.exists())
                self.assertEqual(stop_mock.call_count, 2)

    def test_invalid_prior_bundle_signature_blocks_without_target_mutation(self) -> None:
        with lifecycle_fixture() as fixture:
            fixture._tree(fixture.bridge_bundle)
            fixture.bridge_root.chmod(0o700)
            prior_payload = fixture.bridge_bundle / "Contents/Resources/data.bin"
            prior_payload.write_bytes(b"untrusted prior tree payload")
            prior_tree = artifact_contract.canonical_tree_sha256(fixture.bridge_bundle)

            with patched_lifecycle(fixture) as (context, doctor_mock, stop_mock):
                with mock.patch.object(
                    runtime_install,
                    "_inspect_stable_bundle",
                    side_effect=artifact_contract.ArtifactError(
                        "sealing.signature",
                        "fixture signature is invalid",
                    ),
                ):
                    report = runtime_install.install_runtime(context, fixture.artifact_root)

            self.assertFalse(report.ok)
            self.assertEqual(report.reason_code, "plan.blocked")
            self.assertEqual(
                artifact_contract.canonical_tree_sha256(fixture.bridge_bundle),
                prior_tree,
            )
            self.assertFalse(fixture.journal.exists())
            self.assertFalse(fixture.global_lock.exists())
            doctor_mock.assert_not_called()
            stop_mock.assert_not_called()

    def test_three_cycles_restore_non_anchor_state_and_retain_bundle(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            _,
        ):
            for _ in range(3):
                installed = runtime_install.install_runtime(context, fixture.artifact_root)
                self.assertTrue(installed.ok)
                self.assertEqual(fixture.stock.read_bytes(), b"patched payload")
                self.assertTrue(fixture.created.is_file())
                self.assertTrue(fixture._tree_owned())

                uninstalled = runtime_install.uninstall_runtime(context, fixture.artifact_root)
                self.assertTrue(uninstalled.ok)
                self.assertEqual(fixture.stock.read_bytes(), b"stock payload")
                self.assertFalse(fixture.created.exists())
                self.assertTrue(fixture._tree_owned())

    def test_interrupted_install_recovers_before_uninstall(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            stop_mock,
        ):
            real_executor = runtime_install._executor

            def crashing_executor(
                kind: runtime_install.MutationKind,
                plan: dict[str, Any],
                paths: runtime_install.LifecyclePaths,
                *,
                tree_ownership_validator: runtime_transaction.TreeOwnershipValidator | None = None,
            ) -> runtime_transaction.TransactionExecutor:
                executor = real_executor(
                    kind,
                    plan,
                    paths,
                    tree_ownership_validator=tree_ownership_validator,
                )
                if kind == "install":
                    def crash(step_id: str, phase: str) -> None:
                        if step_id == "replace_stock" and phase == "after-mutation":
                            raise SimulatedCrash("fixture crash")

                    executor.failure_injector = crash
                return executor

            with mock.patch.object(runtime_install, "_executor", side_effect=crashing_executor):
                with self.assertRaises(SimulatedCrash):
                    runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertEqual(json.loads(fixture.journal.read_text())["state"], "running")

            recovered = runtime_install.uninstall_runtime(context, fixture.artifact_root)
            self.assertFalse(recovered.ok)
            self.assertEqual(recovered.state, "recovered")
            self.assertEqual(recovered.reason_code, "transaction.retry_required")
            self.assertEqual(fixture.stock.read_bytes(), b"stock payload")
            self.assertFalse(fixture.journal.exists())
            self.assertEqual(len(list(fixture.history_root.glob("*.json"))), 1)
            self.assertEqual(stop_mock.call_count, 2)

    def test_capacity_refuses_before_journal_or_target_mutation(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            stop_mock,
        ):
            with mock.patch.object(runtime_install, "filesystem_free_bytes", return_value=0):
                report = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertFalse(report.ok)
            self.assertEqual(report.reason_code, "capacity.insufficient")
            self.assertFalse(fixture.journal.exists())
            self.assertEqual(fixture.stock.read_bytes(), b"stock payload")
            self.assertFalse(fixture.created.exists())
            self.assertFalse(fixture.transaction_root.exists())
            stop_mock.assert_not_called()

    def test_global_lock_serializes_both_directions(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            _,
        ):
            fixture._tree(fixture.bridge_bundle)
            fixture.bridge_root.chmod(0o700)
            fixture.global_lock_root.mkdir(mode=0o700, exist_ok=True)
            holder_script = """
import fcntl
import os
import sys

descriptor = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(descriptor, fcntl.LOCK_EX)
print("ready", flush=True)
sys.stdin.readline()
fcntl.flock(descriptor, fcntl.LOCK_UN)
os.close(descriptor)
"""
            holder = subprocess.Popen(
                [sys.executable, "-c", holder_script, str(fixture.global_lock)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                holder_stdout = holder.stdout
                self.assertIsNotNone(holder_stdout)
                if holder_stdout is None:
                    raise AssertionError("lock holder stdout is unavailable")
                self.assertEqual(holder_stdout.readline().strip(), "ready")
                for command in (runtime_install.install_runtime, runtime_install.uninstall_runtime):
                    report = command(context, fixture.artifact_root)
                    self.assertFalse(report.ok)
                    self.assertEqual(report.reason_code, "transaction.busy")
            finally:
                if holder.stdin is not None and holder.poll() is None:
                    holder.stdin.write("release\n")
                    holder.stdin.flush()
                    holder.stdin.close()
                if holder.poll() is None:
                    holder.wait(timeout=10)
                if holder.stdout is not None:
                    holder.stdout.close()
            self.assertFalse(fixture.journal.exists())

    def test_plan_blocker_and_open_target_fail_closed(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            stop_mock,
        ):
            fixture.stock.write_bytes(b"foreign stock")
            blocked = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertEqual(blocked.reason_code, "plan.blocked")
            self.assertEqual(blocked.blockers[0]["id"], "assert_stock")
            self.assertFalse(fixture.journal.exists())
            self.assertFalse(fixture.transaction_root.exists())
            stop_mock.assert_not_called()

        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            _,
        ):
            context.runner = BusyTargetRunner()
            busy = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertEqual(busy.reason_code, "runtime.target_busy")
            self.assertFalse(fixture.journal.exists())
            self.assertEqual(fixture.stock.read_bytes(), b"stock payload")

    def test_missing_external_parent_blocks_before_stop(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            stop_mock,
        ):
            fixture.created = fixture.root / "external-missing" / "created.bin"
            blocked = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertEqual(blocked.reason_code, "plan.blocked")
            self.assertTrue(
                any("mutation parent is missing" in blocker["reason"] for blocker in blocked.blockers)
            )
            stop_mock.assert_not_called()
            self.assertFalse(fixture.transaction_root.exists())

    def test_tree_open_file_inspection_enumerates_bundle_contents(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            _,
        ):
            fixture._tree(fixture.bridge_bundle)
            fixture.bridge_root.chmod(0o700)
            (fixture.bridge_bundle / "Contents/Resources/data.bin").write_bytes(
                b"prior signed tree payload"
            )
            runner = RecordingClosedTargetRunner()
            context.runner = runner
            installed = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertTrue(installed.ok)
            inspected = {argument for call in runner.calls for argument in call}
            self.assertIn(
                str(fixture.bridge_bundle / "Contents/Resources/data.bin"),
                inspected,
            )

    def test_busy_recovery_preserves_incomplete_journal(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            _,
        ):
            real_executor = runtime_install._executor

            def crashing_executor(
                kind: runtime_install.MutationKind,
                plan: dict[str, Any],
                paths: runtime_install.LifecyclePaths,
                *,
                tree_ownership_validator: runtime_transaction.TreeOwnershipValidator | None = None,
            ) -> runtime_transaction.TransactionExecutor:
                executor = real_executor(
                    kind,
                    plan,
                    paths,
                    tree_ownership_validator=tree_ownership_validator,
                )
                if kind == "install":
                    def crash(step_id: str, phase: str) -> None:
                        if step_id == "replace_stock" and phase == "after-mutation":
                            raise SimulatedCrash("fixture crash")

                    executor.failure_injector = crash
                return executor

            with mock.patch.object(runtime_install, "_executor", side_effect=crashing_executor):
                with self.assertRaises(SimulatedCrash):
                    runtime_install.install_runtime(context, fixture.artifact_root)
            context.runner = BusyTargetRunner()
            blocked = runtime_install.uninstall_runtime(context, fixture.artifact_root)
            self.assertEqual(blocked.reason_code, "runtime.target_busy")
            self.assertEqual(json.loads(fixture.journal.read_text())["state"], "running")

    def test_transaction_root_symlink_is_rejected(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            _,
        ):
            outside = fixture.root / "outside"
            outside.mkdir()
            fixture.transaction_root.symlink_to(outside, target_is_directory=True)
            report = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertFalse(report.ok)
            self.assertIn(report.reason_code, {"path.symlink", "transaction.path_unsafe"})
            self.assertEqual(fixture.stock.read_bytes(), b"stock payload")

    def test_planner_emits_tree_digest_and_allowed_roots(self) -> None:
        with lifecycle_fixture() as fixture:
            operation = artifact_contract.resolve_plan_operation(
                {
                    "id": "assert_bridge_owned",
                    "resource": "bridge",
                    "action": "assert_absent_or_owned",
                    "source": "payload/ALVRMacOSBridge.app",
                    "marker": MARKER,
                    "target": "${TARGET}",
                },
                fixture.artifact_root,
                {"TARGET": str(fixture.bridge_bundle)},
                [fixture.root],
            )
            self.assertEqual(operation["sourceTreeSha256"], fixture.tree_sha256)
            self.assertTrue(operation["ready"])

    def test_tree_capacity_counts_empty_entries(self) -> None:
        with lifecycle_fixture() as fixture:
            empty_tree = fixture.root / "empty-tree"
            empty_tree.mkdir()
            for index in range(100):
                (empty_tree / f"empty-{index:03d}").touch()
            self.assertGreaterEqual(
                runtime_install._tree_bytes(empty_tree),
                101 * runtime_install.CAPACITY_ENTRY_BYTES,
            )

    def test_cleanup_failure_preserves_committed_state(self) -> None:
        transaction = {
            "schemaVersion": 1,
            "ok": False,
            "state": "committed",
            "transactionId": "a" * 32,
            "planDigest": "b" * 64,
            "journal": "/fixture/transaction.json",
            "applied": ["replace_stock"],
            "rolledBack": [],
            "failure": None,
            "rollbackFailures": [],
            "cleanupFailures": ["undo cleanup failed"],
        }
        error = runtime_transaction.TransactionError(
            "transaction.cleanup_failed",
            "Transaction committed but cleanup did not complete",
            report=transaction,
        )
        report = runtime_install._error_report("install", None, error)
        self.assertFalse(report.ok)
        self.assertEqual(report.state, "committed")
        self.assertEqual(report.cleanup_failures, ("undo cleanup failed",))

    def test_profile_plan_expands_every_target_and_validates_unchanged(self) -> None:
        with lifecycle_fixture() as lifecycle:
            fixture = ProfilePlanFixture(lifecycle)
            plan = fixture.plan
            self.assertEqual(plan["schemaVersion"], 2)
            self.assertEqual(plan["profile"]["id"], "the-lab")
            self.assertEqual(
                [target["id"] for target in plan["profile"]["targets"]],
                ["hub", "secret-shop", "robot-repair"],
            )
            install_effects = [
                operation
                for operation in plan["install"]
                if operation["action"] in runtime_transaction.INSTALL_EFFECTS
            ]
            self.assertEqual(len(install_effects), 15)
            self.assertEqual(
                sum(operation["id"] == "retain_shared_install" for operation in plan["install"]),
                1,
            )
            self.assertEqual(
                len({operation["resource"] for operation in install_effects}),
                len(install_effects),
            )
            for target in fixture.profile.targets:
                suffix = target.id.replace("-", "_")
                guard = next(
                    operation
                    for operation in plan["install"]
                    if operation["id"] == f"verify_stock_openvr_{suffix}"
                )
                replacement = next(
                    operation
                    for operation in install_effects
                    if operation["id"] == f"install_openvr_shim_{suffix}"
                )
                backup = next(
                    operation
                    for operation in plan["install"]
                    if operation["id"] == f"backup_stock_openvr_{suffix}"
                )
                self.assertEqual(guard["expectedSha256"], target.stock_openvr_sha256)
                self.assertEqual(
                    replacement["target"],
                    str(target.openvr_directory / "openvr_api.dll"),
                )
                self.assertIn(f"the-lab-{target.id}", backup["backup"])

            executor = fixture.prepare_executor("install")
            executor.validate()
            self.assertEqual(executor.plan_identity, fixture.profile.plan_identity)

    def test_profile_plan_admits_all_targets_before_first_mutation(self) -> None:
        with lifecycle_fixture() as lifecycle:
            fixture = ProfilePlanFixture(lifecycle)
            missing_target = fixture.profile.targets[1]
            (missing_target.openvr_directory / "openvr_api.dll").unlink()
            executor = fixture.prepare_executor("install")

            with self.assertRaises(runtime_transaction.TransactionError):
                executor.execute()

            self.assertFalse(lifecycle.journal.exists())
            for target in fixture.profile.targets:
                stock = target.openvr_directory / "openvr_api.dll"
                if target.id != missing_target.id:
                    self.assertEqual(stock.read_bytes(), fixture.stock_payloads[target.id])
                self.assertFalse((target.openvr_directory / "openvr_api.real.dll").exists())
                self.assertFalse((target.graphics_directory / "d3d11.dll").exists())
                self.assertFalse((target.graphics_directory / "dxgi.dll").exists())
                self.assertFalse(
                    (target.graphics_directory / "alvr_iosurface_bridge.dll").exists()
                )

    def test_profile_plan_rolls_back_every_target_on_failure(self) -> None:
        with lifecycle_fixture() as lifecycle:
            fixture = ProfilePlanFixture(lifecycle)
            executor = fixture.prepare_executor("install")

            def fail_last_target(step_id: str, phase: str) -> None:
                if (
                    step_id == "install_game_wine_bridge_windows_robot_repair"
                    and phase == "after-mutation"
                ):
                    raise RuntimeError("fixture failure")

            executor.failure_injector = fail_last_target
            with self.assertRaises(runtime_transaction.TransactionError) as raised:
                executor.execute()
            self.assertEqual(raised.exception.code, "transaction.rolled_back")
            for target in fixture.profile.targets:
                self.assertEqual(
                    (target.openvr_directory / "openvr_api.dll").read_bytes(),
                    fixture.stock_payloads[target.id],
                )
                self.assertFalse((target.openvr_directory / "openvr_api.real.dll").exists())
                self.assertFalse((target.graphics_directory / "d3d11.dll").exists())
                self.assertFalse((target.graphics_directory / "dxgi.dll").exists())
                self.assertFalse(
                    (target.graphics_directory / "alvr_iosurface_bridge.dll").exists()
                )

    def test_profile_plan_uninstalls_every_target_in_one_transaction(self) -> None:
        with lifecycle_fixture() as lifecycle:
            fixture = ProfilePlanFixture(lifecycle)
            install_executor = fixture.prepare_executor("install")
            installed = install_executor.execute()
            self.assertEqual(installed.state, "committed")
            paths = runtime_install.lifecycle_paths(fixture.plan)
            runtime_install._archive_terminal_journal(paths, install_executor)

            uninstalled = fixture.prepare_executor("uninstall").execute()
            self.assertEqual(uninstalled.state, "committed")
            journal = json.loads(lifecycle.journal.read_text())
            self.assertEqual(journal["schemaVersion"], 3)
            self.assertEqual(journal["kind"], "uninstall")
            archived = runtime_install._archive_prior_committed_journal(paths, journal)
            self.assertTrue(archived.is_file())
            self.assertFalse(lifecycle.journal.exists())
            for target in fixture.profile.targets:
                self.assertEqual(
                    (target.openvr_directory / "openvr_api.dll").read_bytes(),
                    fixture.stock_payloads[target.id],
                )
                self.assertFalse((target.openvr_directory / "openvr_api.real.dll").exists())
                self.assertFalse((target.graphics_directory / "d3d11.dll").exists())
                self.assertFalse((target.graphics_directory / "dxgi.dll").exists())
                self.assertFalse(
                    (target.graphics_directory / "alvr_iosurface_bridge.dll").exists()
                )

    def test_profile_plan_recovers_every_target_after_interruption(self) -> None:
        with lifecycle_fixture() as lifecycle:
            fixture = ProfilePlanFixture(lifecycle)
            executor = fixture.prepare_executor("install")

            def crash_last_target(step_id: str, phase: str) -> None:
                if (
                    step_id == "install_game_wine_bridge_windows_robot_repair"
                    and phase == "after-mutation"
                ):
                    raise SimulatedCrash("fixture crash")

            executor.failure_injector = crash_last_target
            with self.assertRaises(SimulatedCrash):
                executor.execute()
            interrupted = json.loads(lifecycle.journal.read_text())
            self.assertEqual(interrupted["schemaVersion"], 3)
            self.assertEqual(interrupted["planIdentity"], fixture.profile.plan_identity)

            recovered = fixture.prepare_executor("install").recover()
            self.assertEqual(recovered.state, "rolled-back")
            for target in fixture.profile.targets:
                self.assertEqual(
                    (target.openvr_directory / "openvr_api.dll").read_bytes(),
                    fixture.stock_payloads[target.id],
                )
                self.assertFalse((target.openvr_directory / "openvr_api.real.dll").exists())
                self.assertFalse((target.graphics_directory / "d3d11.dll").exists())
                self.assertFalse((target.graphics_directory / "dxgi.dll").exists())
                self.assertFalse(
                    (target.graphics_directory / "alvr_iosurface_bridge.dll").exists()
                )

    def test_profile_admission_checks_stock_hashes_only_for_install(self) -> None:
        with lifecycle_fixture() as lifecycle:
            fixture = ProfilePlanFixture(lifecycle)
            loaded = runtime_profile.LoadedProfile(
                path=REPO_ROOT / "runtime/profiles/the-lab.json",
                data={"id": "the-lab"},
                sha256=fixture.profile.sha256,
            )
            context = RuntimeContext(bindings_path=lifecycle.root / "bindings.json")
            with (
                mock.patch.object(
                    artifact_contract,
                    "resolve_bindings",
                    return_value={},
                ),
                mock.patch.object(
                    runtime_profile,
                    "load_curated_profile",
                    return_value=loaded,
                ),
                mock.patch.object(
                    runtime_profile,
                    "verify_steam_identity",
                    return_value=(
                        fixture.profile.install_root,
                        lifecycle.root / "appmanifest.acf",
                        "a" * 64,
                    ),
                ),
                mock.patch.object(
                    runtime_profile,
                    "resolve_profile_targets",
                    return_value=fixture.profile.targets,
                ) as resolve_targets,
            ):
                runtime_install._admit_profile_plan(
                    "install",
                    context,
                    {},
                    lifecycle.artifact_root,
                    fixture.plan,
                )
                runtime_install._admit_profile_plan(
                    "uninstall",
                    context,
                    {},
                    lifecycle.artifact_root,
                    fixture.plan,
                )

            self.assertEqual(
                [call.kwargs["require_stock_openvr"] for call in resolve_targets.call_args_list],
                [True, False],
            )

    def test_profile_bound_installed_state_rejects_another_profile_plan(self) -> None:
        with lifecycle_fixture() as fixture, patched_lifecycle(fixture) as (
            context,
            _,
            _,
        ):
            def profile_plan(*args: Any) -> dict[str, Any]:
                plan = fixture.plan()
                profile_id = args[5] if len(args) > 5 else None
                plan["schemaVersion"] = 2
                plan["profile"] = {
                    "id": profile_id or "the-lab",
                    "sha256": ("a" if profile_id == "the-lab" else "b") * 64,
                    "installRoot": str(fixture.targets_root),
                    "targets": [],
                }
                return plan

            with (
                mock.patch.object(
                    runtime_install,
                    "_build_plan",
                    side_effect=profile_plan,
                ),
                mock.patch.object(runtime_install, "_admit_profile_plan"),
            ):
                installed = runtime_install.install_runtime(
                    context,
                    fixture.artifact_root,
                    "the-lab",
                )
                blocked = runtime_install.uninstall_runtime(
                    context,
                    fixture.artifact_root,
                    "aircar",
                )

            self.assertTrue(installed.ok, installed.to_dict())
            self.assertFalse(blocked.ok)
            self.assertEqual(blocked.reason_code, "transaction.journal_mismatch")
            journal = json.loads(fixture.journal.read_text())
            self.assertEqual(journal["schemaVersion"], 3)
            self.assertEqual(journal["kind"], "install")
            self.assertEqual(journal["planIdentity"]["profileId"], "the-lab")
            self.assertEqual(fixture.stock.read_bytes(), b"patched payload")

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS bundle signing")
    def test_probe_artifact_staging_preserves_signed_bundle_identity(self) -> None:
        script = REPO_ROOT / "tools" / "run_real_native_iosurface_probe.sh"
        harness = r'''
set -euo pipefail

script=$1
root=$2
artifact_native_bridge_bundle="$root/source/ALVRMacOSBridge.app"
native_bridge_install_staging="$root/staged/native-bridge-install"
native_bridge_install_program="$native_bridge_install_staging/Contents/MacOS/alvr_macos_bridge"
native_bridge_bundle="$root/stable/ALVRMacOSBridge.app"
run_dir="$root/run"
artifact_mode=1

mkdir -p \
    "$artifact_native_bridge_bundle/Contents/MacOS" \
    "$artifact_native_bridge_bundle/Contents/Resources" \
    "$(dirname "$native_bridge_bundle")" \
    "$run_dir"
cp /usr/bin/true "$artifact_native_bridge_bundle/Contents/MacOS/alvr_macos_bridge"
cat >"$artifact_native_bridge_bundle/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>alvr_macos_bridge</string>
<key>CFBundleIdentifier</key><string>com.example.probe-bundle</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
EOF
printf '%s\n' '{"artifactId":"mac-alvr-runtime","bundleId":"com.example.probe-bundle","ownershipSchemaVersion":1}' \
    >"$artifact_native_bridge_bundle/Contents/Resources/runtime-owner.json"
printf '%s\n' '{"schemaVersion":1}' \
    >"$artifact_native_bridge_bundle/Contents/Resources/runtime-sealing.json"
codesign --force --deep --sign - --identifier com.example.probe-bundle \
    "$artifact_native_bridge_bundle" >/dev/null 2>&1
chmod 0555 "$artifact_native_bridge_bundle"
source_cdhash=$(codesign -dv --verbose=4 "$artifact_native_bridge_bundle" 2>&1 |
    sed -n 's/^CDHash=//p')

eval "$(awk '/^stage_native_bridge_install_tree\(\)/ { capture=1 } capture { print } capture && /^}$/ { exit }' "$script")"
eval "$(awk '/^move_staged_native_bridge_bundle\(\)/ { capture=1 } capture { print } capture && /^}$/ { exit }' "$script")"
eval "$(awk '/^native_bridge_bundle_matches_staging\(\)/ { capture=1 } capture { print } capture && /^}$/ { exit }' "$script")"
stage_native_bridge_install_tree

codesign --verify --strict --deep "$native_bridge_install_staging"
staged_cdhash=$(codesign -dv --verbose=4 "$native_bridge_install_staging" 2>&1 |
    sed -n 's/^CDHash=//p')
[[ -n $source_cdhash && $staged_cdhash == "$source_cdhash" ]]
diff -qr "$artifact_native_bridge_bundle" "$native_bridge_install_staging"
move_staged_native_bridge_bundle
[[ ! -e $native_bridge_install_staging && -d $native_bridge_bundle ]]
[[ $(stat -f '%Lp' "$native_bridge_bundle") == 555 ]]
codesign --verify --strict --deep "$native_bridge_bundle"
installed_cdhash=$(codesign -dv --verbose=4 "$native_bridge_bundle" 2>&1 |
    sed -n 's/^CDHash=//p')
[[ $installed_cdhash == "$source_cdhash" ]]
diff -qr "$artifact_native_bridge_bundle" "$native_bridge_bundle"
stage_native_bridge_install_tree
native_bridge_signature_identifier=$(codesign -dv --verbose=4 \
    "$native_bridge_install_program" 2>&1 | sed -n 's/^Identifier=//p')
native_bridge_signature_team=$(codesign -dv --verbose=4 \
    "$native_bridge_install_program" 2>&1 | sed -n 's/^TeamIdentifier=//p')
native_bridge_signature_cdhash=$(codesign -dv --verbose=4 \
    "$native_bridge_install_program" 2>&1 | sed -n 's/^CDHash=//p')
native_bridge_bundle_matches_staging
/usr/bin/grep -q '^mode=artifact-preserved$' "$run_dir/native-bridge-codesign.log"
'''
        CODE_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="probe-bundle-stage-", dir=CODE_ROOT) as root:
            subprocess.run(
                ["bash", "-c", harness, "probe-stage", str(script), root],
                check=True,
            )

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS bundle signing and flags")
    def test_probe_bundle_removal_restores_modes_after_delete_failure(self) -> None:
        script = REPO_ROOT / "tools" / "run_real_native_iosurface_probe.sh"
        harness = r'''
set -euo pipefail

script=$1
root=$2
failure_bundle="$root/failure/ALVRMacOSBridge.app"
success_bundle="$root/success/ALVRMacOSBridge.app"
locked="$failure_bundle/Contents/Resources/locked.dat"
locked_macos="$failure_bundle/Contents/MacOS/locked.dat"

cleanup() {
    chflags nouchg "$locked" 2>/dev/null || true
    chflags nouchg "$locked_macos" 2>/dev/null || true
    chmod -R u+w "$root" 2>/dev/null || true
    rm -rf "$root"
}
trap cleanup EXIT

make_bundle() {
    local bundle=$1
    mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
    cp /usr/bin/true "$bundle/Contents/MacOS/alvr_macos_bridge"
    cat >"$bundle/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>alvr_macos_bridge</string>
<key>CFBundleIdentifier</key><string>com.example.probe-bundle</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
EOF
    cat >"$bundle/Contents/Resources/runtime-owner.json" <<'EOF'
{"artifactId":"mac-alvr-runtime","bundleId":"com.example.probe-bundle","ownershipSchemaVersion":1}
EOF
    printf locked >"$bundle/Contents/Resources/locked.dat"
    printf locked >"$bundle/Contents/MacOS/locked.dat"
    codesign --force --deep --sign - --identifier com.example.probe-bundle \
        "$bundle" >/dev/null 2>&1
    find "$bundle" -type d -exec chmod 0555 {} +
    find "$bundle" -type f ! -path '*/MacOS/alvr_macos_bridge' -exec chmod 0444 {} +
    chmod 0555 "$bundle/Contents/MacOS/alvr_macos_bridge"
}

eval "$(awk '/^remove_owned_native_bridge_bundle\(\)/ { capture=1 } capture { print } capture && /^}$/ { exit }' "$script")"

make_bundle "$failure_bundle"
native_bridge_signature_identifier=$(codesign -dv --verbose=4 \
    "$failure_bundle/Contents/MacOS/alvr_macos_bridge" 2>&1 | sed -n 's/^Identifier=//p')
native_bridge_signature_team=$(codesign -dv --verbose=4 \
    "$failure_bundle/Contents/MacOS/alvr_macos_bridge" 2>&1 | sed -n 's/^TeamIdentifier=//p')
native_bridge_bundle_id=com.example.probe-bundle
root_mode=$(stat -f '%Lp' "$failure_bundle")
contents_mode=$(stat -f '%Lp' "$failure_bundle/Contents")
macos_mode=$(stat -f '%Lp' "$failure_bundle/Contents/MacOS")
resources_mode=$(stat -f '%Lp' "$failure_bundle/Contents/Resources")
chflags uchg "$locked"
chflags uchg "$locked_macos"
set +e
remove_owned_native_bridge_bundle "$failure_bundle" >/dev/null 2>"$root/failure.log"
failure_status=$?
set -e
[[ $failure_status -ne 0 && -d $failure_bundle ]]
[[ $(stat -f '%Lp' "$failure_bundle") == "$root_mode" ]]
[[ $(stat -f '%Lp' "$failure_bundle/Contents") == "$contents_mode" ]]
[[ $(stat -f '%Lp' "$failure_bundle/Contents/MacOS") == "$macos_mode" ]]
[[ $(stat -f '%Lp' "$failure_bundle/Contents/Resources") == "$resources_mode" ]]

chflags nouchg "$locked"
chflags nouchg "$locked_macos"
chmod -R u+w "$failure_bundle"
rm -rf "$failure_bundle"
make_bundle "$success_bundle"
remove_owned_native_bridge_bundle "$success_bundle"
[[ ! -e $success_bundle && ! -L $success_bundle ]]
'''
        CODE_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="probe-bundle-mode-", dir=CODE_ROOT) as root:
            result = subprocess.run(
                ["/bin/bash", "-c", harness, "probe-bundle-test", str(script), root],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_json_contract_returns_domain_exit_one(self) -> None:
        report = runtime_install.MutationReport(
            command="install",
            ok=False,
            state="blocked",
            reason_code="artifact.sealing_required",
            message="fixture gate",
        )
        stdout = io.StringIO()
        with (
            mock.patch.object(
                runtime_cli,
                "install_runtime",
                return_value=report,
            ) as install_mock,
            contextlib.redirect_stdout(stdout),
        ):
            status = runtime_cli.main(
                [
                    "install",
                    "--artifact",
                    "/fixture",
                    "--profile",
                    "the-lab",
                    "--json",
                ]
            )
        self.assertEqual(status, 1)
        install_mock.assert_called_once()
        self.assertEqual(install_mock.call_args.args[2], "the-lab")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["reasonCode"], "artifact.sealing_required")
        self.assertEqual(payload["command"], "install")


if __name__ == "__main__":
    unittest.main(verbosity=2)
