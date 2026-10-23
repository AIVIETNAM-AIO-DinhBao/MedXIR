"""Small injectable subprocess boundary used by executor implementations."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from tools.kaggle.models import CommandResult


def run_command(
    args: Sequence[str], *, cwd: Path | None = None, timeout_seconds: float = 600
) -> CommandResult:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
        env=environment,
    )
    return CommandResult(tuple(str(arg) for arg in args), completed.returncode, completed.stdout, completed.stderr)
