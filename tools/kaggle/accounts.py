"""Local, per-command Kaggle account profiles. Tokens never enter run records."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import ConfigurationError, KaggleCommandError

_ACCOUNT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_AUTH_VARIABLES = ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY", "KAGGLE_CONFIG_DIR")


def accounts_dir() -> Path:
    configured = os.environ.get("RESEARCH_KAGGLE_ACCOUNTS_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "agent-auto-research" / "kaggle"


def account_names() -> list[str]:
    directory = accounts_dir()
    if not directory.is_dir():
        return []
    return sorted(
        path.name.removesuffix(".token")
        for path in directory.glob("*.token")
        if path.is_file() and _ACCOUNT_NAME.fullmatch(path.name.removesuffix(".token"))
    )


def add_account(account: str, source: Path) -> Path:
    """Copy an API token into the local profile store with owner-only permissions."""
    if not _ACCOUNT_NAME.fullmatch(account):
        raise ConfigurationError(f"invalid Kaggle account name: {account!r}")
    try:
        token = source.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(f"cannot read API token file: {source}") from exc
    if not token or any(character.isspace() for character in token):
        raise ConfigurationError(f"API token file is empty or malformed: {source}")
    directory = accounts_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise ConfigurationError(f"cannot create Kaggle accounts directory: {directory}") from exc
    path = directory / f"{account}.token"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ConfigurationError(f"Kaggle account {account!r} is already connected") from exc
    except OSError as exc:
        raise ConfigurationError(f"cannot create Kaggle account profile: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(token + "\n")
    try:
        with account_session(account):
            verify_account(account)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _token_for(account: str) -> str:
    if not _ACCOUNT_NAME.fullmatch(account):
        raise ConfigurationError(f"invalid Kaggle account name: {account!r}")
    path = accounts_dir() / f"{account}.token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(f"cannot read Kaggle account token: {path}") from exc
    if not token or any(character.isspace() for character in token):
        raise ConfigurationError(f"Kaggle account token is empty or malformed: {path}")
    return token


@contextmanager
def account_session(account: str) -> Iterator[None]:
    """Scope the selected credential to a single CLI process lifecycle."""
    token = _token_for(account)
    previous = {name: os.environ.get(name) for name in _AUTH_VARIABLES}
    try:
        os.environ.pop("KAGGLE_USERNAME", None)
        os.environ.pop("KAGGLE_KEY", None)
        os.environ["KAGGLE_API_TOKEN"] = token
        os.environ["KAGGLE_CONFIG_DIR"] = str(accounts_dir() / ".empty-config")
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def verify_account(account: str) -> None:
    """Fail before submission if the token resolves to another Kaggle user."""
    try:
        from kaggle import KaggleApi

        api = KaggleApi()
        api.authenticate()
        observed = api.config_values.get("username")
    except SystemExit as exc:
        raise KaggleCommandError(f"Kaggle authentication failed for {account}") from exc
    except Exception as exc:  # Kaggle SDK errors vary by version.
        raise KaggleCommandError(f"could not verify Kaggle account {account}: {exc}") from exc
    if not isinstance(observed, str) or observed.casefold() != account.casefold():
        raise ConfigurationError(
            f"Kaggle profile {account!r} authenticated as {observed!r}; refusing to continue"
        )


def gpu_quota_hours(output: str) -> float:
    """Parse the official CLI's JSON quota table, rejecting unknown formats."""
    try:
        rows = json.loads(output)
    except json.JSONDecodeError as exc:
        raise KaggleCommandError("Kaggle quota response was not JSON") from exc
    if isinstance(rows, dict):
        rows = rows.get("rows")
    if not isinstance(rows, list):
        raise KaggleCommandError("Kaggle quota response has an unknown format")
    for row in rows:
        if not isinstance(row, dict):
            continue
        values = {str(key).casefold(): value for key, value in row.items()}
        if str(values.get("resource", "")).casefold() != "gpu":
            continue
        remaining = str(values.get("remaining", ""))
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)h\s*", remaining)
        if not match:
            raise KaggleCommandError("Kaggle GPU quota response has an unknown remaining value")
        return float(match.group(1))
    raise KaggleCommandError("Kaggle quota response did not include GPU hours")


def choose_account(
    executor: object, *, required_gpu_hours: float = 0, exclude: set[str] | None = None
) -> str:
    names = [name for name in account_names() if name not in (exclude or set())]
    if not names:
        raise ConfigurationError(f"no Kaggle account tokens found in {accounts_dir()}")
    if required_gpu_hours <= 0:
        failures = []
        for name in names:
            try:
                with account_session(name):
                    verify_account(name)
                return name
            except (ConfigurationError, KaggleCommandError) as exc:
                failures.append(f"{name}: {exc}")
        raise ConfigurationError("no usable Kaggle account: " + "; ".join(failures))
    candidates: list[tuple[float, str]] = []
    failures: list[str] = []
    for name in names:
        try:
            with account_session(name):
                verify_account(name)
                remaining = gpu_quota_hours(executor.quota().stdout)
            if remaining >= required_gpu_hours:
                candidates.append((remaining, name))
            else:
                failures.append(f"{name}: {remaining:.2f}h remaining")
        except (ConfigurationError, KaggleCommandError) as exc:
            failures.append(f"{name}: {exc}")
    if not candidates:
        detail = "; ".join(failures)
        raise ConfigurationError(
            f"no Kaggle account has verified GPU quota of {required_gpu_hours:.2f}h: {detail}"
        )
    return max(candidates)[1]
