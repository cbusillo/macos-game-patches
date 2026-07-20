"""Artifact-aware install and uninstall coordination for the Mac ALVR runtime."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import stat
from dataclasses import dataclass
from typing import Any, Callable, Literal, Sequence, cast

import build_runtime_artifact as artifact_contract
import runtime_descriptor
import runtime_transaction
from runtime_control import (
    LIVE_STATES,
    ControlError,
    RuntimeContext,
    doctor_runtime,
    global_lifecycle_lock,
    load_runtime_contract,
    status_runtime,
    stop_runtime,
    verify_artifact_reference,
)


MutationKind = Literal["install", "uninstall"]
CAPACITY_OVERHEAD_BYTES = 1024 * 1024
CAPACITY_ENTRY_BYTES = 4096
INCOMPLETE_TRANSACTION_STATES = frozenset({"prepared", "running", "rolling-back"})
TRANSACTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
INSTALL_CONTAINER_IDS = (
    "backup_root",
    "runtime_state",
    "bridge_bundle_root",
)


class RuntimeInstallError(Exception):
    """Stable machine-readable lifecycle coordination failure."""

    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context


@dataclass(frozen=True)
class CapacityCheck:
    path: pathlib.Path
    device: int
    required_bytes: int
    available_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "device": self.device,
            "requiredBytes": self.required_bytes,
            "availableBytes": self.available_bytes,
        }


@dataclass(frozen=True)
class MutationReport:
    command: MutationKind
    ok: bool
    state: str
    reason_code: str
    message: str
    artifact: dict[str, Any] | None = None
    plan_digest: str | None = None
    blockers: tuple[dict[str, Any], ...] = ()
    journal: pathlib.Path | None = None
    archived_journal: pathlib.Path | None = None
    transaction_id: str | None = None
    applied: tuple[str, ...] = ()
    rolled_back: tuple[str, ...] = ()
    failure: dict[str, Any] | None = None
    rollback_failures: tuple[dict[str, Any], ...] = ()
    cleanup_failures: tuple[str, ...] = ()
    recovered: bool = False
    stop_actions: tuple[str, ...] = ()
    capacity: tuple[CapacityCheck, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "command": self.command,
            "ok": self.ok,
            "state": self.state,
            "reasonCode": self.reason_code,
            "message": self.message,
            "artifact": self.artifact,
            "planDigest": self.plan_digest,
            "blockers": list(self.blockers),
            "journal": str(self.journal) if self.journal is not None else None,
            "archivedJournal": (
                str(self.archived_journal) if self.archived_journal is not None else None
            ),
            "transactionId": self.transaction_id,
            "applied": list(self.applied),
            "rolledBack": list(self.rolled_back),
            "failure": self.failure,
            "rollbackFailures": list(self.rollback_failures),
            "cleanupFailures": list(self.cleanup_failures),
            "recovered": self.recovered,
            "stopActions": list(self.stop_actions),
            "capacity": [check.to_dict() for check in self.capacity],
        }


@dataclass(frozen=True)
class LifecyclePaths:
    lifecycle_root: pathlib.Path
    transaction_root: pathlib.Path
    history_root: pathlib.Path
    journal: pathlib.Path
    global_lock: pathlib.Path
    journal_lock: pathlib.Path
    undo_root: pathlib.Path
    allowed_roots: tuple[pathlib.Path, ...]


def _absolute(path: pathlib.Path | str) -> pathlib.Path:
    return pathlib.Path(os.path.abspath(pathlib.Path(path).expanduser()))


def _artifact_error(error: artifact_contract.ArtifactError) -> RuntimeInstallError:
    return RuntimeInstallError(error.code, error.message, **error.context)


def _descriptor_error(error: runtime_descriptor.DescriptorError) -> RuntimeInstallError:
    return RuntimeInstallError(error.code, error.message, **error.context)


def _ensure_allowed(
    path: pathlib.Path,
    allowed_roots: Sequence[pathlib.Path],
    operation: str,
) -> None:
    try:
        artifact_contract.ensure_target_allowed(path, list(allowed_roots), operation)
    except artifact_contract.ArtifactError as error:
        raise _artifact_error(error) from error


def _validate_private_directory(path: pathlib.Path) -> None:
    try:
        with runtime_descriptor.DescriptorSession([path]) as session:
            metadata = session.bind(path).lstat()
    except runtime_descriptor.DescriptorError as error:
        raise _descriptor_error(error) from error
    assert metadata is not None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeInstallError(
            "transaction.path_unsafe",
            "Runtime-owned path is not a regular directory",
            path=str(path),
        )
    if metadata.st_uid != os.getuid():
        raise RuntimeInstallError(
            "transaction.owner_mismatch",
            "Runtime-owned directory belongs to another user",
            path=str(path),
            owner=metadata.st_uid,
            expectedOwner=os.getuid(),
        )
    mode = stat.S_IMODE(metadata.st_mode)
    if mode != 0o700:
        raise RuntimeInstallError(
            "transaction.mode_unsafe",
            "Runtime-owned directory must use mode 0700",
            path=str(path),
            mode=f"{mode:04o}",
        )


def ensure_private_directory(
    path: pathlib.Path,
    allowed_roots: Sequence[pathlib.Path],
    operation: str,
) -> None:
    path = _absolute(path)
    _ensure_allowed(path, allowed_roots, operation)
    roots = [_absolute(root) for root in allowed_roots]
    candidates = [root for root in roots if path == root or path.is_relative_to(root)]
    if not candidates:
        raise RuntimeInstallError(
            "transaction.path_unsafe",
            "Runtime-owned directory is outside its allowed roots",
            path=str(path),
        )
    authority = max(candidates, key=lambda item: len(item.parts))
    try:
        runtime_descriptor.ensure_private_directory(
            path,
            authority,
            owner_uid=os.getuid(),
        )
    except runtime_descriptor.DescriptorError as error:
        raise _descriptor_error(error) from error


def _validate_private_file(path: pathlib.Path, mode: int) -> None:
    try:
        with runtime_descriptor.DescriptorSession([path.parent]) as session:
            metadata = session.bind(path).lstat()
    except runtime_descriptor.DescriptorError as error:
        raise _descriptor_error(error) from error
    if metadata is None:
        raise RuntimeInstallError(
            "transaction.path_unsafe",
            "Runtime-owned metadata file is missing",
            path=str(path),
        )
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeInstallError(
            "transaction.path_unsafe",
            "Runtime-owned metadata path is not a regular file",
            path=str(path),
        )
    if metadata.st_uid != os.getuid():
        raise RuntimeInstallError(
            "transaction.owner_mismatch",
            "Runtime-owned metadata file belongs to another user",
            path=str(path),
            owner=metadata.st_uid,
            expectedOwner=os.getuid(),
        )
    actual_mode = stat.S_IMODE(metadata.st_mode)
    if actual_mode != mode:
        raise RuntimeInstallError(
            "transaction.mode_unsafe",
            f"Runtime-owned metadata file must use mode {mode:04o}",
            path=str(path),
            mode=f"{actual_mode:04o}",
        )


def _mutable_state_index(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = plan.get("mutableState")
    if not isinstance(records, list):
        raise RuntimeInstallError(
            "plan.invalid",
            "Resolved plan is missing mutable state records",
        )
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise RuntimeInstallError(
                "plan.invalid",
                "Resolved mutable state record is invalid",
            )
        if record["id"] in index:
            raise RuntimeInstallError(
                "plan.invalid",
                "Resolved mutable state contains a duplicate id",
                itemId=record["id"],
            )
        index[record["id"]] = record
    return index


def _state_path(index: dict[str, dict[str, Any]], item_id: str) -> pathlib.Path:
    record = index.get(item_id)
    if record is None or not isinstance(record.get("location"), str):
        raise RuntimeInstallError(
            "repository.contract",
            "Runtime manifest is missing lifecycle mutable state",
            itemId=item_id,
        )
    path = pathlib.Path(record["location"])
    if not path.is_absolute():
        raise RuntimeInstallError(
            "plan.invalid",
            "Resolved lifecycle path must be absolute",
            itemId=item_id,
            path=str(path),
        )
    return _absolute(path)


def lifecycle_paths(plan: dict[str, Any]) -> LifecyclePaths:
    raw_allowed = plan.get("allowedTargetRoots")
    if not isinstance(raw_allowed, list) or not raw_allowed:
        raise RuntimeInstallError(
            "plan.invalid",
            "Resolved plan is missing allowed target roots",
        )
    allowed_roots = tuple(_absolute(root) for root in raw_allowed if isinstance(root, str))
    if len(allowed_roots) != len(raw_allowed):
        raise RuntimeInstallError(
            "plan.invalid",
            "Resolved plan contains a non-path target root",
        )
    state = _mutable_state_index(plan)
    paths = LifecyclePaths(
        lifecycle_root=_state_path(state, "lifecycle_root"),
        transaction_root=_state_path(state, "transaction_root"),
        history_root=_state_path(state, "transaction_history"),
        journal=_state_path(state, "transaction_journal"),
        global_lock=_state_path(state, "transaction_lock"),
        journal_lock=_state_path(state, "transaction_journal_lock"),
        undo_root=_state_path(state, "transaction_undo"),
        allowed_roots=allowed_roots,
    )
    expected_journal_lock = paths.journal.with_suffix(paths.journal.suffix + ".lock")
    expected_undo = paths.journal.with_name(f"{paths.journal.name}.undo")
    relationships = {
        "transaction_root": paths.transaction_root.parent == paths.lifecycle_root,
        "transaction_history": paths.history_root.parent == paths.transaction_root,
        "transaction_journal": paths.journal.parent == paths.transaction_root,
        "transaction_lock": paths.global_lock.parent == paths.lifecycle_root,
        "transaction_journal_lock": paths.journal_lock == expected_journal_lock,
        "transaction_undo": paths.undo_root == expected_undo,
    }
    invalid = [item_id for item_id, valid in relationships.items() if not valid]
    if invalid:
        raise RuntimeInstallError(
            "repository.contract",
            "Lifecycle paths do not share the declared transaction layout",
            items=invalid,
        )
    for item_id, path in (
        ("lifecycle_root", paths.lifecycle_root),
        ("transaction_root", paths.transaction_root),
        ("transaction_history", paths.history_root),
        ("transaction_journal", paths.journal),
        ("transaction_lock", paths.global_lock),
        ("transaction_journal_lock", paths.journal_lock),
        ("transaction_undo", paths.undo_root),
    ):
        _ensure_allowed(path, paths.allowed_roots, item_id)
    return paths


def _build_plan(
    context: RuntimeContext,
    artifact: pathlib.Path,
    manifest: dict[str, Any],
    manifest_hash: str,
    lock_hash: str,
) -> dict[str, Any]:
    try:
        return artifact_contract.build_plan(
            manifest,
            artifact,
            context.bindings_path,
            expected_manifest_hash=manifest_hash,
            expected_lock_hash=lock_hash,
        )
    except artifact_contract.ArtifactError as error:
        raise _artifact_error(error) from error


def _refresh_plan_readiness(plan: dict[str, Any], kind: MutationKind) -> None:
    operations = plan.get(kind)
    if not isinstance(operations, list):
        raise RuntimeInstallError(
            "plan.invalid",
            "Resolved plan is missing lifecycle operations",
            kind=kind,
        )
    blockers = [
        operation["id"]
        for operation in operations
        if isinstance(operation, dict)
        and isinstance(operation.get("id"), str)
        and operation.get("ready") is not True
    ]
    sealing_blockers = ["artifact.sealing_required"] if plan.get("requiresSealing") is not False else []
    plan[f"{kind}Ready"] = not sealing_blockers and not blockers
    plan[f"{kind}Blockers"] = [*sealing_blockers, *blockers]


def _inspect_stable_bundle(
    bundle: pathlib.Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return artifact_contract.inspect_signed_bundle(bundle, manifest)


def _qualify_stable_bundles(
    plan: dict[str, Any],
    manifest: dict[str, Any],
    *,
    bundle_inspector: Callable[[pathlib.Path, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    operations = plan.get("install")
    if not isinstance(operations, list):
        raise RuntimeInstallError(
            "plan.invalid",
            "Resolved plan is missing lifecycle operations",
            kind="install",
        )
    inspector = bundle_inspector or _inspect_stable_bundle
    for operation in operations:
        if not isinstance(operation, dict):
            raise RuntimeInstallError(
                "plan.invalid",
                "Resolved lifecycle operation is invalid",
            )
        if (
            operation.get("action") != "assert_absent_or_owned"
            or operation.get("ownershipPolicy") != "developer-id-bundle"
            or operation.get("ownership") != "qualification-required"
        ):
            continue
        target_value = operation.get("target")
        observed_tree = operation.get("targetTreeSha256")
        if not isinstance(target_value, str) or not isinstance(observed_tree, str):
            raise RuntimeInstallError(
                "plan.invalid",
                "Developer ID bundle qualification is missing its target identity",
                operation=operation.get("id"),
            )
        target = _absolute(target_value)
        try:
            evidence = inspector(target, manifest)
        except artifact_contract.ArtifactError as error:
            operation["ownership"] = "foreign"
            operation["ready"] = False
            operation["blockedReason"] = f"{error.code}: {error.message}"
            operation["ownershipFailure"] = {
                "code": error.code,
                "message": error.message,
                "context": error.context,
            }
            continue
        confirmed_tree = artifact_contract.canonical_tree_sha256(target)
        if confirmed_tree != observed_tree:
            operation["ownership"] = "changed"
            operation["ready"] = False
            operation["blockedReason"] = (
                "existing tree changed during Developer ID lifecycle qualification"
            )
            continue
        operation["ownership"] = "managed-signed-prior"
        operation["qualifiedTargetTreeSha256"] = observed_tree
        operation["ownershipEvidence"] = evidence
        operation["ready"] = True
        operation.pop("blockedReason", None)
    _refresh_plan_readiness(plan, "install")
    return plan


def _executor(
    kind: MutationKind,
    plan: dict[str, Any],
    paths: LifecyclePaths,
    *,
    tree_ownership_validator: runtime_transaction.TreeOwnershipValidator | None = None,
) -> runtime_transaction.TransactionExecutor:
    operations = plan.get(kind)
    if not isinstance(operations, list):
        raise RuntimeInstallError(
            "plan.invalid",
            "Resolved plan is missing lifecycle operations",
            kind=kind,
        )
    artifact = plan.get("artifact")
    if not isinstance(artifact, str):
        raise RuntimeInstallError("plan.invalid", "Resolved plan is missing its artifact path")
    return runtime_transaction.TransactionExecutor(
        kind=kind,
        operations=operations,
        artifact_root=pathlib.Path(artifact),
        journal_path=paths.journal,
        transaction_root=paths.transaction_root,
        allowed_roots=paths.allowed_roots,
        tree_ownership_validator=tree_ownership_validator,
    )


def _read_active_journal(paths: LifecyclePaths) -> dict[str, Any] | None:
    try:
        with runtime_descriptor.DescriptorSession([paths.transaction_root]) as session:
            journal = session.bind(paths.journal)
            if not journal.exists():
                return None
            payload = journal.read_bytes()
    except runtime_descriptor.DescriptorError as error:
        if error.code in {"transaction.parent_missing", "transaction.root_missing"}:
            return None
        raise _descriptor_error(error) from error
    _validate_private_file(paths.journal, 0o600)
    try:
        value = json.loads(
            payload,
            object_pairs_hook=artifact_contract.reject_duplicate_keys,
            parse_constant=artifact_contract.reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, artifact_contract.ArtifactError) as error:
        raise RuntimeInstallError(
            "transaction.journal_invalid",
            "Active lifecycle journal is not valid JSON",
            detail=str(error),
        ) from error
    if not isinstance(value, dict):
        raise RuntimeInstallError(
            "transaction.journal_invalid",
            "Active lifecycle journal must be a JSON object",
            path=str(paths.journal),
        )
    return value


def _archive_terminal_journal(
    paths: LifecyclePaths,
    executor: runtime_transaction.TransactionExecutor,
) -> pathlib.Path:
    ensure_private_directory(paths.history_root, paths.allowed_roots, "transaction_history")
    destination = executor.archive_terminal(paths.history_root)
    _validate_private_file(destination, 0o600)
    return destination


def _archive_prior_committed_journal(
    paths: LifecyclePaths,
    journal: dict[str, Any],
) -> pathlib.Path:
    expected_keys = {
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
    transaction_id = journal.get("transactionId")
    plan_digest = journal.get("planDigest")
    steps = journal.get("steps")
    if (
        set(journal) != expected_keys
        or journal.get("schemaVersion") != 2
        or journal.get("kind") not in {"install", "uninstall"}
        or journal.get("state") != "committed"
        or not isinstance(transaction_id, str)
        or TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is None
        or not isinstance(plan_digest, str)
        or SHA256_PATTERN.fullmatch(plan_digest) is None
        or not isinstance(journal.get("createdAt"), str)
        or not journal["createdAt"]
        or not isinstance(journal.get("updatedAt"), str)
        or not journal["updatedAt"]
        or journal.get("currentStep") is not None
        or journal.get("failure") is not None
        or journal.get("rollbackFailures") != []
        or journal.get("cleanupFailures") != []
        or journal.get("cleanupInProgress") != {}
        or not isinstance(steps, list)
        or not steps
    ):
        raise RuntimeInstallError(
            "transaction.journal_mismatch",
            "Prior-plan journal is not an exact clean committed transaction",
            path=str(paths.journal),
        )
    expected_undo_root = paths.journal.with_name(f"{paths.journal.name}.undo")
    if journal.get("undoRoot") != str(expected_undo_root):
        raise RuntimeInstallError(
            "transaction.journal_invalid",
            "Prior-plan journal has an unexpected rollback root",
            path=str(paths.journal),
        )
    cleanup_paths: set[pathlib.Path] = set()
    for index, step in enumerate(steps):
        if (
            not isinstance(step, dict)
            or set(step) != {"id", "action", "target", "status", "undo"}
            or not isinstance(step.get("id"), str)
            or not step["id"]
            or not isinstance(step.get("action"), str)
            or not step["action"]
            or not isinstance(step.get("target"), str)
            or not pathlib.Path(step["target"]).is_absolute()
            or step.get("status") != "applied"
            or not isinstance(step.get("undo"), dict)
        ):
            raise RuntimeInstallError(
                "transaction.journal_invalid",
                "Prior-plan committed journal step is invalid",
                path=str(paths.journal),
                index=index,
            )
        undo = step["undo"]
        commit_cleanup_paths = undo.get("commitCleanupPaths")
        if not isinstance(commit_cleanup_paths, list) or not all(
            isinstance(value, str) and pathlib.Path(value).is_absolute()
            for value in commit_cleanup_paths
        ):
            raise RuntimeInstallError(
                "transaction.journal_invalid",
                "Prior-plan committed journal cleanup paths are invalid",
                path=str(paths.journal),
                index=index,
            )
        cleanup_paths.update(pathlib.Path(value) for value in commit_cleanup_paths)
        if undo.get("kind") == "remove-backup" and isinstance(undo.get("path"), str):
            cleanup_paths.add(pathlib.Path(undo["path"]))
        if undo.get("kind") == "remove-created-file" and isinstance(undo.get("target"), str):
            cleanup_paths.add(pathlib.Path(undo["target"]))
    for cleanup_path in sorted(cleanup_paths):
        _ensure_allowed(cleanup_path, paths.allowed_roots, "prior_transaction_cleanup")
        if runtime_transaction.path_lexists(cleanup_path):
            raise RuntimeInstallError(
                "transaction.cleanup_failed",
                "Prior-plan committed journal still has cleanup residue",
                path=str(cleanup_path),
            )
    if runtime_transaction.path_lexists(expected_undo_root):
        raise RuntimeInstallError(
            "transaction.journal_inconsistent",
            "Prior-plan committed journal still has rollback payloads",
            path=str(expected_undo_root),
        )
    ensure_private_directory(paths.history_root, paths.allowed_roots, "transaction_history")
    destination = paths.history_root / (
        f"{transaction_id}-{journal['kind']}-{journal['state']}.json"
    )
    _ensure_allowed(destination, paths.allowed_roots, "transaction_history")
    if runtime_transaction.path_lexists(destination):
        raise RuntimeInstallError(
            "transaction.history_collision",
            "Prior-plan transaction archive already exists",
            path=str(destination),
        )
    with runtime_transaction.descriptor_scope(
        [paths.transaction_root, paths.history_root, *paths.allowed_roots]
    ):
        runtime_transaction.rename_exclusive(paths.journal, destination)
        runtime_transaction.fsync_directory(paths.history_root)
    _validate_private_file(destination, 0o600)
    return destination


def _transaction_report(
    command: MutationKind,
    report: runtime_transaction.TransactionReport,
    artifact: dict[str, Any],
    *,
    state: str,
    reason_code: str,
    message: str,
    ok: bool,
    archived_journal: pathlib.Path | None = None,
    recovered: bool = False,
    stop_actions: Sequence[str] = (),
    capacity: Sequence[CapacityCheck] = (),
) -> MutationReport:
    return MutationReport(
        command=command,
        ok=ok,
        state=state,
        reason_code=reason_code,
        message=message,
        artifact=artifact,
        plan_digest=report.plan_digest,
        journal=report.journal,
        archived_journal=archived_journal,
        transaction_id=report.transaction_id,
        applied=report.applied,
        rolled_back=report.rolled_back,
        failure=report.failure,
        rollback_failures=report.rollback_failures,
        cleanup_failures=report.cleanup_failures,
        recovered=recovered,
        stop_actions=tuple(stop_actions),
        capacity=tuple(capacity),
    )


def _settle_active_journal(
    command: MutationKind,
    artifact: dict[str, Any],
    executors: dict[MutationKind, runtime_transaction.TransactionExecutor],
    paths: LifecyclePaths,
    *,
    stop_actions: Sequence[str] = (),
) -> tuple[MutationReport | None, pathlib.Path | None]:
    journal = _read_active_journal(paths)
    if journal is None:
        return None, None
    existing_kind = journal.get("kind")
    state = journal.get("state")
    if existing_kind not in executors:
        raise RuntimeInstallError(
            "transaction.journal_invalid",
            "Active lifecycle journal has an unsupported direction",
            kind=existing_kind,
        )
    typed_existing_kind = cast(MutationKind, existing_kind)
    executor = executors[typed_existing_kind]
    if state == "committed":
        report = executor.execute()
        if typed_existing_kind == command:
            return (
                _transaction_report(
                    command,
                    report,
                    artifact,
                    state="already-committed",
                    reason_code="transaction.already_committed",
                    message="The requested lifecycle transaction is already committed",
                    ok=True,
                ),
                None,
            )
        return None, _archive_terminal_journal(paths, executor)
    if state in INCOMPLETE_TRANSACTION_STATES or state == "rolled-back":
        report = executor.recover()
        archived = _archive_terminal_journal(paths, executor)
        return (
            _transaction_report(
                command,
                report,
                artifact,
                state="recovered",
                reason_code="transaction.retry_required",
                message=(
                    "An interrupted lifecycle transaction was rolled back; retry the requested command"
                ),
                ok=False,
                archived_journal=archived,
                recovered=True,
                stop_actions=stop_actions,
            ),
            archived,
        )
    executor.recover()
    raise RuntimeInstallError(
        "transaction.journal_invalid",
        "Active lifecycle journal has an unsupported state",
        state=state,
    )


def _container_paths(plan: dict[str, Any]) -> dict[str, pathlib.Path]:
    state = _mutable_state_index(plan)
    return {item_id: _state_path(state, item_id) for item_id in INSTALL_CONTAINER_IDS}


def _is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def _provision_install_directories(plan: dict[str, Any], paths: LifecyclePaths) -> None:
    containers = _container_paths(plan)
    for item_id, container in containers.items():
        ensure_private_directory(container, paths.allowed_roots, item_id)
    operations = plan.get("install")
    if not isinstance(operations, list):
        raise RuntimeInstallError("plan.invalid", "Resolved install plan is missing")
    candidates: list[tuple[str, pathlib.Path]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise RuntimeInstallError("plan.invalid", "Resolved install operation is invalid")
        action = operation.get("action")
        if action in runtime_transaction.PARENT_REQUIRED_ACTIONS:
            target = operation.get("target")
            if isinstance(target, str):
                candidates.append((str(operation.get("id")), _absolute(target).parent))
        backup = operation.get("backup")
        if isinstance(backup, str):
            candidates.append((str(operation.get("id")), _absolute(backup).parent))
    for operation_id, candidate in candidates:
        if candidate.is_dir():
            continue
        if any(_is_within(candidate, root) for root in containers.values()):
            ensure_private_directory(candidate, paths.allowed_roots, operation_id)


def _plan_blockers(plan: dict[str, Any], kind: MutationKind) -> tuple[dict[str, Any], ...]:
    operations = plan.get(kind)
    blocker_ids = plan.get(f"{kind}Blockers")
    if not isinstance(operations, list) or not isinstance(blocker_ids, list):
        raise RuntimeInstallError("plan.invalid", "Resolved lifecycle readiness is invalid")
    indexed = {
        operation.get("id"): operation
        for operation in operations
        if isinstance(operation, dict) and isinstance(operation.get("id"), str)
    }
    blockers: list[dict[str, Any]] = []
    for blocker_id in blocker_ids:
        operation = indexed.get(blocker_id)
        blocker = {
            "id": blocker_id,
            "action": operation.get("action") if operation is not None else None,
            "reason": (
                operation.get("blockedReason")
                if operation is not None
                else "planner did not provide the blocked operation"
            ),
        }
        if operation is not None and isinstance(operation.get("ownershipFailure"), dict):
            blocker["details"] = operation["ownershipFailure"]
        blockers.append(blocker)
    return tuple(blockers)


def _stable_bundle_blockers(
    plan: dict[str, Any],
    kind: MutationKind,
) -> tuple[dict[str, Any], ...]:
    install = plan.get("install")
    operations = plan.get(kind)
    if not isinstance(install, list) or not isinstance(operations, list):
        raise RuntimeInstallError("plan.invalid", "Resolved install plan is missing")
    stable_resources = {
        operation.get("resource")
        for operation in install
        if isinstance(operation, dict)
        and operation.get("ownershipPolicy") == "developer-id-bundle"
    }
    blocker_ids = {
        operation.get("id")
        for operation in operations
        if isinstance(operation, dict)
        and operation.get("resource") in stable_resources
        and operation.get("ready") is not True
    }
    return tuple(
        blocker for blocker in _plan_blockers(plan, kind) if blocker["id"] in blocker_ids
    )


def _parent_blockers(plan: dict[str, Any], kind: MutationKind) -> tuple[dict[str, Any], ...]:
    operations = plan.get(kind)
    if not isinstance(operations, list):
        raise RuntimeInstallError("plan.invalid", "Resolved lifecycle operations are missing")
    provisionable = tuple(_container_paths(plan).values()) if kind == "install" else ()
    blockers: list[dict[str, Any]] = []

    def require_writable_parent(operation: dict[str, Any], path: pathlib.Path) -> None:
        parent = path.parent
        if not parent.exists():
            if kind == "install" and any(_is_within(parent, root) for root in provisionable):
                return
            blockers.append(
                {
                    "id": operation.get("id"),
                    "action": operation.get("action"),
                    "reason": f"mutation parent is missing: {parent}",
                }
            )
            return
        if not parent.is_dir():
            blockers.append(
                {
                    "id": operation.get("id"),
                    "action": operation.get("action"),
                    "reason": f"mutation parent is not a directory: {parent}",
                }
            )
            return
        if not os.access(parent, os.W_OK | os.X_OK):
            blockers.append(
                {
                    "id": operation.get("id"),
                    "action": operation.get("action"),
                    "reason": f"mutation parent is not writable: {parent}",
                }
            )

    for operation in operations:
        if not isinstance(operation, dict):
            raise RuntimeInstallError("plan.invalid", "Resolved lifecycle operation is invalid")
        action = operation.get("action")
        raw_target = operation.get("target")
        if not isinstance(raw_target, str):
            continue
        target = _absolute(raw_target)
        target_write = action in {"replace_file", "create_file", "replace_tree"}
        if action == "restore":
            expected = operation.get("expectedSha256")
            target_write = not (
                isinstance(expected, str)
                and target.is_file()
                and artifact_contract.sha256_file(target) == expected
            )
        elif action in {"remove", "remove_tree"}:
            target_write = runtime_transaction.path_lexists(target)
        if target_write:
            require_writable_parent(operation, target)
        if action == "backup":
            raw_backup = operation.get("backup")
            if isinstance(raw_backup, str):
                backup = _absolute(raw_backup)
                if not backup.exists():
                    require_writable_parent(operation, backup)
    return tuple(blockers)


def _admission_blockers(plan: dict[str, Any], kind: MutationKind) -> tuple[dict[str, Any], ...]:
    blockers = list(_plan_blockers(plan, kind))
    existing = {(item.get("id"), item.get("reason")) for item in blockers}
    for blocker in _parent_blockers(plan, kind):
        identity = (blocker.get("id"), blocker.get("reason"))
        if identity not in existing:
            blockers.append(blocker)
            existing.add(identity)
    return tuple(blockers)


def _mutation_targets(plan: dict[str, Any], kind: MutationKind) -> list[pathlib.Path]:
    operations = plan.get(kind)
    if not isinstance(operations, list):
        raise RuntimeInstallError("plan.invalid", "Resolved lifecycle operations are missing")
    targets: set[pathlib.Path] = set()
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("action") not in runtime_transaction.MUTATING_ACTIONS:
            continue
        for field in ("target", "backup"):
            raw = operation.get(field)
            if isinstance(raw, str):
                path = _absolute(raw)
                if path.exists():
                    targets.add(path)
    return sorted(targets, key=lambda item: str(item))


def _journal_paths(journal: dict[str, Any]) -> list[pathlib.Path]:
    paths: set[pathlib.Path] = set()
    for step in journal.get("steps", []):
        if not isinstance(step, dict):
            continue
        target = step.get("target")
        if isinstance(target, str):
            paths.add(_absolute(target))
        undo = step.get("undo")
        if not isinstance(undo, dict):
            continue
        for field in (
            "path",
            "target",
            "original",
            "staging",
            "rollbackDiscard",
        ):
            raw = undo.get(field)
            if isinstance(raw, str):
                paths.add(_absolute(raw))
        cleanup = undo.get("commitCleanupPaths")
        if isinstance(cleanup, list):
            for raw in cleanup:
                if isinstance(raw, str):
                    paths.add(_absolute(raw))
    return sorted((path for path in paths if path.exists()), key=lambda item: str(item))


def _expanded_inspection_paths(paths: Sequence[pathlib.Path]) -> list[pathlib.Path]:
    expanded: set[pathlib.Path] = set()
    for path in paths:
        try:
            artifact_contract.reject_symlink_components(path)
        except artifact_contract.ArtifactError as error:
            raise _artifact_error(error) from error
        if path.is_file():
            expanded.add(path)
            continue
        if not path.is_dir():
            continue
        expanded.add(path)
        for child in path.rglob("*"):
            metadata = child.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeInstallError(
                    "path.symlink",
                    "Open-file inspection tree contains a symlink",
                    path=str(child),
                )
            if stat.S_ISREG(metadata.st_mode):
                expanded.add(child)
            elif not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeInstallError(
                    "tree.unsupported",
                    "Open-file inspection tree contains an unsupported entry",
                    path=str(child),
                )
    return sorted(expanded, key=lambda item: str(item))


def _ensure_paths_closed(
    context: RuntimeContext,
    paths: Sequence[pathlib.Path],
) -> None:
    targets = _expanded_inspection_paths(paths)
    if not targets:
        return
    for offset in range(0, len(targets), 64):
        chunk = targets[offset : offset + 64]
        result = context.runner.run(
            ["/usr/sbin/lsof", "-Fn", "--", *(str(path) for path in chunk)],
            timeout=15.0,
        )
        if result.error is not None:
            raise RuntimeInstallError(
                "runtime.target_inspection_failed",
                "Open-file inspection did not complete",
                error=result.error,
                stderr=result.stderr,
            )
        if result.returncode == 1:
            continue
        if result.returncode != 0:
            raise RuntimeInstallError(
                "runtime.target_inspection_failed",
                "Open-file inspection returned an unexpected status",
                returncode=result.returncode,
                stderr=result.stderr,
            )
        pids = sorted({line[1:] for line in result.stdout.splitlines() if line.startswith("p")})
        if pids:
            raise RuntimeInstallError(
                "runtime.target_busy",
                "One or more lifecycle mutation targets are open",
                pids=pids,
                targets=[str(path) for path in chunk],
            )


def _ensure_targets_closed(
    context: RuntimeContext,
    plan: dict[str, Any],
    kind: MutationKind,
) -> None:
    _ensure_paths_closed(context, _mutation_targets(plan, kind))


def _tree_bytes(root: pathlib.Path) -> int:
    total = CAPACITY_ENTRY_BYTES
    try:
        artifact_contract.reject_symlink_components(root)
    except artifact_contract.ArtifactError as error:
        raise _artifact_error(error) from error
    for path in root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeInstallError("path.symlink", "Capacity source tree contains a symlink", path=str(path))
        if stat.S_ISREG(metadata.st_mode):
            allocated = getattr(metadata, "st_blocks", 0) * 512
            total += max(metadata.st_size, allocated, CAPACITY_ENTRY_BYTES)
        elif stat.S_ISDIR(metadata.st_mode):
            total += CAPACITY_ENTRY_BYTES
        else:
            raise RuntimeInstallError(
                "tree.unsupported",
                "Capacity source tree contains an unsupported entry",
                path=str(path),
            )
    return total


def _file_capacity_bytes(path: pathlib.Path) -> int:
    metadata = path.stat()
    allocated = getattr(metadata, "st_blocks", 0) * 512
    return max(metadata.st_size, allocated, CAPACITY_ENTRY_BYTES)


def _nearest_existing_directory(path: pathlib.Path) -> pathlib.Path:
    cursor = path if path.is_dir() else path.parent
    while not cursor.exists():
        if cursor.parent == cursor:
            raise RuntimeInstallError(
                "capacity.unavailable",
                "No existing filesystem anchor is available for capacity inspection",
                path=str(path),
            )
        cursor = cursor.parent
    try:
        artifact_contract.reject_symlink_components(cursor)
    except artifact_contract.ArtifactError as error:
        raise _artifact_error(error) from error
    if not cursor.is_dir():
        raise RuntimeInstallError(
            "capacity.unavailable",
            "Capacity inspection anchor is not a directory",
            path=str(cursor),
        )
    return cursor


def filesystem_free_bytes(path: pathlib.Path) -> int:
    return shutil.disk_usage(path).free


def _capacity_checks(
    plan: dict[str, Any],
    kind: MutationKind,
    paths: LifecyclePaths,
) -> tuple[CapacityCheck, ...]:
    operations = plan.get(kind)
    if not isinstance(operations, list):
        raise RuntimeInstallError("plan.invalid", "Resolved lifecycle operations are missing")
    required: dict[int, int] = {}
    representatives: dict[int, pathlib.Path] = {}

    def add(path: pathlib.Path, amount: int) -> None:
        anchor = _nearest_existing_directory(path)
        try:
            device = anchor.stat().st_dev
        except OSError as error:
            raise RuntimeInstallError(
                "capacity.unavailable",
                "Filesystem identity could not be inspected",
                path=str(anchor),
                error=str(error),
            ) from error
        required[device] = required.get(device, 0) + max(amount, 0)
        representatives.setdefault(device, anchor)

    add(paths.transaction_root, 0)
    for operation in operations:
        if not isinstance(operation, dict):
            raise RuntimeInstallError("plan.invalid", "Resolved lifecycle operation is invalid")
        action = operation.get("action")
        raw_target = operation.get("target")
        if not isinstance(raw_target, str):
            raise RuntimeInstallError("plan.invalid", "Lifecycle operation target is missing")
        target = _absolute(raw_target)
        if action == "backup":
            raw_backup = operation.get("backup")
            if isinstance(raw_backup, str):
                backup = _absolute(raw_backup)
                if not backup.exists() and target.is_file():
                    add(backup, _file_capacity_bytes(target))
        elif action in {"replace_file", "create_file"}:
            raw_source = operation.get("source")
            if isinstance(raw_source, str):
                source = _absolute(raw_source)
                if source.is_file():
                    add(target, _file_capacity_bytes(source))
            if action == "replace_file" and target.is_file():
                add(paths.transaction_root, _file_capacity_bytes(target))
        elif action == "replace_tree":
            raw_source = operation.get("source")
            if isinstance(raw_source, str):
                source = _absolute(raw_source)
                if source.is_dir():
                    add(target, _tree_bytes(source))
        elif action == "restore":
            raw_backup = operation.get("backup")
            if isinstance(raw_backup, str):
                backup = _absolute(raw_backup)
                if backup.is_file():
                    add(target, _file_capacity_bytes(backup))
            if target.is_file():
                add(paths.transaction_root, _file_capacity_bytes(target))
    checks: list[CapacityCheck] = []
    for device in sorted(required):
        path = representatives[device]
        try:
            available = filesystem_free_bytes(path)
        except OSError as error:
            raise RuntimeInstallError(
                "capacity.unavailable",
                "Filesystem capacity could not be inspected",
                path=str(path),
                error=str(error),
            ) from error
        estimated = required[device]
        required_bytes = estimated + max(CAPACITY_OVERHEAD_BYTES, estimated // 10)
        check = CapacityCheck(path, device, required_bytes, available)
        checks.append(check)
        if available < required_bytes:
            raise RuntimeInstallError(
                "capacity.insufficient",
                "Insufficient free space for a rollback-safe lifecycle transaction",
                checks=[item.to_dict() for item in checks],
            )
    return tuple(checks)


def _error_report(
    command: MutationKind,
    artifact: dict[str, Any] | None,
    error: RuntimeInstallError | ControlError | runtime_transaction.TransactionError,
    *,
    journal: pathlib.Path | None = None,
    plan_digest: str | None = None,
    archived_journal: pathlib.Path | None = None,
    stop_actions: Sequence[str] = (),
) -> MutationReport:
    context = error.context
    raw_transaction = context.get("report")
    transaction: dict[str, Any] = raw_transaction if isinstance(raw_transaction, dict) else {}
    state = "blocked"
    transaction_state = transaction.get("state")
    if error.code == "transaction.cleanup_failed" and transaction_state in {
        "committed",
        "rolled-back",
    }:
        state = str(transaction_state)
    elif error.code == "transaction.rolled_back":
        state = "rolled-back"
    elif error.code == "transaction.rollback_failed":
        state = "failed"
    capacity = tuple(
        CapacityCheck(
            pathlib.Path(item["path"]),
            int(item["device"]),
            int(item["requiredBytes"]),
            int(item["availableBytes"]),
        )
        for item in context.get("checks", [])
        if isinstance(item, dict)
        and {"path", "device", "requiredBytes", "availableBytes"} <= set(item)
    )
    return MutationReport(
        command=command,
        ok=False,
        state=state,
        reason_code=error.code,
        message=error.message,
        artifact=artifact,
        plan_digest=transaction.get("planDigest") or plan_digest,
        journal=(
            pathlib.Path(transaction["journal"])
            if isinstance(transaction.get("journal"), str)
            else journal
        ),
        archived_journal=archived_journal,
        transaction_id=(
            transaction.get("transactionId")
            if isinstance(transaction.get("transactionId"), str)
            else None
        ),
        applied=tuple(transaction.get("applied") or ()),
        rolled_back=tuple(transaction.get("rolledBack") or ()),
        failure=(transaction.get("failure") if isinstance(transaction.get("failure"), dict) else None),
        rollback_failures=tuple(transaction.get("rollbackFailures") or ()),
        cleanup_failures=tuple(transaction.get("cleanupFailures") or ()),
        stop_actions=tuple(stop_actions),
        capacity=capacity,
    )


def _mutate_runtime(
    command: MutationKind,
    context: RuntimeContext,
    artifact_path: pathlib.Path,
) -> MutationReport:
    artifact: dict[str, Any] | None = None
    plan_digest: str | None = None
    paths: LifecyclePaths | None = None
    archived_journal: pathlib.Path | None = None
    stop_actions: tuple[str, ...] = ()
    try:
        manifest, _, manifest_hash, lock_hash = load_runtime_contract(context)
        tree_ownership_validator: runtime_transaction.TreeOwnershipValidator = (
            lambda bundle, _operation: _inspect_stable_bundle(bundle, manifest)
        )
        artifact_path = _absolute(artifact_path)
        artifact = verify_artifact_reference(context, artifact_path)
        if artifact.get("stage") != "sealed":
            return MutationReport(
                command=command,
                ok=False,
                state="blocked",
                reason_code="artifact.sealing_required",
                message=(
                    "The runtime artifact requires a verified post-sign tree before lifecycle mutation"
                ),
                artifact=artifact,
            )
        admission_plan = _qualify_stable_bundles(
            _build_plan(context, artifact_path, manifest, manifest_hash, lock_hash),
            manifest,
        )
        if admission_plan.get("requiresSealing") is not False:
            return MutationReport(
                command=command,
                ok=False,
                state="blocked",
                reason_code="artifact.sealing_required",
                message=(
                    "The runtime artifact requires a verified post-sign tree before lifecycle mutation"
                ),
                artifact=artifact,
            )
        admission_paths = lifecycle_paths(admission_plan)
        stable_blockers = _stable_bundle_blockers(admission_plan, command)
        if stable_blockers and _read_active_journal(admission_paths) is None:
            return MutationReport(
                command=command,
                ok=False,
                state="blocked",
                reason_code="plan.blocked",
                message="Stable bundle ownership could not be admitted",
                artifact=artifact,
                blockers=stable_blockers,
                journal=admission_paths.journal,
            )
        live_status = status_runtime(context, verify_live_artifact=False)
        live_record = live_status.control_state.get("record")
        if (
            live_status.ok
            and live_status.state in LIVE_STATES
            and isinstance(live_record, dict)
            and live_record.get("schemaVersion") in {2, 3, 4, 5}
        ):
            pre_lock_stop = stop_runtime(context)
            stop_actions = pre_lock_stop.actions
            if not pre_lock_stop.ok:
                return MutationReport(
                    command=command,
                    ok=False,
                    state="blocked",
                    reason_code=pre_lock_stop.reason_code,
                    message=pre_lock_stop.message,
                    artifact=artifact,
                    stop_actions=stop_actions,
                )
        global_lock = _absolute(context.lifecycle_lock_path)
        global_lock_root = global_lock.parent
        ensure_private_directory(
            global_lock_root,
            (global_lock_root,),
            "transaction_lock_root",
        )
        with global_lifecycle_lock(global_lock, (global_lock_root,)):
            plan = _qualify_stable_bundles(
                _build_plan(context, artifact_path, manifest, manifest_hash, lock_hash),
                manifest,
            )
            if plan.get("requiresSealing") is not False:
                return MutationReport(
                    command=command,
                    ok=False,
                    state="blocked",
                    reason_code="artifact.sealing_required",
                    message=(
                        "The runtime artifact requires a verified post-sign tree before lifecycle mutation"
                    ),
                    artifact=artifact,
                )
            paths = lifecycle_paths(plan)
            if paths.global_lock != global_lock:
                raise RuntimeInstallError(
                    "repository.contract",
                    "Resolved lifecycle lock differs from the fixed per-user runtime lock",
                    resolved=str(paths.global_lock),
                    expected=str(global_lock),
                )
            executors: dict[MutationKind, runtime_transaction.TransactionExecutor] = {
                "install": _executor(
                    "install",
                    plan,
                    paths,
                    tree_ownership_validator=tree_ownership_validator,
                ),
                "uninstall": _executor(
                    "uninstall",
                    plan,
                    paths,
                    tree_ownership_validator=tree_ownership_validator,
                ),
            }
            plan_digest = executors[command].plan_digest
            active_journal = _read_active_journal(paths)
            recovery_stop_actions = stop_actions
            if active_journal is not None:
                ensure_private_directory(
                    paths.transaction_root,
                    paths.allowed_roots,
                    "transaction_root",
                )
                existing_kind = active_journal.get("kind")
                if existing_kind not in executors:
                    raise RuntimeInstallError(
                        "transaction.journal_invalid",
                        "Active lifecycle journal has an unsupported direction",
                        kind=existing_kind,
                    )
                existing_executor = executors[cast(MutationKind, existing_kind)]
                if active_journal.get("planDigest") != existing_executor.plan_digest:
                    archived_journal = _archive_prior_committed_journal(paths, active_journal)
                    active_journal = None
                else:
                    validated_journal = existing_executor.inspect()
                    if validated_journal is None:
                        raise RuntimeInstallError(
                            "transaction.journal_missing",
                            "Active lifecycle journal disappeared during inspection",
                            path=str(paths.journal),
                        )
                    if validated_journal["state"] in INCOMPLETE_TRANSACTION_STATES:
                        recovery_stop = stop_runtime(context)
                        recovery_stop_actions = (*stop_actions, *recovery_stop.actions)
                        stop_actions = recovery_stop_actions
                        if not recovery_stop.ok:
                            return MutationReport(
                                command=command,
                                ok=False,
                                state="blocked",
                                reason_code=recovery_stop.reason_code,
                                message=recovery_stop.message,
                                artifact=artifact,
                                plan_digest=plan_digest,
                                journal=paths.journal,
                                stop_actions=recovery_stop_actions,
                            )
                        _ensure_paths_closed(context, _journal_paths(validated_journal))
            settled, settled_archive = _settle_active_journal(
                command,
                artifact,
                executors,
                paths,
                stop_actions=recovery_stop_actions,
            )
            if settled_archive is not None:
                archived_journal = settled_archive
            if settled is not None:
                return settled
            initial_blockers = _admission_blockers(plan, command)
            if plan.get(f"{command}Ready") is not True or initial_blockers:
                return MutationReport(
                    command=command,
                    ok=False,
                    state="blocked",
                    reason_code="plan.blocked",
                    message=f"Runtime {command} plan is not ready",
                    artifact=artifact,
                    plan_digest=plan_digest,
                    blockers=initial_blockers,
                    journal=paths.journal,
                    archived_journal=archived_journal,
                )
            _capacity_checks(plan, command, paths)
            if command == "install":
                doctor = doctor_runtime(context, artifact_path)
                if not doctor.ok:
                    blockers = tuple(
                        {
                            "id": check.id,
                            "reason": check.message,
                            "remediation": check.remediation,
                        }
                        for check in doctor.checks
                        if check.status != "pass"
                    )
                    return MutationReport(
                        command=command,
                        ok=False,
                        state="blocked",
                        reason_code="runtime.doctor_failed",
                        message="Runtime prerequisites are not ready for installation",
                        artifact=artifact,
                        plan_digest=plan_digest,
                        blockers=blockers,
                        journal=paths.journal,
                        archived_journal=archived_journal,
                    )
            stop = stop_runtime(context)
            stop_actions = (*stop_actions, *stop.actions)
            if not stop.ok:
                return MutationReport(
                    command=command,
                    ok=False,
                    state="blocked",
                    reason_code=stop.reason_code,
                    message=stop.message,
                    artifact=artifact,
                    plan_digest=plan_digest,
                    journal=paths.journal,
                    archived_journal=archived_journal,
                    stop_actions=stop_actions,
                )
            ensure_private_directory(
                paths.transaction_root,
                paths.allowed_roots,
                "transaction_root",
            )
            if command == "install":
                _provision_install_directories(plan, paths)
            live_plan = _qualify_stable_bundles(
                _build_plan(context, artifact_path, manifest, manifest_hash, lock_hash),
                manifest,
            )
            live_paths = lifecycle_paths(live_plan)
            if live_paths != paths:
                raise RuntimeInstallError(
                    "plan.drift",
                    "Lifecycle paths changed while the global lock was held",
                )
            executor = _executor(
                command,
                live_plan,
                live_paths,
                tree_ownership_validator=tree_ownership_validator,
            )
            plan_digest = executor.plan_digest
            live_blockers = _admission_blockers(live_plan, command)
            if live_plan.get(f"{command}Ready") is not True or live_blockers:
                return MutationReport(
                    command=command,
                    ok=False,
                    state="blocked",
                    reason_code="plan.blocked",
                    message=f"Runtime {command} plan is not ready",
                    artifact=artifact,
                    plan_digest=plan_digest,
                    blockers=live_blockers,
                    journal=live_paths.journal,
                    archived_journal=archived_journal,
                    stop_actions=stop_actions,
                )
            _ensure_targets_closed(context, live_plan, command)
            capacity = _capacity_checks(live_plan, command, live_paths)
            report = executor.execute()
            _validate_private_file(live_paths.journal, 0o600)
            return _transaction_report(
                command,
                report,
                artifact,
                state="committed",
                reason_code="transaction.committed",
                message=f"Runtime {command} transaction committed",
                ok=True,
                archived_journal=archived_journal,
                stop_actions=stop_actions,
                capacity=capacity,
            )
    except artifact_contract.ArtifactError as error:
        converted = _artifact_error(error)
        return _error_report(
            command,
            artifact,
            converted,
            journal=paths.journal if paths is not None else None,
            plan_digest=plan_digest,
            archived_journal=archived_journal,
            stop_actions=stop_actions,
        )
    except (RuntimeInstallError, ControlError, runtime_transaction.TransactionError) as error:
        return _error_report(
            command,
            artifact,
            error,
            journal=paths.journal if paths is not None else None,
            plan_digest=plan_digest,
            archived_journal=archived_journal,
            stop_actions=stop_actions,
        )


def install_runtime(context: RuntimeContext, artifact: pathlib.Path) -> MutationReport:
    return _mutate_runtime("install", context, artifact)


def uninstall_runtime(context: RuntimeContext, artifact: pathlib.Path) -> MutationReport:
    return _mutate_runtime("uninstall", context, artifact)
