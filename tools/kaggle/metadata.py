"""Generate Kaggle metadata files without embedding credentials."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .models import KernelConfig


def kernel_metadata(config: KernelConfig) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "id": config.id,
        "title": config.title,
        "code_file": config.code_file,
        "language": config.language,
        "kernel_type": config.kernel_type,
        "is_private": config.is_private,
        "enable_gpu": config.enable_gpu,
        "enable_internet": config.enable_internet,
        "dataset_sources": list(config.dataset_sources),
        "competition_sources": list(config.competition_sources),
        "kernel_sources": list(config.kernel_sources),
        "model_sources": list(config.model_sources),
    }
    if config.machine_shape:
        metadata["machine_shape"] = config.machine_shape
    return metadata


def write_kernel_metadata(config: KernelConfig, directory: Path) -> Path:
    code_path = directory / config.code_file
    if not code_path.is_file():
        raise ConfigurationError(f"code_file does not exist in staged job: {config.code_file}")
    path = directory / "kernel-metadata.json"
    path.write_text(json.dumps(kernel_metadata(config), indent=2) + "\n", encoding="utf-8")
    return path
