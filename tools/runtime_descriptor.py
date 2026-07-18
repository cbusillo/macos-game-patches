"""Descriptor-anchored filesystem primitives for runtime transactions."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import pathlib
import stat
import sys
from dataclasses import dataclass
from typing import Any, Iterable


class DescriptorError(Exception):
    """Stable machine-readable descriptor filesystem failure."""

    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context


def _required_flag(name: str) -> int:
    value = getattr(os, name, None)
    if not isinstance(value, int):
        raise DescriptorError(
            "transaction.descriptor_unsupported",
            "Required no-follow descriptor support is unavailable",
            capability=name,
        )
    return value


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | _required_flag("O_DIRECTORY")
        | _required_flag("O_NOFOLLOW")
        | getattr(os, "O_CLOEXEC", 0)
    )


def _file_read_flags() -> int:
    return os.O_RDONLY | _required_flag("O_NOFOLLOW") | getattr(os, "O_CLOEXEC", 0)


def _file_create_flags() -> int:
    return (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | _required_flag("O_NOFOLLOW")
        | getattr(os, "O_CLOEXEC", 0)
    )


def _canonical_absolute(path: pathlib.Path) -> pathlib.Path:
    candidate = pathlib.Path(path)
    if not candidate.is_absolute():
        raise DescriptorError(
            "transaction.path_unsafe",
            "Descriptor path must be absolute",
            path=str(candidate),
        )
    canonical = pathlib.Path(os.path.abspath(candidate))
    if candidate != canonical:
        raise DescriptorError(
            "transaction.path_unsafe",
            "Descriptor path must be canonical",
            path=str(candidate),
        )
    return candidate


def _component_error(error: OSError, path: pathlib.Path) -> DescriptorError:
    if error.errno == errno.ELOOP:
        return DescriptorError("path.symlink", "Path contains a symlink", path=str(path))
    if error.errno == errno.ENOENT:
        return DescriptorError(
            "transaction.parent_missing",
            "Descriptor path component is missing",
            path=str(path),
        )
    if error.errno == errno.ENOTDIR:
        return DescriptorError(
            "transaction.path_unsafe",
            "Descriptor path component is not a directory",
            path=str(path),
        )
    return DescriptorError(
        "transaction.descriptor_operation_failed",
        "Descriptor path traversal failed",
        path=str(path),
        errno=error.errno,
        detail=str(error),
    )


def _open_directory_component(parent: int, component: str, path: pathlib.Path) -> int:
    try:
        return os.open(component, _directory_flags(), dir_fd=parent)
    except OSError as error:
        try:
            metadata = os.stat(component, dir_fd=parent, follow_symlinks=False)
        except OSError:
            metadata = None
        if metadata is not None and stat.S_ISLNK(metadata.st_mode):
            raise DescriptorError("path.symlink", "Path contains a symlink", path=str(path)) from error
        raise _component_error(error, path) from error


def _open_absolute_directory(path: pathlib.Path) -> int:
    canonical = _canonical_absolute(path)
    descriptor = os.open(canonical.anchor, _directory_flags())
    current = pathlib.Path(canonical.anchor)
    try:
        for component in canonical.parts[1:]:
            current /= component
            child = _open_directory_component(descriptor, component, current)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    file_type: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> FileIdentity:
        return cls(metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))

    @classmethod
    def from_payload(cls, value: Any, *, field: str) -> FileIdentity:
        if not isinstance(value, dict) or set(value) != {"device", "inode", "fileType"}:
            raise DescriptorError(
                "transaction.journal_invalid",
                "Journal descriptor identity is invalid",
                field=field,
            )
        if not all(isinstance(value[key], int) and value[key] >= 0 for key in value):
            raise DescriptorError(
                "transaction.journal_invalid",
                "Journal descriptor identity is invalid",
                field=field,
            )
        return cls(value["device"], value["inode"], value["fileType"])

    def to_payload(self) -> dict[str, int]:
        return {
            "device": self.device,
            "inode": self.inode,
            "fileType": self.file_type,
        }


def _require_identity(
    actual: FileIdentity,
    expected: FileIdentity,
    path: pathlib.Path,
) -> None:
    if actual != expected:
        raise DescriptorError(
            "transaction.path_identity_changed",
            "Descriptor path now resolves to a different filesystem object",
            path=str(path),
            expected=expected.to_payload(),
            actual=actual.to_payload(),
        )


def _require_same_device(
    metadata: os.stat_result,
    device: int,
    path: pathlib.Path,
) -> None:
    if metadata.st_dev != device:
        raise DescriptorError(
            "transaction.path_identity_changed",
            "Descriptor path crosses an undeclared device boundary",
            path=str(path),
            expectedDevice=device,
            actualDevice=metadata.st_dev,
        )


@dataclass
class _RootHandle:
    path: pathlib.Path
    descriptor: int
    identity: FileIdentity
    closed: bool = False

    @classmethod
    def open(cls, path: pathlib.Path) -> _RootHandle:
        canonical = _canonical_absolute(path)
        descriptor = _open_absolute_directory(canonical)
        metadata = os.fstat(descriptor)
        return cls(canonical, descriptor, FileIdentity.from_stat(metadata))

    def close(self) -> None:
        if not self.closed:
            os.close(self.descriptor)
            self.closed = True

    def verify_current(self) -> None:
        descriptor = _open_absolute_directory(self.path)
        try:
            _require_identity(
                FileIdentity.from_stat(os.fstat(descriptor)),
                self.identity,
                self.path,
            )
        finally:
            os.close(descriptor)

    def open_relative_directory(self, parts: tuple[str, ...]) -> int:
        descriptor = os.dup(self.descriptor)
        current = self.path
        try:
            for component in parts:
                current /= component
                child = _open_directory_component(descriptor, component, current)
                metadata = os.fstat(child)
                _require_same_device(metadata, self.identity.device, current)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise


@dataclass
class _ParentHandle:
    root: _RootHandle
    path: pathlib.Path
    relative_parts: tuple[str, ...]
    descriptor: int
    identity: FileIdentity
    closed: bool = False

    @classmethod
    def open(
        cls,
        root: _RootHandle,
        path: pathlib.Path,
        relative_parts: tuple[str, ...],
    ) -> _ParentHandle:
        descriptor = root.open_relative_directory(relative_parts)
        return cls(
            root,
            path,
            relative_parts,
            descriptor,
            FileIdentity.from_stat(os.fstat(descriptor)),
        )

    def close(self) -> None:
        if not self.closed:
            os.close(self.descriptor)
            self.closed = True

    def verify_current(self) -> None:
        descriptor = self.root.open_relative_directory(self.relative_parts)
        try:
            _require_identity(
                FileIdentity.from_stat(os.fstat(descriptor)),
                self.identity,
                self.path,
            )
        finally:
            os.close(descriptor)


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(descriptor, payload[offset:])


def _copy_descriptor(source: int, target: int) -> None:
    while True:
        chunk = os.read(source, 1024 * 1024)
        if not chunk:
            break
        _write_all(target, chunk)


def _copy_metadata(source: os.stat_result, target: int) -> None:
    os.fchmod(target, stat.S_IMODE(source.st_mode))
    os.utime(target, ns=(source.st_atime_ns, source.st_mtime_ns))


def _entry_stat(parent: _ParentHandle, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise DescriptorError(
            "transaction.descriptor_operation_failed",
            "Descriptor entry inspection failed",
            path=str(parent.path / name),
            errno=error.errno,
            detail=str(error),
        ) from error


def _require_supported_entry(metadata: os.stat_result, path: pathlib.Path) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise DescriptorError("path.symlink", "Tree contains a symlink", path=str(path))
    if not stat.S_ISREG(metadata.st_mode) and not stat.S_ISDIR(metadata.st_mode):
        raise DescriptorError(
            "transaction.tree_unsupported",
            "Tree contains an unsupported filesystem entry",
            path=str(path),
        )


class DescriptorSession:
    """Pins declared filesystem roots and parent directories for one operation."""

    def __init__(self, roots: Iterable[pathlib.Path]) -> None:
        canonical_roots = {_canonical_absolute(pathlib.Path(root)) for root in roots}
        if not canonical_roots:
            raise DescriptorError(
                "transaction.descriptor_unsupported",
                "Descriptor session requires at least one authority root",
            )
        self._root_paths = sorted(
            canonical_roots,
            key=lambda item: (len(item.parts), str(item)),
            reverse=True,
        )
        self._roots: list[_RootHandle] = []
        self._parents: dict[pathlib.Path, _ParentHandle] = {}
        self._entered = False

    def __enter__(self) -> DescriptorSession:
        if self._entered:
            raise RuntimeError("descriptor session is already active")
        _atomic_rename_function()
        try:
            self._roots = [_RootHandle.open(path) for path in self._root_paths]
        except BaseException:
            for root in reversed(self._roots):
                root.close()
            self._roots = []
            raise
        self._entered = True
        return self

    def __exit__(self, *_: Any) -> None:
        for parent in reversed(list(self._parents.values())):
            parent.close()
        for root in reversed(self._roots):
            root.close()
        self._parents.clear()
        self._roots = []
        self._entered = False

    def _root_for(self, path: pathlib.Path) -> _RootHandle:
        canonical = _canonical_absolute(path)
        for root in self._roots:
            if canonical == root.path or canonical.is_relative_to(root.path):
                return root
        raise DescriptorError(
            "transaction.path_unsafe",
            "Descriptor path is outside declared authority roots",
            path=str(canonical),
        )

    def bind(self, path: pathlib.Path) -> BoundPath:
        if not self._entered:
            raise RuntimeError("descriptor session is not active")
        canonical = _canonical_absolute(path)
        root = self._root_for(canonical)
        if canonical == root.path:
            return BoundPath(self, root, None, None, canonical)
        parent_path = canonical.parent
        parent = self._parents.get(parent_path)
        if parent is None:
            relative = parent_path.relative_to(root.path)
            parent = _ParentHandle.open(root, parent_path, relative.parts)
            self._parents[parent_path] = parent
        return BoundPath(self, root, parent, canonical.name, canonical)

    def verify_all(self) -> None:
        for root in self._roots:
            root.verify_current()
        for parent in self._parents.values():
            parent.verify_current()

    def invalidate_below(self, *paths: pathlib.Path) -> None:
        canonical = tuple(_canonical_absolute(path) for path in paths)
        stale = [
            parent_path
            for parent_path in self._parents
            if any(parent_path == path or parent_path.is_relative_to(path) for path in canonical)
        ]
        for parent_path in stale:
            self._parents.pop(parent_path).close()


@dataclass(frozen=True)
class BoundPath:
    session: DescriptorSession
    root: _RootHandle
    parent: _ParentHandle | None
    name: str | None
    path: pathlib.Path

    def _parent(self) -> _ParentHandle:
        if self.parent is None or self.name is None:
            raise DescriptorError(
                "transaction.path_unsafe",
                "Authority root cannot be used as a mutable leaf",
                path=str(self.path),
            )
        if self.root.closed or self.parent.closed:
            raise DescriptorError(
                "transaction.path_identity_changed",
                "Descriptor binding was invalidated by a directory mutation",
                path=str(self.path),
            )
        return self.parent

    def _leaf_name(self) -> str:
        self._parent()
        assert self.name is not None
        return self.name

    def lstat(self) -> os.stat_result | None:
        if self.name is None:
            return os.fstat(self.root.descriptor)
        return _entry_stat(self._parent(), self.name)

    def identity(self) -> FileIdentity | None:
        metadata = self.lstat()
        return FileIdentity.from_stat(metadata) if metadata is not None else None

    def identity_payload(self) -> dict[str, int] | None:
        identity = self.identity()
        return identity.to_payload() if identity is not None else None

    def parent_identity_payload(self) -> dict[str, int]:
        parent = self._parent()
        return parent.identity.to_payload()

    def require_parent_identity(self, value: Any, *, field: str) -> None:
        expected = FileIdentity.from_payload(value, field=field)
        parent = self._parent()
        parent.verify_current()
        _require_identity(parent.identity, expected, parent.path)

    def exists(self) -> bool:
        return self.lstat() is not None

    def is_file(self) -> bool:
        metadata = self.lstat()
        return metadata is not None and stat.S_ISREG(metadata.st_mode)

    def is_dir(self) -> bool:
        metadata = self.lstat()
        return metadata is not None and stat.S_ISDIR(metadata.st_mode)

    def _open_file(self) -> tuple[int, os.stat_result]:
        parent = self._parent()
        try:
            descriptor = os.open(self._leaf_name(), _file_read_flags(), dir_fd=parent.descriptor)
        except FileNotFoundError as error:
            raise DescriptorError(
                "transaction.source_missing",
                "Descriptor file is missing",
                path=str(self.path),
            ) from error
        except OSError as error:
            code = "path.symlink" if error.errno == errno.ELOOP else "transaction.path_unsafe"
            raise DescriptorError(code, "Descriptor file cannot be opened safely", path=str(self.path)) from error
        metadata = os.fstat(descriptor)
        try:
            _require_same_device(metadata, self.root.identity.device, self.path)
            if not stat.S_ISREG(metadata.st_mode):
                raise DescriptorError(
                    "transaction.path_unsafe",
                    "Descriptor file is not a regular file",
                    path=str(self.path),
                )
            return descriptor, metadata
        except BaseException:
            os.close(descriptor)
            raise

    def _open_directory(self) -> tuple[int, os.stat_result]:
        if self.name is None:
            descriptor = os.dup(self.root.descriptor)
        else:
            parent = self._parent()
            descriptor = _open_directory_component(
                parent.descriptor,
                self._leaf_name(),
                self.path,
            )
        metadata = os.fstat(descriptor)
        try:
            _require_same_device(metadata, self.root.identity.device, self.path)
            return descriptor, metadata
        except BaseException:
            os.close(descriptor)
            raise

    def sha256_file(self) -> str:
        descriptor, _ = self._open_file()
        try:
            return _sha256_descriptor(descriptor)
        finally:
            os.close(descriptor)

    def read_bytes(self) -> bytes:
        descriptor, _ = self._open_file()
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(descriptor)

    def open_read_write_create(self, mode: int = 0o600) -> tuple[int, FileIdentity]:
        parent = self._parent()
        flags = os.O_RDWR | os.O_CREAT | _required_flag("O_NOFOLLOW")
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(self._leaf_name(), flags, mode, dir_fd=parent.descriptor)
        except OSError as error:
            code = "path.symlink" if error.errno == errno.ELOOP else "transaction.lock_failed"
            raise DescriptorError(
                code,
                "Descriptor lock file could not be opened safely",
                path=str(self.path),
                errno=error.errno,
                detail=str(error),
            ) from error
        metadata = os.fstat(descriptor)
        try:
            _require_same_device(metadata, self.root.identity.device, self.path)
            if not stat.S_ISREG(metadata.st_mode):
                raise DescriptorError(
                    "transaction.lock_unsafe",
                    "Descriptor lock path is not a regular file",
                    path=str(self.path),
                )
            identity = FileIdentity.from_stat(metadata)
            self.verify_identity(identity)
            return descriptor, identity
        except BaseException:
            os.close(descriptor)
            raise

    def tree_sha256(self) -> str:
        descriptor, metadata = self._open_directory()
        try:
            entries: list[dict[str, Any]] = [
                {
                    "path": ".",
                    "type": "directory",
                    "mode": stat.S_IMODE(metadata.st_mode),
                }
            ]
            _tree_records(descriptor, self.path, pathlib.PurePosixPath(), self.root, entries)
            entries[1:] = sorted(entries[1:], key=lambda item: item["path"])
            return hashlib.sha256(_canonical_json_bytes(entries)).hexdigest()
        finally:
            os.close(descriptor)

    def tree_cleanup_manifest(self) -> list[dict[str, Any]]:
        descriptor, metadata = self._open_directory()
        identity = FileIdentity.from_stat(metadata)
        records: list[dict[str, Any]] = [
            {
                "path": ".",
                "type": "directory",
                "mode": stat.S_IMODE(metadata.st_mode),
                "identity": identity.to_payload(),
            }
        ]
        try:
            _cleanup_tree_records(
                descriptor,
                self.path,
                pathlib.PurePosixPath(),
                self.root,
                records,
            )
            self.verify_identity(identity)
            current = self.lstat()
            if current is None or stat.S_IMODE(current.st_mode) != stat.S_IMODE(metadata.st_mode):
                raise DescriptorError(
                    "transaction.path_identity_changed",
                    "Cleanup tree root metadata changed during inspection",
                    path=str(self.path),
                )
            records[1:] = sorted(records[1:], key=lambda item: item["path"])
            return records
        finally:
            os.close(descriptor)

    def fsync_parent(self) -> None:
        os.fsync(self._parent().descriptor)

    def fsync_directory(self) -> None:
        descriptor, _ = self._open_directory()
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def verify_identity(self, expected: FileIdentity) -> None:
        actual = self.identity()
        if actual is None:
            raise DescriptorError(
                "transaction.path_identity_changed",
                "Descriptor entry disappeared",
                path=str(self.path),
                expected=expected.to_payload(),
                actual=None,
            )
        _require_identity(actual, expected, self.path)

    def verify_authority(self) -> None:
        self.root.verify_current()
        if self.parent is not None:
            self.parent.verify_current()

    def copy_file_from(self, source: BoundPath) -> FileIdentity:
        source.verify_authority()
        self.verify_authority()
        parent = self._parent()
        if self.exists():
            raise DescriptorError(
                "transaction.temporary_exists",
                "Descriptor copy target already exists",
                path=str(self.path),
            )
        source_descriptor, source_metadata = source._open_file()
        target_descriptor: int | None = None
        identity: FileIdentity | None = None
        try:
            target_descriptor = os.open(
                self._leaf_name(),
                _file_create_flags(),
                0o600,
                dir_fd=parent.descriptor,
            )
            identity = FileIdentity.from_stat(os.fstat(target_descriptor))
            _copy_descriptor(source_descriptor, target_descriptor)
            _copy_metadata(source_metadata, target_descriptor)
            os.fsync(target_descriptor)
            self.verify_identity(identity)
            self.fsync_parent()
            return identity
        except BaseException:
            if identity is not None:
                self._remove_if_identity(identity)
            raise
        finally:
            os.close(source_descriptor)
            if target_descriptor is not None:
                os.close(target_descriptor)

    def write_bytes(self, payload: bytes, mode: int = 0o600) -> FileIdentity:
        self.verify_authority()
        parent = self._parent()
        if self.exists():
            raise DescriptorError(
                "transaction.temporary_exists",
                "Descriptor write target already exists",
                path=str(self.path),
            )
        descriptor: int | None = None
        identity: FileIdentity | None = None
        try:
            descriptor = os.open(
                self._leaf_name(),
                _file_create_flags(),
                mode,
                dir_fd=parent.descriptor,
            )
            identity = FileIdentity.from_stat(os.fstat(descriptor))
            _write_all(descriptor, payload)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            self.verify_identity(identity)
            self.fsync_parent()
            return identity
        except BaseException:
            if identity is not None:
                self._remove_if_identity(identity)
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def make_directory(self, mode: int = 0o700) -> FileIdentity:
        self.verify_authority()
        parent = self._parent()
        if self.exists():
            raise DescriptorError(
                "transaction.temporary_exists",
                "Descriptor directory target already exists",
                path=str(self.path),
            )
        os.mkdir(self._leaf_name(), mode, dir_fd=parent.descriptor)
        descriptor = _open_directory_component(
            parent.descriptor,
            self._leaf_name(),
            self.path,
        )
        try:
            metadata = os.fstat(descriptor)
            _require_same_device(metadata, self.root.identity.device, self.path)
            identity = FileIdentity.from_stat(metadata)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.verify_identity(identity)
        self.fsync_parent()
        return identity

    def copy_tree_from(self, source: BoundPath) -> FileIdentity:
        source.verify_authority()
        self.verify_authority()
        parent = self._parent()
        if self.exists():
            raise DescriptorError(
                "transaction.temporary_exists",
                "Descriptor tree target already exists",
                path=str(self.path),
            )
        source_descriptor, source_metadata = source._open_directory()
        identity: FileIdentity | None = None
        try:
            os.mkdir(self._leaf_name(), 0o700, dir_fd=parent.descriptor)
            target_descriptor = os.open(
                self._leaf_name(),
                _directory_flags(),
                dir_fd=parent.descriptor,
            )
            try:
                target_metadata = os.fstat(target_descriptor)
                _require_same_device(target_metadata, self.root.identity.device, self.path)
                identity = FileIdentity.from_stat(target_metadata)
                _copy_tree_contents(
                    source_descriptor,
                    target_descriptor,
                    source.path,
                    self.path,
                    source.root,
                    self.root,
                )
                os.fchmod(target_descriptor, stat.S_IMODE(source_metadata.st_mode))
                os.utime(
                    target_descriptor,
                    ns=(source_metadata.st_atime_ns, source_metadata.st_mtime_ns),
                )
                os.fsync(target_descriptor)
            finally:
                os.close(target_descriptor)
            self.verify_identity(identity)
            self.fsync_parent()
            return identity
        except BaseException:
            if identity is not None:
                self._remove_if_identity(identity)
            raise
        finally:
            os.close(source_descriptor)

    def _remove_if_identity(self, expected: FileIdentity) -> None:
        actual = self.identity()
        if actual is None or actual != expected:
            return
        try:
            self.remove(expected)
        except DescriptorError:
            return

    def remove(
        self,
        expected: FileIdentity | None = None,
        tree_entries: list[dict[str, Any]] | None = None,
        *,
        allow_cleanup_modes: bool = False,
    ) -> None:
        self.verify_authority()
        metadata = self.lstat()
        if metadata is None:
            return
        _require_supported_entry(metadata, self.path)
        identity = FileIdentity.from_stat(metadata)
        if expected is not None:
            _require_identity(identity, expected, self.path)
        else:
            expected = identity
        parent = self._parent()
        if stat.S_ISREG(metadata.st_mode):
            if tree_entries is not None:
                raise DescriptorError(
                    "transaction.journal_invalid",
                    "File cleanup cannot carry a tree manifest",
                    path=str(self.path),
                )
            descriptor, opened = self._open_file()
            try:
                _require_identity(FileIdentity.from_stat(opened), expected, self.path)
                self.verify_identity(expected)
                os.unlink(self._leaf_name(), dir_fd=parent.descriptor)
            finally:
                os.close(descriptor)
        else:
            expected_entries = (
                {record["path"]: record for record in tree_entries}
                if tree_entries is not None
                else None
            )
            _require_cleanup_entry(
                expected_entries,
                pathlib.PurePosixPath(),
                metadata,
                "directory",
                self.path,
                allow_cleanup_mode=allow_cleanup_modes,
            )
            descriptor, opened = self._open_directory()
            try:
                _require_identity(FileIdentity.from_stat(opened), expected, self.path)
                _remove_tree_contents(
                    descriptor,
                    self.path,
                    pathlib.PurePosixPath(),
                    self.root,
                    expected_entries,
                    allow_cleanup_modes,
                )
            finally:
                os.close(descriptor)
            self.verify_identity(expected)
            os.rmdir(self._leaf_name(), dir_fd=parent.descriptor)
            self.session.invalidate_below(self.path)
        self.fsync_parent()

    def replace_with(self, source: BoundPath) -> None:
        source_parent = source._parent()
        target_parent = self._parent()
        source.verify_authority()
        self.verify_authority()
        os.replace(
            source._leaf_name(),
            self._leaf_name(),
            src_dir_fd=source_parent.descriptor,
            dst_dir_fd=target_parent.descriptor,
        )
        os.fsync(source_parent.descriptor)
        if source_parent.identity != target_parent.identity:
            os.fsync(target_parent.descriptor)

    def rename_exclusive_from(self, source: BoundPath) -> None:
        _rename_exclusive(source, self)

    def exchange_with(self, other: BoundPath) -> None:
        _rename_exchange(self, other)


def _tree_records(
    directory: int,
    absolute: pathlib.Path,
    relative: pathlib.PurePosixPath,
    root: _RootHandle,
    records: list[dict[str, Any]],
) -> None:
    with os.scandir(directory) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        entry_path = absolute / name
        child_relative = relative / name
        try:
            metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError as error:
            raise DescriptorError(
                "transaction.path_identity_changed",
                "Tree entry disappeared during descriptor traversal",
                path=str(entry_path),
            ) from error
        _require_same_device(metadata, root.identity.device, entry_path)
        _require_supported_entry(metadata, entry_path)
        if stat.S_ISREG(metadata.st_mode):
            descriptor = os.open(name, _file_read_flags(), dir_fd=directory)
            try:
                opened = os.fstat(descriptor)
                _require_same_device(opened, root.identity.device, entry_path)
                if not stat.S_ISREG(opened.st_mode):
                    raise DescriptorError(
                        "transaction.path_identity_changed",
                        "Tree file changed type during descriptor traversal",
                        path=str(entry_path),
                    )
                records.append(
                    {
                        "path": child_relative.as_posix(),
                        "type": "file",
                        "mode": stat.S_IMODE(opened.st_mode),
                        "size": opened.st_size,
                        "sha256": _sha256_descriptor(descriptor),
                    }
                )
            finally:
                os.close(descriptor)
        else:
            descriptor = os.open(name, _directory_flags(), dir_fd=directory)
            try:
                opened = os.fstat(descriptor)
                _require_same_device(opened, root.identity.device, entry_path)
                records.append(
                    {
                        "path": child_relative.as_posix(),
                        "type": "directory",
                        "mode": stat.S_IMODE(opened.st_mode),
                    }
                )
                _tree_records(descriptor, entry_path, child_relative, root, records)
            finally:
                os.close(descriptor)


def _copy_tree_contents(
    source: int,
    target: int,
    source_path: pathlib.Path,
    target_path: pathlib.Path,
    source_root: _RootHandle,
    target_root: _RootHandle,
) -> None:
    with os.scandir(source) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        source_entry = source_path / name
        target_entry = target_path / name
        metadata = os.stat(name, dir_fd=source, follow_symlinks=False)
        _require_same_device(metadata, source_root.identity.device, source_entry)
        _require_supported_entry(metadata, source_entry)
        if stat.S_ISREG(metadata.st_mode):
            source_file = os.open(name, _file_read_flags(), dir_fd=source)
            target_file: int | None = None
            try:
                opened = os.fstat(source_file)
                if not stat.S_ISREG(opened.st_mode):
                    raise DescriptorError(
                        "transaction.path_identity_changed",
                        "Source tree file changed type during copy",
                        path=str(source_entry),
                    )
                target_file = os.open(name, _file_create_flags(), 0o600, dir_fd=target)
                target_metadata = os.fstat(target_file)
                _require_same_device(target_metadata, target_root.identity.device, target_entry)
                _copy_descriptor(source_file, target_file)
                _copy_metadata(opened, target_file)
                os.fsync(target_file)
            finally:
                os.close(source_file)
                if target_file is not None:
                    os.close(target_file)
        else:
            source_directory = os.open(name, _directory_flags(), dir_fd=source)
            target_directory: int | None = None
            try:
                opened = os.fstat(source_directory)
                _require_same_device(opened, source_root.identity.device, source_entry)
                os.mkdir(name, 0o700, dir_fd=target)
                target_directory = os.open(name, _directory_flags(), dir_fd=target)
                target_metadata = os.fstat(target_directory)
                _require_same_device(target_metadata, target_root.identity.device, target_entry)
                _copy_tree_contents(
                    source_directory,
                    target_directory,
                    source_entry,
                    target_entry,
                    source_root,
                    target_root,
                )
                os.fchmod(target_directory, stat.S_IMODE(opened.st_mode))
                os.utime(
                    target_directory,
                    ns=(opened.st_atime_ns, opened.st_mtime_ns),
                )
                os.fsync(target_directory)
            finally:
                os.close(source_directory)
                if target_directory is not None:
                    os.close(target_directory)
    os.fsync(target)


def _cleanup_tree_records(
    directory: int,
    path: pathlib.Path,
    relative: pathlib.PurePosixPath,
    root: _RootHandle,
    records: list[dict[str, Any]],
) -> None:
    with os.scandir(directory) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        entry_path = path / name
        entry_relative = relative / name
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
        _require_same_device(metadata, root.identity.device, entry_path)
        _require_supported_entry(metadata, entry_path)
        identity = FileIdentity.from_stat(metadata)
        if stat.S_ISREG(metadata.st_mode):
            descriptor = os.open(name, _file_read_flags(), dir_fd=directory)
            try:
                opened = os.fstat(descriptor)
                _require_identity(FileIdentity.from_stat(opened), identity, entry_path)
                digest = _sha256_descriptor(descriptor)
            finally:
                os.close(descriptor)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            _require_identity(FileIdentity.from_stat(current), identity, entry_path)
            if stat.S_IMODE(current.st_mode) != stat.S_IMODE(opened.st_mode):
                raise DescriptorError(
                    "transaction.path_identity_changed",
                    "Cleanup tree file metadata changed during inspection",
                    path=str(entry_path),
                )
            records.append(
                {
                    "path": entry_relative.as_posix(),
                    "type": "file",
                    "mode": stat.S_IMODE(opened.st_mode),
                    "sha256": digest,
                    "identity": identity.to_payload(),
                }
            )
        else:
            child = os.open(name, _directory_flags(), dir_fd=directory)
            try:
                opened = os.fstat(child)
                _require_identity(FileIdentity.from_stat(opened), identity, entry_path)
                records.append(
                    {
                        "path": entry_relative.as_posix(),
                        "type": "directory",
                        "mode": stat.S_IMODE(opened.st_mode),
                        "identity": identity.to_payload(),
                    }
                )
                _cleanup_tree_records(child, entry_path, entry_relative, root, records)
            finally:
                os.close(child)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            _require_identity(FileIdentity.from_stat(current), identity, entry_path)
            if stat.S_IMODE(current.st_mode) != stat.S_IMODE(opened.st_mode):
                raise DescriptorError(
                    "transaction.path_identity_changed",
                    "Cleanup tree directory metadata changed during inspection",
                    path=str(entry_path),
                )


def _require_cleanup_entry(
    expected_entries: dict[str, dict[str, Any]] | None,
    relative: pathlib.PurePosixPath,
    metadata: os.stat_result,
    entry_type: str,
    path: pathlib.Path,
    *,
    allow_cleanup_mode: bool = False,
) -> dict[str, Any] | None:
    if expected_entries is None:
        return None
    relative_path = relative.as_posix()
    expected = expected_entries.get(relative_path)
    if expected is None or expected.get("type") != entry_type:
        raise DescriptorError(
            "transaction.undo_foreign",
            "Cleanup tree contains an unjournaled entry",
            path=str(path),
            relativePath=relative_path,
        )
    expected_identity = FileIdentity.from_payload(
        expected.get("identity"),
        field=f"cleanupTree.{relative_path}.identity",
    )
    _require_identity(FileIdentity.from_stat(metadata), expected_identity, path)
    actual_mode = stat.S_IMODE(metadata.st_mode)
    expected_mode = expected.get("mode")
    cleanup_mode = (
        expected_mode | stat.S_IWUSR | stat.S_IXUSR
        if isinstance(expected_mode, int) and entry_type == "directory"
        else expected_mode
    )
    if actual_mode != expected_mode and not (
        allow_cleanup_mode and actual_mode == cleanup_mode
    ):
        raise DescriptorError(
            "transaction.undo_foreign",
            "Cleanup tree entry metadata changed",
            path=str(path),
            relativePath=relative_path,
        )
    return expected


def _remove_tree_contents(
    directory: int,
    path: pathlib.Path,
    relative: pathlib.PurePosixPath,
    root: _RootHandle,
    expected_entries: dict[str, dict[str, Any]] | None,
    allow_cleanup_modes: bool,
) -> None:
    directory_metadata = os.fstat(directory)
    if allow_cleanup_modes:
        if directory_metadata.st_uid != os.getuid():
            raise DescriptorError(
                "transaction.owner_mismatch",
                "Verified cleanup directory belongs to another user",
                path=str(path),
                owner=directory_metadata.st_uid,
                expectedOwner=os.getuid(),
            )
        directory_mode = stat.S_IMODE(directory_metadata.st_mode)
        writable_mode = directory_mode | stat.S_IWUSR | stat.S_IXUSR
        if writable_mode != directory_mode:
            os.fchmod(directory, writable_mode)
            os.fsync(directory)
    with os.scandir(directory) as entries:
        names = sorted((entry.name for entry in entries), reverse=True)
    for name in names:
        entry_path = path / name
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
        _require_same_device(metadata, root.identity.device, entry_path)
        _require_supported_entry(metadata, entry_path)
        identity = FileIdentity.from_stat(metadata)
        entry_relative = relative / name
        if stat.S_ISREG(metadata.st_mode):
            expected = _require_cleanup_entry(
                expected_entries,
                entry_relative,
                metadata,
                "file",
                entry_path,
                allow_cleanup_mode=allow_cleanup_modes,
            )
            descriptor = os.open(name, _file_read_flags(), dir_fd=directory)
            try:
                _require_identity(FileIdentity.from_stat(os.fstat(descriptor)), identity, entry_path)
                if expected is not None and _sha256_descriptor(descriptor) != expected["sha256"]:
                    raise DescriptorError(
                        "transaction.undo_foreign",
                        "Cleanup tree file content changed",
                        path=str(entry_path),
                        relativePath=entry_relative.as_posix(),
                    )
                current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                _require_identity(FileIdentity.from_stat(current), identity, entry_path)
                _require_cleanup_entry(
                    expected_entries,
                    entry_relative,
                    current,
                    "file",
                    entry_path,
                    allow_cleanup_mode=allow_cleanup_modes,
                )
                os.unlink(name, dir_fd=directory)
            finally:
                os.close(descriptor)
        else:
            _require_cleanup_entry(
                expected_entries,
                entry_relative,
                metadata,
                "directory",
                entry_path,
                allow_cleanup_mode=allow_cleanup_modes,
            )
            child = os.open(name, _directory_flags(), dir_fd=directory)
            try:
                _require_identity(FileIdentity.from_stat(os.fstat(child)), identity, entry_path)
                _remove_tree_contents(
                    child,
                    entry_path,
                    entry_relative,
                    root,
                    expected_entries,
                    allow_cleanup_modes,
                )
            finally:
                os.close(child)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            _require_identity(FileIdentity.from_stat(current), identity, entry_path)
            _require_cleanup_entry(
                expected_entries,
                entry_relative,
                current,
                "directory",
                entry_path,
                allow_cleanup_mode=allow_cleanup_modes,
            )
            os.rmdir(name, dir_fd=directory)
    os.fsync(directory)


def ensure_private_directory(
    path: pathlib.Path,
    authority_root: pathlib.Path,
    *,
    owner_uid: int,
    mode: int = 0o700,
) -> None:
    target = _canonical_absolute(path)
    authority = _canonical_absolute(authority_root)
    if target != authority and not target.is_relative_to(authority):
        raise DescriptorError(
            "transaction.path_unsafe",
            "Private directory escapes its descriptor authority root",
            path=str(target),
            authorityRoot=str(authority),
        )
    descriptor = os.open(target.anchor, _directory_flags())
    current = pathlib.Path(target.anchor)
    authority_device: int | None = None
    try:
        for component in target.parts[1:]:
            current /= component
            created = False
            try:
                child = _open_directory_component(descriptor, component, current)
            except DescriptorError as error:
                if error.code != "transaction.parent_missing":
                    raise
                on_authority_path = (
                    current == authority
                    or current.is_relative_to(authority)
                    or authority.is_relative_to(current)
                )
                if not on_authority_path:
                    raise DescriptorError(
                        "transaction.parent_missing",
                        "Private directory authority parent is missing",
                        path=str(current),
                        authorityRoot=str(authority),
                    ) from error
                try:
                    os.mkdir(component, mode, dir_fd=descriptor)
                    os.fsync(descriptor)
                    created = True
                except FileExistsError:
                    pass
                child = _open_directory_component(descriptor, component, current)
            metadata = os.fstat(child)
            if current == authority:
                authority_device = metadata.st_dev
            elif authority_device is not None:
                _require_same_device(metadata, authority_device, current)
            if created or current == target:
                if metadata.st_uid != owner_uid:
                    os.close(child)
                    raise DescriptorError(
                        "transaction.owner_mismatch",
                        "Runtime-owned directory belongs to another user",
                        path=str(current),
                        owner=metadata.st_uid,
                        expectedOwner=owner_uid,
                    )
                actual_mode = stat.S_IMODE(metadata.st_mode)
                if actual_mode != mode:
                    os.close(child)
                    raise DescriptorError(
                        "transaction.mode_unsafe",
                        f"Runtime-owned directory must use mode {mode:04o}",
                        path=str(current),
                        mode=f"{actual_mode:04o}",
                    )
            os.close(descriptor)
            descriptor = child
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def descriptor_backend() -> str:
    _atomic_rename_function()
    if sys.platform == "darwin":
        return "renameatx_np(RENAME_EXCL)"
    if sys.platform.startswith("linux"):
        return "renameat2(RENAME_NOREPLACE)"
    raise DescriptorError(
        "transaction.descriptor_unsupported",
        "Descriptor rename backend is unavailable",
        platform=sys.platform,
    )


def probe_authority_root(path: pathlib.Path) -> dict[str, Any]:
    root = _canonical_absolute(path)
    backend = descriptor_backend()
    descriptor = os.open(root.anchor, _directory_flags())
    current = pathlib.Path(root.anchor)
    try:
        for component in root.parts[1:]:
            candidate = current / component
            try:
                child = _open_directory_component(descriptor, component, candidate)
            except DescriptorError as error:
                if error.code != "transaction.parent_missing":
                    raise
                return {
                    "path": str(root),
                    "exists": False,
                    "existingAncestor": str(current),
                    "missingFrom": str(candidate),
                    "backend": backend,
                }
            os.close(descriptor)
            descriptor = child
            current = candidate
        metadata = os.fstat(descriptor)
        return {
            "path": str(root),
            "exists": True,
            "identity": FileIdentity.from_stat(metadata).to_payload(),
            "backend": backend,
        }
    finally:
        os.close(descriptor)


_ATOMIC_RENAME: Any | None = None


def _atomic_rename_function() -> Any:
    global _ATOMIC_RENAME
    if _ATOMIC_RENAME is not None:
        return _ATOMIC_RENAME
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(library, "renameatx_np", None)
    elif sys.platform.startswith("linux"):
        function = getattr(library, "renameat2", None)
    else:
        function = None
    if function is None:
        raise DescriptorError(
            "transaction.descriptor_unsupported",
            "Atomic descriptor rename support is unavailable",
            platform=sys.platform,
        )
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    _ATOMIC_RENAME = function
    return function


def _rename_exclusive(source: BoundPath, target: BoundPath) -> None:
    source_parent = source._parent()
    target_parent = target._parent()
    if source.session is not target.session:
        raise DescriptorError(
            "transaction.path_unsafe",
            "Atomic descriptor rename requires one descriptor session",
            source=str(source.path),
            target=str(target.path),
        )
    source.verify_authority()
    target.verify_authority()
    source_identity = source.identity()
    target_identity = target.identity()
    if source_identity is None:
        raise DescriptorError(
            "transaction.path_identity_changed",
            "Atomic descriptor rename source is missing",
            source=str(source.path),
            target=str(target.path),
        )
    if target_identity is not None:
        raise DescriptorError(
            "transaction.target_changed",
            "Atomic descriptor publication target already exists",
            source=str(source.path),
            target=str(target.path),
        )
    if source_parent.identity.device != target_parent.identity.device:
        raise DescriptorError(
            "transaction.path_identity_changed",
            "Atomic descriptor rename crosses a device boundary",
            source=str(source.path),
            target=str(target.path),
        )
    flags = 0x00000004 if sys.platform == "darwin" else 0x1
    function = _atomic_rename_function()
    ctypes.set_errno(0)
    result = function(
        source_parent.descriptor,
        os.fsencode(source._leaf_name()),
        target_parent.descriptor,
        os.fsencode(target._leaf_name()),
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise DescriptorError(
                "transaction.target_changed",
                "Atomic descriptor publication target already exists",
                source=str(source.path),
                target=str(target.path),
            )
        if error_number in {
            errno.ENOSYS,
            errno.ENOTSUP,
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        }:
            raise DescriptorError(
                "transaction.descriptor_unsupported",
                "Filesystem does not support required atomic descriptor rename",
                source=str(source.path),
                target=str(target.path),
                errno=error_number,
            )
        if error_number == errno.EXDEV:
            raise DescriptorError(
                "transaction.path_identity_changed",
                "Atomic descriptor rename crosses a device boundary",
                source=str(source.path),
                target=str(target.path),
            )
        raise DescriptorError(
            "transaction.descriptor_operation_failed",
            "Atomic descriptor rename failed",
            source=str(source.path),
            target=str(target.path),
            errno=error_number,
            detail=os.strerror(error_number),
        )
    source.session.invalidate_below(source.path, target.path)
    os.fsync(source_parent.descriptor)
    if source_parent.identity != target_parent.identity:
        os.fsync(target_parent.descriptor)
    source.verify_authority()
    target.verify_authority()
    if source.exists():
        raise DescriptorError(
            "transaction.descriptor_operation_failed",
            "Exclusive descriptor rename left the source in place",
            source=str(source.path),
            target=str(target.path),
        )
    published_identity = target.identity()
    if published_identity != source_identity:
        raise DescriptorError(
            "transaction.path_identity_changed",
            "Exclusive descriptor rename published a different filesystem object",
            source=str(source.path),
            target=str(target.path),
            expected=source_identity.to_payload(),
            actual=(published_identity.to_payload() if published_identity is not None else None),
        )


def _rename_exchange(left: BoundPath, right: BoundPath) -> None:
    left_parent = left._parent()
    right_parent = right._parent()
    if left.session is not right.session:
        raise DescriptorError(
            "transaction.path_unsafe",
            "Atomic descriptor exchange requires one descriptor session",
            left=str(left.path),
            right=str(right.path),
        )
    left.verify_authority()
    right.verify_authority()
    left_identity = left.identity()
    right_identity = right.identity()
    if left_identity is None or right_identity is None:
        raise DescriptorError(
            "transaction.path_identity_changed",
            "Atomic descriptor exchange requires both paths to exist",
            left=str(left.path),
            right=str(right.path),
        )
    if left_parent.identity.device != right_parent.identity.device:
        raise DescriptorError(
            "transaction.path_identity_changed",
            "Atomic descriptor exchange crosses a device boundary",
            left=str(left.path),
            right=str(right.path),
        )
    function = _atomic_rename_function()
    ctypes.set_errno(0)
    result = function(
        left_parent.descriptor,
        os.fsencode(left._leaf_name()),
        right_parent.descriptor,
        os.fsencode(right._leaf_name()),
        0x00000002,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {
            errno.ENOENT,
            errno.EXDEV,
        }:
            raise DescriptorError(
                "transaction.path_identity_changed",
                "Atomic descriptor exchange paths changed or crossed a device boundary",
                left=str(left.path),
                right=str(right.path),
                errno=error_number,
            )
        if error_number in {
            errno.ENOSYS,
            errno.ENOTSUP,
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        }:
            raise DescriptorError(
                "transaction.descriptor_unsupported",
                "Filesystem does not support required atomic descriptor exchange",
                left=str(left.path),
                right=str(right.path),
                errno=error_number,
            )
        raise DescriptorError(
            "transaction.descriptor_operation_failed",
            "Atomic descriptor exchange failed",
            left=str(left.path),
            right=str(right.path),
            errno=error_number,
            detail=os.strerror(error_number),
        )
    left.session.invalidate_below(left.path, right.path)
    os.fsync(left_parent.descriptor)
    if left_parent.identity != right_parent.identity:
        os.fsync(right_parent.descriptor)
    left.verify_authority()
    right.verify_authority()
    exchanged_left = left.identity()
    exchanged_right = right.identity()
    if exchanged_left != right_identity or exchanged_right != left_identity:
        raise DescriptorError(
            "transaction.path_identity_changed",
            "Atomic descriptor exchange published different filesystem objects",
            left=str(left.path),
            right=str(right.path),
            expectedLeft=right_identity.to_payload(),
            actualLeft=(exchanged_left.to_payload() if exchanged_left is not None else None),
            expectedRight=left_identity.to_payload(),
            actualRight=(exchanged_right.to_payload() if exchanged_right is not None else None),
        )
