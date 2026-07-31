"""Durable descriptor-anchored filesystem transactions for runtime installation."""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import fcntl
import json
import os
import pathlib
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Literal, Sequence

import build_runtime_artifact as artifact_contract
import runtime_descriptor


SUPPORTED_ACTIONS = frozenset(
    {
        "assert_sha256",
        "assert_absent",
        "assert_absent_or_owned",
        "backup",
        "replace_file",
        "create_file",
        "replace_tree",
        "restore",
        "remove",
        "remove_tree",
        "retain",
        "retain_tree",
    }
)
MUTATING_ACTIONS = frozenset(
    {
        "backup",
        "replace_file",
        "create_file",
        "replace_tree",
        "restore",
        "remove",
        "remove_tree",
    }
)
SOURCE_ACTIONS = frozenset(
    {
        "assert_absent_or_owned",
        "replace_file",
        "create_file",
        "replace_tree",
        "restore",
        "remove",
        "remove_tree",
        "retain_tree",
    }
)
FILE_SOURCE_ACTIONS = frozenset({"replace_file", "create_file", "restore", "remove"})
TREE_SOURCE_ACTIONS = frozenset(
    {"assert_absent_or_owned", "replace_tree", "remove_tree", "retain_tree"}
)
TREE_MARKER_ACTIONS = frozenset(
    {"assert_absent_or_owned", "remove_tree", "retain_tree"}
)
INSTALL_ACTIONS = frozenset(
    {
        "assert_sha256",
        "assert_absent",
        "assert_absent_or_owned",
        "backup",
        "replace_file",
        "create_file",
        "replace_tree",
        "retain",
    }
)
UNINSTALL_ACTIONS = frozenset(
    {"restore", "remove", "remove_tree", "retain", "retain_tree"}
)
INSTALL_EFFECTS = frozenset({"replace_file", "create_file", "replace_tree"})
UNINSTALL_EFFECTS = frozenset({"restore", "remove", "remove_tree"})
PARENT_REQUIRED_ACTIONS = frozenset(
    {"backup", "replace_file", "create_file", "replace_tree", "restore"}
)
TRANSACTION_STATES = frozenset(
    {"prepared", "running", "rolling-back", "committed", "rolled-back", "failed"}
)
STEP_STATUSES = frozenset(
    {"pending", "applying", "applied", "rolling-back", "rolled-back", "rollback-failed"}
)
SEMANTIC_OPERATION_FIELDS = (
    "id",
    "resource",
    "action",
    "target",
    "atomic",
    "sourceSha256",
    "sourceTreeSha256",
    "marker",
    "sourceMarkerSha256",
    "backup",
    "expectedSha256",
    "ownershipPolicy",
    "retainOnUninstall",
)
OBSERVATION_OPERATION_FIELDS = frozenset(
    {
        "ready",
        "blockedReason",
        "sourceFiles",
        "actualSha256",
        "exists",
        "ownership",
        "targetMarkerSha256",
        "targetTreeSha256",
        "backupExists",
        "targetSha256",
        "backupSha256",
        "state",
        "qualifiedTargetTreeSha256",
        "ownershipEvidence",
        "ownershipFailure",
    }
)
KNOWN_OPERATION_FIELDS = (
    frozenset(SEMANTIC_OPERATION_FIELDS)
    | OBSERVATION_OPERATION_FIELDS
    | {"source"}
)
TREE_PHASES = frozenset(
    {
        "intent",
        "staged",
        "original-moved",
        "replacement-installed",
        "rollback-target-moved",
        "rollback-restored",
    }
)
FILE_PHASES = TREE_PHASES
REMOVAL_PHASES = frozenset({"intent", "moved", "restored-after-change"})
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TRANSACTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class TransactionError(Exception):
    """Stable machine-readable transaction failure."""

    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context


FailureInjector = Callable[[str, str], None]
TreeOwnershipValidator = Callable[[pathlib.Path, dict[str, Any]], dict[str, Any]]


_ACTIVE_DESCRIPTOR_SESSION: contextvars.ContextVar[
    runtime_descriptor.DescriptorSession | None
] = contextvars.ContextVar("runtime_transaction_descriptor_session", default=None)


def _transaction_descriptor_error(error: runtime_descriptor.DescriptorError) -> TransactionError:
    return TransactionError(error.code, error.message, **error.context)


def _descriptor_result(callback: Callable[[], Any]) -> Any:
    try:
        return callback()
    except runtime_descriptor.DescriptorError as error:
        raise _transaction_descriptor_error(error) from error


def _bound_path(path: pathlib.Path) -> runtime_descriptor.BoundPath | None:
    session = _ACTIVE_DESCRIPTOR_SESSION.get()
    if session is None:
        return None
    return _descriptor_result(lambda: session.bind(path))


@contextlib.contextmanager
def descriptor_scope(roots: Sequence[pathlib.Path]) -> Iterator[None]:
    try:
        with runtime_descriptor.DescriptorSession(roots) as session:
            token = _ACTIVE_DESCRIPTOR_SESSION.set(session)
            try:
                yield
            finally:
                _ACTIVE_DESCRIPTOR_SESSION.reset(token)
    except runtime_descriptor.DescriptorError as error:
        raise _transaction_descriptor_error(error) from error


@dataclass(frozen=True)
class TransactionReport:
    ok: bool
    state: str
    transaction_id: str
    plan_digest: str
    journal: pathlib.Path
    applied: tuple[str, ...]
    rolled_back: tuple[str, ...]
    failure: dict[str, Any] | None
    rollback_failures: tuple[dict[str, Any], ...]
    cleanup_failures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "ok": self.ok,
            "state": self.state,
            "transactionId": self.transaction_id,
            "planDigest": self.plan_digest,
            "journal": str(self.journal),
            "applied": list(self.applied),
            "rolledBack": list(self.rolled_back),
            "failure": self.failure,
            "rollbackFailures": list(self.rollback_failures),
            "cleanupFailures": list(self.cleanup_failures),
        }


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def path_lexists(path: pathlib.Path) -> bool:
    try:
        bound = _bound_path(path)
    except TransactionError as error:
        if error.code == "transaction.parent_missing":
            return False
        raise
    if bound is not None:
        return bool(_descriptor_result(bound.exists))
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def path_is_file(path: pathlib.Path) -> bool:
    try:
        bound = _bound_path(path)
    except TransactionError as error:
        if error.code == "transaction.parent_missing":
            return False
        raise
    if bound is not None:
        return bool(_descriptor_result(bound.is_file))
    return path.is_file()


def path_is_dir(path: pathlib.Path) -> bool:
    try:
        bound = _bound_path(path)
    except TransactionError as error:
        if error.code == "transaction.parent_missing":
            return False
        raise
    if bound is not None:
        return bool(_descriptor_result(bound.is_dir))
    return path.is_dir()


def sha256_file(path: pathlib.Path) -> str:
    bound = _bound_path(path)
    if bound is not None:
        return str(_descriptor_result(bound.sha256_file))
    return artifact_contract.sha256_file(path)


def load_json(path: pathlib.Path) -> Any:
    bound = _bound_path(path)
    if bound is None:
        try:
            return artifact_contract.load_json(path)
        except artifact_contract.ArtifactError as error:
            raise TransactionError(error.code, error.message, **error.context) from error
    try:
        return json.loads(
            _descriptor_result(bound.read_bytes),
            object_pairs_hook=artifact_contract.reject_duplicate_keys,
            parse_constant=artifact_contract.reject_json_constant,
        )
    except UnicodeDecodeError as error:
        raise TransactionError(
            "json.invalid",
            "JSON is not valid UTF-8",
            path=str(path),
        ) from error
    except json.JSONDecodeError as error:
        raise TransactionError(
            "json.invalid",
            "JSON parsing failed",
            path=str(path),
            line=error.lineno,
            column=error.colno,
            detail=error.msg,
        ) from error
    except artifact_contract.ArtifactError as error:
        raise TransactionError(error.code, error.message, **error.context) from error


def reject_symlink_components(path: pathlib.Path, *, include_leaf: bool = True) -> None:
    try:
        bound = _bound_path(path)
    except TransactionError as error:
        if error.code == "transaction.parent_missing":
            return
        raise
    if bound is not None:
        if include_leaf:
            metadata = _descriptor_result(bound.lstat)
            if metadata is not None and stat.S_ISLNK(metadata.st_mode):
                raise TransactionError("path.symlink", "Path contains a symlink", path=str(path))
        return
    try:
        artifact_contract.reject_symlink_components(path, include_leaf=include_leaf)
    except artifact_contract.ArtifactError as error:
        raise TransactionError(error.code, error.message, **error.context) from error


def fsync_directory(path: pathlib.Path) -> None:
    bound = _bound_path(path)
    if bound is not None:
        _descriptor_result(bound.fsync_directory)
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_tree(root: pathlib.Path) -> None:
    paths = list(root.rglob("*"))
    for path in paths:
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    directories = [path for path in paths if path.is_dir()]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        fsync_directory(directory)
    fsync_directory(root)


def remove_path(path: pathlib.Path) -> None:
    try:
        bound = _bound_path(path)
    except TransactionError as error:
        if error.code == "transaction.parent_missing":
            return
        raise
    if bound is not None:
        _descriptor_result(bound.remove)
        return
    if not path_lexists(path):
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def remove_path_durable(
    path: pathlib.Path,
    expected_identity: runtime_descriptor.FileIdentity | None = None,
    tree_entries: list[dict[str, Any]] | None = None,
) -> None:
    try:
        bound = _bound_path(path)
    except TransactionError as error:
        if error.code == "transaction.parent_missing":
            return
        raise
    if bound is not None:
        _descriptor_result(
            lambda: bound.remove(
                expected_identity,
                tree_entries,
                allow_cleanup_modes=tree_entries is not None,
            )
        )
        return
    if expected_identity is not None or tree_entries is not None:
        raise TransactionError(
            "transaction.descriptor_unsupported",
            "Verified cleanup requires an active descriptor session",
            path=str(path),
        )
    if not path_lexists(path):
        return
    parent = path.parent
    remove_path(path)
    if parent.is_dir():
        fsync_directory(parent)


def atomic_write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    payload = artifact_contract.canonical_json_bytes(value)
    temporary = path.with_name(f".{path.name}.tmp")
    target_bound = _bound_path(path)
    if target_bound is not None:
        temporary_bound = _bound_path(temporary)
        assert temporary_bound is not None
        if _descriptor_result(temporary_bound.exists):
            _descriptor_result(temporary_bound.remove)
        try:
            _descriptor_result(lambda: temporary_bound.write_bytes(payload))
            _descriptor_result(lambda: target_bound.replace_with(temporary_bound))
        except BaseException:
            with contextlib.suppress(TransactionError):
                _descriptor_result(temporary_bound.remove)
            raise
        return
    if path_lexists(temporary):
        reject_symlink_components(temporary, include_leaf=False)
        remove_path_durable(temporary)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise


def reject_tree_symlinks(root: pathlib.Path) -> None:
    bound = _bound_path(root)
    if bound is not None:
        _descriptor_result(bound.tree_sha256)
        return
    reject_symlink_components(root)
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise TransactionError("path.symlink", "Tree contains a symlink", path=str(path))


def tree_sha256(root: pathlib.Path) -> str:
    bound = _bound_path(root)
    if bound is not None:
        return str(_descriptor_result(bound.tree_sha256))
    try:
        return artifact_contract.canonical_tree_sha256(root)
    except artifact_contract.ArtifactError as error:
        code = {
            "tree.missing": "transaction.source_missing",
            "tree.unsupported": "transaction.tree_unsupported",
        }.get(error.code, error.code)
        raise TransactionError(code, error.message, **error.context) from error


def copy_file_atomic(
    source: pathlib.Path,
    target: pathlib.Path,
    staging: pathlib.Path,
) -> None:
    if not path_is_dir(target.parent):
        raise TransactionError(
            "transaction.parent_missing",
            "Atomic file target parent is missing",
            path=str(target.parent),
        )
    if staging.parent != target.parent:
        raise TransactionError(
            "transaction.plan_invalid",
            "Atomic file staging must share the target parent",
            target=str(target),
            staging=str(staging),
        )
    if path_lexists(staging):
        raise TransactionError(
            "transaction.temporary_exists",
            "Atomic file staging path already exists",
            path=str(staging),
        )
    source_bound = _bound_path(source)
    if source_bound is not None:
        target_bound = _bound_path(target)
        staging_bound = _bound_path(staging)
        assert target_bound is not None and staging_bound is not None
        source_digest = sha256_file(source)
        try:
            _descriptor_result(lambda: staging_bound.copy_file_from(source_bound))
            _descriptor_result(lambda: target_bound.rename_exclusive_from(staging_bound))
        except BaseException:
            with contextlib.suppress(TransactionError):
                if path_is_file(staging) and sha256_file(staging) == source_digest:
                    remove_path_durable(staging)
            raise
        return
    try:
        shutil.copy2(source, staging)
        with staging.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(staging, target)
        fsync_directory(target.parent)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            staging.unlink()
        raise


def copy_file_staged(source: pathlib.Path, staging: pathlib.Path) -> None:
    source_bound = _bound_path(source)
    if source_bound is not None:
        staging_bound = _bound_path(staging)
        assert staging_bound is not None
        _descriptor_result(lambda: staging_bound.copy_file_from(source_bound))
        return
    shutil.copy2(source, staging)
    with staging.open("rb") as stream:
        os.fsync(stream.fileno())
    fsync_directory(staging.parent)


def copy_tree_staged(source: pathlib.Path, staging: pathlib.Path) -> None:
    source_bound = _bound_path(source)
    if source_bound is not None:
        staging_bound = _bound_path(staging)
        assert staging_bound is not None
        _descriptor_result(lambda: staging_bound.copy_tree_from(source_bound))
        return
    shutil.copytree(source, staging, copy_function=shutil.copy2)
    reject_tree_symlinks(staging)
    fsync_tree(staging)


def rename_exclusive(source: pathlib.Path, target: pathlib.Path) -> None:
    source_bound = _bound_path(source)
    if source_bound is not None:
        target_bound = _bound_path(target)
        assert target_bound is not None
        _descriptor_result(lambda: target_bound.rename_exclusive_from(source_bound))
        return
    if path_lexists(target):
        raise TransactionError(
            "transaction.target_changed",
            "Exclusive rename target already exists",
            path=str(target),
        )
    os.rename(source, target)
    fsync_directory(target.parent)


