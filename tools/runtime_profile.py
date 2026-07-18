#!/usr/bin/env python3
"""Validate and qualify curated OpenVR game profiles against a sealed runtime."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import stat
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Mapping


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE_ROOT = REPO_ROOT / "runtime" / "profiles"
SCHEMA_PATH = REPO_ROOT / "runtime" / "game-profile.schema.json"
ARTIFACT_VERIFY_TOOL = REPO_ROOT / "tools" / "build_runtime_artifact.py"
CLEANUP_TOOL = REPO_ROOT / "tools" / "vr_stack_cleanup.py"
PROBE_RUNNER = REPO_ROOT / "tools" / "run_real_native_iosurface_probe.sh"
PROFILE_OUTPUT_ROOT = REPO_ROOT / ".code" / "probes" / "013-the-lab-profile-qualification"
CURATED_PROFILE_PATH_PREFIX = "${REPO_ROOT}/runtime/profiles/"

PROFILE_KEYS = {
    "schemaVersion",
    "id",
    "name",
    "source",
    "launch",
    "runtime",
    "geometry",
    "controller",
    "validation",
}
SOURCE_KEYS = {
    "provider",
    "appId",
    "buildId",
    "depots",
    "installedSizeBytes",
    "officialUnmodified",
    "payload",
}
DEPOT_KEYS = {"id", "manifest", "sizeBytes"}
PAYLOAD_KEYS = {"fileCount", "treeSha256", "criticalFiles"}
CRITICAL_FILE_KEYS = {"path", "sha256"}
LAUNCH_KEYS = {
    "installRoot",
    "entrypointTarget",
    "arguments",
    "environment",
    "processPattern",
    "startupTimeoutSeconds",
    "transitionTimeoutSeconds",
}
RUNTIME_KEYS = {"openvrRuntime", "graphicsApi", "targets"}
TARGET_KEYS = {
    "id",
    "role",
    "executable",
    "workingDirectory",
    "openvrDirectory",
    "graphicsDirectory",
    "stockOpenvrSha256",
    "processPattern",
}
GEOMETRY_KEYS = {
    "sourceMode",
    "recommendedPerEye",
    "maximumStereo",
    "allowedStereoTransitions",
    "adaptiveViewport",
    "eyeMappings",
    "targetStereo",
}
DIMENSION_KEYS = {"width", "height"}
ADAPTIVE_KEYS = {
    "enabled",
    "minimumScale",
    "maximumScale",
    "fillRateStepPercent",
    "maximumDimension",
}
CONTROLLER_KEYS = {"profile", "trackedRoles", "requiredInputs", "gameplayChecks"}
VALIDATION_KEYS = {
    "frameCounts",
    "cadence",
    "drops",
    "pose",
    "recovery",
    "cleanup",
    "human",
}
FRAME_COUNT_KEYS = {"smoke", "disconnected", "physical"}
CADENCE_KEYS = {"targetFps", "minimumFps", "maximumFps"}
DROPS_KEYS = {"producer", "native"}
POSE_KEYS = {"maximumGenerationGaps", "exactPairing"}
RECOVERY_KEYS = {"clientRelaunch", "headsetReentry"}
CLEANUP_KEYS = {"requireStockHashes", "requireNoOwnedState"}
HUMAN_KEYS = {"clearAndSmooth"}

IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENVIRONMENT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
TEMPLATE_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
VDF_TOKEN_RE = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])')

REQUIRED_CONTROLLER_INPUTS = {"button", "grip", "pose", "thumbstick", "trigger"}
ALLOWED_EYE_MAPPINGS = {"separate-textures", "side-by-side-left-first"}
ALLOWED_TARGET_ROLES = {"experience", "game", "hub"}
GLOBAL_TARGET_FPS = 90.0
GLOBAL_MINIMUM_FPS = 89.5
GLOBAL_MAXIMUM_FPS = 90.5


class ProfileError(Exception):
    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context


@dataclass(frozen=True)
class LoadedProfile:
    path: pathlib.Path
    data: dict[str, Any]
    sha256: str


@dataclass(frozen=True)
class ResolvedProfileTarget:
    id: str
    role: str
    executable: pathlib.Path
    working_directory: pathlib.Path
    openvr_directory: pathlib.Path
    graphics_directory: pathlib.Path
    stock_openvr_sha256: str
    process_pattern: str


@dataclass(frozen=True)
class InstalledProfile:
    loaded: LoadedProfile
    install_root: pathlib.Path
    steam_manifest: pathlib.Path
    steam_manifest_sha256: str
    targets: tuple[ResolvedProfileTarget, ...]

    @property
    def entrypoint(self) -> ResolvedProfileTarget:
        return self.targets[0]


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileError("profile.invalid", "Expected an object", location=location)
    return value


def require_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProfileError("profile.invalid", "Expected an array", location=location)
    return value


def require_exact_keys(value: dict[str, Any], expected: set[str], location: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ProfileError(
            "profile.invalid",
            "Object keys do not match the profile contract",
            location=location,
            missing=sorted(expected - actual),
            unknown=sorted(actual - expected),
        )


def require_string(value: Any, location: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise ProfileError("profile.invalid", "Expected a string", location=location)
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ProfileError("profile.invalid", "String contains an unsafe character", location=location)
    return value


def require_bool(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise ProfileError("profile.invalid", "Expected a boolean", location=location)
    return value


def require_integer(
    value: Any,
    location: str,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProfileError(
            "profile.invalid",
            "Expected an integer in range",
            location=location,
            minimum=minimum,
        )
    if maximum is not None and value > maximum:
        raise ProfileError(
            "profile.invalid",
            "Expected an integer in range",
            location=location,
            minimum=minimum,
            maximum=maximum,
        )
    return value


def require_number(value: Any, location: str, *, minimum_exclusive: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError("profile.invalid", "Expected a number", location=location)
    result = float(value)
    if not math.isfinite(result) or result <= minimum_exclusive:
        raise ProfileError(
            "profile.invalid",
            "Expected a finite number above the lower bound",
            location=location,
            minimumExclusive=minimum_exclusive,
        )
    return result


def require_identifier(value: Any, location: str) -> str:
    result = require_string(value, location)
    if not IDENTIFIER_RE.fullmatch(result):
        raise ProfileError("profile.invalid", "Invalid identifier", location=location, value=result)
    return result


def require_decimal(value: Any, location: str) -> str:
    result = require_string(value, location)
    if not DECIMAL_RE.fullmatch(result):
        raise ProfileError("profile.invalid", "Expected a positive decimal string", location=location)
    return result


def require_sha256(value: Any, location: str) -> str:
    result = require_string(value, location)
    if not SHA256_RE.fullmatch(result):
        raise ProfileError("profile.invalid", "Expected a lowercase SHA-256", location=location)
    return result


def require_relative_path(value: Any, location: str) -> str:
    result = require_string(value, location)
    if ":" in result or "\\" in result:
        raise ProfileError(
            "profile.invalid",
            "Relative paths must use slash separators and contain no colons",
            location=location,
        )
    path = pathlib.PurePosixPath(result)
    if path.is_absolute() or ".." in path.parts:
        raise ProfileError("profile.invalid", "Unsafe relative path", location=location, value=result)
    return result


def validate_dimensions(value: Any, location: str) -> tuple[int, int]:
    dimensions = require_object(value, location)
    require_exact_keys(dimensions, DIMENSION_KEYS, location)
    width = require_integer(dimensions["width"], f"{location}.width")
    height = require_integer(dimensions["height"], f"{location}.height")
    return width, height


def require_unique_strings(value: Any, location: str, *, allowed: set[str] | None = None) -> list[str]:
    raw_items = require_list(value, location)
    items = [require_string(item, f"{location}[{index}]") for index, item in enumerate(raw_items)]
    if not items:
        raise ProfileError("profile.invalid", "Array must not be empty", location=location)
    if len(items) != len(set(items)):
        raise ProfileError("profile.invalid", "Array entries must be unique", location=location)
    if allowed is not None and not set(items) <= allowed:
        raise ProfileError(
            "profile.invalid",
            "Array contains unsupported values",
            location=location,
            unsupported=sorted(set(items) - allowed),
        )
    return items


def validate_source(profile: dict[str, Any]) -> None:
    source = require_object(profile["source"], "source")
    require_exact_keys(source, SOURCE_KEYS, "source")
    if source["provider"] != "steam":
        raise ProfileError("profile.invalid", "Only Steam sources are supported", location="source.provider")
    require_decimal(source["appId"], "source.appId")
    require_decimal(source["buildId"], "source.buildId")
    require_integer(source["installedSizeBytes"], "source.installedSizeBytes")
    if require_bool(source["officialUnmodified"], "source.officialUnmodified") is not True:
        raise ProfileError(
            "profile.invalid",
            "Qualification requires an official unmodified payload",
            location="source.officialUnmodified",
        )
    depots = require_list(source["depots"], "source.depots")
    if not depots:
        raise ProfileError("profile.invalid", "At least one depot is required", location="source.depots")
    depot_ids: set[str] = set()
    for index, raw_depot in enumerate(depots):
        location = f"source.depots[{index}]"
        depot = require_object(raw_depot, location)
        require_exact_keys(depot, DEPOT_KEYS, location)
        depot_id = require_decimal(depot["id"], f"{location}.id")
        require_decimal(depot["manifest"], f"{location}.manifest")
        require_integer(depot["sizeBytes"], f"{location}.sizeBytes")
        if depot_id in depot_ids:
            raise ProfileError("profile.invalid", "Depot ids must be unique", location=location)
        depot_ids.add(depot_id)
    payload = require_object(source["payload"], "source.payload")
    require_exact_keys(payload, PAYLOAD_KEYS, "source.payload")
    require_integer(payload["fileCount"], "source.payload.fileCount")
    require_sha256(payload["treeSha256"], "source.payload.treeSha256")
    critical_files = require_list(payload["criticalFiles"], "source.payload.criticalFiles")
    if not critical_files:
        raise ProfileError(
            "profile.invalid",
            "At least one critical payload file is required",
            location="source.payload.criticalFiles",
        )
    critical_paths: set[str] = set()
    for index, raw_file in enumerate(critical_files):
        location = f"source.payload.criticalFiles[{index}]"
        critical_file = require_object(raw_file, location)
        require_exact_keys(critical_file, CRITICAL_FILE_KEYS, location)
        relative_path = require_relative_path(critical_file["path"], f"{location}.path")
        require_sha256(critical_file["sha256"], f"{location}.sha256")
        if relative_path in critical_paths:
            raise ProfileError("profile.invalid", "Critical payload paths must be unique", location=location)
        critical_paths.add(relative_path)


def validate_launch(profile: dict[str, Any]) -> None:
    launch = require_object(profile["launch"], "launch")
    require_exact_keys(launch, LAUNCH_KEYS, "launch")
    install_root = require_string(launch["installRoot"], "launch.installRoot")
    unknown_templates = sorted(set(TEMPLATE_RE.findall(install_root)) - {"HOME", "REPO_ROOT", "STEAM_BOTTLE"})
    if unknown_templates:
        raise ProfileError(
            "profile.invalid",
            "Install root contains unsupported templates",
            location="launch.installRoot",
            templates=unknown_templates,
        )
    require_identifier(launch["entrypointTarget"], "launch.entrypointTarget")
    arguments = require_list(launch["arguments"], "launch.arguments")
    for index, argument in enumerate(arguments):
        require_string(argument, f"launch.arguments[{index}]", nonempty=False)
        if any(character.isspace() for character in argument):
            raise ProfileError(
                "profile.invalid",
                "Arguments containing whitespace are not representable by the research runner",
                location=f"launch.arguments[{index}]",
            )
    environment = require_object(launch["environment"], "launch.environment")
    for name, raw_value in environment.items():
        if not ENVIRONMENT_RE.fullmatch(name):
            raise ProfileError("profile.invalid", "Invalid environment key", location="launch.environment")
        value = require_string(raw_value, f"launch.environment.{name}", nonempty=False)
        if any(character.isspace() for character in value):
            raise ProfileError(
                "profile.invalid",
                "Environment values containing whitespace are not supported",
                location=f"launch.environment.{name}",
            )
    process_pattern = require_string(launch["processPattern"], "launch.processPattern")
    try:
        re.compile(process_pattern)
    except re.error as error:
        raise ProfileError(
            "profile.invalid",
            "Invalid process regular expression",
            location="launch.processPattern",
            detail=str(error),
        ) from error
    require_integer(
        launch["startupTimeoutSeconds"],
        "launch.startupTimeoutSeconds",
        maximum=600,
    )
    require_integer(
        launch["transitionTimeoutSeconds"],
        "launch.transitionTimeoutSeconds",
        maximum=600,
    )


def validate_runtime(profile: dict[str, Any]) -> None:
    runtime = require_object(profile["runtime"], "runtime")
    require_exact_keys(runtime, RUNTIME_KEYS, "runtime")
    if runtime["openvrRuntime"] != "custom-openvr-sidecar":
        raise ProfileError("profile.invalid", "Unsupported OpenVR runtime", location="runtime.openvrRuntime")
    if runtime["graphicsApi"] != "d3d11":
        raise ProfileError("profile.invalid", "Only D3D11 is supported", location="runtime.graphicsApi")
    targets = require_list(runtime["targets"], "runtime.targets")
    if not targets:
        raise ProfileError("profile.invalid", "At least one runtime target is required", location="runtime.targets")

    target_ids: set[str] = set()
    executables: set[str] = set()
    openvr_directories: set[str] = set()
    graphics_directories: set[str] = set()
    hub_count = 0
    for index, raw_target in enumerate(targets):
        location = f"runtime.targets[{index}]"
        target = require_object(raw_target, location)
        require_exact_keys(target, TARGET_KEYS, location)
        target_id = require_identifier(target["id"], f"{location}.id")
        if target_id in target_ids:
            raise ProfileError("profile.invalid", "Target ids must be unique", location=f"{location}.id")
        target_ids.add(target_id)
        role = require_string(target["role"], f"{location}.role")
        if role not in ALLOWED_TARGET_ROLES:
            raise ProfileError("profile.invalid", "Unsupported target role", location=f"{location}.role")
        hub_count += int(role == "hub")
        executable = require_relative_path(target["executable"], f"{location}.executable")
        working_directory = require_relative_path(target["workingDirectory"], f"{location}.workingDirectory")
        openvr_directory = require_relative_path(target["openvrDirectory"], f"{location}.openvrDirectory")
        graphics_directory = require_relative_path(target["graphicsDirectory"], f"{location}.graphicsDirectory")
        require_sha256(target["stockOpenvrSha256"], f"{location}.stockOpenvrSha256")
        process_pattern = require_string(target["processPattern"], f"{location}.processPattern")
        try:
            re.compile(process_pattern)
        except re.error as error:
            raise ProfileError(
                "profile.invalid",
                "Invalid target process regular expression",
                location=f"{location}.processPattern",
                detail=str(error),
            ) from error
        for value, seen, label in (
            (executable, executables, "executables"),
            (openvr_directory, openvr_directories, "OpenVR directories"),
            (graphics_directory, graphics_directories, "graphics directories"),
        ):
            if value in seen:
                raise ProfileError("profile.invalid", f"Target {label} must be unique", location=location)
            seen.add(value)
        if working_directory != "." and not pathlib.PurePosixPath(executable).is_relative_to(
            pathlib.PurePosixPath(working_directory)
        ):
            raise ProfileError(
                "profile.invalid",
                "Executable must be within its working directory",
                location=location,
            )

    entrypoint = profile["launch"]["entrypointTarget"]
    if entrypoint not in target_ids:
        raise ProfileError(
            "profile.invalid",
            "Launch entrypoint does not identify a runtime target",
            location="launch.entrypointTarget",
        )
    if hub_count > 1:
        raise ProfileError("profile.invalid", "Only one hub target is allowed", location="runtime.targets")
    if len(targets) > 1 and hub_count != 1:
        raise ProfileError(
            "profile.invalid",
            "Multi-process profiles require exactly one hub",
            location="runtime.targets",
        )


def validate_geometry(profile: dict[str, Any]) -> None:
    geometry = require_object(profile["geometry"], "geometry")
    require_exact_keys(geometry, GEOMETRY_KEYS, "geometry")
    source_mode = require_string(geometry["sourceMode"], "geometry.sourceMode")
    if source_mode not in {"bounded-adaptive", "fixed-with-transitions"}:
        raise ProfileError("profile.invalid", "Unsupported source mode", location="geometry.sourceMode")
    recommended_width, recommended_height = validate_dimensions(
        geometry["recommendedPerEye"], "geometry.recommendedPerEye"
    )
    maximum_width, maximum_height = validate_dimensions(geometry["maximumStereo"], "geometry.maximumStereo")
    target_width, target_height = validate_dimensions(geometry["targetStereo"], "geometry.targetStereo")
    if target_width > maximum_width or target_height > maximum_height:
        raise ProfileError(
            "profile.invalid",
            "Target geometry exceeds the bounded source pool",
            location="geometry.targetStereo",
        )

    transitions = require_list(geometry["allowedStereoTransitions"], "geometry.allowedStereoTransitions")
    transition_values: set[tuple[int, int]] = set()
    for index, transition in enumerate(transitions):
        width, height = validate_dimensions(transition, f"geometry.allowedStereoTransitions[{index}]")
        if width > maximum_width or height > maximum_height:
            raise ProfileError(
                "profile.invalid",
                "Allowed transition exceeds the bounded source pool",
                location=f"geometry.allowedStereoTransitions[{index}]",
            )
        if (width, height) in transition_values:
            raise ProfileError(
                "profile.invalid",
                "Allowed transitions must be unique",
                location="geometry.allowedStereoTransitions",
            )
        transition_values.add((width, height))

    adaptive = require_object(geometry["adaptiveViewport"], "geometry.adaptiveViewport")
    require_exact_keys(adaptive, ADAPTIVE_KEYS, "geometry.adaptiveViewport")
    enabled = require_bool(adaptive["enabled"], "geometry.adaptiveViewport.enabled")
    minimum_scale = require_number(adaptive["minimumScale"], "geometry.adaptiveViewport.minimumScale")
    maximum_scale = require_number(adaptive["maximumScale"], "geometry.adaptiveViewport.maximumScale")
    fill_rate_step = require_integer(
        adaptive["fillRateStepPercent"], "geometry.adaptiveViewport.fillRateStepPercent", minimum=0
    )
    maximum_dimension = require_integer(
        adaptive["maximumDimension"], "geometry.adaptiveViewport.maximumDimension"
    )
    if minimum_scale > maximum_scale:
        raise ProfileError(
            "profile.invalid",
            "Adaptive scale bounds are reversed",
            location="geometry.adaptiveViewport",
        )
    if enabled:
        if source_mode != "bounded-adaptive" or not minimum_scale <= 1.0 <= maximum_scale or fill_rate_step == 0:
            raise ProfileError(
                "profile.invalid",
                "Adaptive profiles require bounded scales around 1.0 and a positive fill-rate step",
                location="geometry.adaptiveViewport",
            )
        required_width = math.ceil(recommended_width * maximum_scale * 2)
        required_height = math.ceil(recommended_height * maximum_scale)
        if required_width > maximum_width or required_height > maximum_height:
            raise ProfileError(
                "profile.invalid",
                "Adaptive maximum does not fit the declared stereo pool",
                location="geometry.adaptiveViewport",
                required={"width": required_width, "height": required_height},
                maximum={"width": maximum_width, "height": maximum_height},
            )
    elif source_mode != "fixed-with-transitions" or minimum_scale != 1.0 or maximum_scale != 1.0 or fill_rate_step != 0:
        raise ProfileError(
            "profile.invalid",
            "Fixed profiles must use a disabled 1.0-scale viewport",
            location="geometry.adaptiveViewport",
        )
    if maximum_dimension > max(maximum_height, maximum_width // 2):
        raise ProfileError(
            "profile.invalid",
            "Per-eye maximum dimension exceeds the stereo pool",
            location="geometry.adaptiveViewport.maximumDimension",
        )
    require_unique_strings(geometry["eyeMappings"], "geometry.eyeMappings", allowed=ALLOWED_EYE_MAPPINGS)


def validate_controller(profile: dict[str, Any]) -> None:
    controller = require_object(profile["controller"], "controller")
    require_exact_keys(controller, CONTROLLER_KEYS, "controller")
    if controller["profile"] != "PSVR2Sense":
        raise ProfileError("profile.invalid", "Only PSVR2Sense is qualified", location="controller.profile")
    roles = require_unique_strings(controller["trackedRoles"], "controller.trackedRoles", allowed={"left", "right"})
    if set(roles) != {"left", "right"}:
        raise ProfileError(
            "profile.invalid",
            "Both controller roles are required",
            location="controller.trackedRoles",
        )
    inputs = require_unique_strings(
        controller["requiredInputs"], "controller.requiredInputs", allowed=REQUIRED_CONTROLLER_INPUTS
    )
    if set(inputs) != REQUIRED_CONTROLLER_INPUTS:
        raise ProfileError(
            "profile.invalid",
            "Profiles may not weaken the controller input gate",
            location="controller.requiredInputs",
        )
    require_unique_strings(controller["gameplayChecks"], "controller.gameplayChecks")


def validate_validation(profile: dict[str, Any]) -> None:
    validation = require_object(profile["validation"], "validation")
    require_exact_keys(validation, VALIDATION_KEYS, "validation")
    frame_counts = require_object(validation["frameCounts"], "validation.frameCounts")
    require_exact_keys(frame_counts, FRAME_COUNT_KEYS, "validation.frameCounts")
    for name in sorted(FRAME_COUNT_KEYS):
        require_integer(frame_counts[name], f"validation.frameCounts.{name}")
    if not frame_counts["smoke"] <= frame_counts["disconnected"] <= frame_counts["physical"]:
        raise ProfileError(
            "profile.invalid",
            "Frame counts must increase from smoke to disconnected to physical",
            location="validation.frameCounts",
        )

    cadence = require_object(validation["cadence"], "validation.cadence")
    require_exact_keys(cadence, CADENCE_KEYS, "validation.cadence")
    target_fps = require_number(cadence["targetFps"], "validation.cadence.targetFps")
    minimum_fps = require_number(cadence["minimumFps"], "validation.cadence.minimumFps")
    maximum_fps = require_number(cadence["maximumFps"], "validation.cadence.maximumFps")
    if target_fps != GLOBAL_TARGET_FPS or minimum_fps < GLOBAL_MINIMUM_FPS or maximum_fps > GLOBAL_MAXIMUM_FPS:
        raise ProfileError(
            "profile.invalid",
            "Profile cadence may not weaken the global 90 Hz gate",
            location="validation.cadence",
        )
    if not minimum_fps <= target_fps <= maximum_fps:
        raise ProfileError("profile.invalid", "Cadence bounds are reversed", location="validation.cadence")

    exact_requirements = (
        ("drops", DROPS_KEYS, {"producer": 0, "native": 0}),
        ("pose", POSE_KEYS, {"maximumGenerationGaps": 0, "exactPairing": True}),
        ("recovery", RECOVERY_KEYS, {"clientRelaunch": True, "headsetReentry": True}),
        ("cleanup", CLEANUP_KEYS, {"requireStockHashes": True, "requireNoOwnedState": True}),
        ("human", HUMAN_KEYS, {"clearAndSmooth": True}),
    )
    for name, keys, expected in exact_requirements:
        value = require_object(validation[name], f"validation.{name}")
        require_exact_keys(value, keys, f"validation.{name}")
        if value != expected:
            raise ProfileError(
                "profile.invalid",
                "Profile may not weaken a global qualification gate",
                location=f"validation.{name}",
                expected=expected,
            )


def validate_profile(profile: Any) -> dict[str, Any]:
    value = require_object(profile, "profile")
    require_exact_keys(value, PROFILE_KEYS, "profile")
    if value["schemaVersion"] != 1:
        raise ProfileError("profile.invalid", "Unsupported profile schema version", location="schemaVersion")
    require_identifier(value["id"], "id")
    require_string(value["name"], "name")
    validate_source(value)
    validate_launch(value)
    validate_runtime(value)
    validate_geometry(value)
    validate_controller(value)
    validate_validation(value)
    return value


def profile_path(profile_argument: str) -> pathlib.Path:
    candidate = pathlib.Path(profile_argument).expanduser()
    if candidate.is_absolute() or candidate.parent != pathlib.Path(".") or candidate.suffix == ".json":
        path = candidate if candidate.is_absolute() else REPO_ROOT / candidate
    else:
        path = PROFILE_ROOT / f"{profile_argument}.json"
    return pathlib.Path(os.path.abspath(path))


def load_profile(profile_argument: str, *, require_canonical: bool = True) -> LoadedProfile:
    path = profile_path(profile_argument)
    reject_symlink_components(path)
    if not path.is_file() or path.is_symlink():
        raise ProfileError("profile.not_found", "Profile is not a regular file", path=str(path))
    try:
        raw_bytes = path.read_bytes()
        raw_value = json.loads(raw_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError("profile.invalid", "Profile JSON could not be loaded", path=str(path), detail=str(error)) from error
    value = validate_profile(raw_value)
    canonical = canonical_json_bytes(value)
    if require_canonical and raw_bytes != canonical:
        raise ProfileError("profile.noncanonical", "Profile JSON is not canonical", path=str(path))
    return LoadedProfile(path=path, data=value, sha256=sha256_bytes(canonical))


def load_curated_profile(
    profile_id: str,
    manifest: dict[str, Any],
    artifact: pathlib.Path,
) -> LoadedProfile:
    if not isinstance(profile_id, str) or not IDENTIFIER_RE.fullmatch(profile_id):
        raise ProfileError(
            "profile.invalid",
            "Runtime start requires one curated lowercase profile identifier",
            profile=profile_id,
        )
    expected_manifest_path = f"{CURATED_PROFILE_PATH_PREFIX}{profile_id}.json"
    raw_sources = manifest.get("sourceFiles")
    if not isinstance(raw_sources, list):
        raise ProfileError("profile.artifact_mismatch", "Runtime manifest has no source file contract")
    matches = [
        item
        for item in raw_sources
        if isinstance(item, dict) and item.get("path") == expected_manifest_path
    ]
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("id"), str)
        or not isinstance(matches[0].get("sha256"), str)
        or not SHA256_RE.fullmatch(matches[0]["sha256"])
    ):
        raise ProfileError(
            "profile.not_curated",
            "Profile is not listed in the sealed runtime source contract",
            profile=profile_id,
        )
    source_id = matches[0]["id"]
    expected_path = PROFILE_ROOT / f"{profile_id}.json"
    loaded = load_profile(str(expected_path))
    if loaded.path != expected_path.resolve(strict=False) or loaded.data["id"] != profile_id:
        raise ProfileError(
            "profile.not_curated",
            "Profile filename and declared identifier do not match",
            profile=profile_id,
            path=str(loaded.path),
        )
    if matches[0]["sha256"] != loaded.sha256:
        raise ProfileError(
            "profile.artifact_mismatch",
            "Checked-in profile bytes differ from the runtime manifest source identity",
            profile=profile_id,
            expected=matches[0]["sha256"],
            actual=loaded.sha256,
        )

    build_inputs_path = artifact / "provenance" / "build-inputs.json"
    if not build_inputs_path.is_file() or build_inputs_path.is_symlink():
        raise ProfileError(
            "profile.artifact_mismatch",
            "Artifact build inputs are unavailable for profile admission",
            path=str(build_inputs_path),
        )
    try:
        build_inputs = json.loads(build_inputs_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError(
            "profile.artifact_mismatch",
            "Artifact build inputs could not be loaded for profile admission",
            path=str(build_inputs_path),
            detail=str(error),
        ) from error
    artifact_sources = build_inputs.get("sourceFiles") if isinstance(build_inputs, dict) else None
    if not isinstance(artifact_sources, list):
        raise ProfileError(
            "profile.artifact_mismatch",
            "Artifact build inputs have no source file records",
            path=str(build_inputs_path),
        )
    source_records = [
        item
        for item in artifact_sources
        if isinstance(item, dict) and item.get("id") == source_id
    ]
    if (
        len(source_records) != 1
        or not isinstance(source_records[0].get("sha256"), str)
        or not SHA256_RE.fullmatch(source_records[0]["sha256"])
    ):
        raise ProfileError(
            "profile.artifact_mismatch",
            "Artifact build inputs do not contain the curated profile identity",
            profile=profile_id,
            sourceId=source_id,
        )
    if source_records[0]["sha256"] != loaded.sha256:
        raise ProfileError(
            "profile.artifact_mismatch",
            "Checked-in profile bytes differ from the sealed artifact source identity",
            profile=profile_id,
            expected=source_records[0]["sha256"],
            actual=loaded.sha256,
        )
    return loaded


def load_bindings(path: pathlib.Path | None) -> dict[str, str]:
    bindings = {
        "HOME": str(pathlib.Path.home()),
        "REPO_ROOT": str(REPO_ROOT),
        "STEAM_BOTTLE": str(
            pathlib.Path.home() / "Library" / "Application Support" / "CrossOver" / "Bottles" / "Steam"
        ),
    }
    if path is None:
        return bindings
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError("binding.invalid", "Binding file could not be loaded", path=str(path), detail=str(error)) from error
    values = require_object(raw, "bindings")
    allowed = {"STEAM_BOTTLE"}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ProfileError("binding.invalid", "Unknown profile bindings", keys=unknown)
    for name, value in values.items():
        bindings[name] = require_string(value, f"bindings.{name}")
    for _ in range(4):
        changed = False
        for name, value in tuple(bindings.items()):
            expanded = TEMPLATE_RE.sub(lambda match: bindings.get(match.group(1), match.group(0)), value)
            if expanded != value:
                bindings[name] = expanded
                changed = True
        if not changed:
            break
    return bindings


def expand_template(value: str, bindings: dict[str, str], location: str) -> str:
    unknown = sorted(set(TEMPLATE_RE.findall(value)) - set(bindings))
    if unknown:
        raise ProfileError("template.unknown", "Unknown path templates", location=location, templates=unknown)
    expanded = TEMPLATE_RE.sub(lambda match: bindings[match.group(1)], value)
    if TEMPLATE_RE.search(expanded):
        raise ProfileError("template.unknown", "Recursive path template", location=location)
    return expanded


def reject_symlink_components(path: pathlib.Path) -> None:
    current = pathlib.Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ProfileError("path.symlink", "Path contains a symlink component", path=str(current))


def resolve_under(root: pathlib.Path, relative: str, location: str) -> pathlib.Path:
    candidate = root.joinpath(*pathlib.PurePosixPath(relative).parts)
    root_resolved = root.resolve(strict=False)
    candidate_resolved = candidate.resolve(strict=False)
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ProfileError("path.unsafe", "Resolved path escapes the install root", location=location)
    reject_symlink_components(candidate)
    return candidate


def require_regular_file(path: pathlib.Path, location: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ProfileError("preflight.missing", "Required regular file is absent", location=location, path=str(path))


def require_directory(path: pathlib.Path, location: str) -> None:
    if not path.is_dir() or path.is_symlink():
        raise ProfileError("preflight.missing", "Required directory is absent", location=location, path=str(path))


def inspect_pe_x86_64(path: pathlib.Path, location: str) -> None:
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                raise ValueError("missing DOS header")
            pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
            stream.seek(pe_offset)
            pe_header = stream.read(6)
            if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
                raise ValueError("missing PE header")
            machine = struct.unpack_from("<H", pe_header, 4)[0]
            if machine != 0x8664:
                raise ValueError(f"unexpected machine 0x{machine:04x}")
    except (OSError, ValueError) as error:
        raise ProfileError(
            "preflight.binary",
            "Expected an x86-64 PE file",
            location=location,
            path=str(path),
            detail=str(error),
        ) from error


def resolve_profile_install_root(
    profile: dict[str, Any],
    bindings: Mapping[str, str],
) -> pathlib.Path:
    install_root = pathlib.Path(
        expand_template(profile["launch"]["installRoot"], dict(bindings), "launch.installRoot")
    )
    if not install_root.is_absolute():
        raise ProfileError("path.unsafe", "Install root must resolve to an absolute path", path=str(install_root))
    steam_bottle_value = bindings.get("STEAM_BOTTLE")
    if not isinstance(steam_bottle_value, str) or not steam_bottle_value:
        raise ProfileError("binding.invalid", "STEAM_BOTTLE is required for profile admission")
    steam_bottle = pathlib.Path(steam_bottle_value)
    if not steam_bottle.is_absolute():
        raise ProfileError(
            "binding.invalid",
            "STEAM_BOTTLE must resolve to an absolute path",
            path=str(steam_bottle),
        )
    reject_symlink_components(steam_bottle)
    reject_symlink_components(install_root)
    steam_bottle_resolved = steam_bottle.resolve(strict=False)
    install_root_resolved = install_root.resolve(strict=False)
    if (
        install_root_resolved != steam_bottle_resolved
        and steam_bottle_resolved not in install_root_resolved.parents
    ):
        raise ProfileError(
            "path.unsafe",
            "Profile install root is outside the configured Steam bottle",
            path=str(install_root),
            steamBottle=str(steam_bottle),
        )
    require_directory(install_root, "launch.installRoot")
    return install_root


def verify_steam_identity(
    profile: dict[str, Any],
    bindings: Mapping[str, str],
) -> tuple[pathlib.Path, pathlib.Path, str]:
    install_root = resolve_profile_install_root(profile, bindings)
    source = profile["source"]
    steam_bottle = pathlib.Path(bindings["STEAM_BOTTLE"])
    manifest_path = (
        steam_bottle
        / "drive_c"
        / "Program Files (x86)"
        / "Steam"
        / "steamapps"
        / f"appmanifest_{source['appId']}.acf"
    )
    require_regular_file(manifest_path, "steam.appManifest")
    try:
        parsed_manifest = parse_vdf(manifest_path.read_text(errors="strict"))
    except (OSError, UnicodeError) as error:
        raise ProfileError(
            "profile.not_installed",
            "Steam app manifest could not be read",
            path=str(manifest_path),
            detail=str(error),
        ) from error
    app_state = require_object(parsed_manifest.get("AppState"), "steam.AppState")
    expected_manifest_values = {
        "appid": source["appId"],
        "buildid": source["buildId"],
        "SizeOnDisk": str(source["installedSizeBytes"]),
        "StateFlags": "4",
    }
    for key, expected in expected_manifest_values.items():
        actual = app_state.get(key)
        if actual != expected:
            raise ProfileError(
                "profile.not_installed",
                "Steam app identity does not match the profile",
                key=key,
                expected=expected,
                actual=actual,
                path=str(manifest_path),
            )
    installed_depots = require_object(app_state.get("InstalledDepots"), "steam.AppState.InstalledDepots")
    for depot in source["depots"]:
        installed = require_object(installed_depots.get(depot["id"]), f"steam.depot.{depot['id']}")
        if installed.get("manifest") != depot["manifest"] or installed.get("size") != str(depot["sizeBytes"]):
            raise ProfileError(
                "profile.not_installed",
                "Installed depot does not match the profile",
                depot=depot["id"],
                expected=depot,
                actual=installed,
            )
    return install_root, manifest_path, sha256_file(manifest_path)


def resolve_profile_targets(
    profile: dict[str, Any],
    install_root: pathlib.Path,
    *,
    require_stock_openvr: bool,
) -> tuple[ResolvedProfileTarget, ...]:
    resolved: list[ResolvedProfileTarget] = []
    for target in ordered_targets(profile):
        executable = resolve_under(install_root, target["executable"], f"target.{target['id']}.executable")
        working_directory = resolve_under(
            install_root,
            target["workingDirectory"],
            f"target.{target['id']}.workingDirectory",
        )
        openvr_directory = resolve_under(
            install_root,
            target["openvrDirectory"],
            f"target.{target['id']}.openvrDirectory",
        )
        graphics_directory = resolve_under(
            install_root,
            target["graphicsDirectory"],
            f"target.{target['id']}.graphicsDirectory",
        )
        require_regular_file(executable, f"target.{target['id']}.executable")
        inspect_pe_x86_64(executable, f"target.{target['id']}.executable")
        require_directory(working_directory, f"target.{target['id']}.workingDirectory")
        require_directory(openvr_directory, f"target.{target['id']}.openvrDirectory")
        require_directory(graphics_directory, f"target.{target['id']}.graphicsDirectory")
        stock_openvr = openvr_directory / "openvr_api.dll"
        require_regular_file(stock_openvr, f"target.{target['id']}.openvr")
        inspect_pe_x86_64(stock_openvr, f"target.{target['id']}.openvr")
        if require_stock_openvr:
            actual_openvr_hash = sha256_file(stock_openvr)
            if actual_openvr_hash != target["stockOpenvrSha256"]:
                raise ProfileError(
                    "preflight.hash",
                    "Stock OpenVR hash does not match the profile",
                    target=target["id"],
                    expected=target["stockOpenvrSha256"],
                    actual=actual_openvr_hash,
                    path=str(stock_openvr),
                )
        resolved.append(
            ResolvedProfileTarget(
                id=target["id"],
                role=target["role"],
                executable=executable,
                working_directory=working_directory,
                openvr_directory=openvr_directory,
                graphics_directory=graphics_directory,
                stock_openvr_sha256=target["stockOpenvrSha256"],
                process_pattern=target["processPattern"],
            )
        )
    return tuple(resolved)


def resolve_installed_profile(
    loaded: LoadedProfile,
    bindings: Mapping[str, str],
) -> InstalledProfile:
    install_root, steam_manifest, steam_manifest_sha256 = verify_steam_identity(
        loaded.data,
        bindings,
    )
    targets = resolve_profile_targets(
        loaded.data,
        install_root,
        require_stock_openvr=False,
    )
    return InstalledProfile(
        loaded=loaded,
        install_root=install_root,
        steam_manifest=steam_manifest,
        steam_manifest_sha256=steam_manifest_sha256,
        targets=targets,
    )


def payload_tree_identity(
    root: pathlib.Path,
    *,
    substitutions: Mapping[str, str] | None = None,
    excluded: set[str] | None = None,
) -> tuple[int, str]:
    substitution_hashes = dict(substitutions or {})
    excluded_paths = set(excluded or set())
    for relative, expected_digest in substitution_hashes.items():
        require_relative_path(relative, "payload.substitutions")
        require_sha256(expected_digest, f"payload.substitutions.{relative}")
    for relative in excluded_paths:
        require_relative_path(relative, "payload.excluded")
    overlap = sorted(set(substitution_hashes) & excluded_paths)
    if overlap:
        raise ProfileError(
            "profile.artifact_mismatch",
            "Projected payload paths cannot be both substituted and excluded",
            paths=overlap,
        )
    relative_files: list[str] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ProfileError("path.symlink", "Payload contains a symlink", path=str(path))
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative not in excluded_paths:
                relative_files.append(relative)
    relative_files.sort()
    missing_substitutions = sorted(set(substitution_hashes) - set(relative_files))
    if missing_substitutions:
        raise ProfileError(
            "profile.artifact_mismatch",
            "Projected payload substitution target is absent",
            paths=missing_substitutions,
        )
    digest = hashlib.sha256()
    for relative in relative_files:
        file_hash = substitution_hashes.get(relative) or sha256_file(root / relative)
        digest.update(f"{file_hash}  {relative}\n".encode())
    return len(relative_files), digest.hexdigest()


def decode_vdf_string(value: str) -> str:
    return value.replace(r"\"", '"').replace(r"\\", "\\")


def parse_vdf(text: str) -> dict[str, Any]:
    tokens: list[str] = []
    for match in VDF_TOKEN_RE.finditer(text):
        tokens.append(decode_vdf_string(match.group(1)) if match.group(1) is not None else match.group(2))

    def parse_object(index: int, nested: bool) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while index < len(tokens):
            token = tokens[index]
            if token == "}":
                if not nested:
                    raise ProfileError("preflight.manifest", "Unexpected VDF closing brace")
                return result, index + 1
            if token == "{":
                raise ProfileError("preflight.manifest", "Unexpected VDF opening brace")
            key = token
            index += 1
            if index >= len(tokens):
                raise ProfileError("preflight.manifest", "Missing VDF value", key=key)
            token = tokens[index]
            if token == "{":
                nested_value, index = parse_object(index + 1, True)
                value: Any = nested_value
            elif token == "}":
                raise ProfileError("preflight.manifest", "Missing VDF value", key=key)
            else:
                value = token
                index += 1
            if key in result:
                raise ProfileError("preflight.manifest", "Duplicate VDF key", key=key)
            result[key] = value
        if nested:
            raise ProfileError("preflight.manifest", "Unclosed VDF object")
        return result, index

    parsed, final_index = parse_object(0, False)
    if final_index != len(tokens):
        raise ProfileError("preflight.manifest", "Trailing VDF tokens")
    return parsed


def verify_artifact(artifact: pathlib.Path) -> dict[str, Any]:
    artifact = artifact.expanduser().resolve(strict=False)
    require_directory(artifact, "artifact")
    result = subprocess.run(
        [sys.executable, str(ARTIFACT_VERIFY_TOOL), "verify", "--artifact", str(artifact)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ProfileError(
            "artifact.invalid",
            "Runtime artifact verification failed",
            path=str(artifact),
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )
    provenance_path = artifact / "provenance" / "artifact.json"
    try:
        provenance = json.loads(provenance_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError(
            "artifact.invalid",
            "Artifact provenance could not be loaded",
            path=str(provenance_path),
            detail=str(error),
        ) from error
    required_payload = [
        "payload/macos/ALVRMacOSBridge.app/Contents/Info.plist",
        "payload/macos/ALVRMacOSBridge.app/Contents/MacOS/alvr_macos_bridge",
        "payload/macos/libMoltenVK.dylib",
        "payload/unix/alvr_iosurface_bridge.so",
        "payload/windows/alvr_iosurface_bridge.dll",
        "payload/windows/d3d11.dll",
        "payload/windows/dxgi.dll",
        "payload/windows/openvr_api.dll",
        "payload/windows/openvr_api.real.dll",
    ]
    for relative in required_payload:
        require_regular_file(artifact / relative, f"artifact.{relative}")
    return provenance


def ordered_targets(profile: dict[str, Any]) -> list[dict[str, Any]]:
    entrypoint = profile["launch"]["entrypointTarget"]
    targets = profile["runtime"]["targets"]
    return sorted(targets, key=lambda target: target["id"] != entrypoint)


def preflight(
    loaded: LoadedProfile,
    artifact: pathlib.Path,
    bindings_path: pathlib.Path | None,
    mode: str,
) -> dict[str, Any]:
    profile = loaded.data
    bindings = load_bindings(bindings_path)
    install_root = pathlib.Path(expand_template(profile["launch"]["installRoot"], bindings, "launch.installRoot"))
    if not install_root.is_absolute():
        raise ProfileError("path.unsafe", "Install root must resolve to an absolute path", path=str(install_root))
    reject_symlink_components(install_root)
    require_directory(install_root, "launch.installRoot")

    artifact_path = artifact.expanduser().resolve(strict=False)
    provenance = verify_artifact(artifact_path)
    source = profile["source"]
    steam_bottle = pathlib.Path(bindings["STEAM_BOTTLE"])
    manifest_path = (
        steam_bottle
        / "drive_c"
        / "Program Files (x86)"
        / "Steam"
        / "steamapps"
        / f"appmanifest_{source['appId']}.acf"
    )
    require_regular_file(manifest_path, "steam.appManifest")
    try:
        parsed_manifest = parse_vdf(manifest_path.read_text(errors="strict"))
    except (OSError, UnicodeError) as error:
        raise ProfileError(
            "preflight.manifest",
            "Steam app manifest could not be read",
            path=str(manifest_path),
            detail=str(error),
        ) from error
    app_state = require_object(parsed_manifest.get("AppState"), "steam.AppState")
    expected_manifest_values = {
        "appid": source["appId"],
        "buildid": source["buildId"],
        "SizeOnDisk": str(source["installedSizeBytes"]),
        "StateFlags": "4",
    }
    for key, expected in expected_manifest_values.items():
        actual = app_state.get(key)
        if actual != expected:
            raise ProfileError(
                "preflight.manifest",
                "Steam app identity does not match the profile",
                key=key,
                expected=expected,
                actual=actual,
                path=str(manifest_path),
            )
    installed_depots = require_object(app_state.get("InstalledDepots"), "steam.AppState.InstalledDepots")
    for depot in source["depots"]:
        installed = require_object(installed_depots.get(depot["id"]), f"steam.depot.{depot['id']}")
        if installed.get("manifest") != depot["manifest"] or installed.get("size") != str(depot["sizeBytes"]):
            raise ProfileError(
                "preflight.manifest",
                "Installed depot does not match the profile",
                depot=depot["id"],
                expected=depot,
                actual=installed,
            )

    resolved_targets: list[dict[str, Any]] = []
    for target in ordered_targets(profile):
        executable = resolve_under(install_root, target["executable"], f"target.{target['id']}.executable")
        working_directory = resolve_under(
            install_root, target["workingDirectory"], f"target.{target['id']}.workingDirectory"
        )
        openvr_directory = resolve_under(
            install_root, target["openvrDirectory"], f"target.{target['id']}.openvrDirectory"
        )
        graphics_directory = resolve_under(
            install_root, target["graphicsDirectory"], f"target.{target['id']}.graphicsDirectory"
        )
        require_regular_file(executable, f"target.{target['id']}.executable")
        inspect_pe_x86_64(executable, f"target.{target['id']}.executable")
        require_directory(working_directory, f"target.{target['id']}.workingDirectory")
        require_directory(openvr_directory, f"target.{target['id']}.openvrDirectory")
        require_directory(graphics_directory, f"target.{target['id']}.graphicsDirectory")
        stock_openvr = openvr_directory / "openvr_api.dll"
        require_regular_file(stock_openvr, f"target.{target['id']}.stockOpenvr")
        inspect_pe_x86_64(stock_openvr, f"target.{target['id']}.stockOpenvr")
        actual_openvr_hash = sha256_file(stock_openvr)
        if actual_openvr_hash != target["stockOpenvrSha256"]:
            raise ProfileError(
                "preflight.hash",
                "Stock OpenVR hash does not match the profile",
                target=target["id"],
                expected=target["stockOpenvrSha256"],
                actual=actual_openvr_hash,
                path=str(stock_openvr),
            )
        staging_targets = [
            openvr_directory / "openvr_api.real.dll",
            graphics_directory / "d3d11.dll",
            graphics_directory / "dxgi.dll",
            graphics_directory / "alvr_iosurface_bridge.dll",
        ]
        log_directories = {graphics_directory, working_directory}
        runtime_logs = sorted(
            {
                path
                for directory in log_directories
                for pattern in ("*_d3d11.log", "*_dxgi.log")
                for path in directory.glob(pattern)
            }
        )
        unexpected = [str(path) for path in staging_targets if path.exists()]
        unexpected.extend(str(path) for path in runtime_logs)
        if unexpected:
            raise ProfileError(
                "preflight.staged",
                "Runtime staging targets already exist",
                target=target["id"],
                paths=unexpected,
            )
        resolved_targets.append(
            {
                "id": target["id"],
                "role": target["role"],
                "executable": str(executable),
                "workingDirectory": str(working_directory),
                "openvrDirectory": str(openvr_directory),
                "graphicsDirectory": str(graphics_directory),
                "stockOpenvrSha256": actual_openvr_hash,
                "processPattern": target["processPattern"],
                "runtimeLogDirectories": sorted(str(path) for path in log_directories),
                "stagingTargets": [str(path) for path in staging_targets],
            }
        )

    payload = source["payload"]
    critical_files: list[dict[str, str]] = []
    for critical_file in payload["criticalFiles"]:
        path = resolve_under(install_root, critical_file["path"], "source.payload.criticalFiles")
        require_regular_file(path, "source.payload.criticalFiles")
        actual_hash = sha256_file(path)
        if actual_hash != critical_file["sha256"]:
            raise ProfileError(
                "preflight.hash",
                "Critical payload file does not match the profile",
                path=str(path),
                expected=critical_file["sha256"],
                actual=actual_hash,
            )
        critical_files.append(
            {"path": critical_file["path"], "sha256": actual_hash}
        )
    payload_file_count, payload_tree_sha256 = payload_tree_identity(install_root)
    if payload_file_count != payload["fileCount"] or payload_tree_sha256 != payload["treeSha256"]:
        raise ProfileError(
            "preflight.payload",
            "Installed payload tree does not match the profile",
            expected={"fileCount": payload["fileCount"], "treeSha256": payload["treeSha256"]},
            actual={"fileCount": payload_file_count, "treeSha256": payload_tree_sha256},
            path=str(install_root),
        )

    entrypoint = resolved_targets[0]
    geometry = profile["geometry"]
    frame_counts = profile["validation"]["frameCounts"]
    cadence = profile["validation"]["cadence"]
    environment = {
        "ALVR_NATIVE_RUNTIME_ARTIFACT": str(artifact_path),
        "ALVR_NATIVE_PROBE_APP_NAME": profile["name"],
        "ALVR_NATIVE_PROBE_ALLOWED_SOURCE_TRANSITIONS": ",".join(
            f"{transition['width']}x{transition['height']}"
            for transition in geometry["allowedStereoTransitions"]
        ),
        "ALVR_NATIVE_PROBE_ARGUMENTS": " ".join(profile["launch"]["arguments"]),
        "ALVR_NATIVE_PROBE_CONNECT": "true" if mode == "physical" else "false",
        "ALVR_NATIVE_PROBE_CONTROLLER_PROFILE": profile["controller"]["profile"],
        "ALVR_NATIVE_PROBE_EXECUTABLE": entrypoint["executable"],
        "ALVR_NATIVE_PROBE_EXTRA_ENV": " ".join(
            f"{name}={value}" for name, value in sorted(profile["launch"]["environment"].items())
        ),
        "ALVR_NATIVE_PROBE_FRAMES": str(frame_counts[mode]),
        "ALVR_NATIVE_PROBE_GAME_DIR": entrypoint["graphicsDirectory"],
        "ALVR_NATIVE_PROBE_GAME_DIRS": ":".join(target["graphicsDirectory"] for target in resolved_targets),
        "ALVR_NATIVE_PROBE_LAUNCHER_SOURCE": str(loaded.path.relative_to(REPO_ROOT)),
        "ALVR_NATIVE_PROBE_MAX_PRODUCER_FPS": str(cadence["maximumFps"]),
        "ALVR_NATIVE_PROBE_MIN_PRODUCER_FPS": str(cadence["minimumFps"]),
        "ALVR_NATIVE_PROBE_OPENVR_DIR": entrypoint["openvrDirectory"],
        "ALVR_NATIVE_PROBE_OPENVR_DIRS": ":".join(target["openvrDirectory"] for target in resolved_targets),
        "ALVR_NATIVE_PROBE_OUTPUT_HEIGHT": str(geometry["targetStereo"]["height"]),
        "ALVR_NATIVE_PROBE_OUTPUT_ROOT": str(PROFILE_OUTPUT_ROOT / profile["id"]),
        "ALVR_NATIVE_PROBE_OUTPUT_WIDTH": str(geometry["targetStereo"]["width"]),
        "ALVR_NATIVE_PROBE_PROCESS_PATTERN": profile["launch"]["processPattern"],
        "ALVR_NATIVE_PROBE_PROFILE_ID": profile["id"],
        "ALVR_NATIVE_PROBE_PROFILE_SHA256": loaded.sha256,
        "ALVR_NATIVE_PROBE_REQUIRE_VISIBLE_CONTENT": "false" if mode == "smoke" else "true",
        "ALVR_NATIVE_PROBE_SOURCE_HEIGHT": str(geometry["maximumStereo"]["height"]),
        "ALVR_NATIVE_PROBE_SOURCE_MODE": geometry["sourceMode"],
        "ALVR_NATIVE_PROBE_SOURCE_WIDTH": str(geometry["maximumStereo"]["width"]),
        "ALVR_NATIVE_PROBE_STARTUP_TIMEOUT_SECONDS": str(profile["launch"]["startupTimeoutSeconds"]),
        "ALVR_NATIVE_PROBE_STOCK_OPENVR_HASH": entrypoint["stockOpenvrSha256"],
        "ALVR_NATIVE_PROBE_STOCK_OPENVR_HASHES": ":".join(
            target["stockOpenvrSha256"] for target in resolved_targets
        ),
        "ALVR_NATIVE_PROBE_TARGET_IDS": ":".join(target["id"] for target in resolved_targets),
        "ALVR_NATIVE_PROBE_TARGET_PROCESS_PATTERNS": ":".join(
            target["processPattern"] for target in resolved_targets
        ),
        "ALVR_NATIVE_PROBE_TARGET_WORKDIRS": ":".join(
            target["workingDirectory"] for target in resolved_targets
        ),
        "ALVR_NATIVE_PROBE_TRANSITION_TIMEOUT_SECONDS": str(profile["launch"]["transitionTimeoutSeconds"]),
        "ALVR_NATIVE_PROBE_WORKDIR": entrypoint["workingDirectory"],
    }
    environment["ALVR_NATIVE_PROBE_EXPECT_SOURCE_TRANSITIONS"] = ""

    return {
        "artifact": {
            "id": provenance["artifact"]["id"],
            "path": str(artifact_path),
            "sealId": provenance["sealId"],
            "version": provenance["artifact"]["version"],
        },
        "profile": {
            "id": profile["id"],
            "name": profile["name"],
            "path": str(loaded.path),
            "sha256": loaded.sha256,
        },
        "ready": True,
        "runner": {
            "command": ["bash", str(PROBE_RUNNER.relative_to(REPO_ROOT))],
            "environment": environment,
            "mode": mode,
        },
        "steam": {
            "appId": source["appId"],
            "appManifest": str(manifest_path),
            "buildId": source["buildId"],
            "depots": source["depots"],
            "installRoot": str(install_root),
            "manifestSha256": sha256_file(manifest_path),
            "payload": {
                "criticalFiles": critical_files,
                "fileCount": payload_file_count,
                "treeSha256": payload_tree_sha256,
            },
        },
        "targets": resolved_targets,
    }


def checked_in_profile_paths() -> list[pathlib.Path]:
    return sorted(PROFILE_ROOT.glob("*.json"))


def command_check(arguments: argparse.Namespace) -> int:
    paths = [profile_path(item) for item in arguments.profiles] if arguments.profiles else checked_in_profile_paths()
    if not SCHEMA_PATH.is_file():
        raise ProfileError("profile.schema", "Profile schema file is absent", path=str(SCHEMA_PATH))
    json.loads(SCHEMA_PATH.read_text())
    loaded = [load_profile(str(path)) for path in paths]
    ids = [profile.data["id"] for profile in loaded]
    if len(ids) != len(set(ids)):
        raise ProfileError("profile.invalid", "Checked-in profile ids must be unique", ids=ids)
    print(
        json.dumps(
            {
                "ok": True,
                "profiles": [
                    {"id": profile.data["id"], "path": str(profile.path), "sha256": profile.sha256}
                    for profile in loaded
                ],
                "schema": str(SCHEMA_PATH),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def command_preflight(arguments: argparse.Namespace) -> int:
    loaded = load_profile(arguments.profile)
    plan = preflight(loaded, arguments.artifact, arguments.bindings, arguments.mode)
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def command_probe(arguments: argparse.Namespace) -> int:
    loaded = load_profile(arguments.profile)
    plan = preflight(loaded, arguments.artifact, arguments.bindings, arguments.mode)
    output_root = PROFILE_OUTPUT_ROOT / loaded.data["id"]
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    plan_path = output_root / f"preflight-{arguments.mode}-{timestamp}.json"
    plan_path.write_bytes(canonical_json_bytes(plan))
    cleanup_result = subprocess.run([sys.executable, str(CLEANUP_TOOL)], cwd=REPO_ROOT, check=False)
    if cleanup_result.returncode != 0:
        raise ProfileError("probe.cleanup", "Pre-probe VR stack cleanup failed", exitCode=cleanup_result.returncode)
    environment = os.environ.copy()
    environment.update(plan["runner"]["environment"])
    print(
        json.dumps(
            {
                "artifactSeal": plan["artifact"]["sealId"],
                "mode": arguments.mode,
                "preflight": str(plan_path),
                "profile": loaded.data["id"],
                "profileSha256": loaded.sha256,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    result = subprocess.run(plan["runner"]["command"], cwd=REPO_ROOT, env=environment, check=False)
    if result.returncode != 0:
        raise ProfileError("probe.failed", "Profile qualification probe failed", exitCode=result.returncode)
    return 0


def command_self_test(_: argparse.Namespace) -> int:
    source_profile = load_profile("the-lab").data
    cases: list[tuple[str, Any]] = []

    unknown = copy.deepcopy(source_profile)
    unknown["unexpected"] = True
    cases.append(("unknown-key", unknown))

    unsafe_path = copy.deepcopy(source_profile)
    unsafe_path["runtime"]["targets"][0]["executable"] = "../outside.exe"
    cases.append(("unsafe-path", unsafe_path))

    duplicate_target = copy.deepcopy(source_profile)
    duplicate_target["runtime"]["targets"][1]["graphicsDirectory"] = duplicate_target["runtime"]["targets"][0][
        "graphicsDirectory"
    ]
    cases.append(("duplicate-target", duplicate_target))

    weak_cadence = copy.deepcopy(source_profile)
    weak_cadence["validation"]["cadence"]["minimumFps"] = 80.0
    cases.append(("weak-cadence", weak_cadence))

    adaptive_overflow = copy.deepcopy(source_profile)
    adaptive_overflow["geometry"]["adaptiveViewport"]["maximumScale"] = 2.0
    cases.append(("adaptive-overflow", adaptive_overflow))

    weak_controller = copy.deepcopy(source_profile)
    weak_controller["controller"]["requiredInputs"].remove("trigger")
    cases.append(("weak-controller", weak_controller))

    results: list[dict[str, Any]] = []
    for name, profile in cases:
        try:
            validate_profile(profile)
        except ProfileError as error:
            results.append({"case": name, "code": error.code, "passed": True})
        else:
            raise ProfileError("self-test.failed", "Invalid profile fixture was accepted", case=name)

    vdf_fixture = '"AppState" { "appid" "450390" "InstalledDepots" { "450391" { "manifest" "7" } } }'
    parsed = parse_vdf(vdf_fixture)
    if parsed["AppState"]["InstalledDepots"]["450391"]["manifest"] != "7":
        raise ProfileError("self-test.failed", "VDF parser fixture failed")
    results.append({"case": "vdf-parser", "passed": True})

    fixture_root = os.environ.get("RUNTIME_FIXTURE_ROOT")
    if fixture_root is None and pathlib.Path("/private/tmp").is_dir():
        fixture_root = "/private/tmp"
    with tempfile.TemporaryDirectory(dir=fixture_root) as temporary_directory:
        path = pathlib.Path(temporary_directory) / "profile.json"
        path.write_bytes(canonical_json_bytes(source_profile))
        loaded = load_profile(str(path))
        if loaded.data["id"] != "the-lab":
            raise ProfileError("self-test.failed", "Canonical profile fixture failed")
    results.append({"case": "canonical-profile", "passed": True})

    print(json.dumps({"ok": True, "results": results}, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Validate checked-in profiles")
    check_parser.add_argument("profiles", nargs="*", help="Profile ids or JSON paths")
    check_parser.set_defaults(handler=command_check)

    self_test_parser = subparsers.add_parser("self-test", help="Run bounded profile fixtures")
    self_test_parser.set_defaults(handler=command_self_test)

    for name, handler in (("preflight", command_preflight), ("probe", command_probe)):
        command_parser = subparsers.add_parser(name, help=f"{name.title()} a profile qualification")
        command_parser.add_argument("--profile", required=True, help="Profile id or JSON path")
        command_parser.add_argument("--artifact", required=True, type=pathlib.Path, help="Sealed runtime artifact")
        command_parser.add_argument("--bindings", type=pathlib.Path, help="Optional profile binding JSON")
        command_parser.add_argument(
            "--mode",
            choices=("smoke", "disconnected", "physical"),
            default="smoke",
            help="Qualification frame-count and connection mode",
        )
        command_parser.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return arguments.handler(arguments)
    except ProfileError as error:
        payload = {"code": error.code, "message": error.message, **error.context}
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 1
    except Exception as error:
        print(
            json.dumps({"code": "internal.error", "message": str(error), "type": type(error).__name__}, sort_keys=True),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
