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
import runtime_transaction
from runtime_control import (
    CheckResult,
    CommandResult,
    DoctorReport,
    RuntimeContext,
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
        tree_ready = not self.bridge_bundle.exists() or self._tree_owned()
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
                "sourceMarkerSha256": self.marker_sha256,
                "target": str(self.bridge_bundle),
                "ready": tree_ready,
                **({} if tree_ready else {"blockedReason": "bridge tree is modified"}),
            },
            {
                "id": "replace_bridge",
                "resource": "bridge",
                "action": "replace_tree",
                "atomic": True,
                "source": str(self.source_tree),
                "sourceTreeSha256": self.tree_sha256,
                "target": str(self.bridge_bundle),
                "ready": tree_ready,
                **({} if tree_ready else {"blockedReason": "bridge tree is modified"}),
            },
        ]
        restore_ready = stock_hash == self.stock_sha256 or (
            stock_hash == self.patched_sha256 and backup_hash == self.stock_sha256
        )
        remove_ready = created_hash in {None, artifact_contract.sha256_file(self.created_source)}
        uninstall_tree_ready = not self.bridge_bundle.exists() or self._tree_owned()
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
                "id": "remove_bridge",
                "resource": "bridge",
                "action": "remove_tree",
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
    with (
        mock.patch.object(
            runtime_install,
            "load_runtime_contract",
            return_value=(manifest, {}, "b" * 64, "c" * 64),
        ),
        mock.patch.object(runtime_install, "verify_artifact_reference", return_value=artifact),
        mock.patch.object(runtime_install, "_build_plan", side_effect=lambda *args: fixture.plan()) as plan,
        mock.patch.object(runtime_install, "doctor_runtime", return_value=doctor) as doctor_mock,
        mock.patch.object(runtime_install, "stop_runtime", return_value=stop) as stop_mock,
    ):
        yield context, doctor_mock, stop_mock


class RuntimeInstallTests(unittest.TestCase):
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

            uninstalled = runtime_install.uninstall_runtime(context, fixture.artifact_root)
            self.assertTrue(uninstalled.ok)
            self.assertEqual(fixture.stock.read_bytes(), b"stock payload")
            self.assertFalse(fixture.created.exists())
            self.assertFalse(fixture.bridge_bundle.exists())
            self.assertTrue(fixture.backup.is_file())
            self.assertIsNotNone(uninstalled.archived_journal)

            uninstall_replay = runtime_install.uninstall_runtime(context, fixture.artifact_root)
            self.assertTrue(uninstall_replay.ok)
            self.assertEqual(uninstall_replay.state, "already-committed")

            reinstalled = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertTrue(reinstalled.ok)
            self.assertNotEqual(reinstalled.transaction_id, installed.transaction_id)
            self.assertEqual(len(list(fixture.history_root.glob("*.json"))), 2)

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
            ) -> runtime_transaction.TransactionExecutor:
                executor = real_executor(kind, plan, paths)
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
            installed = runtime_install.install_runtime(context, fixture.artifact_root)
            self.assertTrue(installed.ok)
            runner = RecordingClosedTargetRunner()
            context.runner = runner
            uninstalled = runtime_install.uninstall_runtime(context, fixture.artifact_root)
            self.assertTrue(uninstalled.ok)
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
            ) -> runtime_transaction.TransactionExecutor:
                executor = real_executor(kind, plan, paths)
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
            mock.patch.object(runtime_cli, "install_runtime", return_value=report),
            contextlib.redirect_stdout(stdout),
        ):
            status = runtime_cli.main(["install", "--artifact", "/fixture", "--json"])
        self.assertEqual(status, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["reasonCode"], "artifact.sealing_required")
        self.assertEqual(payload["command"], "install")


if __name__ == "__main__":
    unittest.main(verbosity=2)
