"""YAML configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigurationError
from .models import KernelConfig


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML configuration: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("configuration root must be a mapping")
    return value


def load_kernel_config(path: Path) -> KernelConfig:
    value = load_yaml(path)
    section = value.get("kernel", value)
    if not isinstance(section, dict):
        raise ConfigurationError("'kernel' configuration must be a mapping")
    try:
        return KernelConfig.from_dict(section)
    except TypeError as exc:
        raise ConfigurationError(f"invalid kernel configuration fields: {exc}") from exc

