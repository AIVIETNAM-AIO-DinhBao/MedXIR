"""Configuration and result models shared by Kaggle commands."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ConfigurationError


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    MISSING_OUTPUT = "MISSING_OUTPUT"
    SUBMIT_ERROR = "SUBMIT_ERROR"

    @property
    def terminal(self) -> bool:
        return self in {
            RunStatus.SUCCESS,
            RunStatus.ERROR,
            RunStatus.TIMEOUT,
            RunStatus.MISSING_OUTPUT,
            RunStatus.SUBMIT_ERROR,
        }


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class KernelSubmission:
    command: CommandResult
    version: int
    url: str | None = None


@dataclass(frozen=True)
class KernelStatusResult:
    status: RunStatus
    raw_status: str | None
    failure_message: str | None
    raw_output: str


@dataclass(frozen=True)
class KernelFetchResult:
    command: CommandResult
    log_files: tuple[Path, ...]
    fetched_files: tuple[Path, ...] = ()
    requested_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class KernelSourceExpectation:
    kernel: str
    version: int
    manifest: str
    manifest_sha256: str

    def __post_init__(self) -> None:
        if self.kernel.count("/") != 1 or any(not part for part in self.kernel.split("/")):
            raise ConfigurationError("kernel source expectation must use 'owner/slug'")
        if self.version <= 0:
            raise ConfigurationError("kernel source expectation version must be positive")
        manifest_path = PurePosixPath(self.manifest.replace("\\", "/"))
        if not self.manifest or manifest_path.is_absolute() or ".." in manifest_path.parts:
            raise ConfigurationError("kernel source expectation manifest must be a safe relative path")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.manifest_sha256):
            raise ConfigurationError("kernel source expectation manifest_sha256 must be a SHA256 hex digest")


@dataclass(frozen=True)
class KernelConfig:
    id: str
    title: str
    code_file: str
    kernel_type: str = "script"
    language: str = "python"
    is_private: bool = True
    enable_gpu: bool = False
    enable_internet: bool = False
    machine_shape: str | None = None
    expected_hardware_contains: str | None = None
    dataset_sources: tuple[str, ...] = ()
    competition_sources: tuple[str, ...] = ()
    kernel_sources: tuple[str, ...] = ()
    model_sources: tuple[str, ...] = ()
    timeout_seconds: int = 3600
    wait_timeout_seconds: int | None = None
    poll_interval_seconds: int = 60
    immutable_kernel: bool = False
    kernel_source_expectations: tuple[KernelSourceExpectation, ...] = ()
    fetch_files: tuple[str, ...] = ()
    required_outputs: tuple[str, ...] = ()
    metrics_file: str | None = None
    json_checks: tuple[dict[str, Any], ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.id.count("/") != 1 or any(not part for part in self.id.split("/")):
            raise ConfigurationError("kernel id must use the form 'owner/slug'")
        if not self.title.strip():
            raise ConfigurationError("kernel title must not be empty")
        if self.kernel_type not in {"script", "notebook"}:
            raise ConfigurationError("kernel_type must be 'script' or 'notebook'")
        if self.language not in {"python", "r", "rmarkdown"}:
            raise ConfigurationError("language must be python, r, or rmarkdown")
        if not self.code_file or Path(self.code_file).is_absolute() or ".." in Path(self.code_file).parts:
            raise ConfigurationError("code_file must be a safe path relative to source_dir")
        normalized_code_file = PurePosixPath(self.code_file.replace("\\", "/"))
        if (
            len(normalized_code_file.parts) == 1
            and normalized_code_file.name.casefold() == "kernel-metadata.json"
        ):
            raise ConfigurationError("code_file must not collide with kernel-metadata.json")
        if self.timeout_seconds <= 0 or self.poll_interval_seconds <= 0:
            raise ConfigurationError("timeouts and polling intervals must be positive")
        if self.wait_timeout_seconds is not None and self.wait_timeout_seconds <= 0:
            raise ConfigurationError("wait_timeout_seconds must be positive")
        if (
            self.expected_hardware_contains is not None
            and not self.expected_hardware_contains.strip()
        ):
            raise ConfigurationError("expected_hardware_contains must not be empty")
        if self.json_checks and not self.metrics_file:
            raise ConfigurationError("json_checks require metrics_file")
        expected_kernels = {item.kernel for item in self.kernel_source_expectations}
        if not expected_kernels.issubset(self.kernel_sources):
            raise ConfigurationError("kernel_source_expectations must refer to configured kernel_sources")
        for label, raw_path in (
            ("metrics_file", self.metrics_file),
            *(("required_output", value) for value in self.required_outputs),
            *(("fetch_file", value) for value in self.fetch_files),
        ):
            if raw_path is None and label == "metrics_file":
                continue
            if (
                not isinstance(raw_path, str)
                or not raw_path
                or Path(raw_path).is_absolute()
                or ".." in PurePosixPath(raw_path.replace("\\", "/")).parts
            ):
                raise ConfigurationError(f"{label} must be a safe relative path: {raw_path}")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> KernelConfig:
        data = dict(value)
        for key in (
            "dataset_sources",
            "competition_sources",
            "kernel_sources",
            "model_sources",
            "kernel_source_expectations",
            "fetch_files",
            "required_outputs",
            "json_checks",
        ):
            data[key] = tuple(data.get(key, ()))
        data["kernel_source_expectations"] = tuple(
            item
            if isinstance(item, KernelSourceExpectation)
            else KernelSourceExpectation(**item)
            for item in data["kernel_source_expectations"]
        )
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunMetadata:
    run_id: str
    status: RunStatus
    executor: str
    kernel: str
    git_commit: str | None
    requested_hardware: str | None
    internet: bool
    started_at: str
    finished_at: str | None = None
    git_dirty: bool | None = None
    submitted_code_sha256: str | None = None
    source_dir: str | None = None
    source_path: str | None = None
    source_sha256: str | None = None
    source_git_tracked: bool | None = None
    config: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    kernel_version: int | None = None
    remote_status: str | None = None
    remote_failure_message: str | None = None
    observed_hardware: str | list[str] | None = None
    expected_hardware_contains: str | None = None
    remote_artifacts: dict[str, Any] | None = None
    account: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data
