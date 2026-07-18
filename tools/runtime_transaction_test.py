"""Hardware-free fixtures for durable runtime filesystem transactions."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from collections.abc import Iterator
from typing import Any
from unittest import mock

import build_runtime_artifact as artifact_contract
import runtime_transaction
from runtime_transaction import TransactionError, TransactionExecutor


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CODE_ROOT = pathlib.Path(os.environ.get("RUNTIME_FIXTURE_ROOT", REPO_ROOT / ".code"))
MARKER = "Contents/Resources/runtime-owner.json"


class SimulatedCrash(BaseException):
    pass


def snapshot_tree(root: pathlib.Path) -> dict[str, tuple[str, int, bytes | str | None]]:
    snapshot: dict[str, tuple[str, int, bytes | str | None]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = metadata.st_mode & 0o7777
        if path.is_symlink():
            snapshot[relative] = ("symlink", mode, path.readlink().as_posix())
        elif path.is_dir():
            snapshot[relative] = ("directory", mode, None)
        elif path.is_file():
            snapshot[relative] = ("file", mode, path.read_bytes())
        else:
            snapshot[relative] = ("other", mode, None)
    return snapshot


class FixtureLayout:
    def __init__(self) -> None:
        CODE_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="runtime-transaction-",
            dir=CODE_ROOT,
        )
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.artifact_root = self.root / "artifact"
        self.target_root = self.root / "targets"
        self.transaction_root = self.root / "transactions"
        self.journal_path = self.transaction_root / "transaction.json"
        self.artifact_root.mkdir()
        self.target_root.mkdir()
        self.transaction_root.mkdir()

    def cleanup(self) -> None:
        self.temporary.cleanup()

    def artifact_file(self, relative: str, payload: bytes) -> pathlib.Path:
        path = self.artifact_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def target_file(self, relative: str, payload: bytes) -> pathlib.Path:
        path = self.target_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def tree(
        self,
        root: pathlib.Path,
        *,
        marker_payload: bytes = b'{"owner":"fixture"}\n',
        data_payload: bytes = b"tree payload",
    ) -> pathlib.Path:
        marker = root / MARKER
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(marker_payload)
        data = root / "Contents/Resources/data.bin"
        data.write_bytes(data_payload)
        return root

    def executor(
        self,
        kind: str,
        operations: list[dict[str, Any]],
        *,
        failure_injector: Any = None,
        journal_path: pathlib.Path | None = None,
        tree_ownership_validator: runtime_transaction.TreeOwnershipValidator | None = None,
    ) -> TransactionExecutor:
        return TransactionExecutor(
            kind=kind,  # type: ignore[arg-type]
            operations=operations,
            artifact_root=self.artifact_root,
            journal_path=journal_path or self.journal_path,
            transaction_root=self.transaction_root,
            allowed_roots=[self.target_root],
            failure_injector=failure_injector,
            tree_ownership_validator=tree_ownership_validator,
        )

    def transaction_temporary_paths(self) -> list[pathlib.Path]:
        paths = [
            path
            for path in self.target_root.rglob("*")
            if "runtime-txn-" in path.name
        ]
        if self.journal_path.with_name(f"{self.journal_path.name}.undo").exists():
            paths.append(self.journal_path.with_name(f"{self.journal_path.name}.undo"))
        return sorted(paths)

    def prepare_install(self) -> tuple[list[dict[str, Any]], dict[str, pathlib.Path]]:
        stock = self.target_file("game/stock.bin", b"stock payload")
        patched_source = self.artifact_file("payload/patched.bin", b"patched payload")
        created_source = self.artifact_file("payload/created.bin", b"created payload")
        created = self.target_root / "game/created.bin"
        backup = self.target_root / "backups/stock.bin"
        backup.parent.mkdir()
        source_tree = self.tree(
            self.artifact_root / "payload/Bridge.app",
            data_payload=b"new tree payload",
        )
        target_tree = self.tree(
            self.target_root / "Bridge.app",
            data_payload=b"new tree payload",
        )
        marker_sha256 = artifact_contract.sha256_file(source_tree / MARKER)
        tree_sha256 = artifact_contract.canonical_tree_sha256(source_tree)
        operations: list[dict[str, Any]] = [
            {
                "id": "assert_stock",
                "resource": "stock",
                "action": "assert_sha256",
                "target": str(stock),
                "expectedSha256": artifact_contract.sha256_file(stock),
                "actualSha256": artifact_contract.sha256_file(stock),
                "ready": True,
            },
            {
                "id": "backup_stock",
                "resource": "stock",
                "action": "backup",
                "target": str(stock),
                "backup": str(backup),
                "backupExists": False,
                "ready": True,
            },
            {
                "id": "replace_stock",
                "resource": "stock",
                "action": "replace_file",
                "atomic": True,
                "source": str(patched_source),
                "sourceSha256": artifact_contract.sha256_file(patched_source),
                "target": str(stock),
                "sourceFiles": 1,
                "ready": True,
            },
            {
                "id": "assert_created_absent",
                "resource": "created",
                "action": "assert_absent",
                "target": str(created),
                "exists": False,
                "ready": True,
            },
            {
                "id": "create_extra",
                "resource": "created",
                "action": "create_file",
                "atomic": True,
                "source": str(created_source),
                "sourceSha256": artifact_contract.sha256_file(created_source),
                "target": str(created),
                "ready": True,
            },
            {
                "id": "assert_bridge_owned",
                "resource": "bridge",
                "action": "assert_absent_or_owned",
                "source": str(source_tree),
                "sourceTreeSha256": tree_sha256,
                "marker": MARKER,
                "sourceMarkerSha256": marker_sha256,
                "target": str(target_tree),
                "ownership": "artifact-owned",
                "ready": True,
            },
            {
                "id": "replace_bridge",
                "resource": "bridge",
                "action": "replace_tree",
                "atomic": True,
                "source": str(source_tree),
                "sourceTreeSha256": tree_sha256,
                "target": str(target_tree),
                "sourceFiles": 2,
                "ready": True,
            },
            {
                "id": "retain_backup",
                "resource": "stock",
                "action": "retain",
                "target": str(backup),
                "exists": False,
                "ready": True,
            },
        ]
        return operations, {
            "stock": stock,
            "patched_source": patched_source,
            "created_source": created_source,
            "created": created,
            "backup": backup,
            "source_tree": source_tree,
            "target_tree": target_tree,
        }

    def prepare_uninstall(self) -> tuple[list[dict[str, Any]], dict[str, pathlib.Path]]:
        installed_source = self.artifact_file("payload/installed.bin", b"installed payload")
        restore_target = self.target_file("game/restore.bin", installed_source.read_bytes())
        backup = self.target_file("backups/restore.bin", b"original payload")
        remove_source = self.artifact_file("payload/remove.bin", b"remove payload")
        remove_target = self.target_file("game/remove.bin", remove_source.read_bytes())
        absent_source = self.artifact_file("payload/already-absent.bin", b"absent payload")
        absent_target = self.target_root / "missing/parent/already-absent.bin"
        source_tree = self.tree(self.artifact_root / "payload/Remove.app")
        target_tree = self.tree(self.target_root / "Remove.app")
        marker_sha256 = artifact_contract.sha256_file(source_tree / MARKER)
        tree_sha256 = artifact_contract.canonical_tree_sha256(source_tree)
        operations = [
            {
                "id": "restore_original",
                "resource": "restore",
                "action": "restore",
                "atomic": True,
                "source": str(installed_source),
                "sourceSha256": artifact_contract.sha256_file(installed_source),
                "target": str(restore_target),
                "backup": str(backup),
                "expectedSha256": artifact_contract.sha256_file(backup),
                "ready": True,
            },
            {
                "id": "remove_file",
                "resource": "remove",
                "action": "remove",
                "source": str(remove_source),
                "sourceSha256": artifact_contract.sha256_file(remove_source),
                "target": str(remove_target),
                "ready": True,
            },
            {
                "id": "remove_tree",
                "resource": "tree",
                "action": "remove_tree",
                "source": str(source_tree),
                "sourceTreeSha256": tree_sha256,
                "marker": MARKER,
                "sourceMarkerSha256": marker_sha256,
                "target": str(target_tree),
                "ready": True,
            },
            {
                "id": "remove_absent",
                "resource": "absent",
                "action": "remove",
                "source": str(absent_source),
                "sourceSha256": artifact_contract.sha256_file(absent_source),
                "target": str(absent_target),
                "exists": False,
                "ready": True,
            },
            {
                "id": "retain_original_backup",
                "resource": "restore",
                "action": "retain",
                "target": str(backup),
                "ready": True,
            },
        ]
        return operations, {
            "restore_target": restore_target,
            "backup": backup,
            "remove_target": remove_target,
            "target_tree": target_tree,
            "absent_target": absent_target,
        }

    def prepare_tree_replace(self) -> tuple[list[dict[str, Any]], pathlib.Path]:
        source_tree = self.tree(
            self.artifact_root / "payload/Crash.app",
            data_payload=b"new crash tree",
        )
        target_tree = self.tree(
            self.target_root / "Crash.app",
            data_payload=b"new crash tree",
        )
        marker_sha256 = artifact_contract.sha256_file(source_tree / MARKER)
        tree_sha256 = artifact_contract.canonical_tree_sha256(source_tree)
        operations: list[dict[str, Any]] = [
            {
                "id": "assert_crash_tree_owned",
                "resource": "crash_tree",
                "action": "assert_absent_or_owned",
                "source": str(source_tree),
                "sourceTreeSha256": tree_sha256,
                "marker": MARKER,
                "sourceMarkerSha256": marker_sha256,
                "target": str(target_tree),
            },
            {
                "id": "replace_crash_tree",
                "resource": "crash_tree",
                "action": "replace_tree",
                "atomic": True,
                "source": str(source_tree),
                "sourceTreeSha256": tree_sha256,
                "target": str(target_tree),
            },
        ]
        return operations, target_tree

    def prepare_managed_tree_replace(
        self,
    ) -> tuple[list[dict[str, Any]], pathlib.Path, dict[str, tuple[str, int, bytes | str | None]]]:
        source_tree = self.tree(
            self.artifact_root / "payload/Managed.app",
            data_payload=b"current managed tree",
        )
        target_tree = self.tree(
            self.target_root / "Managed.app",
            data_payload=b"prior managed tree",
        )
        marker_sha256 = artifact_contract.sha256_file(source_tree / MARKER)
        source_tree_sha256 = artifact_contract.canonical_tree_sha256(source_tree)
        target_tree_sha256 = artifact_contract.canonical_tree_sha256(target_tree)
        operations: list[dict[str, Any]] = [
            {
                "id": "assert_managed_tree_owned",
                "resource": "managed_tree",
                "action": "assert_absent_or_owned",
                "source": str(source_tree),
                "sourceTreeSha256": source_tree_sha256,
                "marker": MARKER,
                "sourceMarkerSha256": marker_sha256,
                "target": str(target_tree),
                "ownershipPolicy": "developer-id-bundle",
                "qualifiedTargetTreeSha256": target_tree_sha256,
                "ownershipEvidence": {"kind": "developer-id"},
                "ready": True,
            },
            {
                "id": "replace_managed_tree",
                "resource": "managed_tree",
                "action": "replace_tree",
                "atomic": True,
                "retainOnUninstall": True,
                "source": str(source_tree),
                "sourceTreeSha256": source_tree_sha256,
                "target": str(target_tree),
                "ready": True,
            },
        ]
        return operations, target_tree, snapshot_tree(target_tree)


@contextlib.contextmanager
def fixture_layout() -> Iterator[FixtureLayout]:
    fixture = FixtureLayout()
    try:
        yield fixture
    finally:
        fixture.cleanup()


class TransactionTests(unittest.TestCase):
    def test_mixed_install_commits_and_replays_without_live_preflight(self) -> None:
        with fixture_layout() as fixture:
            operations, paths = fixture.prepare_install()
            executor = fixture.executor("install", operations)
            report = executor.execute()

            self.assertTrue(report.ok)
            self.assertEqual(report.state, "committed")
            self.assertEqual(paths["stock"].read_bytes(), paths["patched_source"].read_bytes())
            self.assertEqual(paths["backup"].read_bytes(), b"stock payload")
            self.assertEqual(paths["created"].read_bytes(), paths["created_source"].read_bytes())
            self.assertEqual(
                (paths["target_tree"] / "Contents/Resources/data.bin").read_bytes(),
                b"new tree payload",
            )
            self.assertEqual(fixture.transaction_temporary_paths(), [])

            replay_operations = copy.deepcopy(operations)
            for operation in replay_operations:
                operation["ready"] = False
                operation["blockedReason"] = "dynamic diagnostics changed after commit"
                operation["exists"] = True
            paths["patched_source"].unlink()
            paths["created_source"].unlink()

            replay = fixture.executor("install", replay_operations).execute()
            self.assertTrue(replay.ok)
            self.assertEqual(replay.transaction_id, report.transaction_id)
            self.assertEqual(replay.plan_digest, report.plan_digest)
            self.assertEqual(paths["backup"].read_bytes(), b"stock payload")

    def test_uninstall_restore_remove_and_absent_remove_commit(self) -> None:
        with fixture_layout() as fixture:
            operations, paths = fixture.prepare_uninstall()
            report = fixture.executor("uninstall", operations).execute()

            self.assertTrue(report.ok)
            self.assertEqual(paths["restore_target"].read_bytes(), b"original payload")
            self.assertEqual(paths["backup"].read_bytes(), b"original payload")
            self.assertFalse(paths["remove_target"].exists())
            self.assertFalse(paths["target_tree"].exists())
            self.assertFalse(paths["absent_target"].exists())
            self.assertEqual(fixture.transaction_temporary_paths(), [])

        with fixture_layout() as fixture:
            installed_source = fixture.artifact_file("payload/installed.bin", b"installed")
            restored_target = fixture.target_file("game/restored.bin", b"original")
            missing_backup = fixture.target_root / "backups/missing.bin"
            missing_backup.parent.mkdir()
            restored_operations: list[dict[str, Any]] = [
                {
                    "id": "restore_original",
                    "resource": "restored_file",
                    "action": "restore",
                    "atomic": True,
                    "source": str(installed_source),
                    "sourceSha256": artifact_contract.sha256_file(installed_source),
                    "target": str(restored_target),
                    "backup": str(missing_backup),
                    "expectedSha256": artifact_contract.sha256_file(restored_target),
                    "state": "already-restored",
                    "ready": True,
                }
            ]

            report = fixture.executor("uninstall", restored_operations).execute()
            self.assertTrue(report.ok)
            self.assertEqual(restored_target.read_bytes(), b"original")
            self.assertFalse(missing_backup.exists())

        with fixture_layout() as fixture:
            installed_source = fixture.artifact_file("payload/installed.bin", b"installed")
            restore_target = fixture.target_file("game/restore.bin", b"installed")
            backup = fixture.target_file("backups/restore.bin", b"original")
            race_operations: list[dict[str, Any]] = [
                {
                    "id": "restore_original",
                    "resource": "restored_file",
                    "action": "restore",
                    "atomic": True,
                    "source": str(installed_source),
                    "sourceSha256": artifact_contract.sha256_file(installed_source),
                    "target": str(restore_target),
                    "backup": str(backup),
                    "expectedSha256": artifact_contract.sha256_file(backup),
                }
            ]

            def replace_before_intent(step_id: str, phase: str) -> None:
                if step_id == "restore_original" and phase == "before":
                    restore_target.write_bytes(b"foreign")

            with self.assertRaises(TransactionError) as raised:
                fixture.executor(
                    "uninstall",
                    race_operations,
                    failure_injector=replace_before_intent,
                ).execute()
            self.assertEqual(
                raised.exception.code,
                "transaction.rolled_back",
                raised.exception.context,
            )
            self.assertEqual(restore_target.read_bytes(), b"foreign")
            recovered = fixture.executor("uninstall", race_operations).recover()
            self.assertEqual(recovered.state, "rolled-back")

    def test_assertion_failure_precedes_journal_and_target_mutation(self) -> None:
        with fixture_layout() as fixture:
            target = fixture.target_file("game/stock.bin", b"unexpected")
            before = snapshot_tree(fixture.target_root)
            operations: list[dict[str, Any]] = [
                {
                    "id": "assert_stock",
                    "resource": "stock",
                    "action": "assert_sha256",
                    "target": str(target),
                    "expectedSha256": artifact_contract.sha256_bytes(b"expected"),
                }
            ]

            with self.assertRaises(TransactionError) as raised:
                fixture.executor("install", operations).execute()

            self.assertEqual(raised.exception.code, "transaction.assertion_failed")
            self.assertEqual(snapshot_tree(fixture.target_root), before)
            self.assertFalse(fixture.journal_path.exists())

    def test_each_mutation_rolls_back_exactly(self) -> None:
        for failure_step in ("backup_stock", "replace_stock", "create_extra", "replace_bridge"):
            with self.subTest(failure_step=failure_step), fixture_layout() as fixture:
                operations, _ = fixture.prepare_install()
                before = snapshot_tree(fixture.target_root)

                def inject(step_id: str, phase: str) -> None:
                    if step_id == failure_step and phase == "after-mutation":
                        raise RuntimeError(f"fixture failure after {failure_step}")

                with self.assertRaises(TransactionError) as raised:
                    fixture.executor(
                        "install",
                        operations,
                        failure_injector=inject,
                    ).execute()

                self.assertEqual(raised.exception.code, "transaction.rolled_back")
                self.assertEqual(snapshot_tree(fixture.target_root), before)
                journal = json.loads(fixture.journal_path.read_text())
                self.assertEqual(journal["state"], "rolled-back")
                self.assertEqual(journal["cleanupFailures"], [])
                self.assertEqual(fixture.transaction_temporary_paths(), [])

        for failure_step in ("restore_original", "remove_file", "remove_tree"):
            with self.subTest(failure_step=failure_step), fixture_layout() as fixture:
                operations, _ = fixture.prepare_uninstall()
                before = snapshot_tree(fixture.target_root)

                def inject(step_id: str, phase: str) -> None:
                    if step_id == failure_step and phase == "after-mutation":
                        raise RuntimeError(f"fixture failure after {failure_step}")

                with self.assertRaises(TransactionError) as raised:
                    fixture.executor(
                        "uninstall",
                        operations,
                        failure_injector=inject,
                    ).execute()

                self.assertEqual(raised.exception.code, "transaction.rolled_back")
                self.assertEqual(snapshot_tree(fixture.target_root), before)
                journal = json.loads(fixture.journal_path.read_text())
                self.assertEqual(journal["state"], "rolled-back")
                self.assertEqual(journal["cleanupFailures"], [])
                self.assertEqual(fixture.transaction_temporary_paths(), [])

    def test_crash_at_each_tree_boundary_recovers_in_new_executor(self) -> None:
        for crash_phase in (
            "after-intent",
            "after-tree-staged",
            "after-tree-original-moved",
            "after-tree-replacement-installed",
            "after-mutation",
            "after",
        ):
            with self.subTest(crash_phase=crash_phase), fixture_layout() as fixture:
                operations, _ = fixture.prepare_tree_replace()
                before = snapshot_tree(fixture.target_root)

                def crash(step_id: str, phase: str) -> None:
                    if step_id == "replace_crash_tree" and phase == crash_phase:
                        raise SimulatedCrash(crash_phase)

                with self.assertRaises(SimulatedCrash):
                    fixture.executor(
                        "install",
                        operations,
                        failure_injector=crash,
                    ).execute()

                journal = json.loads(fixture.journal_path.read_text())
                self.assertEqual(journal["state"], "running")
                with self.assertRaises(TransactionError) as execute_raised:
                    fixture.executor("install", operations).execute()
                self.assertEqual(execute_raised.exception.code, "transaction.recovery_required")

                report = fixture.executor("install", operations).recover()
                self.assertFalse(report.ok)
                self.assertEqual(report.state, "rolled-back")
                self.assertEqual(snapshot_tree(fixture.target_root), before)
                self.assertEqual(fixture.transaction_temporary_paths(), [])

        with fixture_layout() as fixture:
            operations, target_tree = fixture.prepare_tree_replace()

            def crash_after_tree(step_id: str, phase: str) -> None:
                if step_id == "replace_crash_tree" and phase == "after-mutation":
                    raise SimulatedCrash("remove original payload")

            with self.assertRaises(SimulatedCrash):
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=crash_after_tree,
                ).execute()
            journal = json.loads(fixture.journal_path.read_text())
            replace_step = next(
                step for step in journal["steps"] if step["id"] == "replace_crash_tree"
            )
            shutil.rmtree(pathlib.Path(replace_step["undo"]["original"]))

            with self.assertRaises(TransactionError) as raised:
                fixture.executor("install", operations).recover()
            self.assertEqual(raised.exception.code, "transaction.rollback_failed")
            self.assertEqual(
                (target_tree / "Contents/Resources/data.bin").read_bytes(),
                b"new crash tree",
            )
            failed_journal = json.loads(fixture.journal_path.read_text())
            self.assertEqual(failed_journal["state"], "failed")

        with fixture_layout() as fixture:
            operations, _ = fixture.prepare_tree_replace()
            before = snapshot_tree(fixture.target_root)

            def crash_after_tree(step_id: str, phase: str) -> None:
                if step_id == "replace_crash_tree" and phase == "after-mutation":
                    raise SimulatedCrash("rollback discard fixture")

            with self.assertRaises(SimulatedCrash):
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=crash_after_tree,
                ).execute()

            real_remove = runtime_transaction.remove_path_durable
            crashed = False

            def crash_during_discard(
                path: pathlib.Path,
                expected_identity: runtime_transaction.runtime_descriptor.FileIdentity | None = None,
                tree_entries: list[dict[str, Any]] | None = None,
            ) -> None:
                nonlocal crashed
                if (
                    not crashed
                    and path.name.endswith(".rollback.descriptor-delete")
                    and path.is_dir()
                ):
                    crashed = True
                    raise SimulatedCrash("recursive discard cleanup")
                real_remove(path, expected_identity, tree_entries)

            with mock.patch.object(
                runtime_transaction,
                "remove_path_durable",
                side_effect=crash_during_discard,
            ), self.assertRaises(SimulatedCrash):
                fixture.executor("install", operations).recover()

            interrupted = json.loads(fixture.journal_path.read_text())
            replace_step = next(
                step for step in interrupted["steps"] if step["id"] == "replace_crash_tree"
            )
            self.assertEqual(interrupted["state"], "rolling-back")
            self.assertEqual(replace_step["undo"]["phase"], "rollback-restored")

            report = fixture.executor("install", operations).recover()
            self.assertEqual(report.state, "rolled-back")
            self.assertEqual(snapshot_tree(fixture.target_root), before)
            self.assertEqual(fixture.transaction_temporary_paths(), [])

    def test_crash_at_each_file_publication_boundary_recovers_exactly(self) -> None:
        for crash_phase in (
            "after-file-staged",
            "after-file-original-moved",
            "after-file-replacement-installed",
        ):
            with self.subTest(crash_phase=crash_phase), fixture_layout() as fixture:
                operations, _ = fixture.prepare_install()
                before = snapshot_tree(fixture.target_root)

                def crash(step_id: str, phase: str) -> None:
                    if step_id == "replace_stock" and phase == crash_phase:
                        raise SimulatedCrash(crash_phase)

                with self.assertRaises(SimulatedCrash):
                    fixture.executor(
                        "install",
                        operations,
                        failure_injector=crash,
                    ).execute()

                report = fixture.executor("install", operations).recover()
                self.assertEqual(report.state, "rolled-back")
                self.assertEqual(snapshot_tree(fixture.target_root), before)
                self.assertEqual(fixture.transaction_temporary_paths(), [])

    def test_recovery_refuses_replaced_target_parent_without_redirecting(self) -> None:
        with fixture_layout() as fixture:
            operations, paths = fixture.prepare_install()

            def crash_after_replace(step_id: str, phase: str) -> None:
                if step_id == "replace_stock" and phase == "after-mutation":
                    raise SimulatedCrash("replace target parent")

            with self.assertRaises(SimulatedCrash):
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=crash_after_replace,
                ).execute()

            original_parent = paths["stock"].parent
            moved_parent = fixture.target_root / "game.original"
            original_parent.rename(moved_parent)
            original_parent.mkdir()
            replacement = original_parent / "stock.bin"
            replacement.write_bytes(b"foreign replacement")

            with self.assertRaises(TransactionError) as raised:
                fixture.executor("install", operations).recover()
            self.assertEqual(raised.exception.code, "transaction.rollback_failed")
            self.assertEqual(replacement.read_bytes(), b"foreign replacement")
            self.assertEqual((moved_parent / "stock.bin").read_bytes(), b"patched payload")
            journal = json.loads(fixture.journal_path.read_text())
            self.assertEqual(journal["state"], "failed")
            self.assertEqual(
                journal["rollbackFailures"][0]["code"],
                "transaction.path_identity_changed",
            )

    def test_leaf_substitution_is_preserved_instead_of_overwritten(self) -> None:
        with fixture_layout() as fixture:
            operations, paths = fixture.prepare_install()
            real_stage = runtime_transaction.copy_file_staged
            substituted = False

            def substitute_before_publication(
                source: pathlib.Path,
                staging: pathlib.Path,
            ) -> None:
                nonlocal substituted
                if not substituted and source == paths["patched_source"]:
                    substituted = True
                    paths["stock"].write_bytes(b"foreign replacement")
                real_stage(source, staging)

            with mock.patch.object(
                runtime_transaction,
                "copy_file_staged",
                side_effect=substitute_before_publication,
            ), self.assertRaises(TransactionError) as raised:
                fixture.executor("install", operations).execute()

            self.assertEqual(raised.exception.code, "transaction.rollback_failed")
            self.assertEqual(paths["stock"].read_bytes(), b"foreign replacement")
            journal = json.loads(fixture.journal_path.read_text())
            self.assertEqual(journal["state"], "failed")
            self.assertEqual(
                journal["rollbackFailures"][0]["code"],
                "transaction.undo_foreign",
            )

    def test_cleanup_quarantine_restores_a_substituted_payload(self) -> None:
        with fixture_layout() as fixture:
            operations, paths = fixture.prepare_install()
            real_rename = runtime_transaction.rename_exclusive
            substituted = False

            def substitute_during_cleanup(source: pathlib.Path, target: pathlib.Path) -> None:
                nonlocal substituted
                if (
                    not substituted
                    and source.name.endswith(".original")
                    and target.name.endswith(".descriptor-delete")
                    and source.is_file()
                ):
                    substituted = True
                    source.write_bytes(b"foreign cleanup payload")
                real_rename(source, target)

            with mock.patch.object(
                runtime_transaction,
                "rename_exclusive",
                side_effect=substitute_during_cleanup,
            ), self.assertRaises(TransactionError) as raised:
                fixture.executor("install", operations).execute()

            self.assertEqual(raised.exception.code, "transaction.cleanup_failed")
            self.assertEqual(paths["stock"].read_bytes(), b"patched payload")
            journal = json.loads(fixture.journal_path.read_text())
            self.assertEqual(journal["state"], "committed")
            replace_step = next(step for step in journal["steps"] if step["id"] == "replace_stock")
            original = pathlib.Path(replace_step["undo"]["original"])
            self.assertEqual(original.read_bytes(), b"foreign cleanup payload")
            self.assertFalse(runtime_transaction.TransactionExecutor._cleanup_quarantine(original).exists())

    def test_remove_refuses_and_restores_a_same_content_leaf_substitution(self) -> None:
        with fixture_layout() as fixture:
            source = fixture.artifact_file("payload/remove.bin", b"owned payload")
            target = fixture.target_file("game/remove.bin", source.read_bytes())
            original_inode = target.stat().st_ino
            operations: list[dict[str, Any]] = [
                {
                    "id": "remove_file",
                    "resource": "remove",
                    "action": "remove",
                    "source": str(source),
                    "sourceSha256": artifact_contract.sha256_file(source),
                    "target": str(target),
                    "ready": True,
                }
            ]
            real_rename = runtime_transaction.rename_exclusive
            foreign_inode: int | None = None

            def substitute_before_remove(
                source_path: pathlib.Path,
                target_path: pathlib.Path,
            ) -> None:
                nonlocal foreign_inode
                if foreign_inode is None and source_path == target:
                    foreign = target.with_name("foreign-same-content.bin")
                    foreign.write_bytes(source.read_bytes())
                    os.replace(foreign, target)
                    foreign_inode = target.stat().st_ino
                real_rename(source_path, target_path)

            with mock.patch.object(
                runtime_transaction,
                "rename_exclusive",
                side_effect=substitute_before_remove,
            ), self.assertRaises(TransactionError) as raised:
                fixture.executor("uninstall", operations).execute()

            self.assertEqual(raised.exception.code, "transaction.rolled_back")
            self.assertIsNotNone(foreign_inode)
            self.assertNotEqual(foreign_inode, original_inode)
            self.assertEqual(target.stat().st_ino, foreign_inode)
            self.assertEqual(target.read_bytes(), b"owned payload")
            journal = json.loads(fixture.journal_path.read_text())
            self.assertEqual(journal["state"], "rolled-back")
            self.assertEqual(journal["failure"]["code"], "transaction.target_changed")
            remove_step = next(step for step in journal["steps"] if step["id"] == "remove_file")
            self.assertEqual(remove_step["undo"]["phase"], "restored-after-change")
            self.assertFalse(pathlib.Path(remove_step["undo"]["original"]).exists())

    def test_remove_restores_when_identity_check_fails_after_the_move(self) -> None:
        with fixture_layout() as fixture:
            source = fixture.artifact_file("payload/remove.bin", b"owned payload")
            target = fixture.target_file("game/remove.bin", source.read_bytes())
            target_inode = target.stat().st_ino
            operations: list[dict[str, Any]] = [
                {
                    "id": "remove_file",
                    "resource": "remove",
                    "action": "remove",
                    "source": str(source),
                    "sourceSha256": artifact_contract.sha256_file(source),
                    "target": str(target),
                    "ready": True,
                }
            ]
            real_rename = runtime_transaction.rename_exclusive
            failed_after_move = False

            def fail_after_move(source_path: pathlib.Path, target_path: pathlib.Path) -> None:
                nonlocal failed_after_move
                real_rename(source_path, target_path)
                if not failed_after_move and source_path == target:
                    failed_after_move = True
                    raise TransactionError(
                        "transaction.path_identity_changed",
                        "fixture identity race after rename",
                    )

            with mock.patch.object(
                runtime_transaction,
                "rename_exclusive",
                side_effect=fail_after_move,
            ), self.assertRaises(TransactionError) as raised:
                fixture.executor("uninstall", operations).execute()

            self.assertEqual(raised.exception.code, "transaction.rolled_back")
            self.assertTrue(failed_after_move)
            self.assertEqual(target.stat().st_ino, target_inode)
            self.assertEqual(target.read_bytes(), b"owned payload")
            journal = json.loads(fixture.journal_path.read_text())
            self.assertEqual(journal["state"], "rolled-back")
            remove_step = next(step for step in journal["steps"] if step["id"] == "remove_file")
            self.assertEqual(remove_step["undo"]["phase"], "restored-after-change")
            self.assertFalse(pathlib.Path(remove_step["undo"]["original"]).exists())

    def test_partial_tree_cleanup_resumes_only_for_the_journaled_identity(self) -> None:
        for substitution in ("none", "replace-root", "add-child"):
            with self.subTest(substitution=substitution), fixture_layout() as fixture:
                operations, _ = fixture.prepare_tree_replace()
                real_unlink = runtime_transaction.runtime_descriptor.os.unlink
                crashed = False

                def unlink_then_crash(path: Any, *args: Any, **kwargs: Any) -> None:
                    nonlocal crashed
                    real_unlink(path, *args, **kwargs)
                    if not crashed and path == "data.bin" and kwargs.get("dir_fd") is not None:
                        crashed = True
                        raise SimulatedCrash("partial recursive cleanup")

                with mock.patch.object(
                    runtime_transaction.runtime_descriptor.os,
                    "unlink",
                    side_effect=unlink_then_crash,
                ), self.assertRaises(SimulatedCrash):
                    fixture.executor("install", operations).execute()

                interrupted = json.loads(fixture.journal_path.read_text())
                self.assertEqual(interrupted["state"], "committed")
                self.assertEqual(len(interrupted["cleanupInProgress"]), 1)
                original = pathlib.Path(next(iter(interrupted["cleanupInProgress"])))
                quarantine = runtime_transaction.TransactionExecutor._cleanup_quarantine(original)
                self.assertFalse(original.exists())
                self.assertTrue(quarantine.is_dir())

                if substitution == "replace-root":
                    displaced = quarantine.with_name(f"{quarantine.name}.foreign-displaced")
                    quarantine.rename(displaced)
                    quarantine.mkdir()
                    (quarantine / "foreign.bin").write_bytes(b"foreign cleanup payload")
                    with self.assertRaises(TransactionError) as raised:
                        fixture.executor("install", operations).execute()
                    self.assertEqual(raised.exception.code, "transaction.cleanup_failed")
                    self.assertEqual(
                        (quarantine / "foreign.bin").read_bytes(),
                        b"foreign cleanup payload",
                    )
                elif substitution == "add-child":
                    foreign = quarantine / "foreign.bin"
                    foreign.write_bytes(b"foreign cleanup payload")
                    with self.assertRaises(TransactionError) as raised:
                        fixture.executor("install", operations).execute()
                    self.assertEqual(raised.exception.code, "transaction.cleanup_failed")
                    self.assertEqual(foreign.read_bytes(), b"foreign cleanup payload")
                else:
                    report = fixture.executor("install", operations).execute()
                    self.assertTrue(report.ok)
                    self.assertEqual(report.state, "committed")
                    completed = json.loads(fixture.journal_path.read_text())
                    self.assertEqual(completed["cleanupInProgress"], {})
                    self.assertEqual(fixture.transaction_temporary_paths(), [])

    def test_crash_during_rollback_resumes_rolling_back_step(self) -> None:
        with fixture_layout() as fixture:
            source = fixture.artifact_file("payload/create.bin", b"create payload")
            target = fixture.target_root / "create.bin"
            operations: list[dict[str, Any]] = [
                {
                    "id": "assert_create_absent",
                    "resource": "created_file",
                    "action": "assert_absent",
                    "target": str(target),
                },
                {
                    "id": "create_file",
                    "resource": "created_file",
                    "action": "create_file",
                    "atomic": True,
                    "source": str(source),
                    "sourceSha256": artifact_contract.sha256_file(source),
                    "target": str(target),
                }
            ]

            def crash_rollback(step_id: str, phase: str) -> None:
                if step_id == "create_file" and phase == "after-mutation":
                    raise RuntimeError("start rollback")
                if step_id == "create_file" and phase == "before-rollback":
                    raise SimulatedCrash("rollback crash")

            with self.assertRaises(SimulatedCrash):
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=crash_rollback,
                ).execute()

            journal = json.loads(fixture.journal_path.read_text())
            self.assertEqual(journal["state"], "rolling-back")
            self.assertEqual(journal["steps"][1]["status"], "rolling-back")
            report = fixture.executor("install", operations).recover()
            self.assertEqual(report.state, "rolled-back")
            self.assertFalse(target.exists())
            self.assertEqual(fixture.transaction_temporary_paths(), [])

    def test_plan_or_kind_drift_refuses_and_preserves_running_journal(self) -> None:
        with fixture_layout() as fixture:
            source = fixture.artifact_file("payload/create.bin", b"create payload")
            alternate = fixture.artifact_file("payload/alternate.bin", b"alternate payload")
            target = fixture.target_root / "create.bin"
            operations: list[dict[str, Any]] = [
                {
                    "id": "assert_create_absent",
                    "resource": "created_file",
                    "action": "assert_absent",
                    "target": str(target),
                },
                {
                    "id": "create_file",
                    "resource": "created_file",
                    "action": "create_file",
                    "atomic": True,
                    "source": str(source),
                    "sourceSha256": artifact_contract.sha256_file(source),
                    "target": str(target),
                }
            ]

            def crash(step_id: str, phase: str) -> None:
                if step_id == "create_file" and phase == "after-mutation":
                    raise SimulatedCrash("plan drift fixture")

            with self.assertRaises(SimulatedCrash):
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=crash,
                ).execute()
            journal_before = fixture.journal_path.read_bytes()

            changed = copy.deepcopy(operations)
            changed[1]["source"] = str(alternate)
            changed[1]["sourceSha256"] = artifact_contract.sha256_file(alternate)
            with self.assertRaises(TransactionError) as drift_raised:
                fixture.executor("install", changed).recover()
            self.assertEqual(drift_raised.exception.code, "transaction.journal_mismatch")
            self.assertEqual(fixture.journal_path.read_bytes(), journal_before)

            uninstall_operations: list[dict[str, Any]] = [
                {
                    "id": "remove_file",
                    "resource": "created_file",
                    "action": "remove",
                    "source": str(source),
                    "sourceSha256": artifact_contract.sha256_file(source),
                    "target": str(target),
                }
            ]
            with self.assertRaises(TransactionError) as kind_raised:
                fixture.executor("uninstall", uninstall_operations).recover()
            self.assertEqual(kind_raised.exception.code, "transaction.journal_mismatch")
            self.assertEqual(fixture.journal_path.read_bytes(), journal_before)

            fixture.executor("install", operations).recover()
            self.assertFalse(target.exists())

    def test_target_source_and_journal_symlinks_fail_closed(self) -> None:
        for scenario in ("target", "source", "journal"):
            with self.subTest(scenario=scenario), fixture_layout() as fixture:
                outside = fixture.root / "outside"
                outside.mkdir()
                outside_source = outside / "source.bin"
                outside_source.write_bytes(b"payload")
                target = fixture.target_root / "target.bin"
                source = fixture.artifact_file("payload/source.bin", b"payload")
                journal_path = fixture.journal_path
                if scenario == "target":
                    target_parent = fixture.target_root / "linked"
                    target_parent.symlink_to(outside, target_is_directory=True)
                    target = target_parent / "target.bin"
                elif scenario == "source":
                    source.unlink()
                    source.symlink_to(outside_source)
                else:
                    journal_path.symlink_to(outside / "journal.json")
                operations: list[dict[str, Any]] = [
                    {
                        "id": "assert_target_absent",
                        "resource": "created_file",
                        "action": "assert_absent",
                        "target": str(target),
                    },
                    {
                        "id": "create_file",
                        "resource": "created_file",
                        "action": "create_file",
                        "atomic": True,
                        "source": str(source),
                        "sourceSha256": artifact_contract.sha256_file(outside_source),
                        "target": str(target),
                    }
                ]

                with self.assertRaises(TransactionError) as raised:
                    fixture.executor(
                        "install",
                        operations,
                        journal_path=journal_path,
                    ).execute()

                self.assertEqual(raised.exception.code, "path.symlink")
                self.assertFalse((outside / "target.bin").exists())
                self.assertFalse((outside / "journal.json").exists())

    def test_escape_unknown_duplicate_and_marker_traversal_fail_before_mutation(self) -> None:
        with fixture_layout() as fixture:
            source = fixture.artifact_file("payload/source.bin", b"payload")
            outside_target = fixture.root / "outside.bin"
            escape: list[dict[str, Any]] = [
                {
                    "id": "create_file",
                    "resource": "created_file",
                    "action": "create_file",
                    "atomic": True,
                    "source": str(source),
                    "sourceSha256": artifact_contract.sha256_file(source),
                    "target": str(outside_target),
                }
            ]
            with self.assertRaises(TransactionError) as escape_raised:
                fixture.executor("install", escape).execute()
            self.assertEqual(escape_raised.exception.code, "path.unsafe")

            retain = {
                "id": "retain_path",
                "resource": "retained_path",
                "action": "retain",
                "target": str(fixture.target_root / "retained"),
            }
            with self.assertRaises(TransactionError) as duplicate_raised:
                fixture.executor("install", [retain, dict(retain)]).execute()
            self.assertEqual(duplicate_raised.exception.code, "transaction.plan_invalid")

            unknown = dict(retain)
            unknown["id"] = "unknown_action"
            unknown["action"] = "delete_everything"
            with self.assertRaises(TransactionError) as unknown_raised:
                fixture.executor("install", [unknown]).execute()
            self.assertEqual(unknown_raised.exception.code, "transaction.action_unsupported")

            future = dict(retain)
            future["id"] = "future_field"
            future["futureExecutionMode"] = "unsafe"
            with self.assertRaises(TransactionError) as future_raised:
                fixture.executor("install", [future]).execute()
            self.assertEqual(future_raised.exception.code, "transaction.plan_invalid")

            tree = fixture.tree(fixture.artifact_root / "payload/Tree.app")
            tree_sha256 = artifact_contract.canonical_tree_sha256(tree)
            traversal: list[dict[str, Any]] = [
                {
                    "id": "assert_tree_owned",
                    "resource": "tree",
                    "action": "assert_absent_or_owned",
                    "source": str(tree),
                    "sourceTreeSha256": tree_sha256,
                    "marker": "../runtime-owner.json",
                    "sourceMarkerSha256": artifact_contract.sha256_file(tree / MARKER),
                    "target": str(fixture.target_root / "Tree.app"),
                },
                {
                    "id": "replace_tree",
                    "resource": "tree",
                    "action": "replace_tree",
                    "atomic": True,
                    "source": str(tree),
                    "sourceTreeSha256": tree_sha256,
                    "target": str(fixture.target_root / "Tree.app"),
                }
            ]
            with self.assertRaises(TransactionError):
                fixture.executor("install", traversal).execute()
            self.assertFalse(outside_target.exists())
            self.assertFalse(fixture.journal_path.exists())

    def test_foreign_tree_and_mismatched_backup_refuse_preflight(self) -> None:
        with fixture_layout() as fixture:
            source_tree = fixture.tree(fixture.artifact_root / "payload/Tree.app")
            target_tree = fixture.tree(
                fixture.target_root / "Tree.app",
                marker_payload=b'{"owner":"foreign"}\n',
            )
            tree_sha256 = artifact_contract.canonical_tree_sha256(source_tree)
            tree_operations: list[dict[str, Any]] = [
                {
                    "id": "assert_tree_owned",
                    "resource": "tree",
                    "action": "assert_absent_or_owned",
                    "source": str(source_tree),
                    "sourceTreeSha256": tree_sha256,
                    "marker": MARKER,
                    "sourceMarkerSha256": artifact_contract.sha256_file(source_tree / MARKER),
                    "target": str(target_tree),
                },
                {
                    "id": "replace_tree",
                    "resource": "tree",
                    "action": "replace_tree",
                    "atomic": True,
                    "source": str(source_tree),
                    "sourceTreeSha256": tree_sha256,
                    "target": str(target_tree),
                }
            ]
            before = snapshot_tree(fixture.target_root)
            with self.assertRaises(TransactionError) as tree_raised:
                fixture.executor("install", tree_operations).execute()
            self.assertEqual(tree_raised.exception.code, "transaction.target_foreign")
            self.assertEqual(snapshot_tree(fixture.target_root), before)

        with fixture_layout() as fixture:
            target = fixture.target_file("game/stock.bin", b"stock")
            backup = fixture.target_file("backups/stock.bin", b"foreign")
            backup_operations: list[dict[str, Any]] = [
                {
                    "id": "backup_stock",
                    "resource": "stock",
                    "action": "backup",
                    "target": str(target),
                    "backup": str(backup),
                }
            ]
            before = snapshot_tree(fixture.target_root)
            with self.assertRaises(TransactionError) as backup_raised:
                fixture.executor("install", backup_operations).execute()
            self.assertEqual(backup_raised.exception.code, "transaction.backup_mismatch")
            self.assertEqual(snapshot_tree(fixture.target_root), before)

    def test_rollback_failure_is_journaled_and_payloads_are_preserved(self) -> None:
        with fixture_layout() as fixture:
            source = fixture.artifact_file("payload/patched.bin", b"patched")
            target = fixture.target_file("game/stock.bin", b"stock")
            backup = fixture.target_root / "backups/stock.bin"
            backup.parent.mkdir()
            operations: list[dict[str, Any]] = [
                {
                    "id": "assert_stock",
                    "resource": "stock",
                    "action": "assert_sha256",
                    "target": str(target),
                    "expectedSha256": artifact_contract.sha256_file(target),
                },
                {
                    "id": "backup_stock",
                    "resource": "stock",
                    "action": "backup",
                    "target": str(target),
                    "backup": str(backup),
                },
                {
                    "id": "replace_stock",
                    "resource": "stock",
                    "action": "replace_file",
                    "atomic": True,
                    "source": str(source),
                    "sourceSha256": artifact_contract.sha256_file(source),
                    "target": str(target),
                }
            ]
            executor_holder: dict[str, TransactionExecutor] = {}

            def fail_and_tamper(step_id: str, phase: str) -> None:
                if step_id == "replace_stock" and phase == "after-mutation":
                    raise RuntimeError("force rollback")
                if step_id == "replace_stock" and phase == "before-rollback":
                    undo_files = list(executor_holder["executor"].undo_root.glob("*.original"))
                    self.assertEqual(len(undo_files), 1)
                    undo_files[0].write_bytes(b"tampered undo")

            executor = fixture.executor(
                "install",
                operations,
                failure_injector=fail_and_tamper,
            )
            executor_holder["executor"] = executor
            with self.assertRaises(TransactionError) as raised:
                executor.execute()

            self.assertEqual(raised.exception.code, "transaction.rollback_failed")
            journal = json.loads(fixture.journal_path.read_text())
            self.assertEqual(journal["state"], "failed")
            replace_step = next(step for step in journal["steps"] if step["id"] == "replace_stock")
            self.assertEqual(replace_step["status"], "rollback-failed")
            self.assertEqual(len(journal["rollbackFailures"]), 1)
            self.assertTrue(executor.undo_root.exists())
            self.assertEqual(target.read_bytes(), b"patched")
            with self.assertRaises(TransactionError) as recovery_raised:
                fixture.executor("install", operations).recover()
            self.assertEqual(recovery_raised.exception.code, "transaction.rollback_failed")

    def test_tampered_journal_undo_path_cannot_touch_external_file(self) -> None:
        with fixture_layout() as fixture:
            source = fixture.artifact_file("payload/create.bin", b"created")
            target = fixture.target_root / "created.bin"
            external = fixture.root / "external.bin"
            external.write_bytes(b"external")
            operations: list[dict[str, Any]] = [
                {
                    "id": "assert_created_absent",
                    "resource": "created_file",
                    "action": "assert_absent",
                    "target": str(target),
                },
                {
                    "id": "create_file",
                    "resource": "created_file",
                    "action": "create_file",
                    "atomic": True,
                    "source": str(source),
                    "sourceSha256": artifact_contract.sha256_file(source),
                    "target": str(target),
                }
            ]

            def crash(step_id: str, phase: str) -> None:
                if step_id == "create_file" and phase == "after-mutation":
                    raise SimulatedCrash("tamper fixture")

            with self.assertRaises(SimulatedCrash):
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=crash,
                ).execute()
            journal = json.loads(fixture.journal_path.read_text())
            create_step = next(step for step in journal["steps"] if step["id"] == "create_file")
            create_step["undo"]["target"] = str(external)
            fixture.journal_path.write_text(json.dumps(journal))

            with self.assertRaises(TransactionError) as raised:
                fixture.executor("install", operations).recover()

            self.assertEqual(raised.exception.code, "transaction.journal_invalid")
            self.assertEqual(external.read_bytes(), b"external")
            self.assertEqual(target.read_bytes(), b"created")

        with fixture_layout() as fixture:
            source = fixture.artifact_file("payload/create.bin", b"created")
            target = fixture.target_root / "created.bin"
            forged_operations: list[dict[str, Any]] = [
                {
                    "id": "assert_created_absent",
                    "resource": "created_file",
                    "action": "assert_absent",
                    "target": str(target),
                },
                {
                    "id": "create_file",
                    "resource": "created_file",
                    "action": "create_file",
                    "atomic": True,
                    "source": str(source),
                    "sourceSha256": artifact_contract.sha256_file(source),
                    "target": str(target),
                },
            ]

            def crash_after_intent(step_id: str, phase: str) -> None:
                if step_id == "create_file" and phase == "after-intent":
                    raise SimulatedCrash("forge committed state")

            executor = fixture.executor(
                "install",
                forged_operations,
                failure_injector=crash_after_intent,
            )
            with self.assertRaises(SimulatedCrash):
                executor.execute()
            forged = json.loads(fixture.journal_path.read_text())
            forged["state"] = "committed"
            forged["currentStep"] = None
            for step in forged["steps"]:
                step["status"] = "applied"
            fixture.journal_path.write_text(json.dumps(forged))

            with self.assertRaises(TransactionError) as forged_raised:
                fixture.executor("install", forged_operations).execute()
            self.assertEqual(forged_raised.exception.code, "transaction.journal_inconsistent")
            self.assertFalse(target.exists())
            self.assertTrue(executor.undo_root.exists())

        with fixture_layout() as fixture:
            operations, target_tree = fixture.prepare_tree_replace()
            report = fixture.executor("install", operations).execute()
            self.assertTrue(report.ok)
            (target_tree / "Contents/Resources/data.bin").write_bytes(b"drifted contents")

            with self.assertRaises(TransactionError) as drift_raised:
                fixture.executor("install", operations).execute()
            self.assertEqual(drift_raised.exception.code, "transaction.journal_inconsistent")

        with fixture_layout() as fixture:
            operations, _ = fixture.prepare_tree_replace()
            report = fixture.executor("install", operations).execute()
            self.assertTrue(report.ok)
            replace_operation = next(
                operation for operation in operations if operation["action"] == "replace_tree"
            )
            source_tree = pathlib.Path(replace_operation["source"])
            (source_tree / "Contents/Resources/data.bin").write_bytes(b"updated source")
            drifted_operations = copy.deepcopy(operations)
            updated_tree_sha256 = artifact_contract.canonical_tree_sha256(source_tree)
            for operation in drifted_operations:
                if operation["action"] in {"assert_absent_or_owned", "replace_tree"}:
                    operation["sourceTreeSha256"] = updated_tree_sha256

            with self.assertRaises(TransactionError) as source_drift_raised:
                fixture.executor("install", drifted_operations).execute()
            self.assertEqual(source_drift_raised.exception.code, "transaction.journal_mismatch")

    def test_remove_tree_recovery_rejects_modified_undo_payload(self) -> None:
        with fixture_layout() as fixture:
            operations, _ = fixture.prepare_uninstall()

            def crash(step_id: str, phase: str) -> None:
                if step_id == "remove_tree" and phase == "after-mutation":
                    raise SimulatedCrash("leave moved tree for recovery")

            with self.assertRaises(SimulatedCrash):
                fixture.executor(
                    "uninstall",
                    operations,
                    failure_injector=crash,
                ).execute()
            journal = json.loads(fixture.journal_path.read_text())
            remove_step = next(step for step in journal["steps"] if step["id"] == "remove_tree")
            moved_tree = pathlib.Path(remove_step["undo"]["original"])
            (moved_tree / "Contents/Resources/data.bin").write_bytes(b"foreign payload")

            with self.assertRaises(TransactionError) as raised:
                fixture.executor("uninstall", operations).recover()
            self.assertEqual(raised.exception.code, "transaction.rollback_failed")
            failed = json.loads(fixture.journal_path.read_text())
            self.assertEqual(failed["state"], "failed")
            self.assertTrue(moved_tree.is_dir())

    def test_developer_id_prior_tree_requires_validation_and_updates(self) -> None:
        with fixture_layout() as fixture:
            operations, target_tree, _ = fixture.prepare_managed_tree_replace()
            with self.assertRaises(TransactionError) as unqualified:
                fixture.executor("install", operations).validate()
            self.assertEqual(unqualified.exception.code, "transaction.target_foreign")

            validations: list[pathlib.Path] = []

            def validate_bundle(
                path: pathlib.Path,
                _operation: dict[str, Any],
            ) -> dict[str, Any]:
                validations.append(path)
                return {"kind": "developer-id"}

            report = fixture.executor(
                "install",
                operations,
                tree_ownership_validator=validate_bundle,
            ).execute()
            self.assertTrue(report.ok)
            self.assertGreaterEqual(len(validations), 2)
            source = pathlib.Path(operations[1]["source"])
            self.assertEqual(
                artifact_contract.canonical_tree_sha256(target_tree),
                artifact_contract.canonical_tree_sha256(source),
            )

    def test_developer_id_prior_tree_rolls_back_exactly(self) -> None:
        with fixture_layout() as fixture:
            operations, target_tree, prior_snapshot = fixture.prepare_managed_tree_replace()
            tail_source = fixture.artifact_file("payload/tail.bin", b"tail payload")
            tail_target = fixture.target_root / "tail.bin"
            operations.extend(
                [
                    {
                        "id": "assert_tail_absent",
                        "resource": "tail",
                        "action": "assert_absent",
                        "target": str(tail_target),
                        "ready": True,
                    },
                    {
                        "id": "create_tail",
                        "resource": "tail",
                        "action": "create_file",
                        "atomic": True,
                        "source": str(tail_source),
                        "sourceSha256": artifact_contract.sha256_file(tail_source),
                        "target": str(tail_target),
                        "ready": True,
                    },
                ]
            )

            def fail_after_tail(step_id: str, phase: str) -> None:
                if step_id == "create_tail" and phase == "after-mutation":
                    raise RuntimeError("force exact prior-tree rollback")

            with self.assertRaises(TransactionError) as raised:
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=fail_after_tail,
                    tree_ownership_validator=lambda _path, _operation: {
                        "kind": "developer-id"
                    },
                ).execute()
            self.assertEqual(raised.exception.code, "transaction.rolled_back")
            self.assertEqual(snapshot_tree(target_tree), prior_snapshot)
            self.assertFalse(tail_target.exists())

    def test_developer_id_exchange_crashes_keep_the_stable_target_present(self) -> None:
        with fixture_layout() as fixture:
            operations, target_tree, prior_snapshot = fixture.prepare_managed_tree_replace()
            source_tree = pathlib.Path(operations[1]["source"])

            def crash_after_exchange(step_id: str, phase: str) -> None:
                if step_id == "replace_managed_tree" and phase == "after-tree-exchanged":
                    self.assertTrue(target_tree.is_dir())
                    self.assertEqual(
                        artifact_contract.canonical_tree_sha256(target_tree),
                        artifact_contract.canonical_tree_sha256(source_tree),
                    )
                    raise SimulatedCrash("crash after stable tree exchange")

            with self.assertRaises(SimulatedCrash):
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=crash_after_exchange,
                    tree_ownership_validator=lambda _path, _operation: {
                        "kind": "developer-id"
                    },
                ).execute()
            self.assertTrue(target_tree.is_dir())
            journal = json.loads(fixture.journal_path.read_text())
            self.assertEqual(journal["steps"][1]["undo"]["phase"], "staged")

            recovered = fixture.executor(
                "install",
                operations,
                tree_ownership_validator=lambda _path, _operation: {
                    "kind": "developer-id"
                },
            ).recover()
            self.assertEqual(recovered.state, "rolled-back")
            self.assertTrue(target_tree.is_dir())
            self.assertEqual(snapshot_tree(target_tree), prior_snapshot)

        with fixture_layout() as fixture:
            operations, target_tree, prior_snapshot = fixture.prepare_managed_tree_replace()
            tail_source = fixture.artifact_file("payload/tail.bin", b"tail payload")
            tail_target = fixture.target_root / "tail.bin"
            operations.extend(
                [
                    {
                        "id": "assert_tail_absent",
                        "resource": "tail",
                        "action": "assert_absent",
                        "target": str(tail_target),
                    },
                    {
                        "id": "create_tail",
                        "resource": "tail",
                        "action": "create_file",
                        "atomic": True,
                        "source": str(tail_source),
                        "sourceSha256": artifact_contract.sha256_file(tail_source),
                        "target": str(tail_target),
                    },
                ]
            )

            def crash_during_rollback(step_id: str, phase: str) -> None:
                if step_id == "create_tail" and phase == "after-mutation":
                    raise RuntimeError("begin rollback")
                if (
                    step_id == "replace_managed_tree"
                    and phase == "after-tree-rollback-exchanged"
                ):
                    self.assertTrue(target_tree.is_dir())
                    raise SimulatedCrash("crash after rollback exchange")

            with self.assertRaises(SimulatedCrash):
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=crash_during_rollback,
                    tree_ownership_validator=lambda _path, _operation: {
                        "kind": "developer-id"
                    },
                ).execute()
            self.assertTrue(target_tree.is_dir())
            self.assertEqual(snapshot_tree(target_tree), prior_snapshot)
            recovered = fixture.executor(
                "install",
                operations,
                tree_ownership_validator=lambda _path, _operation: {
                    "kind": "developer-id"
                },
            ).recover()
            self.assertEqual(recovered.state, "rolled-back")
            self.assertEqual(snapshot_tree(target_tree), prior_snapshot)

    def test_stable_bundle_observations_do_not_change_plan_identity(self) -> None:
        with fixture_layout() as fixture:
            operations, _, _ = fixture.prepare_managed_tree_replace()
            baseline = fixture.executor("install", operations)
            observations_changed = copy.deepcopy(operations)
            guard = observations_changed[0]
            guard["qualifiedTargetTreeSha256"] = "f" * 64
            guard["ownershipEvidence"] = {
                "kind": "developer-id",
                "cdhash": "e" * 40,
            }
            guard["ownership"] = "managed-signed-prior"
            observed = fixture.executor("install", observations_changed)
            self.assertEqual(observed.plan_digest, baseline.plan_digest)

            policy_changed = copy.deepcopy(operations)
            policy_changed[0].pop("ownershipPolicy")
            policy_changed[0].pop("qualifiedTargetTreeSha256")
            policy_changed[0].pop("ownershipEvidence")
            semantic = fixture.executor("install", policy_changed)
            self.assertNotEqual(semantic.plan_digest, baseline.plan_digest)

    def test_exchange_rollback_resumes_interrupted_original_cleanup(self) -> None:
        with fixture_layout() as fixture:
            operations, target_tree, prior_snapshot = fixture.prepare_managed_tree_replace()
            tail_source = fixture.artifact_file("payload/tail.bin", b"tail payload")
            tail_target = fixture.target_root / "tail.bin"
            operations.extend(
                [
                    {
                        "id": "assert_tail_absent",
                        "resource": "tail",
                        "action": "assert_absent",
                        "target": str(tail_target),
                    },
                    {
                        "id": "create_tail",
                        "resource": "tail",
                        "action": "create_file",
                        "atomic": True,
                        "source": str(tail_source),
                        "sourceSha256": artifact_contract.sha256_file(tail_source),
                        "target": str(tail_target),
                    },
                ]
            )

            def fail_after_tail(step_id: str, phase: str) -> None:
                if step_id == "create_tail" and phase == "after-mutation":
                    raise RuntimeError("begin exchange rollback cleanup")

            remove_path_durable = runtime_transaction.remove_path_durable

            def crash_original_cleanup(
                path: pathlib.Path,
                *args: Any,
                **kwargs: Any,
            ) -> None:
                if "replace_managed_tree.original.descriptor-delete" in path.name:
                    raise SimulatedCrash("crash during exchanged original cleanup")
                remove_path_durable(path, *args, **kwargs)

            with (
                mock.patch.object(
                    runtime_transaction,
                    "remove_path_durable",
                    side_effect=crash_original_cleanup,
                ),
                self.assertRaises(SimulatedCrash),
            ):
                fixture.executor(
                    "install",
                    operations,
                    failure_injector=fail_after_tail,
                    tree_ownership_validator=lambda _path, _operation: {
                        "kind": "developer-id"
                    },
                ).execute()

            self.assertTrue(target_tree.is_dir())
            self.assertEqual(snapshot_tree(target_tree), prior_snapshot)
            recovered = fixture.executor(
                "install",
                operations,
                tree_ownership_validator=lambda _path, _operation: {
                    "kind": "developer-id"
                },
            ).recover()
            self.assertEqual(recovered.state, "rolled-back")
            self.assertEqual(snapshot_tree(target_tree), prior_snapshot)
            self.assertFalse(fixture.transaction_temporary_paths())

    def test_retain_tree_preserves_exact_target_and_rejects_modified_tree(self) -> None:
        with fixture_layout() as fixture:
            source = fixture.tree(fixture.artifact_root / "payload/Retained.app")
            target = fixture.tree(fixture.target_root / "Retained.app")
            source_tree = artifact_contract.canonical_tree_sha256(source)
            operation = {
                "id": "retain_tree",
                "resource": "retained_tree",
                "action": "retain_tree",
                "source": str(source),
                "sourceTreeSha256": source_tree,
                "marker": MARKER,
                "sourceMarkerSha256": artifact_contract.sha256_file(source / MARKER),
                "target": str(target),
                "ready": True,
            }
            before = snapshot_tree(target)
            report = fixture.executor("uninstall", [operation]).execute()
            self.assertTrue(report.ok)
            self.assertEqual(snapshot_tree(target), before)

        with fixture_layout() as fixture:
            source = fixture.tree(fixture.artifact_root / "payload/Retained.app")
            target = fixture.tree(
                fixture.target_root / "Retained.app",
                data_payload=b"modified retained tree",
            )
            operation = {
                "id": "retain_tree",
                "resource": "retained_tree",
                "action": "retain_tree",
                "source": str(source),
                "sourceTreeSha256": artifact_contract.canonical_tree_sha256(source),
                "marker": MARKER,
                "sourceMarkerSha256": artifact_contract.sha256_file(source / MARKER),
                "target": str(target),
                "ready": False,
                "blockedReason": "retained tree differs",
            }
            with self.assertRaises(TransactionError) as raised:
                fixture.executor("uninstall", [operation]).validate()
            self.assertEqual(raised.exception.code, "transaction.target_foreign")
            self.assertFalse(fixture.journal_path.exists())

        with fixture_layout() as fixture:
            source = fixture.tree(fixture.artifact_root / "payload/Retained.app")
            target = fixture.target_root / "Retained.app"
            operation = {
                "id": "retain_tree",
                "resource": "retained_tree",
                "action": "retain_tree",
                "source": str(source),
                "sourceTreeSha256": artifact_contract.canonical_tree_sha256(source),
                "marker": MARKER,
                "sourceMarkerSha256": artifact_contract.sha256_file(source / MARKER),
                "target": str(target),
                "ready": False,
                "blockedReason": "retained tree is missing",
            }
            with self.assertRaises(TransactionError) as raised:
                fixture.executor("uninstall", [operation]).validate()
            self.assertEqual(raised.exception.code, "transaction.target_missing")
            self.assertFalse(fixture.journal_path.exists())

    def test_file_rollback_is_noop_when_original_target_is_unchanged(self) -> None:
        with fixture_layout() as fixture:
            operations, paths = fixture.prepare_install()
            target_parent = paths["stock"].parent

            def fail_after_intent(step_id: str, phase: str) -> None:
                if step_id == "replace_stock" and phase == "after-intent":
                    target_parent.chmod(0o500)
                    raise RuntimeError("target parent became read-only")

            try:
                with self.assertRaises(TransactionError) as raised:
                    fixture.executor(
                        "install",
                        operations,
                        failure_injector=fail_after_intent,
                    ).execute()
            finally:
                target_parent.chmod(0o700)
            self.assertEqual(raised.exception.code, "transaction.rolled_back")
            report = raised.exception.context["report"]
            self.assertEqual(report["state"], "rolled-back")
            self.assertEqual(paths["stock"].read_bytes(), b"stock payload")

    def test_equivalent_artifact_relocation_preserves_plan_identity(self) -> None:
        with fixture_layout() as fixture:
            operations, _ = fixture.prepare_install()
            original = fixture.executor("install", operations)
            relocated_root = fixture.root / "relocated-artifact"
            shutil.copytree(fixture.artifact_root, relocated_root, copy_function=shutil.copy2)
            relocated_operations = copy.deepcopy(operations)
            for operation in relocated_operations:
                source = operation.get("source")
                if isinstance(source, str):
                    relative = pathlib.Path(source).relative_to(fixture.artifact_root)
                    operation["source"] = str(relocated_root / relative)
            relocated = TransactionExecutor(
                kind="install",
                operations=relocated_operations,
                artifact_root=relocated_root,
                journal_path=fixture.journal_path,
                transaction_root=fixture.transaction_root,
                allowed_roots=[fixture.target_root],
            )
            self.assertEqual(relocated.plan_digest, original.plan_digest)


if __name__ == "__main__":
    unittest.main()
