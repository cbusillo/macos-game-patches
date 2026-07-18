#!/usr/bin/env python3
"""Validate, build, verify, compare, and plan Mac ALVR runtime artifacts."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import pathlib
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "runtime" / "manifest.json"
DEFAULT_LOCK = REPO_ROOT / "runtime" / "manifest.lock.json"
DEFAULT_BINDINGS = REPO_ROOT / ".code" / "runtime-bindings.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / ".code" / "runtime-artifacts"
TOKEN_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:-[a-z0-9][a-z0-9.-]*)?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MODE_PATTERN = re.compile(r"^0[0-7]{3}$")
SOURCE_SUPPLY_CLASSES = {"repo-patch", "repo-source"}
ARTIFACT_SUPPLY_CLASSES = {
    "opaque-local-build",
    "pinned-git-build",
    "repo-source-build",
}
TRUSTED_COMMAND_PREREQUISITES = {
    "host_architecture": ["/usr/bin/uname", "-m"],
    "host_model": ["/usr/sbin/sysctl", "-n", "hw.model"],
    "macos_version": ["/usr/bin/sw_vers", "-productVersion"],
    "macos_build": ["/usr/bin/sw_vers", "-buildVersion"],
    "xcode_build": ["/usr/bin/xcodebuild", "-version"],
}
TRUSTED_PLIST_PREREQUISITES = {
    "crossover_short_version": (
        "${CROSSOVER_APP}/Contents/Info.plist",
        "CFBundleShortVersionString",
    ),
    "crossover_build_version": (
        "${CROSSOVER_APP}/Contents/Info.plist",
        "CFBundleVersion",
    ),
}
TRUSTED_BINDING_DEFAULTS = {
    "CROSSOVER_APP": "/Applications/CrossOver.app",
    "STEAM_BOTTLE": "${HOME}/Library/Application Support/CrossOver/Bottles/Steam",
}
TRUSTED_ALLOWED_TARGET_ROOTS = {
    "${REPO_ROOT}/.code",
    "${HOME}/Library/Application Support/alvr",
    "${CROSSOVER_APP}",
    "${STEAM_BOTTLE}",
}
PLAN_ACTIONS = {
    "assert_absent",
    "assert_absent_or_owned",
    "assert_sha256",
    "backup",
    "create_file",
    "remove",
    "remove_tree",
    "replace_file",
    "replace_tree",
    "retain",
    "restore",
}
INSTALL_EFFECTS = {"create_file", "replace_file", "replace_tree"}
UNINSTALL_EFFECTS = {"remove", "remove_tree", "restore"}
INVERSE_ACTIONS = {
    "create_file": "remove",
    "replace_file": "restore",
    "replace_tree": "remove_tree",
}
SELF_REFERENTIAL_PROVENANCE = {
    pathlib.PurePosixPath("provenance/artifact.json"),
    pathlib.PurePosixPath("provenance/files.sha256"),
}
SEALING_PROVENANCE_PATH = pathlib.PurePosixPath("provenance/sealing.json")
ARTIFACT_STAGES = {"unsealed", "sealed"}


class ArtifactError(Exception):
    """Stable machine-readable runtime artifact failure."""

    def __init__(self, code: str, message: str, **context: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context


def canonical_json_bytes(value: Any) -> bytes:
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


def emit(value: Any, *, stream: Any = sys.stdout) -> None:
    stream.write(canonical_json_bytes(value).decode())


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_tree_sha256(root: pathlib.Path) -> str:
    reject_symlink_components(root)
    if not root.is_dir():
        raise ArtifactError(
            "tree.missing",
            "Tree digest requires a directory",
            path=str(root),
        )
    entries: list[dict[str, Any]] = [
        {
            "path": ".",
            "type": "directory",
            "mode": stat.S_IMODE(root.lstat().st_mode),
        }
    ]
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(metadata.st_mode):
            raise ArtifactError("path.symlink", "Tree contains a symlink", path=str(path))
        if stat.S_ISDIR(metadata.st_mode):
            entries.append(
                {
                    "path": relative,
                    "type": "directory",
                    "mode": stat.S_IMODE(metadata.st_mode),
                }
            )
        elif stat.S_ISREG(metadata.st_mode):
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": stat.S_IMODE(metadata.st_mode),
                    "size": metadata.st_size,
                    "sha256": sha256_file(path),
                }
            )
        else:
            raise ArtifactError(
                "tree.unsupported",
                "Tree contains an unsupported filesystem entry",
                path=str(path),
            )
    return sha256_bytes(canonical_json_bytes(entries))


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError("json.duplicate", "JSON object contains a duplicate key", key=key)
        result[key] = value
    return result


def reject_json_constant(value: str) -> Any:
    raise ArtifactError("json.invalid", "JSON contains a non-finite numeric constant", value=value)


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(
            path.read_text(),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_json_constant,
        )
    except FileNotFoundError as error:
        raise ArtifactError("input.missing", "Required JSON file is missing", path=str(path)) from error
    except json.JSONDecodeError as error:
        raise ArtifactError(
            "json.invalid",
            "JSON parsing failed",
            path=str(path),
            line=error.lineno,
            column=error.colno,
            detail=error.msg,
        ) from error


def write_json(path: pathlib.Path, value: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))
    path.chmod(mode)


def require_object(
    value: Any,
    location: str,
    *,
    required: set[str],
    allowed: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactError("manifest.invalid", "Expected a JSON object", location=location)
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - allowed)
    if missing:
        raise ArtifactError("manifest.invalid", "Object is missing required keys", location=location, keys=missing)
    if unknown:
        raise ArtifactError("manifest.invalid", "Object contains unknown keys", location=location, keys=unknown)
    return value


def require_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ArtifactError("manifest.invalid", "Expected a JSON array", location=location)
    return value


def require_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArtifactError("manifest.invalid", "Expected a non-empty string", location=location)
    return value


def require_bool(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise ArtifactError("manifest.invalid", "Expected a boolean", location=location)
    return value


def require_id(value: Any, location: str) -> str:
    identifier = require_string(value, location)
    if not ID_PATTERN.fullmatch(identifier):
        raise ArtifactError("manifest.invalid", "Identifier has an invalid format", location=location, value=identifier)
    return identifier


def require_slug(value: Any, location: str) -> str:
    identifier = require_string(value, location)
    if not SLUG_PATTERN.fullmatch(identifier):
        raise ArtifactError("manifest.invalid", "Slug has an invalid format", location=location, value=identifier)
    return identifier


def require_version(value: Any, location: str) -> str:
    version = require_string(value, location)
    if not VERSION_PATTERN.fullmatch(version):
        raise ArtifactError("manifest.invalid", "Artifact version has an invalid format", location=location)
    return version


def require_sha256(value: Any, location: str) -> str:
    digest = require_string(value, location)
    if not SHA256_PATTERN.fullmatch(digest):
        raise ArtifactError("manifest.invalid", "Expected a lowercase SHA-256 digest", location=location, value=digest)
    return digest


def safe_relative_path(value: Any, location: str) -> pathlib.PurePosixPath:
    raw = require_string(value, location)
    if "\\" in raw or raw.startswith("/") or "//" in raw:
        raise ArtifactError("path.unsafe", "Artifact path must be a normalized POSIX path", location=location, path=raw)
    raw_parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ArtifactError("path.unsafe", "Artifact path contains an unsafe component", location=location, path=raw)
    path = pathlib.PurePosixPath(raw)
    if path.is_absolute():
        raise ArtifactError("path.unsafe", "Artifact path must be a safe relative path", location=location, path=raw)
    return path


def require_mode(value: Any, location: str) -> int:
    raw = require_string(value, location)
    if not MODE_PATTERN.fullmatch(raw):
        raise ArtifactError("manifest.invalid", "Mode must use four-digit octal notation", location=location, value=raw)
    return int(raw, 8)


def validate_binding_definitions(items: Any) -> set[str]:
    names: set[str] = set()
    for index, raw_item in enumerate(require_list(items, "bindings")):
        location = f"bindings[{index}]"
        item = require_object(
            raw_item,
            location,
            required={"name", "phases", "required"},
            allowed={"name", "phases", "required", "default"},
        )
        name = require_string(item["name"], f"{location}.name")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ArtifactError("manifest.invalid", "Binding name has an invalid format", location=location, name=name)
        if name in names or name in {"REPO_ROOT", "HOME"}:
            raise ArtifactError("manifest.invalid", "Binding name is duplicated or reserved", location=location, name=name)
        names.add(name)
        phases = require_list(item["phases"], f"{location}.phases")
        if (
            not phases
            or len(phases) != len(set(phases))
            or any(phase not in {"validate", "build", "plan"} for phase in phases)
        ):
            raise ArtifactError("manifest.invalid", "Binding phases are invalid", location=location, phases=phases)
        require_bool(item["required"], f"{location}.required")
        if "default" in item:
            require_string(item["default"], f"{location}.default")
        if name in TRUSTED_BINDING_DEFAULTS and item.get("default") != TRUSTED_BINDING_DEFAULTS[name]:
            raise ArtifactError(
                "manifest.invalid",
                "Security-sensitive binding default differs from trusted policy",
                binding=name,
            )
    return names


def validate_git_sources(items: Any, binding_names: set[str]) -> set[str]:
    identifiers: set[str] = set()
    for index, raw_item in enumerate(require_list(items, "gitSources")):
        location = f"gitSources[{index}]"
        item = require_object(
            raw_item,
            location,
            required={"id", "path", "remote", "revision", "cleanPolicy"},
            allowed={"id", "path", "remote", "revision", "cleanPolicy"},
        )
        identifier = require_id(item["id"], f"{location}.id")
        if identifier in identifiers:
            raise ArtifactError("manifest.invalid", "Git source identifier is duplicated", id=identifier)
        identifiers.add(identifier)
        require_string(item["path"], f"{location}.path")
        require_string(item["remote"], f"{location}.remote")
        revision = require_string(item["revision"], f"{location}.revision")
        if revision != "self" and not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ArtifactError("manifest.invalid", "Git revision must be self or a full commit", location=location)
        if revision == "self" and item["path"] != "${REPO_ROOT}":
            raise ArtifactError(
                "manifest.invalid",
                "The self revision is reserved for the checked-out manifest repository",
                location=location,
            )
        if item["cleanPolicy"] not in {"all", "tracked"}:
            raise ArtifactError("manifest.invalid", "Git clean policy is invalid", location=location)
        validate_template_tokens(item["path"], binding_names, f"{location}.path")
    return identifiers


def validate_prerequisites(items: Any, binding_names: set[str]) -> None:
    identifiers: set[str] = set()
    for index, raw_item in enumerate(require_list(items, "prerequisites")):
        location = f"prerequisites[{index}]"
        if not isinstance(raw_item, dict):
            raise ArtifactError("manifest.invalid", "Prerequisite must be an object", location=location)
        identifier = require_id(raw_item.get("id"), f"{location}.id")
        if identifier in identifiers:
            raise ArtifactError("manifest.invalid", "Prerequisite identifier is duplicated", id=identifier)
        identifiers.add(identifier)
        kind = raw_item.get("kind")
        if kind == "command":
            item = require_object(
                raw_item,
                location,
                required={"id", "kind", "argv"},
                allowed={"id", "kind", "argv", "equals", "contains"},
            )
            if ("equals" in item) == ("contains" in item):
                raise ArtifactError("manifest.invalid", "Command prerequisite needs exactly one matcher", location=location)
            argv = require_list(item["argv"], f"{location}.argv")
            if not argv or any(not isinstance(argument, str) or not argument for argument in argv):
                raise ArtifactError("manifest.invalid", "Command argv is invalid", location=location)
            if TRUSTED_COMMAND_PREREQUISITES.get(identifier) != argv:
                raise ArtifactError(
                    "manifest.invalid",
                    "Command prerequisite is not in the trusted policy",
                    id=identifier,
                )
        elif kind == "plist":
            item = require_object(
                raw_item,
                location,
                required={"id", "kind", "path", "key", "equals"},
                allowed={"id", "kind", "path", "key", "equals"},
            )
            require_string(item["path"], f"{location}.path")
            require_string(item["key"], f"{location}.key")
            validate_template_tokens(item["path"], binding_names, f"{location}.path")
            if TRUSTED_PLIST_PREREQUISITES.get(identifier) != (item["path"], item["key"]):
                raise ArtifactError(
                    "manifest.invalid",
                    "Plist prerequisite is not in the trusted policy",
                    id=identifier,
                )
        else:
            raise ArtifactError("manifest.invalid", "Prerequisite kind is invalid", location=location, kind=kind)


def validate_source_files(items: Any, binding_names: set[str]) -> set[str]:
    identifiers: set[str] = set()
    for index, raw_item in enumerate(require_list(items, "sourceFiles")):
        location = f"sourceFiles[{index}]"
        item = require_object(
            raw_item,
            location,
            required={"id", "path", "sha256", "supplyClass"},
            allowed={"id", "path", "sha256", "supplyClass"},
        )
        identifier = require_id(item["id"], f"{location}.id")
        if identifier in identifiers:
            raise ArtifactError("manifest.invalid", "Source file identifier is duplicated", id=identifier)
        identifiers.add(identifier)
        require_string(item["path"], f"{location}.path")
        require_sha256(item["sha256"], f"{location}.sha256")
        supply_class = require_string(item["supplyClass"], f"{location}.supplyClass")
        if supply_class not in SOURCE_SUPPLY_CLASSES:
            raise ArtifactError(
                "manifest.invalid",
                "Repository source file has an unsupported supply class",
                location=location,
                supplyClass=supply_class,
            )
        validate_template_tokens(item["path"], binding_names, f"{location}.path")
    return identifiers


def validate_binary_expectation(value: Any, location: str) -> None:
    binary = require_object(
        value,
        location,
        required={"format", "kind", "architectures"},
        allowed={"format", "kind", "architectures", "authenticode"},
    )
    binary_format = require_string(binary["format"], f"{location}.format")
    kind = require_string(binary["kind"], f"{location}.kind")
    architectures = require_list(binary["architectures"], f"{location}.architectures")
    if (
        not architectures
        or len(architectures) != len(set(architectures))
        or any(architecture not in {"arm64", "x86_64"} for architecture in architectures)
        or architectures != sorted(architectures)
    ):
        raise ArtifactError("manifest.invalid", "Binary architectures must be unique and sorted", location=location)
    if binary_format == "pe":
        if kind != "dll" or binary.get("authenticode") != "absent":
            raise ArtifactError(
                "manifest.invalid",
                "PE runtime inputs must be DLLs with explicitly absent Authenticode",
                location=location,
            )
    elif binary_format == "mach-o":
        if kind not in {"dynamic-library", "executable"} or "authenticode" in binary:
            raise ArtifactError("manifest.invalid", "Mach-O binary expectation is invalid", location=location)
    else:
        raise ArtifactError("manifest.invalid", "Binary format is unsupported", location=location)


def validate_artifacts(items: Any, binding_names: set[str]) -> set[str]:
    identifiers: set[str] = set()
    paths: set[pathlib.PurePosixPath] = set()
    for index, raw_item in enumerate(require_list(items, "artifacts")):
        location = f"artifacts[{index}]"
        item = require_object(
            raw_item,
            location,
            required={
                "id",
                "path",
                "artifactPath",
                "mode",
                "supplyClass",
                "recipe",
                "binary",
                "signature",
            },
            allowed={
                "id",
                "path",
                "artifactPath",
                "mode",
                "supplyClass",
                "recipe",
                "binary",
                "signature",
            },
        )
        identifier = require_id(item["id"], f"{location}.id")
        if identifier in identifiers:
            raise ArtifactError("manifest.invalid", "Artifact identifier is duplicated", id=identifier)
        identifiers.add(identifier)
        artifact_path = safe_relative_path(item["artifactPath"], f"{location}.artifactPath")
        if artifact_path in paths:
            raise ArtifactError("manifest.invalid", "Artifact destination is duplicated", path=str(artifact_path))
        paths.add(artifact_path)
        if artifact_path.parts[0] != "payload":
            raise ArtifactError(
                "manifest.invalid",
                "Runtime artifacts must be staged below payload/",
                location=location,
            )
        require_string(item["path"], f"{location}.path")
        mode = require_mode(item["mode"], f"{location}.mode")
        if mode not in {0o444, 0o555}:
            raise ArtifactError("manifest.invalid", "Published artifact modes must be read-only", location=location)
        supply_class = require_string(item["supplyClass"], f"{location}.supplyClass")
        if supply_class not in ARTIFACT_SUPPLY_CLASSES:
            raise ArtifactError(
                "manifest.invalid",
                "Runtime artifact has an unsupported supply class",
                location=location,
                supplyClass=supply_class,
            )
        require_string(item["recipe"], f"{location}.recipe")
        validate_binary_expectation(item["binary"], f"{location}.binary")
        if item["signature"] not in {"none", "record", "require-lock"}:
            raise ArtifactError("manifest.invalid", "Artifact signature policy is invalid", location=location)
        if item["binary"]["format"] == "pe" and item["signature"] != "none":
            raise ArtifactError("manifest.invalid", "PE inputs cannot use the Mach-O signature policy", location=location)
        if item["binary"]["format"] == "mach-o" and item["signature"] == "record":
            raise ArtifactError(
                "manifest.invalid",
                "Mach-O signatures must be either absent by contract or pinned in the lock",
                location=location,
            )
        validate_template_tokens(item["path"], binding_names, f"{location}.path")
    return identifiers


def validate_generated_files(items: Any) -> set[pathlib.PurePosixPath]:
    identifiers: set[str] = set()
    paths: set[pathlib.PurePosixPath] = set()
    for index, raw_item in enumerate(require_list(items, "generatedFiles")):
        location = f"generatedFiles[{index}]"
        item = require_object(
            raw_item,
            location,
            required={"id", "artifactPath", "mode", "format", "content"},
            allowed={"id", "artifactPath", "mode", "format", "content"},
        )
        identifier = require_id(item["id"], f"{location}.id")
        if identifier in identifiers:
            raise ArtifactError("manifest.invalid", "Generated file identifier is duplicated", id=identifier)
        identifiers.add(identifier)
        artifact_path = safe_relative_path(item["artifactPath"], f"{location}.artifactPath")
        if artifact_path in paths:
            raise ArtifactError("manifest.invalid", "Generated destination is duplicated", path=str(artifact_path))
        paths.add(artifact_path)
        if artifact_path.parts[0] not in {"config", "payload"}:
            raise ArtifactError(
                "manifest.invalid",
                "Generated files must be staged below config/ or payload/",
                location=location,
            )
        mode = require_mode(item["mode"], f"{location}.mode")
        if mode not in {0o444, 0o555}:
            raise ArtifactError("manifest.invalid", "Generated file modes must be read-only", location=location)
        if item["format"] not in {"json", "plist", "text"}:
            raise ArtifactError("manifest.invalid", "Generated format is invalid", location=location)
        if item["format"] == "text" and not isinstance(item["content"], str):
            raise ArtifactError("manifest.invalid", "Generated text content must be a string", location=location)
    return paths


def validate_destination_set(paths: set[pathlib.PurePosixPath]) -> None:
    normalized: dict[str, pathlib.PurePosixPath] = {}
    for path in paths:
        casefolded = path.as_posix().casefold()
        if casefolded in normalized:
            raise ArtifactError(
                "manifest.invalid",
                "Artifact destinations collide on a case-insensitive filesystem",
                paths=sorted([str(path), str(normalized[casefolded])]),
            )
        normalized[casefolded] = path
    ordered = sorted(normalized)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if right.startswith(left + "/"):
                raise ArtifactError(
                    "manifest.invalid",
                    "Artifact file destinations have a path-prefix collision",
                    paths=[str(normalized[left]), str(normalized[right])],
                )


def validate_plan(items: Any, location: str, binding_names: set[str]) -> list[dict[str, Any]]:
    identifiers: set[str] = set()
    common_allowed = {
        "id",
        "resource",
        "action",
        "target",
        "source",
        "marker",
        "backup",
        "expectedSha256",
        "atomic",
    }
    validated: list[dict[str, Any]] = []
    for index, raw_item in enumerate(require_list(items, location)):
        item_location = f"{location}[{index}]"
        item = require_object(
            raw_item,
            item_location,
            required={"id", "resource", "action", "target"},
            allowed=common_allowed,
        )
        identifier = require_id(item["id"], f"{item_location}.id")
        if identifier in identifiers:
            raise ArtifactError("manifest.invalid", "Plan operation identifier is duplicated", id=identifier)
        identifiers.add(identifier)
        require_id(item["resource"], f"{item_location}.resource")
        action = item["action"]
        if action not in PLAN_ACTIONS:
            raise ArtifactError("manifest.invalid", "Plan action is invalid", location=item_location, action=action)
        require_string(item["target"], f"{item_location}.target")
        validate_template_tokens(item["target"], binding_names, f"{item_location}.target")
        source_actions = {
            "assert_absent_or_owned",
            "create_file",
            "remove",
            "remove_tree",
            "replace_file",
            "replace_tree",
            "restore",
        }
        if action in source_actions:
            safe_relative_path(item.get("source"), f"{item_location}.source")
        elif "source" in item:
            raise ArtifactError("manifest.invalid", "Plan source is not valid for this action", location=item_location)
        marker_actions = {"assert_absent_or_owned", "remove_tree"}
        if action in marker_actions:
            safe_relative_path(item.get("marker"), f"{item_location}.marker")
        elif "marker" in item:
            raise ArtifactError("manifest.invalid", "Plan marker is not valid for this action", location=item_location)
        if action in {"backup", "restore"}:
            require_string(item.get("backup"), f"{item_location}.backup")
            validate_template_tokens(item["backup"], binding_names, f"{item_location}.backup")
        elif "backup" in item:
            raise ArtifactError("manifest.invalid", "Plan backup is not valid for this action", location=item_location)
        if action in {"assert_sha256", "restore"}:
            require_sha256(item.get("expectedSha256"), f"{item_location}.expectedSha256")
        elif "expectedSha256" in item:
            raise ArtifactError(
                "manifest.invalid",
                "Expected SHA-256 is not valid for this action",
                location=item_location,
            )
        if "atomic" in item:
            require_bool(item["atomic"], f"{item_location}.atomic")
            if action not in INSTALL_EFFECTS | {"restore"}:
                raise ArtifactError("manifest.invalid", "Atomic is not valid for this action", location=item_location)
        validated.append(item)
    return validated


def validate_plan_inverses(install: list[dict[str, Any]], uninstall: list[dict[str, Any]]) -> None:
    install_effects: dict[str, dict[str, Any]] = {}
    uninstall_effects: dict[str, dict[str, Any]] = {}
    for item in install:
        if item["action"] not in INSTALL_EFFECTS:
            continue
        resource = item["resource"]
        if resource in install_effects:
            raise ArtifactError("manifest.invalid", "Install resource has multiple mutating operations", resource=resource)
        install_effects[resource] = item
    for item in uninstall:
        if item["action"] not in UNINSTALL_EFFECTS:
            continue
        resource = item["resource"]
        if resource in uninstall_effects:
            raise ArtifactError("manifest.invalid", "Uninstall resource has multiple mutating operations", resource=resource)
        uninstall_effects[resource] = item
    if install_effects.keys() != uninstall_effects.keys():
        raise ArtifactError(
            "manifest.invalid",
            "Install and uninstall resources do not have exact inverses",
            missing=sorted(install_effects.keys() - uninstall_effects.keys()),
            extra=sorted(uninstall_effects.keys() - install_effects.keys()),
        )
    for resource, install_item in install_effects.items():
        uninstall_item = uninstall_effects[resource]
        if install_item.get("atomic") is not True:
            raise ArtifactError("manifest.invalid", "Install mutation must be atomic", resource=resource)
        expected_action = INVERSE_ACTIONS[install_item["action"]]
        if uninstall_item["action"] != expected_action:
            raise ArtifactError(
                "manifest.invalid",
                "Uninstall action is not the required inverse",
                resource=resource,
                expected=expected_action,
                actual=uninstall_item["action"],
            )
        if uninstall_item["target"] != install_item["target"]:
            raise ArtifactError("manifest.invalid", "Install and uninstall targets differ", resource=resource)
        if uninstall_item["source"] != install_item["source"]:
            raise ArtifactError("manifest.invalid", "Install and uninstall source guards differ", resource=resource)
        if expected_action == "remove_tree" and uninstall_item["marker"] not in {
            item.get("marker") for item in install if item["resource"] == resource
        }:
            raise ArtifactError("manifest.invalid", "Tree ownership markers differ", resource=resource)
        if expected_action == "restore":
            if uninstall_item.get("atomic") is not True:
                raise ArtifactError("manifest.invalid", "Restoration mutation must be atomic", resource=resource)
            backups = [
                item
                for item in install
                if item["resource"] == resource and item["action"] == "backup"
            ]
            if len(backups) != 1 or backups[0]["backup"] != uninstall_item["backup"]:
                raise ArtifactError("manifest.invalid", "Replacement resource needs one matching backup", resource=resource)
        peers = [item for item in install if item["resource"] == resource]
        required_guard = {
            "create_file": "assert_absent",
            "replace_file": "assert_sha256",
            "replace_tree": "assert_absent_or_owned",
        }[install_item["action"]]
        guards = [item for item in peers if item["action"] == required_guard]
        if len(guards) != 1 or guards[0]["target"] != install_item["target"]:
            raise ArtifactError(
                "manifest.invalid",
                "Install mutation needs one matching ownership or stock guard",
                resource=resource,
                guard=required_guard,
            )
        if required_guard == "assert_absent_or_owned" and (
            guards[0]["source"] != install_item["source"] or guards[0].get("marker") != uninstall_item.get("marker")
        ):
            raise ArtifactError("manifest.invalid", "Tree ownership guards differ", resource=resource)


def validate_mutable_state(items: Any, binding_names: set[str]) -> None:
    identifiers: set[str] = set()
    for index, raw_item in enumerate(require_list(items, "mutableState")):
        location = f"mutableState[{index}]"
        item = require_object(
            raw_item,
            location,
            required={"id", "kind", "location", "owner", "lifecycle"},
            allowed={"id", "kind", "location", "owner", "lifecycle"},
        )
        identifier = require_id(item["id"], f"{location}.id")
        if identifier in identifiers:
            raise ArtifactError("manifest.invalid", "Mutable-state identifier is duplicated", id=identifier)
        identifiers.add(identifier)
        if item["kind"] not in {"directory", "file", "launch-services", "launchd-job", "mach-service"}:
            raise ArtifactError("manifest.invalid", "Mutable-state kind is invalid", location=location)
        value = require_string(item["location"], f"{location}.location")
        validate_template_tokens(value, binding_names, f"{location}.location")
        if item["owner"] not in {"runtime", "shared", "user"}:
            raise ArtifactError("manifest.invalid", "Mutable-state owner is invalid", location=location)
        if item["lifecycle"] not in {"restored", "retained", "transient"}:
            raise ArtifactError("manifest.invalid", "Mutable-state lifecycle is invalid", location=location)


def validate_template_tokens(value: str, binding_names: set[str], location: str) -> None:
    known = binding_names | {"REPO_ROOT", "HOME"}
    unknown = sorted(set(TOKEN_PATTERN.findall(value)) - known)
    if unknown:
        raise ArtifactError("manifest.invalid", "Template contains unknown bindings", location=location, bindings=unknown)


def validate_manifest_structure(manifest: Any) -> dict[str, Any]:
    manifest = require_object(
        manifest,
        "manifest",
        required={
            "schemaVersion",
            "artifact",
            "bindings",
            "gitSources",
            "prerequisites",
            "sourceFiles",
            "artifacts",
            "generatedFiles",
            "mutableState",
            "allowedTargetRoots",
            "installPlan",
            "uninstallPlan",
            "sealing",
        },
        allowed={
            "schemaVersion",
            "artifact",
            "bindings",
            "gitSources",
            "prerequisites",
            "sourceFiles",
            "artifacts",
            "generatedFiles",
            "mutableState",
            "allowedTargetRoots",
            "installPlan",
            "uninstallPlan",
            "sealing",
        },
    )
    if manifest["schemaVersion"] != 1:
        raise ArtifactError("manifest.invalid", "Unsupported manifest schema version")
    artifact = require_object(
        manifest["artifact"],
        "artifact",
        required={"id", "version", "description", "supportMatrix", "buildCommand"},
        allowed={"id", "version", "description", "supportMatrix", "buildCommand"},
    )
    require_slug(artifact["id"], "artifact.id")
    require_version(artifact["version"], "artifact.version")
    require_string(artifact["description"], "artifact.description")
    safe_relative_path(artifact["supportMatrix"], "artifact.supportMatrix")
    build_command = require_list(artifact["buildCommand"], "artifact.buildCommand")
    if not build_command or any(not isinstance(argument, str) or not argument for argument in build_command):
        raise ArtifactError("manifest.invalid", "Artifact build command is invalid")
    binding_names = validate_binding_definitions(manifest["bindings"])
    validate_git_sources(manifest["gitSources"], binding_names)
    validate_prerequisites(manifest["prerequisites"], binding_names)
    validate_source_files(manifest["sourceFiles"], binding_names)
    artifact_ids = validate_artifacts(manifest["artifacts"], binding_names)
    generated_paths = validate_generated_files(manifest["generatedFiles"])
    artifact_paths = {pathlib.PurePosixPath(item["artifactPath"]) for item in manifest["artifacts"]}
    duplicates = sorted(str(path) for path in artifact_paths & generated_paths)
    if duplicates:
        raise ArtifactError("manifest.invalid", "Generated files overlap artifact destinations", paths=duplicates)
    validate_destination_set(artifact_paths | generated_paths)
    validate_mutable_state(manifest["mutableState"], binding_names)
    allowed_roots = require_list(manifest["allowedTargetRoots"], "allowedTargetRoots")
    if not allowed_roots:
        raise ArtifactError("manifest.invalid", "At least one target root is required")
    for index, root in enumerate(allowed_roots):
        require_string(root, f"allowedTargetRoots[{index}]")
        validate_template_tokens(root, binding_names, f"allowedTargetRoots[{index}]")
        if root not in TRUSTED_ALLOWED_TARGET_ROOTS:
            raise ArtifactError(
                "manifest.invalid",
                "Allowed target root is outside the trusted policy",
                location=f"allowedTargetRoots[{index}]",
                root=root,
            )
    install_plan = validate_plan(manifest["installPlan"], "installPlan", binding_names)
    uninstall_plan = validate_plan(manifest["uninstallPlan"], "uninstallPlan", binding_names)
    validate_plan_inverses(install_plan, uninstall_plan)
    sealing_base_fields = {"mode", "bundleId", "teamId", "identity", "timestamp"}
    sealing_output_fields = {
        "bundlePath",
        "executableArtifactId",
        "attestationPath",
        "codeResourcesPath",
    }
    sealing = require_object(
        manifest["sealing"],
        "sealing",
        required=sealing_base_fields,
        allowed=sealing_base_fields | sealing_output_fields,
    )
    if sealing["mode"] != "separate-step":
        raise ArtifactError("manifest.invalid", "Only separate-step sealing is supported")
    present_output_fields = sealing_output_fields & sealing.keys()
    if present_output_fields and present_output_fields != sealing_output_fields:
        raise ArtifactError(
            "manifest.invalid",
            "Post-build sealing fields must be declared together",
            missing=sorted(sealing_output_fields - present_output_fields),
        )
    if present_output_fields:
        bundle_path = safe_relative_path(sealing["bundlePath"], "sealing.bundlePath")
        if bundle_path.suffix != ".app" or bundle_path.parts[0] != "payload":
            raise ArtifactError(
                "manifest.invalid",
                "Sealed bundle path must identify a payload app bundle",
            )
        executable_artifact_id = require_id(
            sealing["executableArtifactId"],
            "sealing.executableArtifactId",
        )
        if executable_artifact_id not in artifact_ids:
            raise ArtifactError(
                "manifest.invalid",
                "Sealed executable artifact is not declared",
                id=executable_artifact_id,
            )
        executable_item = next(
            item for item in manifest["artifacts"] if item["id"] == executable_artifact_id
        )
        executable_path = pathlib.PurePosixPath(executable_item["artifactPath"])
        try:
            executable_relative = executable_path.relative_to(bundle_path)
        except ValueError as error:
            raise ArtifactError(
                "manifest.invalid",
                "Sealed executable must be staged inside the declared bundle",
                id=executable_artifact_id,
            ) from error
        if executable_relative.parts[:2] != ("Contents", "MacOS"):
            raise ArtifactError(
                "manifest.invalid",
                "Sealed executable must be staged below Contents/MacOS",
                id=executable_artifact_id,
            )
        if executable_item["binary"]["format"] != "mach-o":
            raise ArtifactError("manifest.invalid", "Sealed executable must be Mach-O")
        for item in manifest["artifacts"]:
            if item["id"] == executable_artifact_id:
                continue
            item_path = pathlib.PurePosixPath(item["artifactPath"])
            try:
                item_path.relative_to(bundle_path)
            except ValueError:
                continue
            raise ArtifactError(
                "manifest.invalid",
                "Nested bundle code is not supported by the sealing contract",
                id=item["id"],
            )
        bundle_generated_paths: set[pathlib.PurePosixPath] = set()
        for item in manifest["generatedFiles"]:
            item_path = pathlib.PurePosixPath(item["artifactPath"])
            try:
                item_relative = item_path.relative_to(bundle_path)
            except ValueError:
                continue
            bundle_generated_paths.add(item_relative)
            if item_relative != pathlib.PurePosixPath("Contents/Info.plist") and item_relative.parts[
                :2
            ] != ("Contents", "Resources"):
                raise ArtifactError(
                    "manifest.invalid",
                    "Generated bundle files must be Info.plist or resources",
                    id=item["id"],
                )
        if pathlib.PurePosixPath("Contents/Info.plist") not in bundle_generated_paths:
            raise ArtifactError("manifest.invalid", "Sealed bundle must declare Info.plist")
        attestation_relative = safe_relative_path(
            sealing["attestationPath"],
            "sealing.attestationPath",
        )
        code_resources_relative = safe_relative_path(
            sealing["codeResourcesPath"],
            "sealing.codeResourcesPath",
        )
        if attestation_relative.parts[:2] != ("Contents", "Resources"):
            raise ArtifactError(
                "manifest.invalid",
                "Sealing attestation must be staged below Contents/Resources",
            )
        if code_resources_relative != pathlib.PurePosixPath(
            "Contents/_CodeSignature/CodeResources"
        ):
            raise ArtifactError(
                "manifest.invalid",
                "CodeResources must use the canonical app-bundle location",
            )
        sealing_paths = {
            bundle_path / attestation_relative,
            bundle_path / code_resources_relative,
            SEALING_PROVENANCE_PATH,
        }
        overlaps = sorted(str(path) for path in sealing_paths & (artifact_paths | generated_paths))
        if overlaps:
            raise ArtifactError(
                "manifest.invalid",
                "Sealing outputs overlap declared artifact files",
                paths=overlaps,
            )
        validate_destination_set(artifact_paths | generated_paths | sealing_paths)
    require_string(sealing["bundleId"], "sealing.bundleId")
    team_id = require_string(sealing["teamId"], "sealing.teamId")
    identity = require_string(sealing["identity"], "sealing.identity")
    if present_output_fields:
        if not re.fullmatch(r"[A-Z0-9]{10}", team_id):
            raise ArtifactError("manifest.invalid", "Developer ID Team ID is invalid")
        if not re.fullmatch(
            rf"Developer ID Application: .+ \({re.escape(team_id)}\)",
            identity,
        ):
            raise ArtifactError(
                "manifest.invalid",
                "Sealing identity must be a Developer ID Application identity for the Team ID",
            )
    require_bool(sealing["timestamp"], "sealing.timestamp")
    if sealing["timestamp"]:
        raise ArtifactError(
            "manifest.invalid",
            "Content-addressed Developer ID sealing cannot use a timestamp",
        )
    manifest["_artifactIds"] = artifact_ids
    manifest["_bindingNames"] = binding_names
    return manifest


def validate_lock_structure(lock_data: Any, artifact_policies: dict[str, str]) -> dict[str, Any]:
    lock_data = require_object(
        lock_data,
        "lock",
        required={"schemaVersion", "manifestSha256", "artifacts"},
        allowed={"schemaVersion", "manifestSha256", "artifacts"},
    )
    if lock_data["schemaVersion"] != 1:
        raise ArtifactError("lock.invalid", "Unsupported lock schema version")
    require_sha256(lock_data["manifestSha256"], "lock.manifestSha256")
    resolved_ids: set[str] = set()
    for index, raw_item in enumerate(require_list(lock_data["artifacts"], "lock.artifacts")):
        location = f"lock.artifacts[{index}]"
        item = require_object(
            raw_item,
            location,
            required={"id", "sha256"},
            allowed={"id", "sha256", "signature"},
        )
        identifier = require_id(item["id"], f"{location}.id")
        if identifier in resolved_ids:
            raise ArtifactError("lock.invalid", "Lock artifact identifier is duplicated", id=identifier)
        resolved_ids.add(identifier)
        require_sha256(item["sha256"], f"{location}.sha256")
        signature_required = artifact_policies.get(identifier) == "require-lock"
        if signature_required != ("signature" in item):
            raise ArtifactError(
                "lock.invalid",
                "Lock signature presence does not match the manifest policy",
                id=identifier,
                required=signature_required,
            )
        if "signature" in item:
            signature = require_object(
                item["signature"],
                f"{location}.signature",
                required={"kind", "identifier", "teamIdentifier", "cdhash"},
                allowed={"kind", "identifier", "teamIdentifier", "cdhash"},
            )
            if signature["kind"] not in {"adhoc", "developer-id"}:
                raise ArtifactError("lock.invalid", "Code signature kind is invalid", location=location)
            require_string(signature["identifier"], f"{location}.signature.identifier")
            if signature["teamIdentifier"] is not None:
                require_string(signature["teamIdentifier"], f"{location}.signature.teamIdentifier")
            cdhash = require_string(signature["cdhash"], f"{location}.signature.cdhash")
            if not re.fullmatch(r"[0-9a-f]{40}", cdhash):
                raise ArtifactError("lock.invalid", "CDHash must be lowercase SHA-1 hex", location=location)
            if signature["kind"] == "developer-id" and signature["teamIdentifier"] is None:
                raise ArtifactError("lock.invalid", "Developer ID signatures require a team identifier", location=location)
            if signature["kind"] == "adhoc" and signature["teamIdentifier"] is not None:
                raise ArtifactError("lock.invalid", "Ad hoc signatures cannot have a team identifier", location=location)
    artifact_ids = set(artifact_policies)
    if resolved_ids != artifact_ids:
        raise ArtifactError(
            "lock.invalid",
            "Manifest and lock artifact identifiers differ",
            missing=sorted(artifact_ids - resolved_ids),
            extra=sorted(resolved_ids - artifact_ids),
        )
    return lock_data


def load_contract(manifest_path: pathlib.Path, lock_path: pathlib.Path) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    manifest_raw = load_json(manifest_path)
    manifest_hash = sha256_bytes(canonical_json_bytes(manifest_raw))
    manifest = validate_manifest_structure(manifest_raw)
    artifact_ids = manifest.pop("_artifactIds")
    manifest.pop("_bindingNames")
    artifact_policies = {item["id"]: item["signature"] for item in manifest["artifacts"]}
    if set(artifact_policies) != artifact_ids:
        raise ArtifactError("manifest.invalid", "Artifact identity validation failed")
    lock_data = validate_lock_structure(load_json(lock_path), artifact_policies)
    if lock_data["manifestSha256"] != manifest_hash:
        raise ArtifactError(
            "lock.invalid",
            "Lockfile does not match the canonical manifest",
            expected=manifest_hash,
            actual=lock_data["manifestSha256"],
        )
    lock_hash = sha256_bytes(canonical_json_bytes(lock_data))
    return manifest, lock_data, manifest_hash, lock_hash


def expand_template(value: str, bindings: dict[str, str], location: str) -> str:
    result = value
    for _ in range(32):
        tokens = TOKEN_PATTERN.findall(result)
        if not tokens:
            return result
        missing = sorted(set(tokens) - bindings.keys())
        if missing:
            raise ArtifactError("binding.missing", "Template binding is unresolved", location=location, bindings=missing)
        expanded = TOKEN_PATTERN.sub(lambda match: bindings[match.group(1)], result)
        if expanded == result:
            break
        result = expanded
    raise ArtifactError("binding.recursive", "Template bindings contain a recursion", location=location, value=value)


def resolve_bindings(manifest: dict[str, Any], bindings_path: pathlib.Path, phase: str) -> dict[str, str]:
    provided: dict[str, Any] = {}
    if bindings_path.exists():
        loaded = load_json(bindings_path)
        if not isinstance(loaded, dict):
            raise ArtifactError("binding.invalid", "Bindings file must contain a JSON object", path=str(bindings_path))
        provided = loaded
    definitions = {item["name"]: item for item in manifest["bindings"]}
    unknown = sorted(provided.keys() - definitions.keys())
    if unknown:
        raise ArtifactError("binding.unknown", "Bindings file contains unknown names", bindings=unknown)
    values: dict[str, str] = {
        "REPO_ROOT": str(REPO_ROOT.resolve()),
        "HOME": str(pathlib.Path.home().resolve()),
    }
    for name, definition in definitions.items():
        if name in provided:
            if not isinstance(provided[name], str) or not provided[name]:
                raise ArtifactError("binding.invalid", "Binding values must be non-empty strings", binding=name)
            values[name] = provided[name]
        elif "default" in definition:
            values[name] = definition["default"]
    required = sorted(
        name
        for name, definition in definitions.items()
        if phase in definition["phases"] and definition["required"] and name not in values
    )
    if required:
        raise ArtifactError("binding.missing", "Required bindings are missing", bindings=required, phase=phase)
    for name in list(values):
        values[name] = expand_template(values[name], values, f"binding.{name}")
    return values


def resolve_path(template: str, bindings: dict[str, str], location: str) -> pathlib.Path:
    expanded = expand_template(template, bindings, location)
    path = pathlib.Path(expanded).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return pathlib.Path(os.path.abspath(path))


def reject_symlink_components(path: pathlib.Path, *, include_leaf: bool = True) -> None:
    if not path.is_absolute():
        raise ArtifactError("path.unsafe", "Path validation requires an absolute path", path=str(path))
    current = pathlib.Path(path.anchor)
    parts = path.parts[1:] if include_leaf else path.parts[1:-1]
    for part in parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ArtifactError("path.symlink", "Symlink path components are not allowed", path=str(current))


def resolved_without_symlinks(path: pathlib.Path, *, include_leaf: bool = True) -> pathlib.Path:
    reject_symlink_components(path, include_leaf=include_leaf)
    return path.resolve(strict=False)


def run_command(argv: list[str], *, cwd: pathlib.Path | None = None, code: str = "command.failed") -> str:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise ArtifactError(code, "Required command is unavailable", command=argv[0]) from error
    if result.returncode != 0:
        raise ArtifactError(
            code,
            "Command failed",
            command=argv,
            exitCode=result.returncode,
            stderr=result.stderr.strip(),
        )
    return result.stdout.strip()


def normalize_remote(value: str) -> str:
    remote = value.strip()
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote.removeprefix("git@github.com:")
    if remote.endswith(".git"):
        remote = remote[:-4]
    return remote.rstrip("/")


def validate_git_source(item: dict[str, Any], bindings: dict[str, str]) -> dict[str, Any]:
    path = resolve_path(item["path"], bindings, f"gitSources.{item['id']}.path")
    resolved_without_symlinks(path)
    if not path.is_dir():
        raise ArtifactError("git.missing", "Git source directory is missing", id=item["id"], path=str(path))
    actual_revision = run_command(["git", "rev-parse", "HEAD"], cwd=path, code="git.invalid")
    if item["revision"] != "self" and actual_revision != item["revision"]:
        raise ArtifactError(
            "git.revision",
            "Git source is not at the pinned commit",
            id=item["id"],
            expected=item["revision"],
            actual=actual_revision,
        )
    remotes = run_command(["git", "remote"], cwd=path, code="git.invalid").splitlines()
    actual_remotes = {
        normalize_remote(run_command(["git", "remote", "get-url", remote], cwd=path, code="git.invalid"))
        for remote in remotes
        if remote
    }
    expected_remote = normalize_remote(item["remote"])
    if expected_remote not in actual_remotes:
        raise ArtifactError(
            "git.remote",
            "Git source does not contain the expected remote",
            id=item["id"],
            expected=expected_remote,
            actual=sorted(actual_remotes),
        )
    if item["cleanPolicy"] == "all":
        dirty = run_command(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=path,
            code="git.invalid",
        )
    else:
        unstaged = subprocess.run(["git", "diff", "--quiet"], cwd=path, check=False)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=path, check=False)
        if unstaged.returncode not in {0, 1} or staged.returncode not in {0, 1}:
            raise ArtifactError("git.invalid", "Git tracked-state check failed", id=item["id"])
        dirty = "tracked changes" if unstaged.returncode or staged.returncode else ""
    if dirty:
        raise ArtifactError("git.dirty", "Git source is dirty", id=item["id"], detail=dirty)
    tree = run_command(["git", "rev-parse", "HEAD^{tree}"], cwd=path, code="git.invalid")
    return {
        "id": item["id"],
        "revision": actual_revision,
        "tree": tree,
        "remote": expected_remote,
    }


def require_regular_file(path: pathlib.Path, code: str, identifier: str) -> None:
    reject_symlink_components(path)
    try:
        path.lstat()
    except FileNotFoundError as error:
        raise ArtifactError(code, "Required file is missing", id=identifier, path=str(path)) from error
    if path.is_symlink():
        raise ArtifactError("path.symlink", "Symlink inputs are not allowed", id=identifier, path=str(path))
    if not path.is_file():
        raise ArtifactError(code, "Required input is not a regular file", id=identifier, path=str(path))


def validate_source_file(
    item: dict[str, Any],
    bindings: dict[str, str],
    git_roots: list[tuple[str, pathlib.Path]],
) -> dict[str, Any]:
    path = resolve_path(item["path"], bindings, f"sourceFiles.{item['id']}.path")
    require_regular_file(path, "source.missing", item["id"])
    owners: list[tuple[str, pathlib.Path]] = []
    for source_id, root in git_roots:
        try:
            if os.path.commonpath([str(path), str(root)]) == str(root):
                owners.append((source_id, root))
        except ValueError:
            continue
    if not owners:
        raise ArtifactError(
            "source.untracked",
            "Repository source file is not below a declared Git source",
            id=item["id"],
        )
    source_id, source_root = max(owners, key=lambda value: len(value[1].parts))
    relative = path.relative_to(source_root).as_posix()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=source_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        raise ArtifactError(
            "source.untracked",
            "Repository source file is not tracked by Git",
            id=item["id"],
            gitSource=source_id,
        )
    actual_hash = sha256_file(path)
    if actual_hash != item["sha256"]:
        raise ArtifactError(
            "source.hash",
            "Repository source file hash does not match the manifest",
            id=item["id"],
            expected=item["sha256"],
            actual=actual_hash,
        )
    return {
        "id": item["id"],
        "gitSource": source_id,
        "sha256": actual_hash,
        "supplyClass": item["supplyClass"],
    }


def codesign_info(
    path: pathlib.Path,
    *,
    required: bool,
    isolate_bundle_executable: bool = True,
) -> dict[str, Any] | None:
    codesign = pathlib.Path("/usr/bin/codesign")
    if not codesign.is_file():
        if required:
            raise ArtifactError("input.signature", "codesign is unavailable", path=str(codesign))
        return None
    verification_path = path
    temporary_path: pathlib.Path | None = None
    if isolate_bundle_executable and any(part.lower().endswith(".app") for part in path.parts):
        descriptor, temporary_name = tempfile.mkstemp(prefix="runtime-artifact-codesign-")
        os.close(descriptor)
        temporary_path = pathlib.Path(temporary_name)
        shutil.copyfile(path, temporary_path)
        temporary_path.chmod(0o555)
        verification_path = temporary_path
    try:
        verify = subprocess.run(
            [str(codesign), "--verify", "--strict", "--all-architectures", str(verification_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if verify.returncode != 0:
            if required:
                raise ArtifactError(
                    "input.signature",
                    "Code signature verification failed",
                    path=str(path),
                    stderr=verify.stderr.strip(),
                )
            return None
        detail = subprocess.run(
            [str(codesign), "-dv", "--verbose=4", str(verification_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        output = detail.stderr + "\n" + detail.stdout
        fields: dict[str, str | None] = {
            "kind": None,
            "identifier": None,
            "teamIdentifier": None,
            "cdhash": None,
        }
        for line in output.splitlines():
            if line == "Signature=adhoc":
                fields["kind"] = "adhoc"
            elif line.startswith("Identifier="):
                fields["identifier"] = line.split("=", 1)[1]
            elif line.startswith("TeamIdentifier="):
                value = line.split("=", 1)[1]
                fields["teamIdentifier"] = None if value == "not set" else value
            elif line.startswith("CDHash="):
                fields["cdhash"] = line.split("=", 1)[1].lower()
        if fields["kind"] is None and fields["teamIdentifier"] is not None:
            fields["kind"] = "developer-id"
        if fields["identifier"] is None or fields["cdhash"] is None or fields["kind"] is None:
            raise ArtifactError("input.signature", "Code signature metadata is incomplete", path=str(path))
        return fields
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def sealing_artifact_item(manifest: dict[str, Any]) -> dict[str, Any]:
    identifier = manifest["sealing"]["executableArtifactId"]
    return next(item for item in manifest["artifacts"] if item["id"] == identifier)


def sealing_paths(
    manifest: dict[str, Any],
) -> tuple[pathlib.PurePosixPath, pathlib.PurePosixPath, pathlib.PurePosixPath, pathlib.PurePosixPath]:
    sealing = manifest["sealing"]
    bundle = pathlib.PurePosixPath(sealing["bundlePath"])
    executable = pathlib.PurePosixPath(sealing_artifact_item(manifest)["artifactPath"])
    attestation = bundle / pathlib.PurePosixPath(sealing["attestationPath"])
    code_resources = bundle / pathlib.PurePosixPath(sealing["codeResourcesPath"])
    return bundle, executable, attestation, code_resources


def locked_input_record(item: dict[str, Any], lock_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "artifactPath": item["artifactPath"],
        "binary": item["binary"],
        "mode": item["mode"],
        "recipe": item["recipe"],
        "sha256": lock_item["sha256"],
        "signature": lock_item.get("signature"),
        "supplyClass": item["supplyClass"],
    }


def sign_bundle_with_codesign(bundle: pathlib.Path, sealing: dict[str, Any]) -> None:
    codesign = pathlib.Path("/usr/bin/codesign")
    if not codesign.is_file():
        raise ArtifactError("sealing.identity", "codesign is unavailable", path=str(codesign))
    command = [
        str(codesign),
        "--force",
        "--deep",
        "--sign",
        sealing["identity"],
        "--identifier",
        sealing["bundleId"],
        "--timestamp=none",
        str(bundle),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise ArtifactError(
            "sealing.identity",
            "Developer ID bundle signing failed",
            path=str(bundle),
            stderr=result.stderr.strip(),
        )


def inspect_signed_bundle(bundle: pathlib.Path, manifest: dict[str, Any]) -> dict[str, Any]:
    sealing = manifest["sealing"]
    _, executable_relative, _, _ = sealing_paths(manifest)
    artifact_root = bundle.parents[len(pathlib.PurePosixPath(sealing["bundlePath"]).parts) - 1]
    executable = artifact_root / executable_relative
    canonical_tree_sha256(bundle)
    executable_item = sealing_artifact_item(manifest)
    binary = inspect_binary(executable, executable_item["binary"]["format"])
    if binary != executable_item["binary"]:
        raise ArtifactError(
            "sealing.signature",
            "Signed executable has the wrong file type or architecture",
            expected=executable_item["binary"],
            actual=binary,
        )
    codesign = pathlib.Path("/usr/bin/codesign")
    if not codesign.is_file():
        raise ArtifactError("sealing.signature", "codesign is unavailable", path=str(codesign))
    verify = subprocess.run(
        [
            str(codesign),
            "--verify",
            "--strict",
            "--deep",
            "--all-architectures",
            str(bundle),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        raise ArtifactError(
            "sealing.signature",
            "Signed bundle verification failed",
            path=str(bundle),
            stderr=verify.stderr.strip(),
        )
    signature = codesign_info(
        executable,
        required=True,
        isolate_bundle_executable=False,
    )
    if signature is None:
        raise ArtifactError("sealing.signature", "Signed executable has no code signature")
    detail = subprocess.run(
        [str(codesign), "-dv", "--verbose=4", str(executable)],
        check=False,
        capture_output=True,
        text=True,
    )
    if detail.returncode != 0:
        raise ArtifactError(
            "sealing.signature",
            "Signed executable metadata could not be read",
            path=str(executable),
            stderr=detail.stderr.strip(),
        )
    authorities: list[str] = []
    timestamped = False
    for line in (detail.stderr + "\n" + detail.stdout).splitlines():
        if line.startswith("Authority="):
            authorities.append(line.split("=", 1)[1])
        elif line.startswith("Timestamp="):
            timestamped = True
    info_path = bundle / "Contents" / "Info.plist"
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise ArtifactError(
            "sealing.signature",
            "Signed bundle Info.plist is invalid",
            path=str(info_path),
        ) from error
    expected_signature = {
        "kind": "developer-id",
        "identifier": sealing["bundleId"],
        "teamIdentifier": sealing["teamId"],
    }
    actual_signature = {
        "kind": signature["kind"],
        "identifier": signature["identifier"],
        "teamIdentifier": signature["teamIdentifier"],
    }
    if actual_signature != expected_signature:
        raise ArtifactError(
            "sealing.signature",
            "Signed executable identity differs from the sealing policy",
            expected=expected_signature,
            actual=actual_signature,
        )
    if not authorities or authorities[0] != sealing["identity"]:
        raise ArtifactError(
            "sealing.signature",
            "Signed executable authority differs from the sealing identity",
            expected=sealing["identity"],
            actual=authorities[0] if authorities else None,
        )
    if timestamped != sealing["timestamp"]:
        raise ArtifactError(
            "sealing.signature",
            "Signed executable timestamp policy differs",
            expected=sealing["timestamp"],
            actual=timestamped,
        )
    if info.get("CFBundleIdentifier") != sealing["bundleId"]:
        raise ArtifactError(
            "sealing.signature",
            "Signed bundle identifier differs from the sealing policy",
            expected=sealing["bundleId"],
            actual=info.get("CFBundleIdentifier"),
        )
    if info.get("CFBundleExecutable") != executable.name:
        raise ArtifactError(
            "sealing.signature",
            "Signed bundle executable differs from the sealing policy",
            expected=executable.name,
            actual=info.get("CFBundleExecutable"),
        )
    return {
        "kind": signature["kind"],
        "identifier": signature["identifier"],
        "teamIdentifier": signature["teamIdentifier"],
        "cdhash": signature["cdhash"],
        "authority": authorities[0],
        "timestamp": timestamped,
    }


def inspect_pe(path: pathlib.Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            dos_header = handle.read(64)
            if len(dos_header) != 64 or dos_header[:2] != b"MZ":
                raise ArtifactError("input.type", "PE input does not contain an MZ header", path=str(path))
            pe_offset = struct.unpack_from("<I", dos_header, 0x3C)[0]
            if pe_offset < 64 or pe_offset > 16 * 1024 * 1024:
                raise ArtifactError("input.type", "PE header offset is invalid", path=str(path))
            handle.seek(pe_offset)
            signature_and_coff = handle.read(24)
            if len(signature_and_coff) != 24 or signature_and_coff[:4] != b"PE\0\0":
                raise ArtifactError("input.type", "PE signature is invalid", path=str(path))
            machine, _, _, _, _, optional_size, characteristics = struct.unpack_from(
                "<HHIIIHH", signature_and_coff, 4
            )
            optional_header = handle.read(optional_size)
    except OSError as error:
        raise ArtifactError("input.type", "PE input could not be inspected", path=str(path)) from error
    if machine != 0x8664:
        raise ArtifactError("input.type", "Only x86-64 PE inputs are supported", path=str(path), machine=machine)
    if len(optional_header) < 148 or struct.unpack_from("<H", optional_header, 0)[0] != 0x20B:
        raise ArtifactError("input.type", "PE input is not PE32+", path=str(path))
    directory_count = struct.unpack_from("<I", optional_header, 108)[0]
    certificate_offset = 0
    certificate_size = 0
    if directory_count > 4 and len(optional_header) >= 152:
        certificate_offset, certificate_size = struct.unpack_from("<II", optional_header, 144)
    authenticode = "present" if certificate_offset or certificate_size else "absent"
    return {
        "format": "pe",
        "kind": "dll" if characteristics & 0x2000 else "executable",
        "architectures": ["x86_64"],
        "authenticode": authenticode,
    }


def inspect_macho(path: pathlib.Path) -> dict[str, Any]:
    file_tool = pathlib.Path("/usr/bin/file")
    lipo_tool = pathlib.Path("/usr/bin/lipo")
    if not file_tool.is_file() or not lipo_tool.is_file():
        raise ArtifactError("input.type", "Required Mach-O inspection tools are unavailable")
    description = run_command([str(file_tool), "-b", str(path)], code="input.type")
    if not description.startswith("Mach-O"):
        raise ArtifactError("input.type", "Input is not Mach-O", path=str(path))
    if "dynamically linked shared library" in description:
        kind = "dynamic-library"
    elif "executable" in description:
        kind = "executable"
    else:
        raise ArtifactError("input.type", "Mach-O kind is unsupported", path=str(path))
    architecture_output = run_command([str(lipo_tool), "-archs", str(path)], code="input.type")
    architectures = sorted(architecture_output.split())
    if not architectures or any(architecture not in {"arm64", "x86_64"} for architecture in architectures):
        raise ArtifactError(
            "input.type",
            "Mach-O architectures are unsupported",
            path=str(path),
            architectures=architectures,
        )
    return {"format": "mach-o", "kind": kind, "architectures": architectures}


def inspect_binary(path: pathlib.Path, expected_format: str) -> dict[str, Any]:
    if expected_format == "pe":
        return inspect_pe(path)
    if expected_format == "mach-o":
        return inspect_macho(path)
    raise ArtifactError("input.type", "Binary format is unsupported", format=expected_format)


def inspect_artifact_file(
    item: dict[str, Any],
    lock_item: dict[str, Any],
    path: pathlib.Path,
) -> dict[str, Any]:
    require_regular_file(path, "input.missing", item["id"])
    actual_hash = sha256_file(path)
    if actual_hash != lock_item["sha256"]:
        raise ArtifactError(
            "input.hash",
            "Runtime binary hash does not match the lockfile",
            id=item["id"],
            expected=lock_item["sha256"],
            actual=actual_hash,
        )
    binary = inspect_binary(path, item["binary"]["format"])
    if binary != item["binary"]:
        raise ArtifactError(
            "input.type",
            "Runtime binary has the wrong file type or architecture",
            id=item["id"],
            expected=item["binary"],
            actual=binary,
        )
    signature = None
    if binary["format"] == "mach-o":
        signature = codesign_info(path, required=item["signature"] == "require-lock")
        if item["signature"] == "none" and signature is not None:
            raise ArtifactError("input.signature", "Mach-O input is unexpectedly signed", id=item["id"])
    if item["signature"] == "require-lock":
        expected_signature = lock_item.get("signature")
        if expected_signature is None or signature != expected_signature:
            raise ArtifactError(
                "input.signature",
                "Runtime binary signature does not match the lockfile",
                id=item["id"],
                expected=expected_signature,
                actual=signature,
            )
    return {
        "id": item["id"],
        "artifactPath": item["artifactPath"],
        "binary": binary,
        "mode": item["mode"],
        "recipe": item["recipe"],
        "sha256": actual_hash,
        "signature": signature,
        "supplyClass": item["supplyClass"],
    }


def validate_artifact_input(
    item: dict[str, Any],
    lock_item: dict[str, Any],
    bindings: dict[str, str],
) -> tuple[dict[str, Any], pathlib.Path]:
    path = resolve_path(item["path"], bindings, f"artifacts.{item['id']}.path")
    return inspect_artifact_file(item, lock_item, path), path


def validate_prerequisite(item: dict[str, Any], bindings: dict[str, str]) -> dict[str, Any]:
    if item["kind"] == "command":
        actual = run_command(item["argv"], code="prerequisite.mismatch")
        expected = item.get("equals")
        if expected is not None and actual != expected:
            raise ArtifactError(
                "prerequisite.mismatch",
                "Command prerequisite does not match",
                id=item["id"],
                expected=expected,
                actual=actual,
            )
        contains = item.get("contains")
        if contains is not None and contains not in actual:
            raise ArtifactError(
                "prerequisite.mismatch",
                "Command prerequisite does not contain the expected value",
                id=item["id"],
                expected=contains,
                actual=actual,
            )
        return {"id": item["id"], "kind": "command", "actual": actual}
    path = resolve_path(item["path"], bindings, f"prerequisites.{item['id']}.path")
    require_regular_file(path, "prerequisite.mismatch", item["id"])
    try:
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
    except (plistlib.InvalidFileException, OSError) as error:
        raise ArtifactError("prerequisite.mismatch", "Prerequisite plist is unreadable", id=item["id"]) from error
    actual = plist.get(item["key"])
    if actual != item["equals"]:
        raise ArtifactError(
            "prerequisite.mismatch",
            "Plist prerequisite does not match",
            id=item["id"],
            key=item["key"],
            expected=item["equals"],
        )
    return {"id": item["id"], "kind": "plist", "key": item["key"], "actual": actual}


def validate_environment(
    manifest: dict[str, Any],
    lock_data: dict[str, Any],
    bindings_path: pathlib.Path,
    phase: str,
) -> dict[str, Any]:
    bindings = resolve_bindings(manifest, bindings_path, phase)
    lock_items = {item["id"]: item for item in lock_data["artifacts"]}
    sources: list[dict[str, Any]] = []
    git_roots: list[tuple[str, pathlib.Path]] = []
    for item in manifest["gitSources"]:
        sources.append(validate_git_source(item, bindings))
        git_roots.append(
            (
                item["id"],
                resolve_path(item["path"], bindings, f"gitSources.{item['id']}.path"),
            )
        )
    source_files = [validate_source_file(item, bindings, git_roots) for item in manifest["sourceFiles"]]
    prerequisites = [validate_prerequisite(item, bindings) for item in manifest["prerequisites"]]
    artifacts: list[dict[str, Any]] = []
    artifact_paths: dict[str, pathlib.Path] = {}
    for item in manifest["artifacts"]:
        record, source_path = validate_artifact_input(item, lock_items[item["id"]], bindings)
        artifacts.append(record)
        artifact_paths[item["id"]] = source_path
    return {
        "bindings": bindings,
        "gitSources": sources,
        "sourceFiles": source_files,
        "prerequisites": prerequisites,
        "artifacts": artifacts,
        "artifactPaths": artifact_paths,
    }


def generated_file_bytes(item: dict[str, Any]) -> bytes:
    if item["format"] == "json":
        return canonical_json_bytes(item["content"])
    if item["format"] == "plist":
        return plistlib.dumps(item["content"], fmt=plistlib.FMT_XML, sort_keys=True)
    return item["content"].encode("utf-8")


def stage_generated_file(root: pathlib.Path, item: dict[str, Any]) -> None:
    path = root / pathlib.PurePosixPath(item["artifactPath"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(generated_file_bytes(item))
    path.chmod(int(item["mode"], 8) & ~0o222)


def content_records(root: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative_path = pathlib.PurePosixPath(path.relative_to(root).as_posix())
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ArtifactError("artifact.verify", "Artifact contains a symlink", path=str(relative_path))
        if path.is_dir():
            continue
        if not path.is_file():
            raise ArtifactError("artifact.verify", "Artifact contains an unsupported file type", path=str(relative_path))
        if relative_path in SELF_REFERENTIAL_PROVENANCE:
            continue
        records.append(
            {
                "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
                "path": relative_path.as_posix(),
                "sha256": sha256_file(path),
                "size": metadata.st_size,
            }
        )
    return records


def compute_seal(manifest_hash: str, lock_hash: str, records: list[dict[str, Any]]) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "files": records,
                "lockSha256": lock_hash,
                "manifestSha256": manifest_hash,
            }
        )
    )


def make_tree_writable(root: pathlib.Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
        except FileNotFoundError:
            pass
    try:
        root.chmod(root.stat().st_mode | stat.S_IWUSR)
    except FileNotFoundError:
        pass


def make_tree_read_only(root: pathlib.Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            current = stat.S_IMODE(path.stat().st_mode)
            path.chmod(current & ~0o222)
        elif path.is_dir():
            path.chmod(0o555)
    root.chmod(0o555)


def remove_stale_sealing_paths(output_root: pathlib.Path) -> list[str]:
    removed: list[str] = []
    for path in sorted(output_root.iterdir()):
        if not path.name.startswith((".seal-", ".publish-")):
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ArtifactError(
                "artifact.publish",
                "Stale sealing path has an unsafe type",
                path=str(path),
            )
        if metadata.st_uid != os.getuid():
            raise ArtifactError(
                "artifact.publish",
                "Stale sealing path has an unexpected owner",
                path=str(path),
                owner=metadata.st_uid,
            )
        canonical_tree_sha256(path)
        make_tree_writable(path)
        shutil.rmtree(path)
        removed.append(path.name)
    if removed:
        fsync_directory(output_root)
    return removed


def fsync_tree(root: pathlib.Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    for path in sorted((entry for entry in root.rglob("*") if entry.is_dir()), reverse=True):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path: pathlib.Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def require_canonical_json(path: pathlib.Path, value: Any) -> None:
    if path.read_bytes() != canonical_json_bytes(value):
        raise ArtifactError("artifact.verify", "Artifact JSON is not canonical", path=str(path))


def validate_sealed_git_sources(manifest: dict[str, Any], value: Any) -> None:
    actual_sources = require_list(value, "artifact.buildInputs.gitSources")
    if len(actual_sources) != len(manifest["gitSources"]):
        raise ArtifactError("artifact.verify", "Sealed Git-source count differs from the manifest")
    for index, (expected, raw_actual) in enumerate(zip(manifest["gitSources"], actual_sources, strict=True)):
        actual = require_object(
            raw_actual,
            f"artifact.buildInputs.gitSources[{index}]",
            required={"id", "revision", "tree", "remote"},
            allowed={"id", "revision", "tree", "remote"},
        )
        if actual["id"] != expected["id"] or actual["remote"] != normalize_remote(expected["remote"]):
            raise ArtifactError("artifact.verify", "Sealed Git-source identity differs", id=expected["id"])
        if not re.fullmatch(r"[0-9a-f]{40}", require_string(actual["revision"], "git revision")):
            raise ArtifactError("artifact.verify", "Sealed Git revision is invalid", id=expected["id"])
        if not re.fullmatch(r"[0-9a-f]{40}", require_string(actual["tree"], "git tree")):
            raise ArtifactError("artifact.verify", "Sealed Git tree is invalid", id=expected["id"])
        if expected["revision"] != "self" and actual["revision"] != expected["revision"]:
            raise ArtifactError("artifact.verify", "Sealed Git revision differs from its pin", id=expected["id"])


def validate_sealed_prerequisites(manifest: dict[str, Any], value: Any) -> None:
    actual_prerequisites = require_list(value, "artifact.buildInputs.prerequisites")
    if len(actual_prerequisites) != len(manifest["prerequisites"]):
        raise ArtifactError("artifact.verify", "Sealed prerequisite count differs from the manifest")
    for index, (expected, raw_actual) in enumerate(
        zip(manifest["prerequisites"], actual_prerequisites, strict=True)
    ):
        location = f"artifact.buildInputs.prerequisites[{index}]"
        if expected["kind"] == "command":
            actual = require_object(
                raw_actual,
                location,
                required={"id", "kind", "actual"},
                allowed={"id", "kind", "actual"},
            )
            output = require_string(actual["actual"], f"{location}.actual")
            matches = output == expected["equals"] if "equals" in expected else expected["contains"] in output
        else:
            actual = require_object(
                raw_actual,
                location,
                required={"id", "kind", "key", "actual"},
                allowed={"id", "kind", "key", "actual"},
            )
            matches = actual["key"] == expected["key"] and actual["actual"] == expected["equals"]
        if actual["id"] != expected["id"] or actual["kind"] != expected["kind"] or not matches:
            raise ArtifactError("artifact.verify", "Sealed prerequisite record differs", id=expected["id"])


def source_git_owner(manifest: dict[str, Any], source_path: str) -> str:
    candidates = [
        item
        for item in manifest["gitSources"]
        if source_path == item["path"] or source_path.startswith(item["path"].rstrip("/") + "/")
    ]
    if not candidates:
        raise ArtifactError("artifact.verify", "Source file has no sealed Git owner", path=source_path)
    return max(candidates, key=lambda item: len(item["path"]))["id"]


def validate_sealing_attestation(
    value: Any,
    manifest: dict[str, Any],
    lock_data: dict[str, Any],
    manifest_hash: str,
    lock_hash: str,
) -> dict[str, Any]:
    attestation = require_object(
        value,
        "artifact.sealingAttestation",
        required={
            "schemaVersion",
            "artifactId",
            "artifactVersion",
            "bundleId",
            "sourceSealId",
            "manifestSha256",
            "lockSha256",
            "sourceTreeSha256",
            "sourceExecutableSha256",
        },
        allowed={
            "schemaVersion",
            "artifactId",
            "artifactVersion",
            "bundleId",
            "sourceSealId",
            "manifestSha256",
            "lockSha256",
            "sourceTreeSha256",
            "sourceExecutableSha256",
        },
    )
    if attestation["schemaVersion"] != 1:
        raise ArtifactError("artifact.verify", "Sealing attestation schema is unsupported")
    for key in [
        "sourceSealId",
        "manifestSha256",
        "lockSha256",
        "sourceTreeSha256",
        "sourceExecutableSha256",
    ]:
        require_sha256(attestation[key], f"artifact.sealingAttestation.{key}")
    lock_items = {item["id"]: item for item in lock_data["artifacts"]}
    executable_id = manifest["sealing"]["executableArtifactId"]
    expected = {
        "artifactId": manifest["artifact"]["id"],
        "artifactVersion": manifest["artifact"]["version"],
        "bundleId": manifest["sealing"]["bundleId"],
        "manifestSha256": manifest_hash,
        "lockSha256": lock_hash,
        "sourceExecutableSha256": lock_items[executable_id]["sha256"],
    }
    actual = {key: attestation[key] for key in expected}
    if actual != expected:
        raise ArtifactError(
            "artifact.verify",
            "Sealing attestation differs from the artifact contract",
            expected=expected,
            actual=actual,
        )
    return attestation


def validate_sealing_record(
    value: Any,
    manifest: dict[str, Any],
    attestation: dict[str, Any],
) -> dict[str, Any]:
    record = require_object(
        value,
        "artifact.sealing",
        required={
            "schemaVersion",
            "sourceSealId",
            "bundlePath",
            "attestationPath",
            "codeResourcesPath",
            "attestationSha256",
            "sourceTreeSha256",
            "sealedTreeSha256",
            "sealedExecutableSha256",
            "signature",
        },
        allowed={
            "schemaVersion",
            "sourceSealId",
            "bundlePath",
            "attestationPath",
            "codeResourcesPath",
            "attestationSha256",
            "sourceTreeSha256",
            "sealedTreeSha256",
            "sealedExecutableSha256",
            "signature",
        },
    )
    if record["schemaVersion"] != 1:
        raise ArtifactError("artifact.verify", "Sealing provenance schema is unsupported")
    for key in [
        "sourceSealId",
        "attestationSha256",
        "sourceTreeSha256",
        "sealedTreeSha256",
        "sealedExecutableSha256",
    ]:
        require_sha256(record[key], f"artifact.sealing.{key}")
    bundle, _, attestation_path, code_resources = sealing_paths(manifest)
    expected_paths = {
        "bundlePath": bundle.as_posix(),
        "attestationPath": attestation_path.as_posix(),
        "codeResourcesPath": code_resources.as_posix(),
    }
    actual_paths = {key: record[key] for key in expected_paths}
    if actual_paths != expected_paths:
        raise ArtifactError(
            "artifact.verify",
            "Sealing provenance paths differ from the manifest",
            expected=expected_paths,
            actual=actual_paths,
        )
    if (
        record["sourceSealId"] != attestation["sourceSealId"]
        or record["sourceTreeSha256"] != attestation["sourceTreeSha256"]
    ):
        raise ArtifactError(
            "artifact.verify",
            "Sealing provenance differs from the signed attestation",
        )
    signature = require_object(
        record["signature"],
        "artifact.sealing.signature",
        required={
            "kind",
            "identifier",
            "teamIdentifier",
            "cdhash",
            "authority",
            "timestamp",
        },
        allowed={
            "kind",
            "identifier",
            "teamIdentifier",
            "cdhash",
            "authority",
            "timestamp",
        },
    )
    if signature["kind"] != "developer-id":
        raise ArtifactError("artifact.verify", "Sealed bundle signature kind is invalid")
    require_string(signature["identifier"], "artifact.sealing.signature.identifier")
    require_string(signature["teamIdentifier"], "artifact.sealing.signature.teamIdentifier")
    cdhash = require_string(signature["cdhash"], "artifact.sealing.signature.cdhash")
    if not re.fullmatch(r"[0-9a-f]{40}", cdhash):
        raise ArtifactError("artifact.verify", "Sealed bundle CDHash is invalid")
    require_string(signature["authority"], "artifact.sealing.signature.authority")
    require_bool(signature["timestamp"], "artifact.sealing.signature.timestamp")
    sealing = manifest["sealing"]
    expected_signature = {
        "kind": "developer-id",
        "identifier": sealing["bundleId"],
        "teamIdentifier": sealing["teamId"],
        "authority": sealing["identity"],
        "timestamp": sealing["timestamp"],
    }
    actual_signature = {key: signature[key] for key in expected_signature}
    if actual_signature != expected_signature:
        raise ArtifactError(
            "artifact.verify",
            "Sealed bundle signature policy differs from the manifest",
            expected=expected_signature,
            actual=actual_signature,
        )
    return record


def expected_artifact_files(
    manifest: dict[str, Any],
    lock_data: dict[str, Any],
    build_inputs: dict[str, Any],
    metadata: dict[str, Any],
    checksums: str,
    *,
    stage: str,
    sealing_record: dict[str, Any] | None = None,
    sealing_attestation: dict[str, Any] | None = None,
) -> dict[pathlib.PurePosixPath, tuple[int, bytes | None]]:
    expected: dict[pathlib.PurePosixPath, tuple[int, bytes | None]] = {}
    for item in manifest["artifacts"]:
        expected[pathlib.PurePosixPath(item["artifactPath"])] = (int(item["mode"], 8), None)
    for item in manifest["generatedFiles"]:
        expected[pathlib.PurePosixPath(item["artifactPath"])] = (
            int(item["mode"], 8),
            generated_file_bytes(item),
        )
    expected[pathlib.PurePosixPath("plans/install.template.json")] = (
        0o444,
        canonical_json_bytes(manifest["installPlan"]),
    )
    expected[pathlib.PurePosixPath("plans/uninstall.template.json")] = (
        0o444,
        canonical_json_bytes(manifest["uninstallPlan"]),
    )
    expected[pathlib.PurePosixPath("contract/manifest.json")] = (0o444, canonical_json_bytes(manifest))
    expected[pathlib.PurePosixPath("contract/manifest.lock.json")] = (0o444, canonical_json_bytes(lock_data))
    expected[pathlib.PurePosixPath("provenance/build-inputs.json")] = (
        0o444,
        canonical_json_bytes(build_inputs),
    )
    expected[pathlib.PurePosixPath("provenance/artifact.json")] = (0o444, canonical_json_bytes(metadata))
    expected[pathlib.PurePosixPath("provenance/files.sha256")] = (0o444, checksums.encode("utf-8"))
    if stage == "sealed":
        if sealing_record is None or sealing_attestation is None:
            raise ArtifactError("artifact.verify", "Sealed artifact provenance is incomplete")
        _, _, attestation_path, code_resources = sealing_paths(manifest)
        expected[attestation_path] = (0o444, canonical_json_bytes(sealing_attestation))
        expected[code_resources] = (0o444, None)
        expected[SEALING_PROVENANCE_PATH] = (0o444, canonical_json_bytes(sealing_record))
    return expected


def expected_artifact_directories(files: set[pathlib.PurePosixPath]) -> set[pathlib.PurePosixPath]:
    directories: set[pathlib.PurePosixPath] = set()
    for file_path in files:
        parent = file_path.parent
        while parent != pathlib.PurePosixPath("."):
            directories.add(parent)
            parent = parent.parent
    return directories


def verify_artifact(
    path: pathlib.Path,
    *,
    bundle_inspector: Callable[[pathlib.Path, dict[str, Any]], dict[str, Any]] = inspect_signed_bundle,
    artifact_inspector: Callable[
        [dict[str, Any], dict[str, Any], pathlib.Path], dict[str, Any]
    ] = inspect_artifact_file,
) -> dict[str, Any]:
    artifact_root = pathlib.Path(os.path.abspath(path.expanduser()))
    resolved_without_symlinks(artifact_root)
    metadata_path = artifact_root / "provenance" / "artifact.json"
    checksums_path = artifact_root / "provenance" / "files.sha256"
    build_inputs_path = artifact_root / "provenance" / "build-inputs.json"
    manifest_path = artifact_root / "contract" / "manifest.json"
    lock_path = artifact_root / "contract" / "manifest.lock.json"
    if not all(
        required_path.is_file()
        for required_path in [metadata_path, checksums_path, build_inputs_path, manifest_path, lock_path]
    ) or not artifact_root.is_dir():
        raise ArtifactError("artifact.verify", "Artifact provenance is missing", path=str(artifact_root))
    manifest, lock_data, manifest_hash, lock_hash = load_contract(manifest_path, lock_path)
    metadata_value = load_json(metadata_path)
    metadata_schema = metadata_value.get("schemaVersion") if isinstance(metadata_value, dict) else None
    metadata_fields = {
        "schemaVersion",
        "artifact",
        "sealId",
        "manifestSha256",
        "lockSha256",
        "buildInputsSha256",
        "files",
    }
    if metadata_schema == 1:
        metadata = require_object(
            metadata_value,
            "artifact.metadata",
            required=metadata_fields,
            allowed=metadata_fields,
        )
        stage = "unsealed"
    elif metadata_schema == 2:
        metadata = require_object(
            metadata_value,
            "artifact.metadata",
            required=metadata_fields | {"stage"},
            allowed=metadata_fields | {"stage"},
        )
        stage = require_string(metadata["stage"], "artifact.metadata.stage")
        if stage not in ARTIFACT_STAGES:
            raise ArtifactError("artifact.verify", "Artifact stage is unsupported", stage=stage)
    else:
        raise ArtifactError("artifact.verify", "Artifact metadata schema is unsupported")
    require_sha256(metadata["sealId"], "artifact.metadata.sealId")
    require_sha256(metadata["manifestSha256"], "artifact.metadata.manifestSha256")
    require_sha256(metadata["lockSha256"], "artifact.metadata.lockSha256")
    require_sha256(metadata["buildInputsSha256"], "artifact.metadata.buildInputsSha256")
    if metadata["artifact"] != manifest["artifact"]:
        raise ArtifactError("artifact.verify", "Artifact identity differs from its sealed manifest")
    if metadata["manifestSha256"] != manifest_hash or metadata["lockSha256"] != lock_hash:
        raise ArtifactError("artifact.verify", "Artifact contract hashes do not match metadata")
    build_inputs = require_object(
        load_json(build_inputs_path),
        "artifact.buildInputs",
        required={
            "schemaVersion",
            "buildCommand",
            "gitSources",
            "sourceFiles",
            "prerequisites",
            "inputs",
            "sealing",
        },
        allowed={
            "schemaVersion",
            "buildCommand",
            "gitSources",
            "sourceFiles",
            "prerequisites",
            "inputs",
            "sealing",
        },
    )
    if build_inputs["schemaVersion"] != 1:
        raise ArtifactError("artifact.verify", "Build-input metadata schema is unsupported")
    if sha256_bytes(canonical_json_bytes(build_inputs)) != metadata["buildInputsSha256"]:
        raise ArtifactError("artifact.verify", "Build-input metadata hash does not match")
    if build_inputs["buildCommand"] != manifest["artifact"]["buildCommand"]:
        raise ArtifactError("artifact.verify", "Build command differs from the sealed manifest")
    if build_inputs["sealing"] != manifest["sealing"]:
        raise ArtifactError("artifact.verify", "Sealing boundary differs from the sealed manifest")
    validate_sealed_git_sources(manifest, build_inputs["gitSources"])
    validate_sealed_prerequisites(manifest, build_inputs["prerequisites"])
    expected_name = f"{manifest['artifact']['id']}-{manifest['artifact']['version']}-{metadata['sealId']}"
    if artifact_root.name != expected_name:
        raise ArtifactError(
            "artifact.verify",
            "Artifact directory name does not match its content address",
            expected=expected_name,
            actual=artifact_root.name,
        )
    expected_source_files = {
        item["id"]: {
            "id": item["id"],
            "gitSource": source_git_owner(manifest, item["path"]),
            "sha256": item["sha256"],
            "supplyClass": item["supplyClass"],
        }
        for item in manifest["sourceFiles"]
    }
    actual_source_list = require_list(build_inputs["sourceFiles"], "sourceFiles")
    if [item.get("id") if isinstance(item, dict) else None for item in actual_source_list] != [
        item["id"] for item in manifest["sourceFiles"]
    ]:
        raise ArtifactError("artifact.verify", "Build-input source-file order differs from the manifest")
    actual_source_files: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(actual_source_list):
        item = require_object(
            raw_item,
            f"sourceFiles[{index}]",
            required={"id", "gitSource", "sha256", "supplyClass"},
            allowed={"id", "gitSource", "sha256", "supplyClass"},
        )
        identifier = require_id(item["id"], f"sourceFiles[{index}].id")
        require_id(item["gitSource"], f"sourceFiles[{index}].gitSource")
        require_sha256(item["sha256"], f"sourceFiles[{index}].sha256")
        if identifier in actual_source_files:
            raise ArtifactError("artifact.verify", "Build-input source identity is duplicated", id=identifier)
        actual_source_files[identifier] = item
    if set(actual_source_files) != set(expected_source_files):
        raise ArtifactError("artifact.verify", "Build-input source-file identities differ from the manifest")
    for identifier, expected in expected_source_files.items():
        actual = actual_source_files[identifier]
        if actual != expected:
            raise ArtifactError("artifact.verify", "Build-input source-file record differs", id=identifier)
    lock_items = {item["id"]: item for item in lock_data["artifacts"]}
    expected_input_records = [
        locked_input_record(item, lock_items[item["id"]]) for item in manifest["artifacts"]
    ]
    if build_inputs["inputs"] != expected_input_records:
        raise ArtifactError("artifact.verify", "Build-input runtime records differ from the lock")
    sealed_executable_id = manifest["sealing"].get("executableArtifactId") if stage == "sealed" else None
    for item in manifest["artifacts"]:
        if item["id"] == sealed_executable_id:
            continue
        artifact_path = artifact_root / pathlib.PurePosixPath(item["artifactPath"])
        try:
            input_record = artifact_inspector(item, lock_items[item["id"]], artifact_path)
        except ArtifactError as error:
            raise ArtifactError(
                "artifact.verify",
                "Sealed runtime payload failed input verification",
                id=item["id"],
                cause=error.code,
            ) from error
        if input_record != locked_input_record(item, lock_items[item["id"]]):
            raise ArtifactError(
                "artifact.verify",
                "Runtime payload differs from its locked input record",
                id=item["id"],
            )
    sealing_record: dict[str, Any] | None = None
    sealing_attestation: dict[str, Any] | None = None
    if stage == "sealed":
        if "bundlePath" not in manifest["sealing"]:
            raise ArtifactError("artifact.verify", "Legacy artifact contracts cannot be sealed")
        bundle_relative, executable_relative, attestation_relative, code_resources_relative = sealing_paths(
            manifest
        )
        bundle = artifact_root / bundle_relative
        executable = artifact_root / executable_relative
        attestation_path = artifact_root / attestation_relative
        code_resources_path = artifact_root / code_resources_relative
        sealing_path = artifact_root / SEALING_PROVENANCE_PATH
        if not all(
            required.is_file()
            for required in [executable, attestation_path, code_resources_path, sealing_path]
        ):
            raise ArtifactError(
                "artifact.verify",
                "Sealed artifact files are incomplete",
                path=str(artifact_root),
            )
        sealing_attestation = validate_sealing_attestation(
            load_json(attestation_path),
            manifest,
            lock_data,
            manifest_hash,
            lock_hash,
        )
        sealing_record = validate_sealing_record(
            load_json(sealing_path),
            manifest,
            sealing_attestation,
        )
        require_canonical_json(attestation_path, sealing_attestation)
        require_canonical_json(sealing_path, sealing_record)
        if sha256_file(attestation_path) != sealing_record["attestationSha256"]:
            raise ArtifactError("artifact.verify", "Signed sealing attestation hash differs")
        if sha256_file(executable) != sealing_record["sealedExecutableSha256"]:
            raise ArtifactError("artifact.verify", "Signed executable hash differs from sealing provenance")
        sealed_tree = canonical_tree_sha256(bundle)
        if sealed_tree != sealing_record["sealedTreeSha256"]:
            raise ArtifactError(
                "artifact.verify",
                "Signed bundle tree differs from sealing provenance",
                expected=sealing_record["sealedTreeSha256"],
                actual=sealed_tree,
            )
        actual_signature = bundle_inspector(bundle, manifest)
        if actual_signature != sealing_record["signature"]:
            raise ArtifactError(
                "sealing.signature",
                "Signed bundle metadata differs from sealing provenance",
                expected=sealing_record["signature"],
                actual=actual_signature,
            )
    require_canonical_json(manifest_path, load_json(manifest_path))
    require_canonical_json(lock_path, load_json(lock_path))
    require_canonical_json(build_inputs_path, build_inputs)
    require_canonical_json(metadata_path, metadata)
    records = content_records(artifact_root)
    if records != metadata["files"]:
        raise ArtifactError("artifact.verify", "Artifact file records do not match", path=str(artifact_root))
    seal = compute_seal(metadata["manifestSha256"], metadata["lockSha256"], records)
    if seal != metadata["sealId"]:
        raise ArtifactError(
            "artifact.verify",
            "Artifact seal does not match",
            path=str(artifact_root),
            expected=metadata["sealId"],
            actual=seal,
        )
    expected_checksums = "".join(f"{record['sha256']}  {record['path']}\n" for record in records)
    if checksums_path.read_bytes() != expected_checksums.encode("utf-8"):
        raise ArtifactError("artifact.verify", "Artifact checksum file does not match", path=str(artifact_root))
    expected_files = expected_artifact_files(
        manifest,
        lock_data,
        build_inputs,
        metadata,
        expected_checksums,
        stage=stage,
        sealing_record=sealing_record,
        sealing_attestation=sealing_attestation,
    )
    record_paths = {pathlib.PurePosixPath(record["path"]) for record in records}
    expected_record_paths = set(expected_files) - SELF_REFERENTIAL_PROVENANCE
    if record_paths != expected_record_paths:
        raise ArtifactError(
            "artifact.verify",
            "Artifact content records include missing or undeclared files",
            missing=sorted(str(path) for path in expected_record_paths - record_paths),
            extra=sorted(str(path) for path in record_paths - expected_record_paths),
        )
    actual_files = {
        pathlib.PurePosixPath(entry.relative_to(artifact_root).as_posix())
        for entry in artifact_root.rglob("*")
        if entry.is_file()
    }
    if actual_files != set(expected_files):
        raise ArtifactError(
            "artifact.verify",
            "Artifact contains missing or undeclared files",
            missing=sorted(str(path) for path in set(expected_files) - actual_files),
            extra=sorted(str(path) for path in actual_files - set(expected_files)),
        )
    for relative, (expected_mode, expected_bytes) in expected_files.items():
        actual_path = artifact_root / relative
        actual_mode = stat.S_IMODE(actual_path.lstat().st_mode)
        if actual_mode != expected_mode:
            raise ArtifactError(
                "artifact.verify",
                "Artifact file mode differs from the contract",
                path=str(relative),
                expected=f"{expected_mode:04o}",
                actual=f"{actual_mode:04o}",
            )
        if expected_bytes is not None and actual_path.read_bytes() != expected_bytes:
            raise ArtifactError(
                "artifact.verify",
                "Artifact generated file differs from the sealed contract",
                path=str(relative),
            )
    expected_directories = expected_artifact_directories(set(expected_files))
    actual_directories = {
        pathlib.PurePosixPath(entry.relative_to(artifact_root).as_posix())
        for entry in artifact_root.rglob("*")
        if entry.is_dir()
    }
    if actual_directories != expected_directories:
        raise ArtifactError(
            "artifact.verify",
            "Artifact contains missing or undeclared directories",
            missing=sorted(str(path) for path in expected_directories - actual_directories),
            extra=sorted(str(path) for path in actual_directories - expected_directories),
        )
    for directory in [artifact_root, *(artifact_root / path for path in expected_directories)]:
        mode = stat.S_IMODE(directory.lstat().st_mode)
        if mode != 0o555:
            raise ArtifactError(
                "artifact.verify",
                "Artifact directory mode differs from the contract",
                path=str(directory.relative_to(artifact_root)) if directory != artifact_root else ".",
                expected="0555",
                actual=f"{mode:04o}",
            )
    return {**metadata, "stage": stage}


def build_artifact(
    manifest: dict[str, Any],
    lock_data: dict[str, Any],
    manifest_hash: str,
    lock_hash: str,
    validation: dict[str, Any],
    output_root: pathlib.Path,
    *,
    artifact_inspector: Callable[
        [dict[str, Any], dict[str, Any], pathlib.Path], dict[str, Any]
    ] = inspect_artifact_file,
) -> dict[str, Any]:
    output_root = pathlib.Path(os.path.abspath(output_root.expanduser()))
    resolved_without_symlinks(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root / ".build.lock"
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ArtifactError("artifact.publish", "Another artifact build owns the output lock") from error
        temp_root = pathlib.Path(tempfile.mkdtemp(prefix=".build-", dir=output_root))
        published_root: pathlib.Path | None = None
        try:
            artifact_items = {item["id"]: item for item in manifest["artifacts"]}
            lock_items = {item["id"]: item for item in lock_data["artifacts"]}
            for record in validation["artifacts"]:
                item = artifact_items[record["id"]]
                destination = temp_root / pathlib.PurePosixPath(item["artifactPath"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(validation["artifactPaths"][item["id"]], destination)
                destination.chmod(int(item["mode"], 8))
                staged_record = artifact_inspector(item, lock_items[item["id"]], destination)
                if staged_record != record:
                    raise ArtifactError("artifact.publish", "Staged runtime input differs from validated source", id=item["id"])
            for item in manifest["generatedFiles"]:
                stage_generated_file(temp_root, item)
            write_json(temp_root / "plans" / "install.template.json", manifest["installPlan"], 0o444)
            write_json(temp_root / "plans" / "uninstall.template.json", manifest["uninstallPlan"], 0o444)
            write_json(temp_root / "contract" / "manifest.json", manifest, 0o444)
            write_json(temp_root / "contract" / "manifest.lock.json", lock_data, 0o444)
            build_inputs = {
                "schemaVersion": 1,
                "buildCommand": manifest["artifact"]["buildCommand"],
                "gitSources": validation["gitSources"],
                "sourceFiles": validation["sourceFiles"],
                "prerequisites": validation["prerequisites"],
                "inputs": validation["artifacts"],
                "sealing": manifest["sealing"],
            }
            write_json(temp_root / "provenance" / "build-inputs.json", build_inputs, 0o444)
            records = content_records(temp_root)
            seal = compute_seal(manifest_hash, lock_hash, records)
            metadata = {
                "schemaVersion": 2,
                "stage": "unsealed",
                "artifact": manifest["artifact"],
                "sealId": seal,
                "manifestSha256": manifest_hash,
                "lockSha256": lock_hash,
                "buildInputsSha256": sha256_bytes(canonical_json_bytes(build_inputs)),
                "files": records,
            }
            write_json(temp_root / "provenance" / "artifact.json", metadata, 0o444)
            checksums = "".join(f"{record['sha256']}  {record['path']}\n" for record in records)
            checksums_path = temp_root / "provenance" / "files.sha256"
            checksums_path.write_bytes(checksums.encode("utf-8"))
            checksums_path.chmod(0o444)
            artifact_name = f"{manifest['artifact']['id']}-{manifest['artifact']['version']}-{seal}"
            destination_root = output_root / artifact_name
            make_tree_read_only(temp_root)
            fsync_tree(temp_root)
            if destination_root.exists():
                existing = verify_artifact(
                    destination_root,
                    artifact_inspector=artifact_inspector,
                )
                if existing["sealId"] != seal or existing["files"] != records:
                    raise ArtifactError(
                        "artifact.publish",
                        "Existing content-addressed artifact differs",
                        path=str(destination_root),
                    )
                make_tree_writable(temp_root)
                shutil.rmtree(temp_root)
                return {
                    "path": str(destination_root),
                    "reused": True,
                    "sealId": seal,
                    "stage": "unsealed",
                }
            os.replace(temp_root, destination_root)
            published_root = destination_root
            fsync_directory(output_root)
            verified = verify_artifact(destination_root, artifact_inspector=artifact_inspector)
            return {
                "path": str(destination_root),
                "reused": False,
                "sealId": verified["sealId"],
                "stage": verified["stage"],
            }
        except Exception:
            cleanup_root = published_root if published_root is not None else temp_root
            make_tree_writable(cleanup_root)
            shutil.rmtree(cleanup_root, ignore_errors=True)
            if published_root is not None:
                fsync_directory(output_root)
            raise
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def seal_artifact(
    manifest: dict[str, Any],
    lock_data: dict[str, Any],
    manifest_hash: str,
    lock_hash: str,
    artifact: pathlib.Path,
    output_root: pathlib.Path,
    *,
    signer: Callable[[pathlib.Path, dict[str, Any]], None] = sign_bundle_with_codesign,
    bundle_inspector: Callable[[pathlib.Path, dict[str, Any]], dict[str, Any]] = inspect_signed_bundle,
    artifact_inspector: Callable[
        [dict[str, Any], dict[str, Any], pathlib.Path], dict[str, Any]
    ] = inspect_artifact_file,
) -> dict[str, Any]:
    if "bundlePath" not in manifest["sealing"]:
        raise ArtifactError("sealing.source_mismatch", "Legacy artifact contracts cannot be sealed")
    source_root = pathlib.Path(os.path.abspath(artifact.expanduser()))
    source_metadata = verify_artifact(
        source_root,
        bundle_inspector=bundle_inspector,
        artifact_inspector=artifact_inspector,
    )
    if source_metadata["stage"] != "unsealed":
        raise ArtifactError(
            "sealing.source_mismatch",
            "Only an unsealed immutable artifact can be sealed",
            stage=source_metadata["stage"],
        )
    if (
        source_metadata["manifestSha256"] != manifest_hash
        or source_metadata["lockSha256"] != lock_hash
    ):
        raise ArtifactError(
            "sealing.source_mismatch",
            "Source artifact belongs to another manifest or lockfile",
        )
    output_root = pathlib.Path(os.path.abspath(output_root.expanduser()))
    resolved_without_symlinks(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root / ".build.lock"
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ArtifactError("artifact.publish", "Another artifact build owns the output lock") from error
        remove_stale_sealing_paths(output_root)
        temporary_parent = pathlib.Path(tempfile.mkdtemp(prefix=".seal-", dir=output_root))
        published_root: pathlib.Path | None = None
        publication_staging: pathlib.Path | None = None
        try:
            staged_source = temporary_parent / source_root.name
            shutil.copytree(source_root, staged_source, copy_function=shutil.copy2)
            staged_metadata = verify_artifact(
                staged_source,
                bundle_inspector=bundle_inspector,
                artifact_inspector=artifact_inspector,
            )
            if staged_metadata["sealId"] != source_metadata["sealId"]:
                raise ArtifactError(
                    "sealing.source_mismatch",
                    "Copied source artifact differs from the verified source",
                )
            bundle_relative, executable_relative, attestation_relative, code_resources_relative = sealing_paths(
                manifest
            )
            bundle = staged_source / bundle_relative
            executable = staged_source / executable_relative
            attestation_path = staged_source / attestation_relative
            code_resources = staged_source / code_resources_relative
            source_tree = canonical_tree_sha256(bundle)
            make_tree_writable(staged_source)
            lock_items = {item["id"]: item for item in lock_data["artifacts"]}
            executable_id = manifest["sealing"]["executableArtifactId"]
            attestation = {
                "schemaVersion": 1,
                "artifactId": manifest["artifact"]["id"],
                "artifactVersion": manifest["artifact"]["version"],
                "bundleId": manifest["sealing"]["bundleId"],
                "sourceSealId": source_metadata["sealId"],
                "manifestSha256": manifest_hash,
                "lockSha256": lock_hash,
                "sourceTreeSha256": source_tree,
                "sourceExecutableSha256": lock_items[executable_id]["sha256"],
            }
            write_json(attestation_path, attestation, 0o444)
            signer(bundle, manifest["sealing"])
            if not code_resources.is_file():
                raise ArtifactError(
                    "sealing.signature",
                    "Bundle signing did not produce CodeResources",
                    path=str(code_resources),
                )
            for item in manifest["artifacts"]:
                (staged_source / pathlib.PurePosixPath(item["artifactPath"])).chmod(
                    int(item["mode"], 8)
                )
            for item in manifest["generatedFiles"]:
                (staged_source / pathlib.PurePosixPath(item["artifactPath"])).chmod(
                    int(item["mode"], 8)
                )
            attestation_path.chmod(0o444)
            code_resources.chmod(0o444)
            make_tree_read_only(bundle)
            signature = bundle_inspector(bundle, manifest)
            sealed_tree = canonical_tree_sha256(bundle)
            sealing_record = {
                "schemaVersion": 1,
                "sourceSealId": source_metadata["sealId"],
                "bundlePath": bundle_relative.as_posix(),
                "attestationPath": attestation_relative.as_posix(),
                "codeResourcesPath": code_resources_relative.as_posix(),
                "attestationSha256": sha256_file(attestation_path),
                "sourceTreeSha256": source_tree,
                "sealedTreeSha256": sealed_tree,
                "sealedExecutableSha256": sha256_file(executable),
                "signature": signature,
            }
            write_json(staged_source / SEALING_PROVENANCE_PATH, sealing_record, 0o444)
            make_tree_read_only(staged_source)
            records = content_records(staged_source)
            seal = compute_seal(manifest_hash, lock_hash, records)
            build_inputs = load_json(staged_source / "provenance" / "build-inputs.json")
            metadata = {
                "schemaVersion": 2,
                "stage": "sealed",
                "artifact": manifest["artifact"],
                "sealId": seal,
                "manifestSha256": manifest_hash,
                "lockSha256": lock_hash,
                "buildInputsSha256": sha256_bytes(canonical_json_bytes(build_inputs)),
                "files": records,
            }
            provenance = staged_source / "provenance"
            metadata_path = provenance / "artifact.json"
            checksums_path = provenance / "files.sha256"
            staged_source.chmod(0o755)
            provenance.chmod(0o755)
            metadata_path.chmod(0o644)
            checksums_path.chmod(0o644)
            write_json(metadata_path, metadata, 0o444)
            checksums = "".join(f"{record['sha256']}  {record['path']}\n" for record in records)
            checksums_path.write_bytes(checksums.encode("utf-8"))
            checksums_path.chmod(0o444)
            provenance.chmod(0o555)
            staged_source.chmod(0o555)
            artifact_name = f"{manifest['artifact']['id']}-{manifest['artifact']['version']}-{seal}"
            staged_final = temporary_parent / artifact_name
            os.replace(staged_source, staged_final)
            fsync_tree(staged_final)
            verified = verify_artifact(
                staged_final,
                bundle_inspector=bundle_inspector,
                artifact_inspector=artifact_inspector,
            )
            destination_root = output_root / artifact_name
            if destination_root.exists():
                existing = verify_artifact(
                    destination_root,
                    bundle_inspector=bundle_inspector,
                    artifact_inspector=artifact_inspector,
                )
                if existing["sealId"] != seal or existing["files"] != records:
                    raise ArtifactError(
                        "artifact.publish",
                        "Existing content-addressed sealed artifact differs",
                        path=str(destination_root),
                    )
                make_tree_writable(temporary_parent)
                shutil.rmtree(temporary_parent)
                return {
                    "path": str(destination_root),
                    "reused": True,
                    "sealId": seal,
                    "sourceSealId": source_metadata["sealId"],
                    "stage": "sealed",
                }
            publication_staging = output_root / f".publish-{temporary_parent.name.removeprefix('.seal-')}"
            if os.path.lexists(publication_staging):
                raise ArtifactError(
                    "artifact.publish",
                    "Sealed artifact publication staging path already exists",
                    path=str(publication_staging),
                )
            staged_final.chmod(0o755)
            try:
                os.replace(staged_final, publication_staging)
            except PermissionError as error:
                raise ArtifactError(
                    "artifact.publish",
                    "Sealed artifact could not enter publication staging",
                    source=str(staged_final),
                    sourceParentMode=f"{stat.S_IMODE(staged_final.parent.lstat().st_mode):04o}",
                    destination=str(publication_staging),
                    destinationParentMode=f"{stat.S_IMODE(output_root.lstat().st_mode):04o}",
                ) from error
            publication_staging.chmod(0o555)
            temporary_parent.rmdir()
            fsync_tree(publication_staging)
            fsync_directory(output_root)
            os.replace(publication_staging, destination_root)
            publication_staging = None
            published_root = destination_root
            fsync_directory(output_root)
            verified = verify_artifact(
                destination_root,
                bundle_inspector=bundle_inspector,
                artifact_inspector=artifact_inspector,
            )
            return {
                "path": str(destination_root),
                "reused": False,
                "sealId": verified["sealId"],
                "sourceSealId": source_metadata["sealId"],
                "stage": verified["stage"],
            }
        except Exception:
            if published_root is not None:
                cleanup_root = published_root
            elif publication_staging is not None and os.path.lexists(publication_staging):
                cleanup_root = publication_staging
            else:
                cleanup_root = temporary_parent
            make_tree_writable(cleanup_root)
            shutil.rmtree(cleanup_root, ignore_errors=True)
            if os.path.lexists(temporary_parent):
                make_tree_writable(temporary_parent)
                shutil.rmtree(temporary_parent, ignore_errors=True)
            if published_root is not None:
                fsync_directory(output_root)
            raise
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def ensure_target_allowed(path: pathlib.Path, allowed_roots: list[pathlib.Path], operation: str) -> None:
    resolved = resolved_without_symlinks(path)
    for root in allowed_roots:
        resolved_root = resolved_without_symlinks(root)
        try:
            if os.path.commonpath([str(resolved), str(resolved_root)]) == str(resolved_root):
                return
        except ValueError:
            continue
    raise ArtifactError("path.unsafe", "Plan target escapes the allowed roots", operation=operation, path=str(resolved))


def resolve_plan_operation(
    item: dict[str, Any],
    artifact_root: pathlib.Path,
    bindings: dict[str, str],
    allowed_roots: list[pathlib.Path],
) -> dict[str, Any]:
    target = resolve_path(item["target"], bindings, f"plan.{item['id']}.target")
    ensure_target_allowed(target, allowed_roots, item["id"])
    result: dict[str, Any] = {
        "id": item["id"],
        "resource": item["resource"],
        "action": item["action"],
        "target": str(target),
    }
    ready = True
    reason: str | None = None
    source: pathlib.Path | None = None
    source_marker: pathlib.Path | None = None
    target_marker: pathlib.Path | None = None
    backup: pathlib.Path | None = None
    if "atomic" in item:
        result["atomic"] = item["atomic"]
    if "source" in item:
        source_relative = safe_relative_path(item["source"], f"plan.{item['id']}.source")
        source = (artifact_root / source_relative).resolve(strict=False)
        if os.path.commonpath([str(source), str(artifact_root)]) != str(artifact_root):
            raise ArtifactError("path.unsafe", "Plan source escapes the artifact", operation=item["id"])
        if not source.exists():
            raise ArtifactError("plan.unresolved", "Plan source is missing from the artifact", operation=item["id"])
        result["source"] = str(source)
        if source.is_file():
            result["sourceSha256"] = sha256_file(source)
        else:
            result["sourceFiles"] = sum(1 for path in source.rglob("*") if path.is_file())
            result["sourceTreeSha256"] = canonical_tree_sha256(source)
    if "marker" in item:
        if source is None or not source.is_dir():
            raise ArtifactError("plan.unresolved", "Tree ownership marker requires a directory source", operation=item["id"])
        marker_relative = safe_relative_path(item["marker"], f"plan.{item['id']}.marker")
        source_marker = source / marker_relative
        require_regular_file(source_marker, "plan.unresolved", item["id"])
        target_marker = target / marker_relative
        result["marker"] = marker_relative.as_posix()
        result["sourceMarkerSha256"] = sha256_file(source_marker)
    if "backup" in item:
        backup = resolve_path(item["backup"], bindings, f"plan.{item['id']}.backup")
        ensure_target_allowed(backup, allowed_roots, item["id"])
        result["backup"] = str(backup)
        result["backupExists"] = backup.exists()
    action = item["action"]
    if action == "assert_sha256":
        result["expectedSha256"] = item["expectedSha256"]
        if not target.is_file():
            ready = False
            reason = "expected regular file is missing"
        else:
            actual = sha256_file(target)
            result["actualSha256"] = actual
            if actual != item["expectedSha256"]:
                ready = False
                reason = "target hash does not match the required stock input"
    elif action == "assert_absent":
        result["exists"] = target.exists()
        if target.exists():
            ready = False
            reason = "target already exists"
    elif action == "assert_absent_or_owned":
        result["exists"] = target.exists()
        if not target.exists():
            result["ownership"] = "absent"
        elif target_marker is not None and target_marker.is_file() and source_marker is not None:
            actual_marker = sha256_file(target_marker)
            result["targetMarkerSha256"] = actual_marker
            if actual_marker == sha256_file(source_marker):
                target_tree_sha256 = canonical_tree_sha256(target)
                result["targetTreeSha256"] = target_tree_sha256
                if target_tree_sha256 == result["sourceTreeSha256"]:
                    result["ownership"] = "artifact-owned"
                else:
                    ready = False
                    reason = "existing tree content differs from the artifact-owned tree"
                    result["ownership"] = "modified"
            else:
                ready = False
                reason = "existing tree has a foreign ownership marker"
                result["ownership"] = "foreign"
        else:
            ready = False
            reason = "existing tree has no artifact ownership marker"
            result["ownership"] = "unproven"
    elif action == "backup":
        if target.is_file():
            result["targetSha256"] = sha256_file(target)
            if backup is not None and backup.exists():
                if not backup.is_file() or sha256_file(backup) != result["targetSha256"]:
                    ready = False
                    reason = "existing backup does not match the target"
        else:
            ready = False
            reason = "backup source is missing"
    elif action == "restore":
        if source is None or backup is None:
            raise ArtifactError("plan.unresolved", "Restore operation is incomplete", operation=item["id"])
        result["expectedSha256"] = item["expectedSha256"]
        target_hash = sha256_file(target) if target.is_file() else None
        backup_hash = sha256_file(backup) if backup.is_file() else None
        result["targetSha256"] = target_hash
        result["backupSha256"] = backup_hash
        if target_hash == item["expectedSha256"]:
            result["state"] = "already-restored"
        elif target_hash == sha256_file(source) and backup_hash == item["expectedSha256"]:
            result["state"] = "ready-to-restore"
        else:
            ready = False
            reason = "target and backup do not prove a safe restoration"
            result["state"] = "blocked"
    elif action == "remove":
        result["exists"] = target.exists()
        if target.exists():
            if source is None or not source.is_file() or not target.is_file():
                ready = False
                reason = "removal target is not the expected regular file"
            else:
                target_hash = sha256_file(target)
                result["targetSha256"] = target_hash
                if target_hash != sha256_file(source):
                    ready = False
                    reason = "removal target does not match the artifact-owned file"
    elif action == "remove_tree":
        result["exists"] = target.exists()
        if target.exists():
            if target_marker is None or source_marker is None or not target_marker.is_file():
                ready = False
                reason = "tree removal target has no ownership marker"
            else:
                marker_hash = sha256_file(target_marker)
                result["targetMarkerSha256"] = marker_hash
                if marker_hash != sha256_file(source_marker):
                    ready = False
                    reason = "tree removal target has a foreign ownership marker"
                else:
                    target_tree_sha256 = canonical_tree_sha256(target)
                    result["targetTreeSha256"] = target_tree_sha256
                    if target_tree_sha256 != result["sourceTreeSha256"]:
                        ready = False
                        reason = "tree removal target content differs from the artifact-owned tree"
    elif action == "retain":
        result["exists"] = target.exists()
    result["ready"] = ready
    if reason is not None:
        result["blockedReason"] = reason
    return result


def resolve_mutable_state(
    manifest: dict[str, Any],
    bindings: dict[str, str],
    allowed_roots: list[pathlib.Path],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    path_kinds = {"directory", "file", "launch-services"}
    for item in manifest["mutableState"]:
        record = {
            "id": item["id"],
            "kind": item["kind"],
            "owner": item["owner"],
            "lifecycle": item["lifecycle"],
        }
        if item["kind"] in path_kinds:
            location = resolve_path(item["location"], bindings, f"mutableState.{item['id']}.location")
            ensure_target_allowed(location, allowed_roots, item["id"])
            record["location"] = str(location)
            record["exists"] = location.exists()
        else:
            record["location"] = expand_template(
                item["location"], bindings, f"mutableState.{item['id']}.location"
            )
        resolved.append(record)
    return resolved


def plan_is_fixture_only(plan: dict[str, Any]) -> bool:
    fixture_root_value = plan.get("fixtureRoot")
    allowed_root_values = plan.get("allowedTargetRoots")
    if not isinstance(fixture_root_value, str) or not isinstance(allowed_root_values, list):
        return False
    fixture_root = pathlib.Path(fixture_root_value)
    if not fixture_root.is_absolute():
        return False
    fixture_text = str(fixture_root)
    for raw_root in allowed_root_values:
        if not isinstance(raw_root, str) or not pathlib.Path(raw_root).is_absolute():
            return False
        try:
            if os.path.commonpath([raw_root, fixture_text]) != fixture_text:
                return False
        except ValueError:
            return False
    return True


def build_plan(
    manifest: dict[str, Any],
    artifact_root: pathlib.Path,
    bindings_path: pathlib.Path,
    *,
    expected_manifest_hash: str | None = None,
    expected_lock_hash: str | None = None,
    bundle_inspector: Callable[[pathlib.Path, dict[str, Any]], dict[str, Any]] = inspect_signed_bundle,
    artifact_inspector: Callable[
        [dict[str, Any], dict[str, Any], pathlib.Path], dict[str, Any]
    ] = inspect_artifact_file,
) -> dict[str, Any]:
    artifact_root = resolved_without_symlinks(
        pathlib.Path(os.path.abspath(artifact_root.expanduser()))
    )
    metadata = verify_artifact(
        artifact_root,
        bundle_inspector=bundle_inspector,
        artifact_inspector=artifact_inspector,
    )
    manifest_hash = sha256_bytes(canonical_json_bytes(manifest))
    if metadata["manifestSha256"] != (expected_manifest_hash or manifest_hash):
        raise ArtifactError(
            "plan.unresolved",
            "Artifact was built from a different manifest",
            artifactManifestSha256=metadata["manifestSha256"],
            requestedManifestSha256=expected_manifest_hash or manifest_hash,
        )
    if expected_lock_hash is not None and metadata["lockSha256"] != expected_lock_hash:
        raise ArtifactError(
            "plan.unresolved",
            "Artifact was built from a different lockfile",
            artifactLockSha256=metadata["lockSha256"],
            requestedLockSha256=expected_lock_hash,
        )
    bindings = resolve_bindings(manifest, bindings_path, "plan")
    allowed_roots = [
        resolve_path(template, bindings, f"allowedTargetRoots[{index}]")
        for index, template in enumerate(manifest["allowedTargetRoots"])
    ]
    mutable_state = resolve_mutable_state(manifest, bindings, allowed_roots)
    install = [
        resolve_plan_operation(item, artifact_root, bindings, allowed_roots)
        for item in manifest["installPlan"]
    ]
    uninstall = [
        resolve_plan_operation(item, artifact_root, bindings, allowed_roots)
        for item in manifest["uninstallPlan"]
    ]
    requires_sealing = metadata["stage"] != "sealed"
    sealing_blockers = ["artifact.sealing_required"] if requires_sealing else []
    return {
        "schemaVersion": 1,
        "artifact": str(artifact_root),
        "sealId": metadata["sealId"],
        "artifactStage": metadata["stage"],
        "requiresSealing": requires_sealing,
        "installReady": not requires_sealing and all(item["ready"] for item in install),
        "uninstallReady": not requires_sealing and all(item["ready"] for item in uninstall),
        "installBlockers": [
            *sealing_blockers,
            *(item["id"] for item in install if not item["ready"]),
        ],
        "uninstallBlockers": [
            *sealing_blockers,
            *(item["id"] for item in uninstall if not item["ready"]),
        ],
        "fixtureRoot": str((REPO_ROOT / ".code").resolve()),
        "allowedTargetRoots": [str(root) for root in allowed_roots],
        "mutableState": mutable_state,
        "install": install,
        "uninstall": uninstall,
    }


def compare_artifacts(
    left: pathlib.Path,
    right: pathlib.Path,
    *,
    bundle_inspector: Callable[[pathlib.Path, dict[str, Any]], dict[str, Any]] = inspect_signed_bundle,
    artifact_inspector: Callable[
        [dict[str, Any], dict[str, Any], pathlib.Path], dict[str, Any]
    ] = inspect_artifact_file,
) -> dict[str, Any]:
    left_metadata = verify_artifact(
        left,
        bundle_inspector=bundle_inspector,
        artifact_inspector=artifact_inspector,
    )
    right_metadata = verify_artifact(
        right,
        bundle_inspector=bundle_inspector,
        artifact_inspector=artifact_inspector,
    )
    if left_metadata["sealId"] != right_metadata["sealId"] or left_metadata["files"] != right_metadata["files"]:
        raise ArtifactError(
            "artifact.compare",
            "Artifacts are not equivalent",
            leftSeal=left_metadata["sealId"],
            rightSeal=right_metadata["sealId"],
        )
    return {"equivalent": True, "sealId": left_metadata["sealId"]}


def expect_error(code: str, action: Any) -> None:
    try:
        action()
    except ArtifactError as error:
        if error.code != code:
            raise ArtifactError(
                "self-test.failed",
                "Self-test raised the wrong error",
                expected=code,
                actual=error.code,
            ) from error
        return
    raise ArtifactError("self-test.failed", "Self-test expected an error", expected=code)



def write_minimal_pe(path: pathlib.Path) -> None:
    image = bytearray(0x200)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", image, 0x84, 0x8664, 0, 0, 0, 0, 0xF0, 0x2022)
    struct.pack_into("<H", image, 0x98, 0x20B)
    struct.pack_into("<I", image, 0x98 + 108, 16)
    path.write_bytes(image)


def self_test() -> dict[str, Any]:
    completed: list[str] = []
    code_root = REPO_ROOT / ".code"
    code_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="runtime-artifact-test-", ignore_cleanup_errors=True
    ) as temp_name, tempfile.TemporaryDirectory(
        prefix="runtime-artifact-plan-", dir=code_root, ignore_cleanup_errors=True
    ) as target_name:
        temp_root = pathlib.Path(temp_name).resolve()
        source_repo = temp_root / "source-repo"
        source_repo.mkdir()
        run_command(["git", "init", "-q"], cwd=source_repo)
        run_command(["git", "config", "user.email", "runtime@example.invalid"], cwd=source_repo)
        run_command(["git", "config", "user.name", "Runtime Test"], cwd=source_repo)
        tracked = source_repo / "tracked.txt"
        tracked.write_text("source\n")
        run_command(["git", "add", "tracked.txt"], cwd=source_repo)
        run_command(["git", "commit", "-qm", "fixture"], cwd=source_repo)
        run_command(
            ["git", "remote", "add", "origin", "https://example.invalid/runtime.git"], cwd=source_repo
        )
        revision = run_command(["git", "rev-parse", "HEAD"], cwd=source_repo)
        runtime_input = temp_root / "input.dll"
        write_minimal_pe(runtime_input)
        target_root = pathlib.Path(target_name).resolve()
        stock_target = target_root / "stock.bin"
        stock_target.write_bytes(b"stock\n")
        manifest_path = temp_root / "manifest.json"
        lock_path = temp_root / "manifest.lock.json"
        bindings_path = temp_root / "bindings.json"
        fixture_manifest = {
            "schemaVersion": 1,
            "artifact": {
                "id": "fixture-runtime",
                "version": "1.0.0",
                "description": "fixture",
                "supportMatrix": "docs/fixture.md",
                "buildCommand": ["python3", "tools/build_runtime_artifact.py", "build"],
            },
            "bindings": [
                {"name": "SOURCE_REPO", "phases": ["validate", "build"], "required": True},
                {"name": "INPUT_FILE", "phases": ["validate", "build"], "required": True},
            ],
            "gitSources": [
                {
                    "id": "fixture_source",
                    "path": "${SOURCE_REPO}",
                    "remote": "https://example.invalid/runtime",
                    "revision": revision,
                    "cleanPolicy": "all",
                }
            ],
            "prerequisites": [],
            "sourceFiles": [
                {
                    "id": "tracked_source",
                    "path": "${SOURCE_REPO}/tracked.txt",
                    "sha256": sha256_file(tracked),
                    "supplyClass": "repo-source",
                }
            ],
            "artifacts": [
                {
                    "id": "fixture_binary",
                    "path": "${INPUT_FILE}",
                    "artifactPath": "payload/fixture.dll",
                    "mode": "0444",
                    "supplyClass": "opaque-local-build",
                    "recipe": "fixture",
                    "binary": {
                        "format": "pe",
                        "kind": "dll",
                        "architectures": ["x86_64"],
                        "authenticode": "absent",
                    },
                    "signature": "none",
                }
            ],
            "generatedFiles": [
                {
                    "id": "fixture_config",
                    "artifactPath": "config/fixture.json",
                    "mode": "0444",
                    "format": "json",
                    "content": {"fixture": True},
                }
            ],
            "mutableState": [
                {
                    "id": "fixture_target",
                    "kind": "directory",
                    "location": str(target_root),
                    "owner": "runtime",
                    "lifecycle": "restored",
                }
            ],
            "allowedTargetRoots": ["${REPO_ROOT}/.code"],
            "installPlan": [
                {
                    "id": "verify_stock",
                    "resource": "fixture_file",
                    "action": "assert_sha256",
                    "target": str(stock_target),
                    "expectedSha256": sha256_file(stock_target),
                },
                {
                    "id": "verify_fixture_absent",
                    "resource": "fixture_file",
                    "action": "assert_absent",
                    "target": str(target_root / "installed.dll"),
                },
                {
                    "id": "create_fixture",
                    "resource": "fixture_file",
                    "action": "create_file",
                    "source": "payload/fixture.dll",
                    "target": str(target_root / "installed.dll"),
                    "atomic": True,
                },
            ],
            "uninstallPlan": [
                {
                    "id": "remove_fixture",
                    "resource": "fixture_file",
                    "action": "remove",
                    "source": "payload/fixture.dll",
                    "target": str(target_root / "installed.dll"),
                }
            ],
            "sealing": {
                "mode": "separate-step",
                "bundleId": "example.fixture",
                "teamId": "FIXTURE",
                "identity": "fixture",
                "timestamp": False,
            },
        }
        fixture_lock = {
            "schemaVersion": 1,
            "manifestSha256": sha256_bytes(canonical_json_bytes(fixture_manifest)),
            "artifacts": [{"id": "fixture_binary", "sha256": sha256_file(runtime_input)}],
        }
        bindings = {
            "SOURCE_REPO": str(source_repo),
            "INPUT_FILE": str(runtime_input),
        }
        write_json(manifest_path, fixture_manifest)
        write_json(lock_path, fixture_lock)
        write_json(bindings_path, bindings)
        manifest, lock_data, manifest_hash, lock_hash = load_contract(manifest_path, lock_path)
        validation = validate_environment(manifest, lock_data, bindings_path, "build")
        completed.append("valid")

        artifact_a = pathlib.Path(
            build_artifact(manifest, lock_data, manifest_hash, lock_hash, validation, temp_root / "output-a")[
                "path"
            ]
        )
        artifact_b = pathlib.Path(
            build_artifact(manifest, lock_data, manifest_hash, lock_hash, validation, temp_root / "output-b")[
                "path"
            ]
        )
        compare_artifacts(artifact_a, artifact_b)
        if any(stat.S_IMODE(path.lstat().st_mode) & 0o222 for path in [artifact_a, *artifact_a.rglob("*")]):
            raise ArtifactError("self-test.failed", "Published fixture is writable")
        plan = build_plan(manifest, artifact_a, bindings_path)
        if (
            plan["install"][0]["target"] != str(stock_target.resolve())
            or plan["mutableState"][0]["location"] != str(target_root)
            or plan["installReady"]
            or plan["installBlockers"] != ["artifact.sealing_required"]
        ):
            raise ArtifactError("self-test.failed", "Plan did not resolve exact ready operations")
        completed.extend(["atomic-build", "compare", "read-only", "plan"])

        runtime_input.unlink()
        expect_error("input.missing", lambda: validate_environment(manifest, lock_data, bindings_path, "build"))
        runtime_input.write_bytes(b"changed\n")
        expect_error("input.hash", lambda: validate_environment(manifest, lock_data, bindings_path, "build"))
        write_minimal_pe(runtime_input)
        completed.extend(["missing", "hash-mismatch"])

        type_lock = json.loads(json.dumps(fixture_lock))
        runtime_input.write_bytes(b"not a PE file\n")
        type_lock["artifacts"][0]["sha256"] = sha256_file(runtime_input)
        write_json(lock_path, type_lock)
        type_manifest, type_lock_data, _, _ = load_contract(manifest_path, lock_path)
        expect_error("input.type", lambda: validate_environment(type_manifest, type_lock_data, bindings_path, "build"))
        write_minimal_pe(runtime_input)
        write_json(lock_path, fixture_lock)
        completed.append("wrong-type")

        tracked.write_text("dirty\n")
        expect_error("git.dirty", lambda: validate_environment(manifest, lock_data, bindings_path, "build"))
        tracked.write_text("source\n")
        untracked = source_repo / "untracked.txt"
        untracked.write_text("dirty\n")
        expect_error("git.dirty", lambda: validate_environment(manifest, lock_data, bindings_path, "build"))
        untracked.unlink()
        completed.extend(["dirty-tracked", "dirty-untracked"])

        wrong_manifest = json.loads(json.dumps(fixture_manifest))
        wrong_manifest["gitSources"][0]["revision"] = "0" * 40
        wrong_lock = json.loads(json.dumps(fixture_lock))
        wrong_lock["manifestSha256"] = sha256_bytes(canonical_json_bytes(wrong_manifest))
        write_json(manifest_path, wrong_manifest)
        write_json(lock_path, wrong_lock)
        wrong_loaded, wrong_lock_loaded, _, _ = load_contract(manifest_path, lock_path)
        expect_error(
            "git.revision",
            lambda: validate_environment(wrong_loaded, wrong_lock_loaded, bindings_path, "build"),
        )
        completed.append("wrong-commit")

        unsafe_manifest = json.loads(json.dumps(fixture_manifest))
        unsafe_manifest["artifacts"][0]["artifactPath"] = "../escape"
        unsafe_lock = json.loads(json.dumps(fixture_lock))
        unsafe_lock["manifestSha256"] = sha256_bytes(canonical_json_bytes(unsafe_manifest))
        write_json(manifest_path, unsafe_manifest)
        write_json(lock_path, unsafe_lock)
        expect_error("path.unsafe", lambda: load_contract(manifest_path, lock_path))
        completed.append("unsafe-destination")

        write_json(manifest_path, fixture_manifest)
        write_json(lock_path, fixture_lock)
        symlink_input = temp_root / "input-link.dll"
        symlink_input.symlink_to(runtime_input)
        write_json(bindings_path, {**bindings, "INPUT_FILE": str(symlink_input)})
        expect_error("path.symlink", lambda: validate_environment(manifest, lock_data, bindings_path, "build"))
        write_json(bindings_path, bindings)
        completed.append("symlink-input")

        mismatched_manifest = copy.deepcopy(manifest)
        mismatched_manifest["artifact"]["description"] = "different fixture"
        expect_error(
            "plan.unresolved",
            lambda: build_plan(mismatched_manifest, artifact_b, bindings_path),
        )
        completed.append("plan-contract-mismatch")

        config_path = artifact_a / "config" / "fixture.json"
        config_path.chmod(0o400)
        expect_error("artifact.verify", lambda: verify_artifact(artifact_a))
        config_path.chmod(0o444)
        artifact_b.chmod(0o755)
        extra_directory = artifact_b / "undeclared-empty-directory"
        extra_directory.mkdir(mode=0o555)
        artifact_b.chmod(0o555)
        expect_error("artifact.verify", lambda: verify_artifact(artifact_b))
        completed.extend(["exact-file-mode", "undeclared-directory"])

        sealing_input = temp_root / "fixture-bridge"
        sealing_input.write_bytes(b"fixture bridge\n")
        sealing_manifest_path = temp_root / "sealing-manifest.json"
        sealing_lock_path = temp_root / "sealing-manifest.lock.json"
        sealing_bindings_path = temp_root / "sealing-bindings.json"
        sealing_manifest = {
            "schemaVersion": 1,
            "artifact": {
                "id": "fixture-sealed-runtime",
                "version": "1.0.0",
                "description": "sealed fixture",
                "supportMatrix": "docs/fixture.md",
                "buildCommand": ["python3", "tools/build_runtime_artifact.py", "build"],
            },
            "bindings": [],
            "gitSources": [],
            "prerequisites": [],
            "sourceFiles": [],
            "artifacts": [
                {
                    "id": "fixture_bridge",
                    "path": str(sealing_input),
                    "artifactPath": "payload/macos/FixtureBridge.app/Contents/MacOS/fixture_bridge",
                    "mode": "0555",
                    "supplyClass": "opaque-local-build",
                    "recipe": "fixture",
                    "binary": {
                        "format": "mach-o",
                        "kind": "executable",
                        "architectures": ["arm64"],
                    },
                    "signature": "none",
                }
            ],
            "generatedFiles": [
                {
                    "id": "fixture_info",
                    "artifactPath": "payload/macos/FixtureBridge.app/Contents/Info.plist",
                    "mode": "0444",
                    "format": "plist",
                    "content": {
                        "CFBundleExecutable": "fixture_bridge",
                        "CFBundleIdentifier": "example.fixture.sealed",
                        "CFBundlePackageType": "APPL",
                    },
                },
                {
                    "id": "fixture_owner",
                    "artifactPath": (
                        "payload/macos/FixtureBridge.app/Contents/Resources/runtime-owner.json"
                    ),
                    "mode": "0444",
                    "format": "json",
                    "content": {"artifactId": "fixture-sealed-runtime"},
                },
            ],
            "mutableState": [],
            "allowedTargetRoots": ["${REPO_ROOT}/.code"],
            "installPlan": [],
            "uninstallPlan": [],
            "sealing": {
                "mode": "separate-step",
                "bundlePath": "payload/macos/FixtureBridge.app",
                "executableArtifactId": "fixture_bridge",
                "attestationPath": "Contents/Resources/runtime-sealing.json",
                "codeResourcesPath": "Contents/_CodeSignature/CodeResources",
                "bundleId": "example.fixture.sealed",
                "teamId": "FIXTURE001",
                "identity": "Developer ID Application: Runtime Fixture (FIXTURE001)",
                "timestamp": False,
            },
        }
        sealing_lock = {
            "schemaVersion": 1,
            "manifestSha256": sha256_bytes(canonical_json_bytes(sealing_manifest)),
            "artifacts": [{"id": "fixture_bridge", "sha256": sha256_file(sealing_input)}],
        }
        write_json(sealing_manifest_path, sealing_manifest)
        write_json(sealing_lock_path, sealing_lock)
        write_json(sealing_bindings_path, {})
        (
            loaded_sealing_manifest,
            loaded_sealing_lock,
            sealing_manifest_hash,
            sealing_lock_hash,
        ) = load_contract(sealing_manifest_path, sealing_lock_path)
        sealing_item = loaded_sealing_manifest["artifacts"][0]
        sealing_lock_item = loaded_sealing_lock["artifacts"][0]

        def fake_artifact_inspector(
            item: dict[str, Any],
            lock_item: dict[str, Any],
            path: pathlib.Path,
        ) -> dict[str, Any]:
            require_regular_file(path, "input.missing", item["id"])
            if sha256_file(path) != lock_item["sha256"]:
                raise ArtifactError("input.hash", "Fixture input hash differs")
            return locked_input_record(item, lock_item)

        def fake_code_resources(bundle: pathlib.Path) -> bytes:
            info = bundle / "Contents" / "Info.plist"
            owner = bundle / "Contents" / "Resources" / "runtime-owner.json"
            attestation = bundle / "Contents" / "Resources" / "runtime-sealing.json"
            return canonical_json_bytes(
                {
                    "attestation": sha256_file(attestation),
                    "info": sha256_file(info),
                    "owner": sha256_file(owner),
                }
            )

        def fake_signer_with_cms(bundle: pathlib.Path, cms_marker: bytes) -> None:
            executable = bundle / "Contents" / "MacOS" / "fixture_bridge"
            executable.write_bytes(executable.read_bytes() + b"\nCMS:" + cms_marker)
            resources = bundle / "Contents" / "_CodeSignature" / "CodeResources"
            resources.parent.mkdir(parents=True)
            resources.write_bytes(fake_code_resources(bundle))

        def fake_signer_a(bundle: pathlib.Path, _: dict[str, Any]) -> None:
            fake_signer_with_cms(bundle, b"A")

        def fake_signer_b(bundle: pathlib.Path, _: dict[str, Any]) -> None:
            fake_signer_with_cms(bundle, b"B")

        def fake_bundle_inspector(
            bundle: pathlib.Path,
            manifest_value: dict[str, Any],
        ) -> dict[str, Any]:
            resources = bundle / "Contents" / "_CodeSignature" / "CodeResources"
            if resources.read_bytes() != fake_code_resources(bundle):
                raise ArtifactError("sealing.signature", "Fixture resource signature differs")
            executable = bundle / "Contents" / "MacOS" / "fixture_bridge"
            code_bytes, marker, cms_bytes = executable.read_bytes().partition(b"\nCMS:")
            if marker != b"\nCMS:" or cms_bytes not in {b"A", b"B"}:
                raise ArtifactError("sealing.signature", "Fixture CMS signature differs")
            policy = manifest_value["sealing"]
            return {
                "kind": "developer-id",
                "identifier": policy["bundleId"],
                "teamIdentifier": policy["teamId"],
                "cdhash": hashlib.sha1(
                    code_bytes,
                    usedforsecurity=False,
                ).hexdigest(),
                "authority": policy["identity"],
                "timestamp": policy["timestamp"],
            }

        sealing_validation = {
            "gitSources": [],
            "sourceFiles": [],
            "prerequisites": [],
            "artifacts": [locked_input_record(sealing_item, sealing_lock_item)],
            "artifactPaths": {"fixture_bridge": sealing_input},
        }
        unsealed_artifact = pathlib.Path(
            build_artifact(
                loaded_sealing_manifest,
                loaded_sealing_lock,
                sealing_manifest_hash,
                sealing_lock_hash,
                sealing_validation,
                temp_root / "sealing-unsealed",
                artifact_inspector=fake_artifact_inspector,
            )["path"]
        )
        unsealed_plan = build_plan(
            loaded_sealing_manifest,
            unsealed_artifact,
            sealing_bindings_path,
            bundle_inspector=fake_bundle_inspector,
            artifact_inspector=fake_artifact_inspector,
        )
        if not unsealed_plan["requiresSealing"] or unsealed_plan["artifactStage"] != "unsealed":
            raise ArtifactError("self-test.failed", "Unsealed fixture did not remain mutation-gated")
        stale_seal = temp_root / "sealing-output-a" / ".seal-stale"
        stale_publish = temp_root / "sealing-output-a" / ".publish-stale"
        stale_seal.mkdir(parents=True)
        stale_publish.mkdir()
        (stale_seal / "partial").write_bytes(b"partial\n")
        sealed_artifact_a = pathlib.Path(
            seal_artifact(
                loaded_sealing_manifest,
                loaded_sealing_lock,
                sealing_manifest_hash,
                sealing_lock_hash,
                unsealed_artifact,
                temp_root / "sealing-output-a",
                signer=fake_signer_a,
                bundle_inspector=fake_bundle_inspector,
                artifact_inspector=fake_artifact_inspector,
            )["path"]
        )
        if stale_seal.exists() or stale_publish.exists():
            raise ArtifactError("self-test.failed", "Stale sealing paths were not reconciled")
        sealed_artifact_b = pathlib.Path(
            seal_artifact(
                loaded_sealing_manifest,
                loaded_sealing_lock,
                sealing_manifest_hash,
                sealing_lock_hash,
                unsealed_artifact,
                temp_root / "sealing-output-b",
                signer=fake_signer_b,
                bundle_inspector=fake_bundle_inspector,
                artifact_inspector=fake_artifact_inspector,
            )["path"]
        )
        expect_error(
            "artifact.compare",
            lambda: compare_artifacts(
                sealed_artifact_a,
                sealed_artifact_b,
                bundle_inspector=fake_bundle_inspector,
                artifact_inspector=fake_artifact_inspector,
            ),
        )
        sealed_record_a = load_json(sealed_artifact_a / SEALING_PROVENANCE_PATH)
        sealed_record_b = load_json(sealed_artifact_b / SEALING_PROVENANCE_PATH)
        if (
            sealed_record_a["signature"] != sealed_record_b["signature"]
            or sealed_record_a["sealedTreeSha256"] == sealed_record_b["sealedTreeSha256"]
        ):
            raise ArtifactError(
                "self-test.failed",
                "Fixture CMS variance did not preserve stable code identity",
            )
        sealed_plan = build_plan(
            loaded_sealing_manifest,
            sealed_artifact_a,
            sealing_bindings_path,
            bundle_inspector=fake_bundle_inspector,
            artifact_inspector=fake_artifact_inspector,
        )
        if sealed_plan["requiresSealing"] or sealed_plan["artifactStage"] != "sealed":
            raise ArtifactError("self-test.failed", "Sealed fixture did not become plan-ready")
        relocated_root = temp_root / "sealing-relocated"
        relocated_root.mkdir()
        relocated_artifact = relocated_root / sealed_artifact_a.name
        shutil.copytree(sealed_artifact_a, relocated_artifact, copy_function=shutil.copy2)
        verify_artifact(
            relocated_artifact,
            bundle_inspector=fake_bundle_inspector,
            artifact_inspector=fake_artifact_inspector,
        )
        expect_error(
            "sealing.source_mismatch",
            lambda: seal_artifact(
                loaded_sealing_manifest,
                loaded_sealing_lock,
                sealing_manifest_hash,
                sealing_lock_hash,
                sealed_artifact_a,
                temp_root / "sealing-refused",
                signer=fake_signer_a,
                bundle_inspector=fake_bundle_inspector,
                artifact_inspector=fake_artifact_inspector,
            ),
        )
        make_tree_writable(relocated_artifact)
        (
            relocated_artifact
            / "payload/macos/FixtureBridge.app/Contents/_CodeSignature/CodeResources"
        ).write_bytes(b"tampered\n")
        make_tree_read_only(relocated_artifact)
        expect_error(
            "artifact.verify",
            lambda: verify_artifact(
                relocated_artifact,
                bundle_inspector=fake_bundle_inspector,
                artifact_inspector=fake_artifact_inspector,
            ),
        )
        completed.extend(
            [
                "unsealed-gate",
                "post-build-seal",
                "stale-seal-cleanup",
                "sealed-cms-variance",
                "sealed-plan",
                "sealed-relocation",
                "sealed-source-refusal",
                "sealed-tamper",
            ]
        )

        make_tree_writable(artifact_a)
        (artifact_a / "payload" / "fixture.dll").write_bytes(b"tampered\n")
        expect_error("artifact.verify", lambda: verify_artifact(artifact_a))
        completed.append("tamper-detection")
    return {"ok": True, "tests": completed}


def validate_checked_repository_sources(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    bindings = {"REPO_ROOT": str(REPO_ROOT.resolve()), "HOME": str(pathlib.Path.home().resolve())}
    self_sources = [item for item in manifest["gitSources"] if item["revision"] == "self"]
    if len(self_sources) != 1:
        raise ArtifactError("manifest.invalid", "Manifest must declare exactly one self Git source")
    self_source = self_sources[0]
    git_roots = [(self_source["id"], REPO_ROOT.resolve())]
    records: list[dict[str, Any]] = []
    for item in manifest["sourceFiles"]:
        tokens = set(TOKEN_PATTERN.findall(item["path"]))
        if not tokens <= bindings.keys():
            continue
        records.append(validate_source_file(item, bindings, git_roots))
    support_path = REPO_ROOT / safe_relative_path(manifest["artifact"]["supportMatrix"], "artifact.supportMatrix")
    require_regular_file(support_path, "source.missing", "support_matrix")
    return records


def command_contract_digests(args: argparse.Namespace) -> dict[str, Any]:
    manifest_raw = load_json(args.manifest)
    manifest = validate_manifest_structure(copy.deepcopy(manifest_raw))
    manifest.pop("_artifactIds")
    manifest.pop("_bindingNames")
    bindings = {"REPO_ROOT": str(REPO_ROOT.resolve()), "HOME": str(pathlib.Path.home().resolve())}
    digests: list[dict[str, str]] = []
    updated = copy.deepcopy(manifest_raw)
    updated_files = {item["id"]: item for item in updated["sourceFiles"]}
    for item in manifest["sourceFiles"]:
        tokens = set(TOKEN_PATTERN.findall(item["path"]))
        if not tokens <= bindings.keys():
            continue
        path = resolve_path(item["path"], bindings, f"sourceFiles.{item['id']}.path")
        require_regular_file(path, "source.missing", item["id"])
        try:
            relative = path.relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError as error:
            raise ArtifactError(
                "source.untracked",
                "Contract digest source is outside the manifest repository",
                id=item["id"],
            ) from error
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if tracked.returncode != 0:
            raise ArtifactError(
                "source.untracked",
                "Contract digest source is not tracked or staged",
                id=item["id"],
            )
        digest = sha256_file(path)
        updated_files[item["id"]]["sha256"] = digest
        digests.append({"id": item["id"], "sha256": digest})
    return {
        "ok": True,
        "manifestSha256": sha256_bytes(canonical_json_bytes(updated)),
        "sourceFiles": digests,
    }


def command_check(args: argparse.Namespace) -> dict[str, Any]:
    manifest, lock_data, manifest_hash, lock_hash = load_contract(args.manifest, args.lock)
    source_files = validate_checked_repository_sources(manifest)
    return {
        "ok": True,
        "artifact": manifest["artifact"],
        "artifactCount": len(lock_data["artifacts"]),
        "lockSha256": lock_hash,
        "manifestSha256": manifest_hash,
        "sourceFileCount": len(source_files),
    }


def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    manifest, lock_data, manifest_hash, lock_hash = load_contract(args.manifest, args.lock)
    validation = validate_environment(manifest, lock_data, args.bindings, "validate")
    return {
        "ok": True,
        "artifact": manifest["artifact"],
        "artifactCount": len(validation["artifacts"]),
        "gitSources": validation["gitSources"],
        "lockSha256": lock_hash,
        "manifestSha256": manifest_hash,
        "prerequisites": validation["prerequisites"],
        "sourceFileCount": len(validation["sourceFiles"]),
    }


def command_build(args: argparse.Namespace) -> dict[str, Any]:
    manifest, lock_data, manifest_hash, lock_hash = load_contract(args.manifest, args.lock)
    validation = validate_environment(manifest, lock_data, args.bindings, "build")
    result = build_artifact(manifest, lock_data, manifest_hash, lock_hash, validation, args.output_root)
    return {"ok": True, **result}


def command_seal(args: argparse.Namespace) -> dict[str, Any]:
    manifest, lock_data, manifest_hash, lock_hash = load_contract(args.manifest, args.lock)
    output_root = args.output_root or args.artifact.resolve().parent
    result = seal_artifact(
        manifest,
        lock_data,
        manifest_hash,
        lock_hash,
        args.artifact,
        output_root,
    )
    return {"ok": True, **result}


def command_plan(args: argparse.Namespace) -> dict[str, Any]:
    manifest, _, manifest_hash, lock_hash = load_contract(args.manifest, args.lock)
    return {
        "ok": True,
        **build_plan(
            manifest,
            args.artifact,
            args.bindings,
            expected_manifest_hash=manifest_hash,
            expected_lock_hash=lock_hash,
        ),
    }


def command_verify(args: argparse.Namespace) -> dict[str, Any]:
    metadata = verify_artifact(args.artifact)
    return {
        "ok": True,
        "path": str(args.artifact.resolve()),
        "sealId": metadata["sealId"],
        "stage": metadata["stage"],
    }


def command_compare(args: argparse.Namespace) -> dict[str, Any]:
    return {"ok": True, **compare_artifacts(args.left, args.right)}


def command_self_test(_: argparse.Namespace) -> dict[str, Any]:
    return self_test()


def add_contract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lock", type=pathlib.Path, default=DEFAULT_LOCK)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Validate manifest and lock structure")
    add_contract_arguments(check_parser)
    check_parser.set_defaults(handler=command_check)

    digest_parser = subparsers.add_parser(
        "contract-digests",
        help="Compute proposed repository-source and manifest digests without writing files",
    )
    digest_parser.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    digest_parser.set_defaults(handler=command_contract_digests)

    validate_parser = subparsers.add_parser("validate", help="Validate local source and binary inputs")
    add_contract_arguments(validate_parser)
    validate_parser.add_argument("--bindings", type=pathlib.Path, default=DEFAULT_BINDINGS)
    validate_parser.set_defaults(handler=command_validate)

    build_command = subparsers.add_parser("build", help="Build an immutable content-addressed artifact")
    add_contract_arguments(build_command)
    build_command.add_argument("--bindings", type=pathlib.Path, default=DEFAULT_BINDINGS)
    build_command.add_argument("--output-root", type=pathlib.Path, default=DEFAULT_OUTPUT_ROOT)
    build_command.set_defaults(handler=command_build)

    seal_parser = subparsers.add_parser(
        "seal",
        help="Developer ID sign an immutable artifact and publish its final content address",
    )
    add_contract_arguments(seal_parser)
    seal_parser.add_argument("--artifact", type=pathlib.Path, required=True)
    seal_parser.add_argument("--output-root", type=pathlib.Path)
    seal_parser.set_defaults(handler=command_seal)

    plan_parser = subparsers.add_parser("plan", help="Emit exact read-only install and uninstall operations")
    add_contract_arguments(plan_parser)
    plan_parser.add_argument("--artifact", type=pathlib.Path, required=True)
    plan_parser.add_argument("--bindings", type=pathlib.Path, default=DEFAULT_BINDINGS)
    plan_parser.set_defaults(handler=command_plan)

    verify_parser = subparsers.add_parser("verify", help="Verify an immutable artifact and its seal")
    verify_parser.add_argument("--artifact", type=pathlib.Path, required=True)
    verify_parser.set_defaults(handler=command_verify)

    compare_parser = subparsers.add_parser("compare", help="Compare two independently built artifacts")
    compare_parser.add_argument("left", type=pathlib.Path)
    compare_parser.add_argument("right", type=pathlib.Path)
    compare_parser.set_defaults(handler=command_compare)

    self_test_parser = subparsers.add_parser("self-test", help="Run bounded hardware-free validation fixtures")
    self_test_parser.set_defaults(handler=command_self_test)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.handler(args)
    except ArtifactError as error:
        emit(
            {
                "ok": False,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "context": error.context,
                },
            },
            stream=sys.stderr,
        )
        return 2
    except Exception as error:
        emit(
            {
                "ok": False,
                "error": {
                    "code": "internal.error",
                    "message": "Unexpected runtime artifact failure",
                    "context": {"type": type(error).__name__},
                },
            },
            stream=sys.stderr,
        )
        return 3
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
