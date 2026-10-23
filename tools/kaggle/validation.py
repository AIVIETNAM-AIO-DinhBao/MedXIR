"""Generic output-contract validation for smoke and full jobs."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import MissingOutputError, OutputValidationError
from .models import KernelConfig


def sha256_file(path: Path) -> str:
    """Compute a file identity without interpreting its framework-specific contents."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise OutputValidationError(f"could not read artifact for SHA256: {path}") from exc
    return digest.hexdigest()


def verify_manifest_sha256(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Verify a mounted manifest before trusting its artifact declarations."""
    actual_sha256 = sha256_file(path)
    if actual_sha256.casefold() != expected_sha256.casefold():
        raise OutputValidationError(
            f"artifact manifest SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    return load_artifact_manifest(path)


def load_artifact_manifest(path: Path) -> dict[str, Any]:
    """Parse the generic, lightweight remote-artifact manifest convention."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutputValidationError(f"artifact manifest is not valid JSON: {path.name}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise OutputValidationError("artifact manifest must use schema_version 1")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise OutputValidationError("artifact manifest artifacts must be a list")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise OutputValidationError("artifact manifest entries must be objects")
        for field in ("name", "kind", "path", "sha256"):
            if not isinstance(artifact.get(field), str) or not artifact[field].strip():
                raise OutputValidationError(f"artifact manifest entry requires non-empty {field!r}")
        artifact_path = PurePosixPath(artifact["path"].replace("\\", "/"))
        if artifact_path.is_absolute() or ".." in artifact_path.parts or str(artifact_path) in {"", "."}:
            raise OutputValidationError("artifact manifest entry path must be a safe relative path")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", artifact["sha256"]):
            raise OutputValidationError("artifact manifest entry sha256 must be a SHA256 hex digest")
    return manifest


def observed_hardware_from_result(result: Any) -> str | list[str] | None:
    """Extract hardware only from a validated remote result object."""
    if not isinstance(result, dict):
        return None
    explicit = result.get("observed_hardware")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    if isinstance(explicit, list) and explicit and all(
        isinstance(item, str) and item.strip() for item in explicit
    ):
        return explicit
    gpus = result.get("gpus")
    if isinstance(gpus, list) and gpus and all(
        isinstance(item, str) and item.strip() for item in gpus
    ):
        return gpus
    gpu = result.get("gpu")
    if isinstance(gpu, str) and gpu.strip():
        return gpu
    return None


def validate_expected_hardware(
    expected: str | None, observed: str | list[str] | None
) -> None:
    """Require a case-insensitive substring match when hardware is expected."""
    if expected is None:
        return
    if observed is None:
        raise OutputValidationError(
            f"Expected hardware containing {expected!r}, "
            "but the remote result did not report observed hardware."
        )
    observed_values = [observed] if isinstance(observed, str) else observed
    if not any(expected.casefold() in value.casefold() for value in observed_values):
        reported = observed if isinstance(observed, str) else ", ".join(observed)
        raise OutputValidationError(
            f"Expected hardware containing {expected!r}, "
            f"but remote runtime reported {reported!r}."
        )


def _resolve_path(value: Any, dotted_path: str) -> Any:
    current = value
    for component in dotted_path.split("."):
        if isinstance(current, dict) and component in current:
            current = current[component]
        elif isinstance(current, list) and component.isdigit() and int(component) < len(current):
            current = current[int(component)]
        else:
            raise OutputValidationError(f"JSON check path does not exist: {dotted_path}")
    return current


def _validate_json_checks(metrics: Any, checks: tuple[dict[str, Any], ...]) -> None:
    operators = {
        "eq": lambda actual, expected: actual == expected,
        "ne": lambda actual, expected: actual != expected,
        "gt": lambda actual, expected: actual > expected,
        "gte": lambda actual, expected: actual >= expected,
        "lt": lambda actual, expected: actual < expected,
        "lte": lambda actual, expected: actual <= expected,
    }
    for check in checks:
        path, operator = check.get("path"), check.get("op", "eq")
        if not isinstance(path, str) or "value" not in check or operator not in operators:
            raise OutputValidationError(f"invalid JSON check: {check!r}")
        actual, expected = _resolve_path(metrics, path), check["value"]
        try:
            passed = operators[operator](actual, expected)
        except TypeError as exc:
            raise OutputValidationError(
                f"JSON check types are incompatible at {path}: {actual!r} {operator} {expected!r}"
            ) from exc
        if not passed:
            raise OutputValidationError(
                f"JSON check failed at {path}: {actual!r} {operator} {expected!r}"
            )


def _assert_finite(value: Any, location: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise OutputValidationError(f"invalid numeric value at {location}: {value}")
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, f"{location}[{index}]")


def validate_outputs(artifacts_dir: Path, config: KernelConfig, run_dir: Path) -> dict[str, Any] | None:
    for relative in config.required_outputs:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise OutputValidationError(f"unsafe required output path: {relative}")
        if not (artifacts_dir / path).exists():
            raise MissingOutputError(f"required output is missing: {relative}")

    if not config.metrics_file:
        return None
    metrics_path = Path(config.metrics_file)
    if metrics_path.is_absolute() or ".." in metrics_path.parts:
        raise OutputValidationError(f"unsafe metrics path: {config.metrics_file}")
    source = artifacts_dir / metrics_path
    if not source.is_file():
        raise MissingOutputError(f"metrics file is missing: {config.metrics_file}")
    try:
        metrics = json.loads(source.read_text(encoding="utf-8"), parse_constant=lambda value: float(value))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise OutputValidationError(f"metrics file is not valid JSON: {config.metrics_file}") from exc
    _assert_finite(metrics)
    _validate_json_checks(metrics, config.json_checks)
    shutil.copy2(source, run_dir / "metrics.json")
    return metrics