def rename_exchange(left: pathlib.Path, right: pathlib.Path) -> None:
    left_bound = _bound_path(left)
    if left_bound is None:
        raise TransactionError(
            "transaction.descriptor_required",
            "Atomic tree exchange requires an active descriptor session",
            left=str(left),
            right=str(right),
        )
    right_bound = _bound_path(right)
    assert right_bound is not None
    _descriptor_result(lambda: left_bound.exchange_with(right_bound))


def make_directory(path: pathlib.Path, mode: int) -> None:
    bound = _bound_path(path)
    if bound is not None:
        _descriptor_result(lambda: bound.make_directory(mode))
        return
    path.mkdir(mode=mode, parents=False, exist_ok=False)
    fsync_directory(path.parent)


def parent_identity_payload(
    path: pathlib.Path,
    *,
    allow_missing: bool = False,
) -> dict[str, int] | None:
    try:
        bound = _bound_path(path)
    except TransactionError as error:
        if allow_missing and error.code == "transaction.parent_missing":
            return None
        raise
    if bound is None:
        metadata = path.parent.stat()
        return runtime_descriptor.FileIdentity.from_stat(metadata).to_payload()
    return dict(_descriptor_result(bound.parent_identity_payload))


def validate_parent_identity_payload(
    value: Any,
    *,
    field: str,
    allow_missing: bool = False,
) -> None:
    if value is None and allow_missing:
        return
    _descriptor_result(
        lambda: runtime_descriptor.FileIdentity.from_payload(value, field=field)
    )


def path_identity(path: pathlib.Path) -> runtime_descriptor.FileIdentity | None:
    try:
        bound = _bound_path(path)
    except TransactionError as error:
        if error.code == "transaction.parent_missing":
            return None
        raise
    if bound is not None:
        return _descriptor_result(bound.identity)
    try:
        return runtime_descriptor.FileIdentity.from_stat(path.lstat())
    except FileNotFoundError:
        return None


def require_path_identity(
    path: pathlib.Path,
    expected: runtime_descriptor.FileIdentity,
) -> None:
    actual = path_identity(path)
    if actual != expected:
        raise TransactionError(
            "transaction.path_identity_changed",
            "Cleanup path now resolves to a different filesystem object",
            path=str(path),
            expected=expected.to_payload(),
            actual=actual.to_payload() if actual is not None else None,
        )


def tree_cleanup_manifest(path: pathlib.Path) -> list[dict[str, Any]]:
    bound = _bound_path(path)
    if bound is None:
        raise TransactionError(
            "transaction.descriptor_unsupported",
            "Cleanup tree inspection requires an active descriptor session",
            path=str(path),
        )
    return list(_descriptor_result(bound.tree_cleanup_manifest))


def cleanup_tree_record_matches(
    expected: dict[str, Any] | None,
    actual: dict[str, Any],
) -> bool:
    if expected == actual:
        return True
    if expected is None or expected.get("type") != "directory" or actual.get("type") != (
        "directory"
    ):
        return False
    expected_mode = expected.get("mode")
    if not isinstance(expected_mode, int):
        return False
    normalized = dict(actual)
    if normalized.get("mode") != expected_mode | stat.S_IWUSR | stat.S_IXUSR:
        return False
    normalized["mode"] = expected_mode
    return normalized == expected


def validate_cleanup_tree_entries(
    value: Any,
    *,
    field: str,
) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise TransactionError(
            "transaction.journal_invalid",
            "Journal cleanup tree manifest is invalid",
            field=field,
        )
    records: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(value):
        record_field = f"{field}[{index}]"
        if not isinstance(record, dict):
            raise TransactionError(
                "transaction.journal_invalid",
                "Journal cleanup tree record is invalid",
                field=record_field,
            )
        entry_type = record.get("type")
        expected_keys = (
            {"path", "type", "mode", "identity", "sha256"}
            if entry_type == "file"
            else {"path", "type", "mode", "identity"}
        )
        raw_path = record.get("path")
        mode = record.get("mode")
        if (
            entry_type not in {"file", "directory"}
            or set(record) != expected_keys
            or not isinstance(raw_path, str)
            or raw_path in records
            or type(mode) is not int
            or not 0 <= mode <= 0o7777
        ):
            raise TransactionError(
                "transaction.journal_invalid",
                "Journal cleanup tree record is invalid",
                field=record_field,
            )
        if raw_path == ".":
            relative = pathlib.PurePosixPath()
        else:
            relative = pathlib.PurePosixPath(raw_path)
            if (
                relative.is_absolute()
                or relative.as_posix() != raw_path
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise TransactionError(
                    "transaction.journal_invalid",
                    "Journal cleanup tree path is invalid",
                    field=f"{record_field}.path",
                )
        if entry_type == "file" and (
            not isinstance(record.get("sha256"), str)
            or SHA256_PATTERN.fullmatch(record["sha256"]) is None
        ):
            raise TransactionError(
                "transaction.journal_invalid",
                "Journal cleanup tree file digest is invalid",
                field=f"{record_field}.sha256",
            )
        identity = _descriptor_result(
            lambda: runtime_descriptor.FileIdentity.from_payload(
                record.get("identity"),
                field=f"{record_field}.identity",
            )
        )
        expected_file_type = stat.S_IFREG if entry_type == "file" else stat.S_IFDIR
        if identity.file_type != expected_file_type:
            raise TransactionError(
                "transaction.journal_invalid",
                "Journal cleanup tree identity type is invalid",
                field=f"{record_field}.identity",
            )
        records[raw_path] = record
    root = records.get(".")
    if root is None or root["type"] != "directory" or value[0]["path"] != ".":
        raise TransactionError(
            "transaction.journal_invalid",
            "Journal cleanup tree root is invalid",
            field=field,
        )
    for raw_path, record in records.items():
        if raw_path == ".":
            continue
        parent = pathlib.PurePosixPath(raw_path).parent.as_posix()
        parent_record = records.get(parent)
        if parent_record is None or parent_record["type"] != "directory":
            raise TransactionError(
                "transaction.journal_invalid",
                "Journal cleanup tree parent is invalid",
                field=f"{field}.{raw_path}",
            )
    return value


def require_parent_identity(
    path: pathlib.Path,
    value: Any,
    *,
    field: str,
    allow_missing: bool = False,
) -> None:
    if value is None and allow_missing:
        return
    try:
        bound = _bound_path(path)
    except TransactionError as error:
        if error.code == "transaction.parent_missing":
            raise TransactionError(
                "transaction.path_identity_changed",
                "Journaled descriptor parent is missing",
                path=str(path.parent),
                field=field,
            ) from error
        raise
    if bound is None:
        expected = _descriptor_result(
            lambda: runtime_descriptor.FileIdentity.from_payload(value, field=field)
        )
        actual = runtime_descriptor.FileIdentity.from_stat(path.parent.stat())
        if actual != expected:
            raise TransactionError(
                "transaction.path_identity_changed",
                "Journaled descriptor parent identity changed",
                path=str(path.parent),
                field=field,
            )
        return
    _descriptor_result(lambda: bound.require_parent_identity(value, field=field))


def error_payload(error: BaseException) -> dict[str, Any]:
    if isinstance(error, TransactionError):
        return {"code": error.code, "message": error.message, "context": error.context}
    return {
        "code": "transaction.operation_failed",
        "message": str(error) or type(error).__name__,
        "context": {"type": type(error).__name__},
    }


def semantic_operation(operation: dict[str, Any]) -> dict[str, Any]:
    if operation.get("action") == "retain":
        return {
            key: operation[key]
            for key in ("id", "resource", "action")
            if key in operation
        }
    return {key: operation[key] for key in SEMANTIC_OPERATION_FIELDS if key in operation}


def validate_plan_identity(
    value: dict[str, str] | None,
) -> dict[str, str] | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"profileId", "profileSha256"}
        or not isinstance(value.get("profileId"), str)
        or PROFILE_ID_PATTERN.fullmatch(value["profileId"]) is None
        or not isinstance(value.get("profileSha256"), str)
        or SHA256_PATTERN.fullmatch(value["profileSha256"]) is None
    ):
        raise TransactionError(
            "transaction.plan_invalid",
            "Transaction plan identity is invalid",
        )
    return dict(value)


def semantic_plan_digest(
    operations: Sequence[dict[str, Any]],
    plan_identity: dict[str, str] | None = None,
) -> str:
    identity = validate_plan_identity(plan_identity)
    plan = [semantic_operation(operation) for operation in operations]
    digest_input: Any = plan
    if identity is not None:
        digest_input = {
            "operations": plan,
            "planIdentity": identity,
        }
    return artifact_contract.sha256_bytes(
        artifact_contract.canonical_json_bytes(digest_input)
    )


