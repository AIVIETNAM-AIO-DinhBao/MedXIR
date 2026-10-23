"""Persistent, machine-readable run records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from tools.kaggle.errors import ConfigurationError
from tools.kaggle.models import RunMetadata


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def next_id(self, kind: str) -> str:
        prefix = "smoke" if kind == "smoke" else "exp"
        directory = self.root / ("smoke" if kind == "smoke" else "experiments")
        existing = []
        if directory.exists():
            pattern = re.compile(rf"^{prefix}_(\d+)$")
            existing = [int(match.group(1)) for item in directory.iterdir() if (match := pattern.match(item.name))]
        return f"{prefix}_{max(existing, default=0) + 1:04d}"

    def create(self, run_id: str, kind: str, metadata: RunMetadata, config: dict[str, Any]) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id):
            raise ConfigurationError("run_id may contain only letters, numbers, '.', '_' and '-'")
        category = "smoke" if kind == "smoke" else "experiments"
        run_dir = self.root / category / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ConfigurationError(f"run already exists and will not be overwritten: {run_dir}") from exc
        (run_dir / "artifacts").mkdir()
        (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        (run_dir / "stdout.log").write_text("", encoding="utf-8")
        (run_dir / "stderr.log").write_text("", encoding="utf-8")
        self.write_metadata(run_dir, metadata)
        return run_dir

    @staticmethod
    def write_metadata(run_dir: Path, metadata: RunMetadata) -> None:
        path = run_dir / "metadata.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(metadata.as_dict(), indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def append_log(run_dir: Path, name: str, value: str) -> None:
        if not value:
            return
        with (run_dir / name).open("a", encoding="utf-8") as stream:
            stream.write(value)
            if not value.endswith("\n"):
                stream.write("\n")

