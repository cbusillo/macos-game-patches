"""Hardware-free fixtures for descriptor-anchored filesystem primitives."""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

import build_runtime_artifact as artifact_contract
import runtime_descriptor
from runtime_descriptor import DescriptorError, DescriptorSession, FileIdentity


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CODE_ROOT = pathlib.Path(os.environ.get("RUNTIME_FIXTURE_ROOT", REPO_ROOT / ".code"))


class DescriptorTests(unittest.TestCase):
    def setUp(self) -> None:
        CODE_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="runtime-descriptor-", dir=CODE_ROOT)
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.source_root = self.root / "source"
        self.target_root = self.root / "target"
        self.source_root.mkdir()
        self.target_root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_file_copy_displacement_and_exclusive_publication(self) -> None:
        source = self.source_root / "payload.bin"
        target = self.target_root / "installed.bin"
        staging = self.target_root / ".installed.staging"
        original = self.target_root / ".installed.original"
        rollback = self.target_root / ".installed.rollback"
        source.write_bytes(b"new payload")
        target.write_bytes(b"stock payload")

        with DescriptorSession([self.source_root, self.target_root]) as session:
            source_entry = session.bind(source)
            target_entry = session.bind(target)
            staging_entry = session.bind(staging)
            original_entry = session.bind(original)
            rollback_entry = session.bind(rollback)
            staging_entry.copy_file_from(source_entry)
            original_entry.rename_exclusive_from(target_entry)
            target_entry.rename_exclusive_from(staging_entry)

            self.assertEqual(target_entry.read_bytes(), b"new payload")
            self.assertEqual(original_entry.read_bytes(), b"stock payload")

            rollback_entry.rename_exclusive_from(target_entry)
            target_entry.rename_exclusive_from(original_entry)
            self.assertEqual(target_entry.read_bytes(), b"stock payload")
            rollback_entry.remove()

            absent = session.bind(self.target_root / "created.bin")
            staging_entry.copy_file_from(source_entry)
            absent.rename_exclusive_from(staging_entry)
            self.assertEqual(absent.read_bytes(), b"new payload")

            second_staging = session.bind(self.target_root / ".created.staging")
            second_staging.copy_file_from(source_entry)
            with self.assertRaises(DescriptorError) as raised:
                absent.rename_exclusive_from(second_staging)
            self.assertEqual(raised.exception.code, "transaction.target_changed")
            self.assertTrue(second_staging.exists())

    def test_tree_copy_matches_canonical_digest_and_removes_exactly(self) -> None:
        source = self.source_root / "Bridge.app"
        (source / "a").mkdir(parents=True)
        (source / "a" / "child.bin").write_bytes(b"child")
        (source / "a-z.bin").write_bytes(b"sibling")
        (source / "empty").mkdir()
        target = self.target_root / ".Bridge.staging"

        with DescriptorSession([self.source_root, self.target_root]) as session:
            source_entry = session.bind(source)
            target_entry = session.bind(target)
            target_entry.copy_tree_from(source_entry)
            self.assertEqual(
                target_entry.tree_sha256(),
                artifact_contract.canonical_tree_sha256(source),
            )
            target_entry.remove()
            self.assertFalse(target_entry.exists())

    def test_directory_rename_invalidates_cached_descendant_descriptors(self) -> None:
        source = self.source_root / "Bridge.app"
        target = self.target_root / "Bridge.app"
        staging = self.target_root / ".Bridge.staging"
        original = self.target_root / ".Bridge.original"
        (source / "Contents").mkdir(parents=True)
        (target / "Contents").mkdir(parents=True)
        (source / "Contents" / "value.bin").write_bytes(b"new")
        (target / "Contents" / "value.bin").write_bytes(b"old")

        with DescriptorSession([self.source_root, self.target_root]) as session:
            cached_target_child = session.bind(target / "Contents" / "value.bin")
            self.assertEqual(cached_target_child.read_bytes(), b"old")
            staging_entry = session.bind(staging)
            staging_entry.copy_tree_from(session.bind(source))
            session.bind(original).rename_exclusive_from(session.bind(target))
            session.bind(target).rename_exclusive_from(staging_entry)
            with self.assertRaises(DescriptorError) as raised:
                cached_target_child.read_bytes()
            self.assertEqual(raised.exception.code, "transaction.path_identity_changed")
            self.assertEqual(
                session.bind(target / "Contents" / "value.bin").read_bytes(),
                b"new",
            )

    def test_parent_identity_drift_preserves_the_pinned_parent(self) -> None:
        live = self.target_root / "live"
        moved = self.target_root / "live.original"
        live.mkdir()
        original = live / "value.bin"
        original.write_bytes(b"original")

        with DescriptorSession([self.target_root]) as session:
            original_entry = session.bind(original)
            live.rename(moved)
            live.mkdir()
            (live / "value.bin").write_bytes(b"replacement")

            with self.assertRaises(DescriptorError) as raised:
                session.verify_all()
            self.assertEqual(raised.exception.code, "transaction.path_identity_changed")
            self.assertEqual(original_entry.read_bytes(), b"original")
            self.assertEqual((live / "value.bin").read_bytes(), b"replacement")

    def test_root_identity_drift_is_detected(self) -> None:
        authority = self.target_root / "authority"
        moved = self.target_root / "authority.original"
        authority.mkdir()

        with DescriptorSession([authority]) as session:
            session.bind(authority / "value.bin")
            authority.rename(moved)
            authority.mkdir()
            with self.assertRaises(DescriptorError) as raised:
                session.verify_all()
            self.assertEqual(raised.exception.code, "transaction.path_identity_changed")

    def test_symlink_root_and_tree_entry_fail_closed(self) -> None:
        real = self.target_root / "real"
        linked = self.target_root / "linked"
        real.mkdir()
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(DescriptorError) as raised:
            with DescriptorSession([linked]):
                pass
        self.assertEqual(raised.exception.code, "path.symlink")

        tree = self.source_root / "tree"
        tree.mkdir()
        (tree / "escape").symlink_to(self.target_root, target_is_directory=True)
        with DescriptorSession([self.source_root]) as session:
            with self.assertRaises(DescriptorError) as raised:
                session.bind(tree).tree_sha256()
        self.assertEqual(raised.exception.code, "path.symlink")

    def test_unsupported_tree_entry_fails_closed(self) -> None:
        tree = self.source_root / "tree"
        tree.mkdir()
        fifo = tree / "pipe"
        os.mkfifo(fifo)
        with DescriptorSession([self.source_root]) as session:
            with self.assertRaises(DescriptorError) as raised:
                session.bind(tree).tree_sha256()
        self.assertEqual(raised.exception.code, "transaction.tree_unsupported")

    def test_private_directory_chain_is_created_without_following_symlinks(self) -> None:
        authority = self.target_root / "owned"
        target = authority / "transactions" / "history"
        runtime_descriptor.ensure_private_directory(
            target,
            authority,
            owner_uid=os.getuid(),
        )
        for path in (authority, authority / "transactions", target):
            self.assertTrue(path.is_dir())
            self.assertEqual(path.stat().st_mode & 0o7777, 0o700)

        unsafe = self.target_root / "unsafe"
        unsafe.symlink_to(self.source_root, target_is_directory=True)
        with self.assertRaises(DescriptorError) as raised:
            runtime_descriptor.ensure_private_directory(
                unsafe / "state",
                unsafe,
                owner_uid=os.getuid(),
            )
        self.assertEqual(raised.exception.code, "path.symlink")

        wrong_mode = self.target_root / "wrong-mode"
        wrong_mode.mkdir(mode=0o755)
        with self.assertRaises(DescriptorError) as raised:
            runtime_descriptor.ensure_private_directory(
                wrong_mode,
                wrong_mode,
                owner_uid=os.getuid(),
            )
        self.assertEqual(raised.exception.code, "transaction.mode_unsafe")

    def test_authority_probe_is_non_mutating_and_reports_missing_suffix(self) -> None:
        existing = runtime_descriptor.probe_authority_root(self.target_root)
        self.assertTrue(existing["exists"])
        self.assertIn("RENAME_", existing["backend"])

        missing_path = self.target_root / "not-created" / "child"
        missing = runtime_descriptor.probe_authority_root(missing_path)
        self.assertFalse(missing["exists"])
        self.assertEqual(missing["missingFrom"], str(self.target_root / "not-created"))
        self.assertFalse((self.target_root / "not-created").exists())

    def test_parent_identity_payload_rejects_drift_and_tampering(self) -> None:
        target = self.target_root / "value.bin"
        target.write_bytes(b"payload")
        with DescriptorSession([self.target_root]) as session:
            entry = session.bind(target)
            payload = entry.parent_identity_payload()
            entry.require_parent_identity(payload, field="parentIdentity")
            tampered = dict(payload)
            tampered["inode"] += 1
            with self.assertRaises(DescriptorError) as raised:
                entry.require_parent_identity(tampered, field="parentIdentity")
            self.assertEqual(raised.exception.code, "transaction.path_identity_changed")

            with self.assertRaises(DescriptorError) as raised:
                FileIdentity.from_payload({"device": 1}, field="parentIdentity")
            self.assertEqual(raised.exception.code, "transaction.journal_invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