class TransactionExecutor:
    def __init__(
        self,
        *,
        kind: Literal["install", "uninstall"],
        operations: Sequence[dict[str, Any]],
        artifact_root: pathlib.Path,
        journal_path: pathlib.Path,
        transaction_root: pathlib.Path,
        allowed_roots: Sequence[pathlib.Path],
        failure_injector: FailureInjector | None = None,
        tree_ownership_validator: TreeOwnershipValidator | None = None,
        plan_identity: dict[str, str] | None = None,
    ) -> None:
        self.kind = kind
        self.artifact_root = self._absolute_input_path(artifact_root)
        self.journal_path = self._absolute_input_path(journal_path)
        self.transaction_root = self._absolute_input_path(transaction_root)
        self.allowed_roots = [self._absolute_input_path(root) for root in allowed_roots]
        self.operations = []
        for operation in operations:
            if not isinstance(operation, dict):
                raise TransactionError(
                    "transaction.plan_invalid",
                    "Transaction operations must be JSON objects",
                )
            self.operations.append(dict(operation))
        self.failure_injector = failure_injector
        self.tree_ownership_validator = tree_ownership_validator
        self.plan_identity = validate_plan_identity(plan_identity)
        self.plan_digest = semantic_plan_digest(self.operations, self.plan_identity)
        self.lock_path = self.journal_path.with_suffix(self.journal_path.suffix + ".lock")

    @staticmethod
    def _absolute_input_path(path: pathlib.Path) -> pathlib.Path:
        return pathlib.Path(os.path.abspath(pathlib.Path(path).expanduser()))

    @property
    def undo_root(self) -> pathlib.Path:
        return self.journal_path.with_name(f"{self.journal_path.name}.undo")

    @contextlib.contextmanager
    def _descriptors(self) -> Iterator[None]:
        roots = [self.artifact_root, self.transaction_root, *self.allowed_roots]
        with descriptor_scope(roots):
            yield

    def _inject(self, step_id: str, phase: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(step_id, phase)

    def _ensure_under(
        self,
        path: pathlib.Path,
        roots: Sequence[pathlib.Path],
        operation: str,
        *,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        try:
            artifact_contract.ensure_target_allowed(path, list(roots), operation)
        except artifact_contract.ArtifactError as error:
            if error.code == "path.symlink":
                raise TransactionError(error.code, error.message, **error.context) from error
            raise TransactionError(
                code or error.code,
                message or error.message,
                **error.context,
            ) from error

    def _ensure_allowed(self, path: pathlib.Path, operation: str) -> None:
        self._ensure_under(path, self.allowed_roots, operation)

    def _ensure_transaction_path(self, path: pathlib.Path, operation: str) -> None:
        self._ensure_under(
            path,
            [self.transaction_root],
            operation,
            code="transaction.path_unsafe",
            message="Transaction metadata escapes the transaction root",
        )

    def _absolute_operation_path(
        self,
        operation: dict[str, Any],
        key: str,
    ) -> pathlib.Path:
        raw = operation.get(key)
        if not isinstance(raw, str) or not raw:
            raise TransactionError(
                "transaction.plan_invalid",
                f"Operation is missing a {key} path",
                operation=operation.get("id"),
            )
        path = pathlib.Path(raw)
        if not path.is_absolute():
            raise TransactionError(
                "transaction.plan_invalid",
                f"{key.title()} path must be absolute",
                operation=operation.get("id"),
                path=raw,
            )
        canonical = pathlib.Path(os.path.abspath(path))
        if path != canonical:
            raise TransactionError(
                "transaction.plan_invalid",
                f"{key.title()} path must be canonical",
                operation=operation.get("id"),
                path=raw,
            )
        return path

    def _source_path(self, operation: dict[str, Any]) -> pathlib.Path:
        path = self._absolute_operation_path(operation, "source")
        self._ensure_under(
            path,
            [self.artifact_root],
            operation["id"],
            code="transaction.source_unsafe",
            message="Operation source escapes the artifact root",
        )
        return path

    def _mutable_path(self, operation: dict[str, Any], key: str) -> pathlib.Path:
        path = self._absolute_operation_path(operation, key)
        self._ensure_allowed(path, operation["id"])
        return path

    def _marker_relative(self, operation: dict[str, Any]) -> pathlib.PurePosixPath:
        try:
            return artifact_contract.safe_relative_path(
                operation.get("marker"),
                f"transaction.{operation['id']}.marker",
            )
        except artifact_contract.ArtifactError as error:
            raise TransactionError(error.code, error.message, **error.context) from error

    def _required_sha256(self, operation: dict[str, Any], key: str) -> str:
        value = operation.get(key)
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            raise TransactionError(
                "transaction.plan_invalid",
                f"Operation {key} must be a lowercase SHA-256 digest",
                operation=operation["id"],
                field=key,
            )
        return value

    def _validate_root(self, root: pathlib.Path, label: str) -> None:
        reject_symlink_components(root)
        if root == pathlib.Path(root.anchor):
            raise TransactionError(
                "path.unsafe",
                "Filesystem roots cannot be transaction trust roots",
                root=label,
                path=str(root),
            )
        if not root.is_dir():
            raise TransactionError(
                "transaction.root_missing",
                "Transaction trust root must already exist",
                root=label,
                path=str(root),
            )

    def _validate_operation_schema(self, operation: dict[str, Any]) -> None:
        action = operation["action"]
        unknown_fields = sorted(set(operation) - KNOWN_OPERATION_FIELDS)
        if unknown_fields:
            raise TransactionError(
                "transaction.plan_invalid",
                "Operation contains unknown fields",
                operation=operation["id"],
                fields=unknown_fields,
            )
        self._mutable_path(operation, "target")
        resource = operation.get("resource")
        if not isinstance(resource, str) or ID_PATTERN.fullmatch(resource) is None:
            raise TransactionError(
                "transaction.plan_invalid",
                "Operation resource has an invalid format",
                operation=operation["id"],
                resource=resource,
            )
        if "atomic" in operation and not isinstance(operation["atomic"], bool):
            raise TransactionError(
                "transaction.plan_invalid",
                "Operation atomic flag must be a boolean",
                operation=operation["id"],
            )
        ownership_policy = operation.get("ownershipPolicy")
        if ownership_policy is not None and (
            action != "assert_absent_or_owned"
            or ownership_policy != "developer-id-bundle"
        ):
            raise TransactionError(
                "transaction.plan_invalid",
                "Operation ownership policy is invalid for this action",
                operation=operation["id"],
            )
        if "retainOnUninstall" in operation and (
            action != "replace_tree" or operation["retainOnUninstall"] is not True
        ):
            raise TransactionError(
                "transaction.plan_invalid",
                "Operation retained-uninstall policy is invalid for this action",
                operation=operation["id"],
            )
        if "qualifiedTargetTreeSha256" in operation:
            if ownership_policy != "developer-id-bundle":
                raise TransactionError(
                    "transaction.plan_invalid",
                    "Qualified target digest requires a Developer ID ownership policy",
                    operation=operation["id"],
                )
            self._required_sha256(operation, "qualifiedTargetTreeSha256")
        if "ownershipEvidence" in operation and (
            ownership_policy != "developer-id-bundle"
            or not isinstance(operation["ownershipEvidence"], dict)
        ):
            raise TransactionError(
                "transaction.plan_invalid",
                "Ownership evidence requires a Developer ID ownership policy",
                operation=operation["id"],
            )
        if action in SOURCE_ACTIONS:
            self._source_path(operation)
        elif "source" in operation or "sourceSha256" in operation:
            raise TransactionError(
                "transaction.plan_invalid",
                "Operation action does not accept a source",
                operation=operation["id"],
            )
        if action in FILE_SOURCE_ACTIONS:
            self._required_sha256(operation, "sourceSha256")
        if action in TREE_SOURCE_ACTIONS:
            self._required_sha256(operation, "sourceTreeSha256")
        if action in TREE_MARKER_ACTIONS:
            self._marker_relative(operation)
            self._required_sha256(operation, "sourceMarkerSha256")
        elif "marker" in operation or "sourceMarkerSha256" in operation:
            raise TransactionError(
                "transaction.plan_invalid",
                "Operation action does not accept a tree marker",
                operation=operation["id"],
            )
        if action in {"backup", "restore"}:
            self._mutable_path(operation, "backup")
        elif "backup" in operation:
            raise TransactionError(
                "transaction.plan_invalid",
                "Operation action does not accept a backup path",
                operation=operation["id"],
            )
        if action in {"assert_sha256", "restore"}:
            self._required_sha256(operation, "expectedSha256")
        elif "expectedSha256" in operation:
            raise TransactionError(
                "transaction.plan_invalid",
                "Operation action does not accept an expected digest",
                operation=operation["id"],
            )

    def _matching_install_guard(
        self,
        operation: dict[str, Any],
        guard_action: str,
    ) -> dict[str, Any]:
        operation_index = next(
            index
            for index, candidate in enumerate(self.operations)
            if candidate["id"] == operation["id"]
        )
        matches = [
            candidate
            for index, candidate in enumerate(self.operations)
            if index < operation_index
            and candidate.get("resource") == operation["resource"]
            and candidate.get("action") == guard_action
            and candidate.get("target") == operation["target"]
        ]
        if guard_action == "assert_absent_or_owned":
            matches = [
                candidate
                for candidate in matches
                if candidate.get("source") == operation.get("source")
            ]
        if len(matches) != 1:
            raise TransactionError(
                "transaction.plan_invalid",
                "Install mutation requires one preceding matching guard",
                operation=operation["id"],
                guard=guard_action,
                resource=operation["resource"],
            )
        return matches[0]

    def _tree_contract(self, operation: dict[str, Any]) -> dict[str, Any]:
        if operation["action"] == "replace_tree":
            return self._matching_install_guard(operation, "assert_absent_or_owned")
        return operation

    def _validate_plan_relationships(self) -> None:
        allowed_actions = INSTALL_ACTIONS if self.kind == "install" else UNINSTALL_ACTIONS
        effect_resources: set[str] = set()
        for operation in self.operations:
            action = operation["action"]
            if action not in allowed_actions:
                raise TransactionError(
                    "transaction.plan_invalid",
                    "Operation action does not belong to the transaction kind",
                    operation=operation["id"],
                    action=action,
                    kind=self.kind,
                )
            if self.kind == "install" and action in INSTALL_EFFECTS:
                resource = operation["resource"]
                if resource in effect_resources:
                    raise TransactionError(
                        "transaction.plan_invalid",
                        "Install resource has multiple mutating operations",
                        resource=resource,
                    )
                effect_resources.add(resource)
                if operation.get("atomic") is not True:
                    raise TransactionError(
                        "transaction.plan_invalid",
                        "Install mutation must be atomic",
                        operation=operation["id"],
                    )
                guard_action = {
                    "replace_file": "assert_sha256",
                    "create_file": "assert_absent",
                    "replace_tree": "assert_absent_or_owned",
                }[action]
                self._matching_install_guard(operation, guard_action)
                if action == "replace_file":
                    operation_index = self.operations.index(operation)
                    backups = [
                        candidate
                        for index, candidate in enumerate(self.operations)
                        if index < operation_index
                        and candidate.get("resource") == resource
                        and candidate.get("action") == "backup"
                        and candidate.get("target") == operation["target"]
                    ]
                    if len(backups) != 1:
                        raise TransactionError(
                            "transaction.plan_invalid",
                            "File replacement requires one preceding matching backup",
                            operation=operation["id"],
                            resource=resource,
                        )
            if self.kind == "uninstall" and action in UNINSTALL_EFFECTS:
                resource = operation["resource"]
                if resource in effect_resources:
                    raise TransactionError(
                        "transaction.plan_invalid",
                        "Uninstall resource has multiple mutating operations",
                        resource=resource,
                    )
                effect_resources.add(resource)
            if (
                self.kind == "uninstall"
                and action == "restore"
                and operation.get("atomic") is not True
            ):
                raise TransactionError(
                    "transaction.plan_invalid",
                    "Restoration mutation must be atomic",
                    operation=operation["id"],
                )

    def _validate_structure(self) -> None:
        if self.kind not in {"install", "uninstall"}:
            raise TransactionError(
                "transaction.kind_invalid",
                "Transaction kind is unsupported",
                kind=self.kind,
            )
        if not self.operations:
            raise TransactionError("transaction.plan_invalid", "Transaction plan is empty")
        if not self.allowed_roots:
            raise TransactionError(
                "transaction.plan_invalid",
                "At least one mutable target root is required",
            )
        self._validate_root(self.artifact_root, "artifact")
        self._validate_root(self.transaction_root, "transaction")
        for index, root in enumerate(self.allowed_roots):
            self._validate_root(root, f"allowed[{index}]")
        self._ensure_transaction_path(self.journal_path, "transaction_journal")
        self._ensure_transaction_path(self.lock_path, "transaction_lock")
        self._ensure_transaction_path(self.undo_root, "transaction_undo")
        if not self.journal_path.parent.is_dir():
            raise TransactionError(
                "transaction.journal_parent_missing",
                "Transaction journal parent must already exist",
                path=str(self.journal_path.parent),
            )
        seen: set[str] = set()
        for operation in self.operations:
            identifier = operation.get("id")
            action = operation.get("action")
            if not isinstance(identifier, str) or ID_PATTERN.fullmatch(identifier) is None:
                raise TransactionError(
                    "transaction.plan_invalid",
                    "Operation id has an invalid format",
                    operation=identifier,
                )
            if identifier in seen:
                raise TransactionError(
                    "transaction.plan_invalid",
                    "Transaction plan contains a duplicate operation id",
                    operation=identifier,
                )
            seen.add(identifier)
            if action not in SUPPORTED_ACTIONS:
                raise TransactionError(
                    "transaction.action_unsupported",
                    "Transaction action is unsupported",
                    operation=identifier,
                    action=action,
                )
            self._validate_operation_schema(operation)
        self._validate_plan_relationships()

    def validate(self) -> None:
        self._validate_structure()
        with self._descriptors():
            self._preflight()

    def _verify_source_file(self, operation: dict[str, Any]) -> pathlib.Path:
        source = self._source_path(operation)
        if not path_is_file(source):
            raise TransactionError(
                "transaction.source_missing",
                "Operation source file is missing",
                operation=operation["id"],
                path=str(source),
            )
        expected = self._required_sha256(operation, "sourceSha256")
        actual = sha256_file(source)
        if actual != expected:
            raise TransactionError(
                "transaction.source_mismatch",
                "Operation source hash does not match the plan",
                operation=operation["id"],
                expected=expected,
                actual=actual,
            )
        return source

    def _verify_source_tree(self, operation: dict[str, Any]) -> pathlib.Path:
        source = self._source_path(operation)
        if not path_is_dir(source):
            raise TransactionError(
                "transaction.source_missing",
                "Operation source tree is missing",
                operation=operation["id"],
                path=str(source),
            )
        reject_tree_symlinks(source)
        expected_tree = self._required_sha256(operation, "sourceTreeSha256")
        actual_tree = tree_sha256(source)
        if actual_tree != expected_tree:
            raise TransactionError(
                "transaction.source_mismatch",
                "Source tree content does not match the plan",
                operation=operation["id"],
                expected=expected_tree,
                actual=actual_tree,
            )
        contract = self._tree_contract(operation)
        marker = self._marker_relative(contract)
        source_marker = source.joinpath(*marker.parts)
        if not path_is_file(source_marker):
            raise TransactionError(
                "transaction.source_missing",
                "Source tree ownership marker is missing",
                operation=operation["id"],
                path=str(source_marker),
            )
        expected = self._required_sha256(contract, "sourceMarkerSha256")
        actual = sha256_file(source_marker)
        if actual != expected:
            raise TransactionError(
                "transaction.source_mismatch",
                "Source ownership marker does not match the plan",
                operation=operation["id"],
                expected=expected,
                actual=actual,
            )
        return source

    def _validate_owned_tree(
        self,
        operation: dict[str, Any],
        target: pathlib.Path,
    ) -> str | None:
        if not path_lexists(target):
            return None
        if not path_is_dir(target):
            raise TransactionError(
                "transaction.target_foreign",
                "Tree target is not an owned directory",
                operation=operation["id"],
                path=str(target),
            )
        reject_tree_symlinks(target)
        contract = self._tree_contract(operation)
        marker = self._marker_relative(contract)
        target_marker = target.joinpath(*marker.parts)
        expected = self._required_sha256(contract, "sourceMarkerSha256")
        if not path_is_file(target_marker) or sha256_file(target_marker) != expected:
            raise TransactionError(
                "transaction.target_foreign",
                "Tree target ownership marker does not match",
                operation=operation["id"],
                path=str(target),
            )
        expected_tree = self._required_sha256(contract, "sourceTreeSha256")
        actual_tree = tree_sha256(target)
        if actual_tree == expected_tree:
            return actual_tree
        if contract.get("ownershipPolicy") != "developer-id-bundle":
            raise TransactionError(
                "transaction.target_foreign",
                "Tree target content does not match the artifact-owned tree",
                operation=operation["id"],
                path=str(target),
                expected=expected_tree,
                actual=actual_tree,
            )
        qualified_tree = self._required_sha256(contract, "qualifiedTargetTreeSha256")
        if actual_tree != qualified_tree:
            raise TransactionError(
                "transaction.target_changed",
                "Tree target changed after Developer ID lifecycle qualification",
                operation=operation["id"],
                path=str(target),
                expected=qualified_tree,
                actual=actual_tree,
            )
        if self.tree_ownership_validator is None:
            raise TransactionError(
                "transaction.target_foreign",
                "Developer ID tree ownership has not been independently validated",
                operation=operation["id"],
                path=str(target),
            )
        try:
            self.tree_ownership_validator(target, contract)
        except artifact_contract.ArtifactError as error:
            raise TransactionError(
                "transaction.target_foreign",
                "Developer ID tree ownership validation failed",
                operation=operation["id"],
                path=str(target),
                causeCode=error.code,
                causeMessage=error.message,
            ) from error
        except TransactionError:
            raise
        except Exception as error:
            raise TransactionError(
                "transaction.target_foreign",
                "Developer ID tree ownership validation failed",
                operation=operation["id"],
                path=str(target),
                causeType=type(error).__name__,
            ) from error
        confirmed_tree = tree_sha256(target)
        if confirmed_tree != actual_tree:
            raise TransactionError(
                "transaction.target_changed",
                "Tree target changed during Developer ID lifecycle qualification",
                operation=operation["id"],
                path=str(target),
                expected=actual_tree,
                actual=confirmed_tree,
            )
        return actual_tree

    def _preflight_operation(self, operation: dict[str, Any]) -> None:
        action = operation["action"]
        target = self._mutable_path(operation, "target")
        if action in PARENT_REQUIRED_ACTIONS and not path_is_dir(target.parent):
            raise TransactionError(
                "transaction.parent_missing",
                "Mutation target parent is missing",
                operation=operation["id"],
                path=str(target.parent),
            )
        if action == "assert_sha256":
            expected = self._required_sha256(operation, "expectedSha256")
            if not path_is_file(target) or sha256_file(target) != expected:
                raise TransactionError(
                    "transaction.assertion_failed",
                    "Target hash assertion failed",
                    operation=operation["id"],
                    path=str(target),
                    expected=expected,
                )
        elif action == "assert_absent":
            if path_lexists(target):
                raise TransactionError(
                    "transaction.assertion_failed",
                    "Target must be absent",
                    operation=operation["id"],
                    path=str(target),
                )
        elif action == "assert_absent_or_owned":
            self._verify_source_tree(operation)
            self._validate_owned_tree(operation, target)
        elif action == "backup":
            backup = self._mutable_path(operation, "backup")
            if not path_is_dir(backup.parent):
                raise TransactionError(
                    "transaction.parent_missing",
                    "Backup target parent is missing",
                    operation=operation["id"],
                    path=str(backup.parent),
                )
            if not path_is_file(target):
                raise TransactionError(
                    "transaction.target_missing",
                    "Backup source is missing",
                    operation=operation["id"],
                    path=str(target),
                )
            if path_lexists(backup) and (
                not path_is_file(backup)
                or sha256_file(backup) != sha256_file(target)
            ):
                raise TransactionError(
                    "transaction.backup_mismatch",
                    "Existing backup does not match its target",
                    operation=operation["id"],
                    path=str(backup),
                )
        elif action == "replace_file":
            self._verify_source_file(operation)
            guard = self._matching_install_guard(operation, "assert_sha256")
            expected = self._required_sha256(guard, "expectedSha256")
            if not path_is_file(target) or sha256_file(target) != expected:
                raise TransactionError(
                    "transaction.target_foreign",
                    "Replacement target no longer matches its stock guard",
                    operation=operation["id"],
                    path=str(target),
                    expected=expected,
                )
        elif action == "create_file":
            self._verify_source_file(operation)
            if path_lexists(target):
                raise TransactionError(
                    "transaction.target_exists",
                    "Create target already exists",
                    operation=operation["id"],
                    path=str(target),
                )
        elif action == "replace_tree":
            self._verify_source_tree(operation)
            self._validate_owned_tree(operation, target)
        elif action == "restore":
            source = self._verify_source_file(operation)
            backup = self._mutable_path(operation, "backup")
            expected = self._required_sha256(operation, "expectedSha256")
            target_hash = sha256_file(target) if path_is_file(target) else None
            if target_hash == expected:
                return
            if target_hash != sha256_file(source):
                raise TransactionError(
                    "transaction.target_foreign",
                    "Restore target is neither installed nor already restored",
                    operation=operation["id"],
                    path=str(target),
                )
            if not path_is_file(backup) or sha256_file(backup) != expected:
                raise TransactionError(
                    "transaction.backup_mismatch",
                    "Restore backup does not match the expected original",
                    operation=operation["id"],
                    path=str(backup),
                )
        elif action == "remove":
            source = self._verify_source_file(operation)
            if path_lexists(target) and (
                not path_is_file(target)
                or sha256_file(target) != sha256_file(source)
            ):
                raise TransactionError(
                    "transaction.target_foreign",
                    "Removal target does not match the artifact source",
                    operation=operation["id"],
                    path=str(target),
                )
        elif action == "remove_tree":
            self._verify_source_tree(operation)
            self._validate_owned_tree(operation, target)
        elif action == "retain_tree":
            self._verify_source_tree(operation)
            if not path_lexists(target):
                raise TransactionError(
                    "transaction.target_missing",
                    "Retained tree target is missing",
                    operation=operation["id"],
                    path=str(target),
                )
            self._validate_owned_tree(operation, target)
        elif action == "retain":
            return

    def _step_temporary_path(
        self,
        index: int,
        operation: dict[str, Any],
        suffix: str,
        *,
        sibling_of: pathlib.Path | None = None,
    ) -> pathlib.Path:
        token = f"runtime-txn-{self.plan_digest[:16]}-{index:03d}-{operation['id']}"
        if sibling_of is None:
            path = self.undo_root / f"{token}.{suffix}"
            self._ensure_transaction_path(path, operation["id"])
        else:
            path = sibling_of.parent / f".{sibling_of.name}.{token}.{suffix}"
            self._ensure_allowed(path, operation["id"])
        return path

    def _sibling_temporary_paths(
        self,
        index: int,
        operation: dict[str, Any],
    ) -> tuple[pathlib.Path, ...]:
        action = operation["action"]
        target = self._mutable_path(operation, "target")
        if action == "backup":
            backup = self._mutable_path(operation, "backup")
            return (self._step_temporary_path(index, operation, "staging", sibling_of=backup),)
        if action in {"replace_file", "restore"}:
            return (
                self._step_temporary_path(index, operation, "original", sibling_of=target),
                self._step_temporary_path(index, operation, "staging", sibling_of=target),
                self._step_temporary_path(index, operation, "rollback", sibling_of=target),
            )
        if action == "create_file":
            return (self._step_temporary_path(index, operation, "staging", sibling_of=target),)
        if action == "replace_tree":
            return (
                self._step_temporary_path(index, operation, "original", sibling_of=target),
                self._step_temporary_path(index, operation, "staging", sibling_of=target),
                self._step_temporary_path(index, operation, "rollback", sibling_of=target),
            )
        if action in {"remove", "remove_tree"}:
            return (self._step_temporary_path(index, operation, "removed", sibling_of=target),)
        return ()

    def _ensure_fresh_workspace(self) -> None:
        if path_lexists(self.undo_root):
            raise TransactionError(
                "transaction.temporary_exists",
                "Transaction undo root already exists",
                path=str(self.undo_root),
            )
        for index, operation in enumerate(self.operations):
            for path in self._sibling_temporary_paths(index, operation):
                for candidate in (path, self._cleanup_quarantine(path)):
                    if path_lexists(candidate):
                        raise TransactionError(
                            "transaction.temporary_exists",
                            "Transaction temporary path already exists",
                            operation=operation["id"],
                            path=str(candidate),
                        )

    def _preflight(self) -> None:
        for operation in self.operations:
            self._preflight_operation(operation)
        self._ensure_fresh_workspace()

    def _new_journal(self) -> dict[str, Any]:
        timestamp = utc_now()
        journal: dict[str, Any] = {
            "schemaVersion": 3 if self.plan_identity is not None else 2,
            "transactionId": uuid.uuid4().hex,
            "kind": self.kind,
            "planDigest": self.plan_digest,
            "state": "prepared",
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "currentStep": None,
            "undoRoot": str(self.undo_root),
            "steps": [
                {
                    "id": operation["id"],
                    "action": operation["action"],
                    "target": operation["target"],
                    "status": "pending",
                    "undo": None,
                }
                for operation in self.operations
            ],
            "failure": None,
            "rollbackFailures": [],
            "cleanupFailures": [],
            "cleanupInProgress": {},
        }
        if self.plan_identity is not None:
            journal["planIdentity"] = self.plan_identity
        return journal

    def _invalid_journal(self, message: str, **context: Any) -> TransactionError:
        return TransactionError("transaction.journal_invalid", message, **context)

    def _validate_descriptor_parents(
        self,
        undo: dict[str, Any],
        expected: dict[str, tuple[pathlib.Path, bool]],
        *,
        require_current: bool,
    ) -> None:
        records = undo.get("descriptorParents")
        if not isinstance(records, dict) or set(records) != set(expected):
            raise self._invalid_journal("Transaction journal descriptor parents are invalid")
        for name, (path, allow_missing) in expected.items():
            value = records[name]
            try:
                validate_parent_identity_payload(
                    value,
                    field=f"descriptorParents.{name}",
                    allow_missing=allow_missing,
                )
                if require_current:
                    require_parent_identity(
                        path,
                        value,
                        field=f"descriptorParents.{name}",
                        allow_missing=allow_missing,
                    )
            except TransactionError as error:
                if require_current:
                    raise
                raise self._invalid_journal(
                    "Transaction journal descriptor parent is invalid",
                    parent=name,
                ) from error

    def _require_undo_descriptor_parents(
        self,
        operation: dict[str, Any],
        undo: dict[str, Any],
    ) -> None:
        action = operation["action"]
        target = self._mutable_path(operation, "target")
        if action == "backup":
            self._validate_descriptor_parents(
                undo,
                {
                    "target": (target, False),
                    "backup": (self._mutable_path(operation, "backup"), False),
                },
                require_current=True,
            )
        elif action in {"replace_file", "restore"}:
            self._validate_descriptor_parents(
                undo,
                {"target": (target, False)},
                require_current=True,
            )
        elif action in {"create_file", "replace_tree"}:
            self._validate_descriptor_parents(
                undo,
                {"target": (target, False)},
                require_current=True,
            )
        elif action in {"remove", "remove_tree"}:
            self._validate_descriptor_parents(
                undo,
                {"target": (target, not undo["existed"])},
                require_current=True,
            )

    def _require_committed_descriptor_parents(
        self,
        operation: dict[str, Any],
        undo: dict[str, Any],
    ) -> None:
        action = operation["action"]
        target = self._mutable_path(operation, "target")
        records = undo["descriptorParents"]
        require_parent_identity(
            target,
            records["target"],
            field="descriptorParents.target",
            allow_missing=action in {"remove", "remove_tree"} and not undo["existed"],
        )
        if action == "backup":
            backup = self._mutable_path(operation, "backup")
            require_parent_identity(
                backup,
                records["backup"],
                field="descriptorParents.backup",
            )

    def _validate_undo(
        self,
        index: int,
        operation: dict[str, Any],
        undo: Any,
    ) -> None:
        if not isinstance(undo, dict):
            raise self._invalid_journal("Transaction journal undo record is invalid", index=index)
        action = operation["action"]
        target = self._mutable_path(operation, "target")
        if action not in MUTATING_ACTIONS:
            expected = {"kind": "none", "commitCleanupPaths": []}
            if undo != expected:
                raise self._invalid_journal("Transaction journal undo record is invalid", index=index)
            return
        if action == "backup":
            backup = self._mutable_path(operation, "backup")
            staging = self._step_temporary_path(index, operation, "staging", sibling_of=backup)
            expected_keys = {
                "kind",
                "path",
                "staging",
                "created",
                "backupSha256",
                "descriptorParents",
                "commitCleanupPaths",
            }
            if set(undo) != expected_keys or undo.get("kind") != "remove-backup":
                raise self._invalid_journal("Transaction journal backup undo is invalid", index=index)
            if (
                undo.get("path") != str(backup)
                or undo.get("staging") != str(staging)
                or not isinstance(undo.get("created"), bool)
                or not isinstance(undo.get("backupSha256"), str)
                or SHA256_PATTERN.fullmatch(undo["backupSha256"]) is None
                or undo.get("commitCleanupPaths") != [str(staging)]
            ):
                raise self._invalid_journal("Transaction journal backup undo is invalid", index=index)
            self._validate_descriptor_parents(
                undo,
                {"target": (target, False), "backup": (backup, False)},
                require_current=False,
            )
            return
        if action in {"replace_file", "restore"}:
            original = self._step_temporary_path(
                index,
                operation,
                "original",
                sibling_of=target,
            )
            staging = self._step_temporary_path(index, operation, "staging", sibling_of=target)
            rollback_discard = self._step_temporary_path(
                index,
                operation,
                "rollback",
                sibling_of=target,
            )
            expected_applied = (
                self._required_sha256(operation, "sourceSha256")
                if action == "replace_file"
                else self._required_sha256(operation, "expectedSha256")
            )
            allowed_originals = (
                {
                    self._required_sha256(
                        self._matching_install_guard(operation, "assert_sha256"),
                        "expectedSha256",
                    )
                }
                if action == "replace_file"
                else {
                    self._required_sha256(operation, "sourceSha256"),
                    self._required_sha256(operation, "expectedSha256"),
                }
            )
            expected_keys = {
                "kind",
                "target",
                "original",
                "staging",
                "rollbackDiscard",
                "originalSha256",
                "appliedSha256",
                "phase",
                "descriptorParents",
                "commitCleanupPaths",
            }
            if set(undo) != expected_keys or undo.get("kind") != "restore-file":
                raise self._invalid_journal("Transaction journal file undo is invalid", index=index)
            if (
                undo.get("target") != str(target)
                or undo.get("original") != str(original)
                or undo.get("staging") != str(staging)
                or undo.get("rollbackDiscard") != str(rollback_discard)
                or not isinstance(undo.get("originalSha256"), str)
                or SHA256_PATTERN.fullmatch(undo["originalSha256"]) is None
                or undo.get("originalSha256") not in allowed_originals
                or undo.get("appliedSha256") != expected_applied
                or undo.get("phase") not in FILE_PHASES
                or undo.get("commitCleanupPaths")
                != [str(original), str(staging), str(rollback_discard)]
            ):
                raise self._invalid_journal("Transaction journal file undo is invalid", index=index)
            self._validate_descriptor_parents(
                undo,
                {"target": (target, False)},
                require_current=False,
            )
            return
        if action == "create_file":
            staging = self._step_temporary_path(index, operation, "staging", sibling_of=target)
            expected_keys = {
                "kind",
                "target",
                "staging",
                "appliedSha256",
                "descriptorParents",
                "commitCleanupPaths",
            }
            if (
                set(undo) != expected_keys
                or undo.get("kind") != "remove-created-file"
                or undo.get("target") != str(target)
                or undo.get("staging") != str(staging)
                or undo.get("appliedSha256")
                != self._required_sha256(operation, "sourceSha256")
                or undo.get("commitCleanupPaths") != [str(staging)]
            ):
                raise self._invalid_journal("Transaction journal create undo is invalid", index=index)
            self._validate_descriptor_parents(
                undo,
                {"target": (target, False)},
                require_current=False,
            )
            return
        if action == "replace_tree":
            contract = self._tree_contract(operation)
            original = self._step_temporary_path(index, operation, "original", sibling_of=target)
            staging = self._step_temporary_path(index, operation, "staging", sibling_of=target)
            rollback_discard = self._step_temporary_path(
                index,
                operation,
                "rollback",
                sibling_of=target,
            )
            expected_keys = {
                "kind",
                "target",
                "original",
                "staging",
                "rollbackDiscard",
                "existed",
                "treePublication",
                "marker",
                "markerSha256",
                "originalTreeSha256",
                "appliedTreeSha256",
                "phase",
                "descriptorParents",
                "commitCleanupPaths",
            }
            if set(undo) != expected_keys or undo.get("kind") != "restore-tree":
                raise self._invalid_journal("Transaction journal tree undo is invalid", index=index)
            expected_publication = (
                "exchange"
                if undo.get("existed") is True
                and contract.get("ownershipPolicy") == "developer-id-bundle"
                else "exclusive-move"
            )
            if (
                undo.get("target") != str(target)
                or undo.get("original") != str(original)
                or undo.get("staging") != str(staging)
                or undo.get("rollbackDiscard") != str(rollback_discard)
                or not isinstance(undo.get("existed"), bool)
                or undo.get("treePublication") != expected_publication
                or undo.get("marker") != self._marker_relative(contract).as_posix()
                or undo.get("markerSha256")
                != self._required_sha256(contract, "sourceMarkerSha256")
                or not isinstance(undo.get("appliedTreeSha256"), str)
                or SHA256_PATTERN.fullmatch(undo["appliedTreeSha256"]) is None
                or undo.get("appliedTreeSha256")
                != self._required_sha256(operation, "sourceTreeSha256")
                or (
                    undo["existed"]
                    and (
                        not isinstance(undo.get("originalTreeSha256"), str)
                        or SHA256_PATTERN.fullmatch(undo["originalTreeSha256"]) is None
                    )
                )
                or (not undo["existed"] and undo.get("originalTreeSha256") is not None)
                or undo.get("phase") not in TREE_PHASES
                or undo.get("commitCleanupPaths")
                != [str(original), str(staging), str(rollback_discard)]
            ):
                raise self._invalid_journal("Transaction journal tree undo is invalid", index=index)
            self._validate_descriptor_parents(
                undo,
                {"target": (target, False)},
                require_current=False,
            )
            return
        if action in {"remove", "remove_tree"}:
            original = self._step_temporary_path(index, operation, "removed", sibling_of=target)
            common = {
                "target": str(target),
                "original": str(original),
                "commitCleanupPaths": [str(original)],
            }
            if action == "remove":
                expected_keys = {
                    "kind",
                    "target",
                    "original",
                    "existed",
                    "expectedSha256",
                    "targetIdentity",
                    "phase",
                    "descriptorParents",
                    "commitCleanupPaths",
                }
                expected = {
                    "kind": "restore-moved-file",
                    **common,
                    "expectedSha256": self._required_sha256(operation, "sourceSha256"),
                }
            else:
                expected_keys = {
                    "kind",
                    "target",
                    "original",
                    "existed",
                    "marker",
                    "markerSha256",
                    "expectedTreeSha256",
                    "targetIdentity",
                    "phase",
                    "descriptorParents",
                    "commitCleanupPaths",
                }
                expected = {
                    "kind": "restore-moved-tree",
                    **common,
                    "marker": self._marker_relative(operation).as_posix(),
                    "markerSha256": self._required_sha256(
                        operation,
                        "sourceMarkerSha256",
                    ),
                    "expectedTreeSha256": self._required_sha256(
                        operation,
                        "sourceTreeSha256",
                    ),
                }
            if (
                set(undo) != expected_keys
                or not isinstance(undo.get("existed"), bool)
                or undo.get("phase") not in REMOVAL_PHASES
                or (
                    not undo["existed"]
                    and (
                        undo.get("targetIdentity") is not None
                        or undo.get("phase") != "intent"
                    )
                )
            ):
                raise self._invalid_journal("Transaction journal removal undo is invalid", index=index)
            for key, value in expected.items():
                if undo.get(key) != value:
                    raise self._invalid_journal(
                        "Transaction journal removal undo is invalid",
                        index=index,
                    )
            if undo["existed"]:
                identity = _descriptor_result(
                    lambda: runtime_descriptor.FileIdentity.from_payload(
                        undo.get("targetIdentity"),
                        field=f"steps[{index}].undo.targetIdentity",
                    )
                )
                expected_file_type = stat.S_IFREG if action == "remove" else stat.S_IFDIR
                if identity.file_type != expected_file_type:
                    raise self._invalid_journal(
                        "Transaction journal removal identity is invalid",
                        index=index,
                    )
            self._validate_descriptor_parents(
                undo,
                {"target": (target, not undo["existed"])},
                require_current=False,
            )
            return
        raise self._invalid_journal("Transaction journal undo action is unsupported", index=index)

    def _validate_journal(self, journal: Any) -> dict[str, Any]:
        base_keys = {
            "schemaVersion",
            "transactionId",
            "kind",
            "planDigest",
            "state",
            "createdAt",
            "updatedAt",
            "currentStep",
            "undoRoot",
            "steps",
            "failure",
            "rollbackFailures",
            "cleanupFailures",
            "cleanupInProgress",
        }
        if not isinstance(journal, dict):
            raise self._invalid_journal("Transaction journal schema is invalid")
        schema_version = journal.get("schemaVersion")
        if schema_version == 2:
            expected_keys = base_keys
            journal_identity = None
        elif schema_version == 3:
            expected_keys = {*base_keys, "planIdentity"}
            try:
                journal_identity = validate_plan_identity(journal.get("planIdentity"))
            except TransactionError as error:
                raise self._invalid_journal("Transaction journal plan identity is invalid") from error
            if journal_identity is None:
                raise self._invalid_journal("Transaction journal plan identity is invalid")
        else:
            raise self._invalid_journal("Transaction journal schema is invalid")
        if set(journal) != expected_keys:
            raise self._invalid_journal("Transaction journal schema is invalid")
        if (
            journal.get("kind") != self.kind
            or journal.get("planDigest") != self.plan_digest
            or journal_identity != self.plan_identity
        ):
            raise TransactionError(
                "transaction.journal_mismatch",
                "Existing transaction journal belongs to another plan or kind",
                expectedKind=self.kind,
                actualKind=journal.get("kind"),
                expectedPlanDigest=self.plan_digest,
                actualPlanDigest=journal.get("planDigest"),
                expectedPlanIdentity=self.plan_identity,
                actualPlanIdentity=journal_identity,
            )
        if (
            not isinstance(journal.get("transactionId"), str)
            or TRANSACTION_ID_PATTERN.fullmatch(journal["transactionId"]) is None
            or journal.get("state") not in TRANSACTION_STATES
            or not isinstance(journal.get("createdAt"), str)
            or not journal["createdAt"]
            or not isinstance(journal.get("updatedAt"), str)
            or not journal["updatedAt"]
            or journal.get("undoRoot") != str(self.undo_root)
        ):
            raise self._invalid_journal("Transaction journal identity is invalid")
        steps = journal.get("steps")
        if not isinstance(steps, list) or len(steps) != len(self.operations):
            raise self._invalid_journal("Transaction journal steps are invalid")
        step_ids = {operation["id"] for operation in self.operations}
        if journal.get("currentStep") is not None and journal["currentStep"] not in step_ids:
            raise self._invalid_journal("Transaction journal current step is invalid")
        if journal["state"] in {"prepared", "committed", "rolled-back", "failed"} and journal[
            "currentStep"
        ] is not None:
            raise self._invalid_journal("Terminal transaction journal has a current step")
        allowed_statuses = {
            "prepared": {"pending"},
            "running": {"pending", "applying", "applied"},
            "rolling-back": {
                "pending",
                "applying",
                "applied",
                "rolling-back",
                "rolled-back",
                "rollback-failed",
            },
            "committed": {"applied"},
            "rolled-back": {"pending", "rolled-back"},
            "failed": {"pending", "rolled-back", "rollback-failed"},
        }[journal["state"]]
        for index, step in enumerate(steps):
            operation = self.operations[index]
            if not isinstance(step, dict) or set(step) != {
                "id",
                "action",
                "target",
                "status",
                "undo",
            }:
                raise self._invalid_journal("Transaction journal step is invalid", index=index)
            if (
                step.get("id") != operation["id"]
                or step.get("action") != operation["action"]
                or step.get("target") != operation["target"]
                or step.get("status") not in STEP_STATUSES
                or step.get("status") not in allowed_statuses
            ):
                raise self._invalid_journal("Transaction journal step is invalid", index=index)
            if step["status"] == "pending":
                if step.get("undo") is not None:
                    raise self._invalid_journal("Pending journal step contains undo intent", index=index)
            else:
                self._validate_undo(index, operation, step.get("undo"))
                if operation["action"] in {"replace_file", "restore", "replace_tree"}:
                    phase = step["undo"]["phase"]
                    if step["status"] == "applied" and phase != "replacement-installed":
                        raise self._invalid_journal(
                            "Applied replacement journal step has an invalid phase",
                            index=index,
                        )
                    if step["status"] == "rolled-back" and phase != "rollback-restored":
                        raise self._invalid_journal(
                            "Rolled-back replacement journal step has an invalid phase",
                            index=index,
                        )
                    if phase.startswith("rollback-") and journal["state"] not in {
                        "rolling-back",
                        "rolled-back",
                        "failed",
                    }:
                        raise self._invalid_journal(
                            "Forward replacement journal step has a rollback phase",
                            index=index,
                        )
        if journal["state"] == "failed" and not any(
            step["status"] == "rollback-failed" for step in steps
        ):
            raise self._invalid_journal("Failed transaction journal has no rollback failure")
        if journal.get("failure") is not None and not isinstance(journal["failure"], dict):
            raise self._invalid_journal("Transaction journal failure is invalid")
        rollback_failures = journal.get("rollbackFailures")
        cleanup_failures = journal.get("cleanupFailures")
        cleanup_in_progress = journal.get("cleanupInProgress")
        if not isinstance(rollback_failures, list) or not all(
            isinstance(item, dict) for item in rollback_failures
        ):
            raise self._invalid_journal("Transaction rollback failures are invalid")
        if not isinstance(cleanup_failures, list) or not all(
            isinstance(item, str) for item in cleanup_failures
        ):
            raise self._invalid_journal("Transaction cleanup failures are invalid")
        possible_cleanup_paths: dict[str, str] = {}
        for step in steps:
            undo = step.get("undo") or {}
            kind = undo.get("kind")
            if kind is None:
                continue
            assert isinstance(kind, str)
            for path in undo.get("commitCleanupPaths") or []:
                possible_cleanup_paths[path] = kind
            if kind == "remove-backup":
                possible_cleanup_paths[undo["path"]] = kind
            elif kind == "remove-created-file":
                possible_cleanup_paths[undo["target"]] = kind
        if not isinstance(cleanup_in_progress, dict) or not all(
            isinstance(path, str)
            and path in possible_cleanup_paths
            and isinstance(record, dict)
            and set(record) == {"phase", "identity", "treeEntries"}
            and record.get("phase") in {"intent", "deleting"}
            for path, record in cleanup_in_progress.items()
        ):
            raise self._invalid_journal("Transaction cleanup progress is invalid")
        for path, record in cleanup_in_progress.items():
            identity = _descriptor_result(
                lambda: runtime_descriptor.FileIdentity.from_payload(
                    record["identity"],
                    field=f"cleanupInProgress.{path}.identity",
                )
            )
            tree_entries = validate_cleanup_tree_entries(
                record["treeEntries"],
                field=f"cleanupInProgress.{path}.treeEntries",
            )
            tree_kind = possible_cleanup_paths[path] in {
                "restore-tree",
                "restore-moved-tree",
            }
            if tree_kind != (tree_entries is not None):
                raise self._invalid_journal("Transaction cleanup tree progress is invalid")
            if tree_entries is not None:
                root_identity_payload = tree_entries[0]["identity"]
                root_identity = _descriptor_result(
                    lambda: runtime_descriptor.FileIdentity.from_payload(
                        root_identity_payload,
                        field=f"cleanupInProgress.{path}.treeEntries[0].identity",
                    )
                )
                if root_identity != identity:
                    raise self._invalid_journal("Transaction cleanup tree identity is invalid")
        return journal

    def _load_journal(self) -> dict[str, Any] | None:
        if not path_lexists(self.journal_path):
            return None
        reject_symlink_components(self.journal_path)
        journal = load_json(self.journal_path)
        return self._validate_journal(journal)

    def _save_journal(self, journal: dict[str, Any]) -> None:
        journal["updatedAt"] = utc_now()
        atomic_write_json(self.journal_path, journal)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        bound = _bound_path(self.lock_path)
        if bound is not None:
            existed = path_lexists(self.lock_path)
            descriptor, _ = _descriptor_result(lambda: bound.open_read_write_create(0o600))
            if not existed:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                bound.fsync_parent()
        else:
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(self.lock_path, flags, 0o600)
            except OSError as error:
                raise TransactionError(
                    "transaction.lock_failed",
                    "Transaction lock could not be opened",
                    path=str(self.lock_path),
                    error=str(error),
                ) from error
        with os.fdopen(descriptor, "r+") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise TransactionError(
                    "transaction.lock_unsafe",
                    "Transaction journal lock has unsafe type, ownership, or mode",
                    path=str(self.lock_path),
                )
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise TransactionError(
                    "transaction.busy",
                    "Another transaction owns this journal",
                    path=str(self.journal_path),
                ) from error
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _report(self, journal: dict[str, Any]) -> TransactionReport:
        applied = tuple(step["id"] for step in journal["steps"] if step["status"] == "applied")
        rolled_back = tuple(
            step["id"] for step in journal["steps"] if step["status"] == "rolled-back"
        )
        rollback_failures = tuple(journal.get("rollbackFailures") or [])
        cleanup_failures = tuple(journal.get("cleanupFailures") or [])
        return TransactionReport(
            journal["state"] == "committed" and not cleanup_failures,
            journal["state"],
            journal["transactionId"],
            journal["planDigest"],
            self.journal_path,
            applied,
            rolled_back,
            journal.get("failure"),
            rollback_failures,
            cleanup_failures,
        )

    def _verify_committed_state(self, journal: dict[str, Any]) -> None:
        for operation, step in zip(self.operations, journal["steps"], strict=True):
            action = operation["action"]
            target = self._mutable_path(operation, "target")
            undo = step.get("undo") or {}
            if action in MUTATING_ACTIONS:
                self._require_committed_descriptor_parents(operation, undo)
            if action == "backup":
                backup = self._mutable_path(operation, "backup")
                expected = undo.get("backupSha256")
                if (
                    not path_is_file(backup)
                    or not isinstance(expected, str)
                    or sha256_file(backup) != expected
                ):
                    raise TransactionError(
                        "transaction.journal_inconsistent",
                        "Committed backup state does not match the journal",
                        operation=operation["id"],
                        path=str(backup),
                    )
            elif action in {"replace_file", "create_file"}:
                expected = self._required_sha256(operation, "sourceSha256")
                if not path_is_file(target) or sha256_file(target) != expected:
                    raise TransactionError(
                        "transaction.journal_inconsistent",
                        "Committed file state does not match the plan",
                        operation=operation["id"],
                        path=str(target),
                    )
            elif action == "replace_tree":
                try:
                    self._validate_owned_tree(operation, target)
                except TransactionError as error:
                    raise TransactionError(
                        "transaction.journal_inconsistent",
                        "Committed tree state does not match the ownership contract",
                        operation=operation["id"],
                        path=str(target),
                    ) from error
                expected = undo.get("appliedTreeSha256")
                if (
                    not path_is_dir(target)
                    or not isinstance(expected, str)
                    or tree_sha256(target) != expected
                ):
                    raise TransactionError(
                        "transaction.journal_inconsistent",
                        "Committed tree state does not match the transaction intent",
                        operation=operation["id"],
                        path=str(target),
                    )
            elif action == "retain_tree":
                try:
                    self._verify_source_tree(operation)
                    if not path_lexists(target):
                        raise TransactionError(
                            "transaction.target_missing",
                            "Retained tree target is missing",
                            operation=operation["id"],
                            path=str(target),
                        )
                    self._validate_owned_tree(operation, target)
                except TransactionError as error:
                    raise TransactionError(
                        "transaction.journal_inconsistent",
                        "Committed retained tree does not match the ownership contract",
                        operation=operation["id"],
                        path=str(target),
                    ) from error
            elif action == "restore":
                expected = self._required_sha256(operation, "expectedSha256")
                if not path_is_file(target) or sha256_file(target) != expected:
                    raise TransactionError(
                        "transaction.journal_inconsistent",
                        "Committed restore state does not match the plan",
                        operation=operation["id"],
                        path=str(target),
                    )
            elif action in {"remove", "remove_tree"} and path_lexists(target):
                raise TransactionError(
                    "transaction.journal_inconsistent",
                    "Committed removal target still exists",
                    operation=operation["id"],
                    path=str(target),
                    )

    def _validate_cleanup_payload(
        self,
        undo: dict[str, Any],
        path: pathlib.Path,
        *,
        logical_path: pathlib.Path | None = None,
    ) -> tuple[runtime_descriptor.FileIdentity, list[dict[str, Any]] | None]:
        identity = path_identity(path)
        if identity is None:
            raise TransactionError(
                "transaction.path_identity_changed",
                "Cleanup payload disappeared during validation",
                path=str(path),
            )

        def finish(
            tree_entries: list[dict[str, Any]] | None = None,
        ) -> tuple[runtime_descriptor.FileIdentity, list[dict[str, Any]] | None]:
            require_path_identity(path, identity)
            if tree_entries is not None:
                root_identity = _descriptor_result(
                    lambda: runtime_descriptor.FileIdentity.from_payload(
                        tree_entries[0]["identity"],
                        field="cleanupTree.root.identity",
                    )
                )
                if root_identity != identity:
                    raise TransactionError(
                        "transaction.path_identity_changed",
                        "Cleanup tree root changed during inspection",
                        path=str(path),
                    )
            return identity, tree_entries

        logical = logical_path or path
        kind = undo.get("kind")
        if kind == "remove-backup":
            expected = undo["backupSha256"]
            if not path_is_file(path) or sha256_file(path) != expected:
                raise TransactionError(
                    "transaction.undo_foreign",
                    "Backup staging cleanup payload changed",
                    path=str(path),
                )
            return finish()
        if kind == "restore-file":
            expected = (
                undo["originalSha256"]
                if logical == pathlib.Path(undo["original"])
                else undo["appliedSha256"]
            )
            if not path_is_file(path) or sha256_file(path) != expected:
                raise TransactionError(
                    "transaction.undo_foreign",
                    "File cleanup payload changed",
                    path=str(path),
                )
            return finish()
        if kind == "remove-created-file":
            if not path_is_file(path) or sha256_file(path) != undo["appliedSha256"]:
                raise TransactionError(
                    "transaction.undo_foreign",
                    "Created-file staging cleanup payload changed",
                    path=str(path),
                )
            return finish()
        if kind == "restore-tree":
            original_cleanup = logical == pathlib.Path(undo["original"])
            exchanged_rollback = (
                undo.get("treePublication") == "exchange"
                and undo.get("phase") == "rollback-restored"
            )
            expected = (
                undo["appliedTreeSha256"]
                if original_cleanup and exchanged_rollback
                else (
                    undo["originalTreeSha256"]
                    if original_cleanup
                    else undo["appliedTreeSha256"]
                )
            )
            if expected is None:
                raise TransactionError(
                    "transaction.undo_foreign",
                    "Unexpected tree cleanup payload exists",
                    path=str(path),
                )
            self._validate_tree_payload(
                path,
                undo["marker"],
                undo["markerSha256"],
                expected,
            )
            return finish(tree_cleanup_manifest(path))
        if kind == "restore-moved-file":
            if not path_is_file(path) or sha256_file(path) != undo["expectedSha256"]:
                raise TransactionError(
                    "transaction.undo_foreign",
                    "Removed-file cleanup payload changed",
                    path=str(path),
                )
            return finish()
        if kind == "restore-moved-tree":
            self._validate_tree_payload(
                path,
                undo["marker"],
                undo["markerSha256"],
                undo["expectedTreeSha256"],
            )
            return finish(tree_cleanup_manifest(path))
        raise TransactionError(
            "transaction.journal_invalid",
            "Unknown cleanup payload kind",
            undoKind=kind,
        )

    @staticmethod
    def _cleanup_quarantine(path: pathlib.Path) -> pathlib.Path:
        return path.with_name(f"{path.name}.descriptor-delete")

    def _validate_partial_cleanup_payload(
        self,
        undo: dict[str, Any],
        path: pathlib.Path,
        expected_tree_entries: list[dict[str, Any]] | None,
    ) -> None:
        kind = undo.get("kind")
        file_kinds = {
            "remove-backup",
            "restore-file",
            "remove-created-file",
            "restore-moved-file",
        }
        tree_kinds = {"restore-tree", "restore-moved-tree"}
        if kind in file_kinds and path_is_file(path) and expected_tree_entries is None:
            return
        if kind in tree_kinds and path_is_dir(path):
            if expected_tree_entries is None:
                raise TransactionError(
                    "transaction.journal_invalid",
                    "Tree cleanup is missing its journaled manifest",
                    path=str(path),
                )
            expected = {record["path"]: record for record in expected_tree_entries}
            for record in tree_cleanup_manifest(path):
                if not cleanup_tree_record_matches(expected.get(record["path"]), record):
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Partial cleanup tree contains changed or unjournaled content",
                        path=str(path),
                        relativePath=record["path"],
                    )
            return
        raise TransactionError(
            "transaction.undo_foreign",
            "Partial cleanup quarantine changed type",
            path=str(path),
            undoKind=kind,
        )

    def _set_cleanup_progress(
        self,
        journal: dict[str, Any],
        path: pathlib.Path,
        *,
        identity: runtime_descriptor.FileIdentity | None = None,
        tree_entries: list[dict[str, Any]] | None = None,
        phase: str | None = None,
    ) -> None:
        value = str(path)
        progress = dict(journal["cleanupInProgress"])
        if phase is not None:
            if identity is None:
                raise AssertionError("Cleanup progress identity is required")
            progress[value] = {
                "phase": phase,
                "identity": identity.to_payload(),
                "treeEntries": tree_entries,
            }
        else:
            progress.pop(value, None)
        journal["cleanupInProgress"] = progress
        self._save_journal(journal)

    def _remove_verified_payload(
        self,
        journal: dict[str, Any],
        undo: dict[str, Any],
        path: pathlib.Path,
        *,
        logical_path: pathlib.Path | None = None,
    ) -> None:
        quarantine = self._cleanup_quarantine(path)
        progress = journal["cleanupInProgress"].get(str(path))
        if progress is None:
            if path_lexists(quarantine):
                raise TransactionError(
                    "transaction.undo_foreign",
                    "Unjournaled cleanup quarantine already exists",
                    path=str(path),
                    quarantine=str(quarantine),
                )
            if not path_lexists(path):
                return
            identity, tree_entries = self._validate_cleanup_payload(
                undo,
                path,
                logical_path=logical_path,
            )
            self._set_cleanup_progress(
                journal,
                path,
                identity=identity,
                tree_entries=tree_entries,
                phase="intent",
            )
            progress = journal["cleanupInProgress"][str(path)]

        identity = _descriptor_result(
            lambda: runtime_descriptor.FileIdentity.from_payload(
                progress["identity"],
                field=f"cleanupInProgress.{path}.identity",
            )
        )
        tree_entries = validate_cleanup_tree_entries(
            progress["treeEntries"],
            field=f"cleanupInProgress.{path}.treeEntries",
        )
        path_exists = path_lexists(path)
        quarantine_exists = path_lexists(quarantine)
        if path_exists and quarantine_exists:
            raise TransactionError(
                "transaction.undo_foreign",
                "Cleanup path and quarantine both exist",
                path=str(path),
                quarantine=str(quarantine),
            )

        if progress["phase"] == "intent":
            if path_exists:
                current_identity, current_tree_entries = self._validate_cleanup_payload(undo, path)
                if current_identity != identity:
                    require_path_identity(path, identity)
                if current_tree_entries != tree_entries:
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Cleanup payload identity manifest changed before quarantine",
                        path=str(path),
                    )
                rename_exclusive(path, quarantine)
            elif not quarantine_exists:
                raise TransactionError(
                    "transaction.undo_foreign",
                    "Journaled cleanup payload is missing before deletion",
                    path=str(path),
                    quarantine=str(quarantine),
                )
            try:
                require_path_identity(quarantine, identity)
                current_identity, current_tree_entries = self._validate_cleanup_payload(
                    undo,
                    quarantine,
                    logical_path=path,
                )
                if current_identity != identity or current_tree_entries != tree_entries:
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Cleanup quarantine does not match its journaled identity manifest",
                        path=str(quarantine),
                    )
            except Exception:
                if not path_lexists(path) and path_lexists(quarantine):
                    with contextlib.suppress(TransactionError):
                        rename_exclusive(quarantine, path)
                        self._set_cleanup_progress(journal, path)
                raise
            self._set_cleanup_progress(
                journal,
                path,
                identity=identity,
                tree_entries=tree_entries,
                phase="deleting",
            )
            quarantine_exists = True
        elif path_exists:
            raise TransactionError(
                "transaction.undo_foreign",
                "Cleanup payload returned after deletion began",
                path=str(path),
                quarantine=str(quarantine),
            )

        if not quarantine_exists:
            self._set_cleanup_progress(journal, path)
            return
        require_path_identity(quarantine, identity)
        self._validate_partial_cleanup_payload(undo, quarantine, tree_entries)
        remove_path_durable(quarantine, identity, tree_entries)
        self._set_cleanup_progress(journal, path)

    def _cleanup_payloads(self, journal: dict[str, Any]) -> None:
        failures: list[str] = []
        for step in journal["steps"]:
            undo = step.get("undo") or {}
            for raw_path in undo.get("commitCleanupPaths") or []:
                path = pathlib.Path(raw_path)
                try:
                    reject_symlink_components(path, include_leaf=False)
                    self._remove_verified_payload(journal, undo, path)
                except Exception as error:
                    failures.append(f"{path}: {error}")
        try:
            reject_symlink_components(self.undo_root, include_leaf=False)
            remove_path_durable(self.undo_root)
        except Exception as error:
            failures.append(f"{self.undo_root}: {error}")
        journal["cleanupFailures"] = failures

    def _finalize_cleanup(self, journal: dict[str, Any]) -> TransactionReport:
        self._cleanup_payloads(journal)
        self._save_journal(journal)
        report = self._report(journal)
        if report.cleanup_failures:
            raise TransactionError(
                "transaction.cleanup_failed",
                "Transaction payload cleanup did not complete",
                report=report.to_dict(),
            )
        return report

    def execute(self) -> TransactionReport:
        self._validate_structure()
        with self._descriptors():
            return self._execute_with_descriptors()

    def _execute_with_descriptors(self) -> TransactionReport:
        with self._locked():
            journal = self._load_journal()
            if journal is not None:
                if journal["state"] == "committed":
                    self._verify_committed_state(journal)
                    return self._finalize_cleanup(journal)
                if journal["state"] in {"prepared", "running", "rolling-back"}:
                    raise TransactionError(
                        "transaction.recovery_required",
                        "Incomplete transaction requires rollback recovery",
                        state=journal["state"],
                        path=str(self.journal_path),
                    )
                if journal["state"] == "rolled-back":
                    self._finalize_cleanup(journal)
                raise TransactionError(
                    "transaction.journal_terminal",
                    "Terminal failed or rolled-back journal must be preserved under a new path",
                    state=journal["state"],
                    path=str(self.journal_path),
                )

            self._preflight()
            journal = self._new_journal()
            self._save_journal(journal)
            try:
                make_directory(self.undo_root, 0o700)
                journal["state"] = "running"
                self._save_journal(journal)
                for index, operation in enumerate(self.operations):
                    step = journal["steps"][index]
                    journal["currentStep"] = operation["id"]
                    self._inject(operation["id"], "before")
                    self._preflight_operation(operation)
                    undo = self._prepare_undo(index, operation)
                    step["undo"] = undo
                    step["status"] = "applying"
                    self._save_journal(journal)
                    self._inject(operation["id"], "after-intent")
                    self._apply_operation(operation, undo, journal)
                    self._inject(operation["id"], "after-mutation")
                    step["status"] = "applied"
                    self._save_journal(journal)
                    self._inject(operation["id"], "after")
                self._verify_committed_state(journal)
                journal["currentStep"] = None
                journal["state"] = "committed"
                self._save_journal(journal)
            except Exception as error:
                journal["failure"] = error_payload(error)
                journal["state"] = "rolling-back"
                self._save_journal(journal)
                self._rollback(journal)
                report = self._report(journal)
                if journal["state"] == "failed":
                    code = "transaction.rollback_failed"
                    message = "Transaction failed and rollback did not complete"
                elif report.cleanup_failures:
                    code = "transaction.cleanup_failed"
                    message = "Transaction rolled back but payload cleanup did not complete"
                else:
                    code = "transaction.rolled_back"
                    message = "Transaction failed and was rolled back"
                raise TransactionError(code, message, report=report.to_dict()) from error
            return self._finalize_cleanup(journal)

    def inspect(self) -> dict[str, Any] | None:
        self._validate_structure()
        with self._descriptors():
            with self._locked():
                return self._load_journal()

    def archive_terminal(self, history_root: pathlib.Path) -> pathlib.Path:
        self._validate_structure()
        history_root = self._absolute_input_path(history_root)
        self._ensure_transaction_path(history_root, "transaction_history")
        if not history_root.is_dir():
            raise TransactionError(
                "transaction.history_missing",
                "Transaction history directory must already exist",
                path=str(history_root),
            )
        with self._descriptors():
            return self._archive_terminal_with_descriptors(history_root)

    def _archive_terminal_with_descriptors(self, history_root: pathlib.Path) -> pathlib.Path:
        with self._locked():
            journal = self._load_journal()
            if journal is None:
                raise TransactionError(
                    "transaction.journal_missing",
                    "No transaction journal exists to archive",
                    path=str(self.journal_path),
                )
            if journal["state"] == "committed":
                self._verify_committed_state(journal)
                self._finalize_cleanup(journal)
            elif journal["state"] == "rolled-back":
                self._finalize_cleanup(journal)
            else:
                raise TransactionError(
                    "transaction.journal_terminal",
                    "Only committed or rolled-back journals may be archived",
                    state=journal["state"],
                    path=str(self.journal_path),
                )
            if path_lexists(self.undo_root):
                raise TransactionError(
                    "transaction.journal_inconsistent",
                    "Terminal transaction still has rollback payloads",
                    path=str(self.undo_root),
                )
            destination = history_root / (
                f"{journal['transactionId']}-{journal['kind']}-{journal['state']}.json"
            )
            self._ensure_transaction_path(destination, "transaction_history")
            reject_symlink_components(destination, include_leaf=False)
            if path_lexists(destination):
                raise TransactionError(
                    "transaction.history_collision",
                    "Transaction journal archive already exists",
                    path=str(destination),
                )
            rename_exclusive(self.journal_path, destination)
            fsync_directory(history_root)
            return destination

    def recover(self) -> TransactionReport:
        self._validate_structure()
        with self._descriptors():
            return self._recover_with_descriptors()

    def _recover_with_descriptors(self) -> TransactionReport:
        with self._locked():
            journal = self._load_journal()
            if journal is None:
                raise TransactionError(
                    "transaction.journal_missing",
                    "No transaction journal exists to recover",
                    path=str(self.journal_path),
                )
            if journal["state"] == "committed":
                self._verify_committed_state(journal)
                return self._finalize_cleanup(journal)
            if journal["state"] == "rolled-back":
                return self._finalize_cleanup(journal)
            if journal["state"] == "failed":
                raise TransactionError(
                    "transaction.rollback_failed",
                    "Transaction journal records an incomplete rollback",
                    report=self._report(journal).to_dict(),
                )
            journal["state"] = "rolling-back"
            self._save_journal(journal)
            self._rollback(journal)
            report = self._report(journal)
            if journal["state"] == "failed":
                raise TransactionError(
                    "transaction.rollback_failed",
                    "Transaction recovery could not complete rollback",
                    report=report.to_dict(),
                )
            if report.cleanup_failures:
                raise TransactionError(
                    "transaction.cleanup_failed",
                    "Transaction recovery rolled back but cleanup did not complete",
                    report=report.to_dict(),
                )
            return report

    def _prepare_undo(self, index: int, operation: dict[str, Any]) -> dict[str, Any]:
        action = operation["action"]
        target = self._mutable_path(operation, "target")
        if action not in MUTATING_ACTIONS:
            return {"kind": "none", "commitCleanupPaths": []}
        if action == "backup":
            backup = self._mutable_path(operation, "backup")
            staging = self._step_temporary_path(index, operation, "staging", sibling_of=backup)
            return {
                "kind": "remove-backup",
                "path": str(backup),
                "staging": str(staging),
                "created": not path_lexists(backup),
                "backupSha256": sha256_file(target),
                "descriptorParents": {
                    "target": parent_identity_payload(target),
                    "backup": parent_identity_payload(backup),
                },
                "commitCleanupPaths": [str(staging)],
            }
        if action in {"replace_file", "restore"}:
            original = self._step_temporary_path(
                index,
                operation,
                "original",
                sibling_of=target,
            )
            staging = self._step_temporary_path(index, operation, "staging", sibling_of=target)
            rollback_discard = self._step_temporary_path(
                index,
                operation,
                "rollback",
                sibling_of=target,
            )
            applied_sha256 = (
                self._required_sha256(operation, "sourceSha256")
                if action == "replace_file"
                else self._required_sha256(operation, "expectedSha256")
            )
            original_sha256 = sha256_file(target)
            if action == "replace_file":
                guard = self._matching_install_guard(operation, "assert_sha256")
                expected_original = self._required_sha256(guard, "expectedSha256")
                if original_sha256 != expected_original:
                    raise TransactionError(
                        "transaction.target_foreign",
                        "Replacement snapshot no longer matches its stock guard",
                        operation=operation["id"],
                        expected=expected_original,
                        actual=original_sha256,
                    )
            return {
                "kind": "restore-file",
                "target": str(target),
                "original": str(original),
                "staging": str(staging),
                "rollbackDiscard": str(rollback_discard),
                "originalSha256": original_sha256,
                "appliedSha256": applied_sha256,
                "phase": "intent",
                "descriptorParents": {
                    "target": parent_identity_payload(target),
                },
                "commitCleanupPaths": [str(original), str(staging), str(rollback_discard)],
            }
        if action == "create_file":
            staging = self._step_temporary_path(index, operation, "staging", sibling_of=target)
            return {
                "kind": "remove-created-file",
                "target": str(target),
                "staging": str(staging),
                "appliedSha256": self._required_sha256(operation, "sourceSha256"),
                "descriptorParents": {
                    "target": parent_identity_payload(target),
                },
                "commitCleanupPaths": [str(staging)],
            }
        if action == "replace_tree":
            contract = self._tree_contract(operation)
            source = self._verify_source_tree(operation)
            original_tree_sha256 = self._validate_owned_tree(operation, target)
            tree_publication = (
                "exchange"
                if original_tree_sha256 is not None
                and contract.get("ownershipPolicy") == "developer-id-bundle"
                else "exclusive-move"
            )
            original = self._step_temporary_path(index, operation, "original", sibling_of=target)
            staging = self._step_temporary_path(index, operation, "staging", sibling_of=target)
            rollback_discard = self._step_temporary_path(
                index,
                operation,
                "rollback",
                sibling_of=target,
            )
            return {
                "kind": "restore-tree",
                "target": str(target),
                "original": str(original),
                "staging": str(staging),
                "rollbackDiscard": str(rollback_discard),
                "existed": original_tree_sha256 is not None,
                "treePublication": tree_publication,
                "marker": self._marker_relative(contract).as_posix(),
                "markerSha256": self._required_sha256(contract, "sourceMarkerSha256"),
                "originalTreeSha256": original_tree_sha256,
                "appliedTreeSha256": self._required_sha256(
                    operation,
                    "sourceTreeSha256",
                ),
                "phase": "intent",
                "descriptorParents": {
                    "target": parent_identity_payload(target),
                },
                "commitCleanupPaths": [str(original), str(staging), str(rollback_discard)],
            }
        if action == "remove":
            original = self._step_temporary_path(index, operation, "removed", sibling_of=target)
            existed = path_is_file(target)
            identity = path_identity(target) if existed else None
            if existed and identity is None:
                raise TransactionError(
                    "transaction.path_identity_changed",
                    "Removal target disappeared while recording transaction intent",
                    operation=operation["id"],
                    path=str(target),
                )
            return {
                "kind": "restore-moved-file",
                "target": str(target),
                "original": str(original),
                "existed": existed,
                "expectedSha256": self._required_sha256(operation, "sourceSha256"),
                "targetIdentity": identity.to_payload() if identity is not None else None,
                "phase": "intent",
                "descriptorParents": {
                    "target": parent_identity_payload(target, allow_missing=True),
                },
                "commitCleanupPaths": [str(original)],
            }
        if action == "remove_tree":
            original = self._step_temporary_path(index, operation, "removed", sibling_of=target)
            existed = path_is_dir(target)
            identity = path_identity(target) if existed else None
            if existed and identity is None:
                raise TransactionError(
                    "transaction.path_identity_changed",
                    "Removal tree disappeared while recording transaction intent",
                    operation=operation["id"],
                    path=str(target),
                )
            return {
                "kind": "restore-moved-tree",
                "target": str(target),
                "original": str(original),
                "existed": existed,
                "marker": self._marker_relative(operation).as_posix(),
                "markerSha256": self._required_sha256(operation, "sourceMarkerSha256"),
                "expectedTreeSha256": self._required_sha256(
                    operation,
                    "sourceTreeSha256",
                ),
                "targetIdentity": identity.to_payload() if identity is not None else None,
                "phase": "intent",
                "descriptorParents": {
                    "target": parent_identity_payload(target, allow_missing=True),
                },
                "commitCleanupPaths": [str(original)],
            }
        raise TransactionError(
            "transaction.action_unsupported",
            "Transaction action has no undo implementation",
            operation=operation["id"],
            action=action,
        )

    def _apply_file_replacement(
        self,
        operation: dict[str, Any],
        source: pathlib.Path,
        target: pathlib.Path,
        undo: dict[str, Any],
        journal: dict[str, Any],
    ) -> None:
        original = pathlib.Path(undo["original"])
        staging = pathlib.Path(undo["staging"])
        rollback_discard = pathlib.Path(undo["rollbackDiscard"])
        if path_lexists(original) or path_lexists(staging) or path_lexists(rollback_discard):
            raise TransactionError(
                "transaction.temporary_exists",
                "File transaction temporary path already exists",
                operation=operation["id"],
            )
        copy_file_staged(source, staging)
        if sha256_file(staging) != undo["appliedSha256"]:
            raise TransactionError(
                "transaction.write_mismatch",
                "Staged file does not match its source",
                operation=operation["id"],
                path=str(staging),
            )
        undo["phase"] = "staged"
        self._save_journal(journal)
        self._inject(operation["id"], "after-file-staged")
        rename_exclusive(target, original)
        if sha256_file(original) != undo["originalSha256"]:
            with contextlib.suppress(TransactionError):
                rename_exclusive(original, target)
            raise TransactionError(
                "transaction.target_changed",
                "File target changed after transaction intent",
                operation=operation["id"],
                path=str(target),
            )
        undo["phase"] = "original-moved"
        self._save_journal(journal)
        self._inject(operation["id"], "after-file-original-moved")
        rename_exclusive(staging, target)
        if sha256_file(target) != undo["appliedSha256"]:
            raise TransactionError(
                "transaction.write_mismatch",
                "Installed file does not match its source",
                operation=operation["id"],
                path=str(target),
            )
        undo["phase"] = "replacement-installed"
        self._save_journal(journal)
        self._inject(operation["id"], "after-file-replacement-installed")

    def _apply_operation(
        self,
        operation: dict[str, Any],
        undo: dict[str, Any],
        journal: dict[str, Any],
    ) -> None:
        self._preflight_operation(operation)
        action = operation["action"]
        target = self._mutable_path(operation, "target")
        if action in {
            "assert_sha256",
            "assert_absent",
            "assert_absent_or_owned",
            "retain",
            "retain_tree",
        }:
            return
        self._require_undo_descriptor_parents(operation, undo)
        if action == "backup":
            if not undo["created"]:
                return
            backup = self._mutable_path(operation, "backup")
            if path_lexists(backup):
                raise TransactionError(
                    "transaction.target_exists",
                    "Backup target appeared after transaction intent",
                    operation=operation["id"],
                    path=str(backup),
                )
            copy_file_atomic(target, backup, pathlib.Path(undo["staging"]))
            if sha256_file(backup) != undo["backupSha256"]:
                raise TransactionError(
                    "transaction.write_mismatch",
                    "Backup does not match its source",
                    operation=operation["id"],
                    path=str(backup),
                )
            return
        if action == "replace_file":
            source = self._verify_source_file(operation)
            self._apply_file_replacement(operation, source, target, undo, journal)
            return
        if action == "create_file":
            source = self._verify_source_file(operation)
            copy_file_atomic(source, target, pathlib.Path(undo["staging"]))
            if sha256_file(target) != undo["appliedSha256"]:
                raise TransactionError(
                    "transaction.write_mismatch",
                    "Installed file does not match its source",
                    operation=operation["id"],
                    path=str(target),
                )
            return
        if action == "replace_tree":
            source = self._verify_source_tree(operation)
            if tree_sha256(source) != undo["appliedTreeSha256"]:
                raise TransactionError(
                    "transaction.source_mismatch",
                    "Tree source changed after transaction intent",
                    operation=operation["id"],
                    path=str(source),
                )
            original = pathlib.Path(undo["original"])
            staging = pathlib.Path(undo["staging"])
            rollback_discard = pathlib.Path(undo["rollbackDiscard"])
            if (
                path_lexists(original)
                or path_lexists(staging)
                or path_lexists(rollback_discard)
            ):
                raise TransactionError(
                    "transaction.temporary_exists",
                    "Tree transaction temporary path already exists",
                    operation=operation["id"],
                )
            tree_publication = undo["treePublication"]
            publication_staging = original if tree_publication == "exchange" else staging
            copy_tree_staged(source, publication_staging)
            if tree_sha256(publication_staging) != undo["appliedTreeSha256"]:
                raise TransactionError(
                    "transaction.write_mismatch",
                    "Staged tree does not match its source",
                    operation=operation["id"],
                    path=str(publication_staging),
                )
            undo["phase"] = "staged"
            self._save_journal(journal)
            self._inject(operation["id"], "after-tree-staged")
            if tree_publication == "exchange":
                if not path_is_dir(target) or tree_sha256(target) != undo["originalTreeSha256"]:
                    raise TransactionError(
                        "transaction.target_changed",
                        "Tree target changed before atomic exchange",
                        operation=operation["id"],
                        path=str(target),
                    )
                rename_exchange(target, original)
                self._inject(operation["id"], "after-tree-exchanged")
                if (
                    tree_sha256(target) != undo["appliedTreeSha256"]
                    or tree_sha256(original) != undo["originalTreeSha256"]
                ):
                    with contextlib.suppress(TransactionError):
                        rename_exchange(target, original)
                    raise TransactionError(
                        "transaction.target_changed",
                        "Atomic tree exchange published unexpected content",
                        operation=operation["id"],
                        path=str(target),
                    )
                undo["phase"] = "replacement-installed"
                self._save_journal(journal)
                self._inject(operation["id"], "after-tree-replacement-installed")
                return
            if path_lexists(target):
                rename_exclusive(target, original)
                if tree_sha256(original) != undo["originalTreeSha256"]:
                    with contextlib.suppress(TransactionError):
                        rename_exclusive(original, target)
                    raise TransactionError(
                        "transaction.target_changed",
                        "Tree target changed after transaction intent",
                        operation=operation["id"],
                        path=str(target),
                    )
                undo["phase"] = "original-moved"
                self._save_journal(journal)
                self._inject(operation["id"], "after-tree-original-moved")
            rename_exclusive(staging, target)
            if tree_sha256(target) != undo["appliedTreeSha256"]:
                raise TransactionError(
                    "transaction.write_mismatch",
                    "Installed tree does not match its source",
                    operation=operation["id"],
                    path=str(target),
                )
            undo["phase"] = "replacement-installed"
            self._save_journal(journal)
            self._inject(operation["id"], "after-tree-replacement-installed")
            return
        if action == "restore":
            backup = self._mutable_path(operation, "backup")
            expected = self._required_sha256(operation, "expectedSha256")
            if path_is_file(target) and sha256_file(target) == expected:
                return
            self._apply_file_replacement(operation, backup, target, undo, journal)
            return
        if action in {"remove", "remove_tree"}:
            if not path_lexists(target):
                return
            if not undo["existed"]:
                raise TransactionError(
                    "transaction.target_changed",
                    "Removal target appeared after transaction intent",
                    operation=operation["id"],
                    path=str(target),
                )
            original = pathlib.Path(undo["original"])
            if path_lexists(original):
                raise TransactionError(
                    "transaction.temporary_exists",
                    "Removal undo path already exists",
                    operation=operation["id"],
                    path=str(original),
                )
            expected_identity = _descriptor_result(
                lambda: runtime_descriptor.FileIdentity.from_payload(
                    undo["targetIdentity"],
                    field=f"{operation['id']}.targetIdentity",
                )
            )
            try:
                rename_exclusive(target, original)
                require_path_identity(original, expected_identity)
                if action == "remove":
                    if not path_is_file(original) or sha256_file(original) != undo["expectedSha256"]:
                        raise TransactionError(
                            "transaction.target_changed",
                            "Removal target changed after transaction intent",
                            operation=operation["id"],
                            path=str(target),
                        )
                else:
                    self._validate_tree_payload(
                        original,
                        undo["marker"],
                        undo["markerSha256"],
                        undo["expectedTreeSha256"],
                    )
            except Exception as error:
                restored = False
                if not path_lexists(target) and path_lexists(original):
                    try:
                        rename_exclusive(original, target)
                        restored = True
                    except TransactionError:
                        pass
                if restored:
                    undo["phase"] = "restored-after-change"
                    self._save_journal(journal)
                raise TransactionError(
                    "transaction.target_changed",
                    "Removal target changed after transaction intent",
                    operation=operation["id"],
                    path=str(target),
                ) from error
            undo["phase"] = "moved"
            self._save_journal(journal)
            return
        raise TransactionError(
            "transaction.action_unsupported",
            "Transaction action has no apply implementation",
            operation=operation["id"],
            action=action,
        )

    def _rollback(self, journal: dict[str, Any]) -> None:
        failures: list[dict[str, Any]] = []
        for step in reversed(journal["steps"]):
            if step["status"] not in {
                "applying",
                "applied",
                "rolling-back",
                "rollback-failed",
            }:
                continue
            step["status"] = "rolling-back"
            journal["currentStep"] = step["id"]
            self._save_journal(journal)
            try:
                self._inject(step["id"], "before-rollback")
                self._undo_step(step, journal)
                step["status"] = "rolled-back"
                self._save_journal(journal)
                self._inject(step["id"], "after-rollback")
            except Exception as error:
                failure = {"operation": step["id"], **error_payload(error)}
                failures.append(failure)
                step["status"] = "rollback-failed"
                self._save_journal(journal)
        journal["currentStep"] = None
        journal["rollbackFailures"] = failures
        journal["state"] = "failed" if failures else "rolled-back"
        self._save_journal(journal)
        if not failures:
            self._cleanup_payloads(journal)
            self._save_journal(journal)

    def _validate_tree_marker(
        self,
        path: pathlib.Path,
        marker: str,
        expected_sha256: str,
    ) -> None:
        if not path_is_dir(path):
            raise TransactionError(
                "transaction.undo_foreign",
                "Rollback tree is not a directory",
                path=str(path),
            )
        reject_tree_symlinks(path)
        marker_path = path.joinpath(*pathlib.PurePosixPath(marker).parts)
        if (
            not path_is_file(marker_path)
            or sha256_file(marker_path) != expected_sha256
        ):
            raise TransactionError(
                "transaction.undo_foreign",
                "Rollback tree ownership marker does not match",
                path=str(path),
            )

    def _validate_tree_payload(
        self,
        path: pathlib.Path,
        marker: str,
        marker_sha256: str,
        tree_digest: str,
    ) -> None:
        self._validate_tree_marker(path, marker, marker_sha256)
        actual = tree_sha256(path)
        if actual != tree_digest:
            raise TransactionError(
                "transaction.undo_foreign",
                "Rollback tree content does not match the journal",
                path=str(path),
                expected=tree_digest,
                actual=actual,
            )

    def _undo_step(self, step: dict[str, Any], journal: dict[str, Any]) -> None:
        undo = step["undo"]
        kind = undo["kind"]
        if kind == "none":
            return
        operation = next(item for item in self.operations if item["id"] == step["id"])
        self._require_undo_descriptor_parents(operation, undo)

        def remove_verified(path: pathlib.Path) -> None:
            self._remove_verified_payload(journal, undo, path)

        if kind == "remove-backup":
            staging = pathlib.Path(undo["staging"])
            remove_verified(staging)
            if not undo["created"]:
                return
            backup = pathlib.Path(undo["path"])
            if path_lexists(backup):
                if (
                    not path_is_file(backup)
                    or sha256_file(backup) != undo["backupSha256"]
                ):
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Rollback refuses to remove a foreign backup",
                        path=str(backup),
                    )
                remove_verified(backup)
            return
        if kind == "restore-file":
            target = pathlib.Path(undo["target"])
            original = pathlib.Path(undo["original"])
            staging = pathlib.Path(undo["staging"])
            rollback_discard = pathlib.Path(undo["rollbackDiscard"])
            if path_lexists(staging):
                if not path_is_file(staging) or sha256_file(staging) != undo["appliedSha256"]:
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "File staging payload changed before rollback",
                        path=str(staging),
                    )
                remove_verified(staging)
            if path_lexists(original):
                if not path_is_file(original) or sha256_file(original) != undo["originalSha256"]:
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "File rollback original payload changed",
                        path=str(original),
                    )
                if path_lexists(target):
                    if not path_is_file(target) or sha256_file(target) != undo["appliedSha256"]:
                        raise TransactionError(
                            "transaction.undo_foreign",
                            "Rollback refuses to move a foreign file target",
                            path=str(target),
                        )
                    if path_lexists(rollback_discard):
                        raise TransactionError(
                            "transaction.undo_foreign",
                            "File rollback discard already exists",
                            path=str(rollback_discard),
                        )
                    rename_exclusive(target, rollback_discard)
                    undo["phase"] = "rollback-target-moved"
                    self._save_journal(journal)
                rename_exclusive(original, target)
                undo["phase"] = "rollback-restored"
                self._save_journal(journal)
            elif undo["phase"] == "rollback-restored":
                if not path_is_file(target) or sha256_file(target) != undo["originalSha256"]:
                    raise TransactionError(
                        "transaction.undo_missing",
                        "Restored rollback file is missing or changed",
                        path=str(target),
                    )
            elif path_lexists(rollback_discard) and path_lexists(target):
                if not path_is_file(target) or sha256_file(target) != undo["originalSha256"]:
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Restored rollback file changed",
                        path=str(target),
                    )
                undo["phase"] = "rollback-restored"
                self._save_journal(journal)
            elif (
                undo["phase"] in {"intent", "staged"}
                and path_is_file(target)
                and sha256_file(target) == undo["originalSha256"]
                and not path_lexists(rollback_discard)
            ):
                undo["phase"] = "rollback-restored"
                self._save_journal(journal)
            elif path_lexists(target):
                raise TransactionError(
                    "transaction.undo_foreign",
                    "Rollback file target is foreign",
                    path=str(target),
                )
            else:
                raise TransactionError(
                    "transaction.undo_missing",
                    "File rollback original payload is missing",
                    path=str(original),
                )
            if path_lexists(rollback_discard):
                if (
                    not path_is_file(rollback_discard)
                    or sha256_file(rollback_discard) != undo["appliedSha256"]
                ):
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "File rollback discard payload changed",
                        path=str(rollback_discard),
                    )
                remove_verified(rollback_discard)
            return
        if kind == "remove-created-file":
            staging = pathlib.Path(undo["staging"])
            remove_verified(staging)
            target = pathlib.Path(undo["target"])
            if path_lexists(target):
                if (
                    not path_is_file(target)
                    or sha256_file(target) != undo["appliedSha256"]
                ):
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Rollback refuses to remove a foreign created file",
                        path=str(target),
                    )
                remove_verified(target)
            return
        if kind == "restore-tree":
            target = pathlib.Path(undo["target"])
            original = pathlib.Path(undo["original"])
            staging = pathlib.Path(undo["staging"])
            rollback_discard = pathlib.Path(undo["rollbackDiscard"])
            remove_verified(staging)
            if undo["treePublication"] == "exchange":
                remove_verified(rollback_discard)
                if undo["phase"] == "rollback-restored":
                    self._validate_tree_payload(
                        target,
                        undo["marker"],
                        undo["markerSha256"],
                        undo["originalTreeSha256"],
                    )
                    self._remove_verified_payload(
                        journal,
                        undo,
                        original,
                        logical_path=staging,
                    )
                    return
                if not path_is_dir(target) or not path_is_dir(original):
                    raise TransactionError(
                        "transaction.undo_missing",
                        "Atomic tree rollback requires both exchange paths",
                        target=str(target),
                        original=str(original),
                    )
                target_tree = tree_sha256(target)
                original_tree = tree_sha256(original)
                if (
                    target_tree == undo["appliedTreeSha256"]
                    and original_tree == undo["originalTreeSha256"]
                ):
                    rename_exchange(target, original)
                    self._inject(step["id"], "after-tree-rollback-exchanged")
                elif not (
                    target_tree == undo["originalTreeSha256"]
                    and original_tree == undo["appliedTreeSha256"]
                ):
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Atomic tree rollback paths contain unexpected content",
                        target=str(target),
                        original=str(original),
                    )
                self._validate_tree_payload(
                    target,
                    undo["marker"],
                    undo["markerSha256"],
                    undo["originalTreeSha256"],
                )
                self._validate_tree_payload(
                    original,
                    undo["marker"],
                    undo["markerSha256"],
                    undo["appliedTreeSha256"],
                )
                undo["phase"] = "rollback-restored"
                self._save_journal(journal)
                self._remove_verified_payload(
                    journal,
                    undo,
                    original,
                    logical_path=staging,
                )
                return
            if undo["existed"]:
                if path_lexists(original):
                    self._validate_tree_payload(
                        original,
                        undo["marker"],
                        undo["markerSha256"],
                        undo["originalTreeSha256"],
                    )
                    if path_lexists(target):
                        self._validate_tree_payload(
                            target,
                            undo["marker"],
                            undo["markerSha256"],
                            undo["appliedTreeSha256"],
                        )
                        if path_lexists(rollback_discard):
                            raise TransactionError(
                                "transaction.undo_foreign",
                                "Tree rollback discard already exists",
                                path=str(rollback_discard),
                            )
                        rename_exclusive(target, rollback_discard)
                        undo["phase"] = "rollback-target-moved"
                        self._save_journal(journal)
                    rename_exclusive(original, target)
                    undo["phase"] = "rollback-restored"
                    self._save_journal(journal)
                    remove_verified(rollback_discard)
                elif undo["phase"] == "rollback-restored":
                    if not path_lexists(target):
                        raise TransactionError(
                            "transaction.undo_missing",
                            "Restored tree target is missing",
                            path=str(target),
                        )
                    self._validate_tree_payload(
                        target,
                        undo["marker"],
                        undo["markerSha256"],
                        undo["originalTreeSha256"],
                    )
                    remove_verified(rollback_discard)
                elif path_lexists(rollback_discard) and path_lexists(target):
                    self._validate_tree_payload(
                        target,
                        undo["marker"],
                        undo["markerSha256"],
                        undo["originalTreeSha256"],
                    )
                    undo["phase"] = "rollback-restored"
                    self._save_journal(journal)
                    remove_verified(rollback_discard)
                elif (
                    undo["phase"] in {"intent", "staged"}
                    and path_lexists(target)
                    and not path_lexists(rollback_discard)
                ):
                    self._validate_tree_payload(
                        target,
                        undo["marker"],
                        undo["markerSha256"],
                        undo["originalTreeSha256"],
                    )
                    undo["phase"] = "rollback-restored"
                    self._save_journal(journal)
                else:
                    raise TransactionError(
                        "transaction.undo_missing",
                        "Tree rollback original payload is missing",
                        path=str(original),
                    )
            elif undo["phase"] == "rollback-restored":
                if path_lexists(target):
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Created tree reappeared after rollback",
                        path=str(target),
                    )
                remove_verified(rollback_discard)
            elif path_lexists(target):
                if path_lexists(rollback_discard):
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Tree rollback target and discard both exist",
                        path=str(target),
                    )
                if undo["phase"] == "intent":
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Tree target appeared before transaction staging",
                        path=str(target),
                    )
                self._validate_tree_payload(
                    target,
                    undo["marker"],
                    undo["markerSha256"],
                    undo["appliedTreeSha256"],
                )
                rename_exclusive(target, rollback_discard)
                undo["phase"] = "rollback-target-moved"
                self._save_journal(journal)
                undo["phase"] = "rollback-restored"
                self._save_journal(journal)
                remove_verified(rollback_discard)
            elif path_lexists(rollback_discard):
                undo["phase"] = "rollback-restored"
                self._save_journal(journal)
                remove_verified(rollback_discard)
            elif undo["phase"] in {"intent", "staged"}:
                undo["phase"] = "rollback-restored"
                self._save_journal(journal)
            else:
                raise TransactionError(
                    "transaction.undo_missing",
                    "Created tree rollback payload is missing",
                    path=str(target),
                )
            return
        if kind in {"restore-moved-file", "restore-moved-tree"}:
            if not undo["existed"]:
                return
            target = pathlib.Path(undo["target"])
            original = pathlib.Path(undo["original"])
            if undo["phase"] == "restored-after-change":
                if path_lexists(original) or not path_lexists(target):
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Changed removal target was not preserved at its original path",
                        path=str(target),
                    )
                return
            expected_identity = _descriptor_result(
                lambda: runtime_descriptor.FileIdentity.from_payload(
                    undo["targetIdentity"],
                    field=f"{step['id']}.targetIdentity",
                )
            )
            if path_lexists(original):
                require_path_identity(original, expected_identity)
                if kind == "restore-moved-file":
                    if (
                        not path_is_file(original)
                        or sha256_file(original) != undo["expectedSha256"]
                    ):
                        raise TransactionError(
                            "transaction.undo_foreign",
                            "Removed file undo payload changed",
                            path=str(original),
                        )
                else:
                    self._validate_tree_payload(
                        original,
                        undo["marker"],
                        undo["markerSha256"],
                        undo["expectedTreeSha256"],
                    )
                if path_lexists(target):
                    raise TransactionError(
                        "transaction.undo_foreign",
                        "Rollback target was recreated before restoration",
                        path=str(target),
                    )
                rename_exclusive(original, target)
                return
            if path_lexists(target):
                require_path_identity(target, expected_identity)
                if kind == "restore-moved-file":
                    if (
                        not path_is_file(target)
                        or sha256_file(target) != undo["expectedSha256"]
                    ):
                        raise TransactionError(
                            "transaction.undo_foreign",
                            "Restored removal target is foreign",
                            path=str(target),
                        )
                else:
                    self._validate_tree_payload(
                        target,
                        undo["marker"],
                        undo["markerSha256"],
                        undo["expectedTreeSha256"],
                    )
                return
            raise TransactionError(
                "transaction.undo_missing",
                "Removed-path undo payload is missing",
                path=str(original),
            )
        raise TransactionError(
            "transaction.journal_invalid",
            "Journal contains an unknown undo action",
            undoKind=kind,
        )
