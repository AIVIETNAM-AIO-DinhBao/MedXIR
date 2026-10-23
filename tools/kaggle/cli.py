"""Non-interactive command-line interface for Kaggle execution."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any

from .accounts import (
    account_names,
    account_session,
    accounts_dir,
    add_account,
    choose_account,
    gpu_quota_hours,
    verify_account,
)
from .config import load_kernel_config
from .errors import ConfigurationError, KaggleHarnessError
from .kernel import (
    KaggleKernelExecutor,
    execute_run,
    kernel_lock,
    preflight_kernel_submission,
    stage_kernel,
    validate_title_slug,
)
from .models import RunStatus


def _emit(**values: Any) -> None:
    print(json.dumps(values, indent=2, default=str))


def _read_run_metadata(run_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((Path(run_dir) / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_run_error(run_dir: Path) -> str | None:
    error = _read_run_metadata(run_dir).get("error")
    return error if isinstance(error, str) else None


def _quota_submission_failure(run_dir: Path) -> bool:
    metadata = _read_run_metadata(run_dir)
    error = str(metadata.get("error") or "").casefold()
    return (
        metadata.get("status") == RunStatus.SUBMIT_ERROR.value
        and metadata.get("kernel_version") is None
        and "quota" in error
        and any(word in error for word in ("exhaust", "exceed", "limit", "insufficient"))
    )


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kaggle-bin", default="kaggle", help="Kaggle CLI executable")
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=600,
        help="Maximum seconds allowed for each local Kaggle CLI process",
    )
    parser.add_argument(
        "--account",
        help="Connected Kaggle username, or 'auto' for quota-aware selection",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.kaggle", description=__doc__)
    commands = parser.add_subparsers(dest="resource", required=True)

    doctor = commands.add_parser("doctor", help="Check that the Kaggle CLI is available")
    doctor.add_argument(
        "--check-auth",
        action="store_true",
        help="Also perform a harmless authenticated Kaggle API request",
    )
    _add_runtime_options(doctor)

    accounts = commands.add_parser("accounts", help="Inspect connected account profiles")
    account_commands = accounts.add_subparsers(dest="action", required=True)
    account_commands.add_parser("list", help="List local profile names without reading tokens")
    add = account_commands.add_parser("add", help="Connect a Kaggle API token")
    add.add_argument("username")
    add.add_argument("--token-file", type=Path, required=True)
    quota = account_commands.add_parser("quota", help="Show GPU quota for connected profiles")
    _add_runtime_options(quota)

    kernel = commands.add_parser("kernel", help="Manage generic Kaggle kernel jobs")
    kernel_commands = kernel.add_subparsers(dest="action", required=True)

    push = kernel_commands.add_parser(
        "push", aliases=["create", "update"], help="Stage and push a kernel version"
    )
    push.add_argument("--config", type=Path, required=True)
    push.add_argument("--source", type=Path, required=True)
    push.add_argument("--work-root", type=Path, default=Path(".work/kaggle"))
    _add_runtime_options(push)

    run = kernel_commands.add_parser("run", help="Push, wait, fetch, validate, and record a run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--source", type=Path, required=True)
    run.add_argument("--kind", choices=("smoke", "experiment"), default="experiment")
    run.add_argument("--run-id")
    run.add_argument("--work-root", type=Path, default=Path(".work/kaggle"))
    run.add_argument("--runs-root", type=Path, default=Path("runs"))
    _add_runtime_options(run)

    status = kernel_commands.add_parser("status", help="Get normalized latest-run status")
    status.add_argument("kernel")
    _add_runtime_options(status)

    wait = kernel_commands.add_parser(
        "wait", help="Block and poll internally until the latest run is terminal"
    )
    wait.add_argument("kernel")
    wait.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Maximum seconds to remain attached; does not cancel the remote kernel",
    )
    wait.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="Seconds between internal status queries (default: 60)",
    )
    _add_runtime_options(wait)

    fetch = kernel_commands.add_parser("fetch", help="Fetch latest output and logs")
    fetch.add_argument("kernel")
    fetch.add_argument("--destination", type=Path, required=True)
    fetch_selection = fetch.add_mutually_exclusive_group()
    fetch_selection.add_argument(
        "--config", type=Path, help="Use fetch_files from a kernel configuration"
    )
    fetch_selection.add_argument(
        "--file", action="append", dest="files",
        help="Fetch one exact remote output path; repeatable"
    )
    _add_runtime_options(fetch)

    return parser


def _executor(args: argparse.Namespace) -> KaggleKernelExecutor:
    return KaggleKernelExecutor(args.kaggle_bin, command_timeout_seconds=args.command_timeout)


def _account_for_kernel(requested: str | None, kernel: str) -> str | None:
    if requested is None:
        return None
    owner = kernel.split("/", 1)[0]
    selected = owner if requested == "auto" else requested
    if selected.casefold() != owner.casefold():
        raise ConfigurationError(
            f"kernel {kernel!r} belongs to {owner!r}, not selected account {selected!r}"
        )
    return selected


def _account_for_config(
    args: argparse.Namespace,
    executor: KaggleKernelExecutor,
    config: Any,
    *,
    exclude: set[str] | None = None,
) -> str | None:
    if args.account != "auto":
        return args.account
    required = config.timeout_seconds / 3600 if config.enable_gpu else 0
    return choose_account(executor, required_gpu_hours=required, exclude=exclude)


def _run_config_for_account(config: Any, account: str | None) -> Any:
    if account is None:
        return config
    slug = config.id.split("/", 1)[1]
    return replace(config, id=f"{account}/{slug}")


def _run_kernel(args: argparse.Namespace, executor: KaggleKernelExecutor) -> int:
    base_config = load_kernel_config(args.config)
    attempted: list[dict[str, Any]] = []
    excluded: set[str] = set()
    while True:
        try:
            account = _account_for_config(args, executor, base_config, exclude=excluded)
        except ConfigurationError as exc:
            if not attempted:
                raise
            last = attempted[-1]
            _emit(
                status=RunStatus.SUBMIT_ERROR.value,
                kernel=last["kernel"],
                account=last["account"],
                run_dir=last["run_dir"],
                error=str(exc),
                attempts=attempted,
            )
            return 1
        config = _run_config_for_account(base_config, account)
        context = account_session(account) if account else nullcontext()
        with context:
            if account:
                verify_account(account)
            run_id = (
                f"{args.run_id}-{account}"
                if args.account == "auto" and args.run_id and account
                else args.run_id
            )
            status, run_dir = execute_run(
                executor,
                config,
                args.source,
                work_root=args.work_root,
                runs_root=args.runs_root,
                kind=args.kind,
                run_id=run_id,
                **({"account": account} if account else {}),
            )
        attempted.append({"account": account, "kernel": config.id, "run_dir": str(run_dir)})
        if args.account != "auto" or not _quota_submission_failure(run_dir):
            _emit(
                status=status.value,
                kernel=config.id,
                account=account,
                run_dir=str(run_dir),
                error=_read_run_error(run_dir) if status != RunStatus.SUCCESS else None,
                attempts=attempted if len(attempted) > 1 else None,
            )
            return 0 if status == RunStatus.SUCCESS else 1
        excluded.add(account)
        if len(excluded) >= len(account_names()):
            _emit(
                status=status.value,
                kernel=config.id,
                account=account,
                run_dir=str(run_dir),
                error=_read_run_error(run_dir),
                attempts=attempted,
            )
            return 1


def _push_kernel(args: argparse.Namespace, executor: KaggleKernelExecutor) -> int:
    base_config = load_kernel_config(args.config)
    account = _account_for_config(args, executor, base_config)
    config = _run_config_for_account(base_config, account)
    context = account_session(account) if account else nullcontext()
    with context:
        if account:
            verify_account(account)
        validate_title_slug(config)
        run_id = f"push-{config.id.replace('/', '-')}-{uuid.uuid4().hex[:8]}"
        build = args.work_root / "builds" / run_id
        with kernel_lock(config.id, args.work_root):
            preflight_kernel_submission(executor, config)
            stage_kernel(args.source, build, config)
            result = executor.submit(build, config.timeout_seconds)
    _emit(
        status="QUEUED",
        kernel=config.id,
        account=account,
        kernel_version=result.version,
        url=result.url,
        output=result.command.stdout.strip(),
        staging=str(build),
    )
    return 0


def _read_kernel(args: argparse.Namespace, executor: KaggleKernelExecutor) -> int:
    account = _account_for_kernel(args.account, args.kernel)
    context = account_session(account) if account else nullcontext()
    with context:
        if account:
            verify_account(account)
        if args.action == "status":
            status_value = executor.status(args.kernel)
            _emit(
                kernel=args.kernel,
                account=account,
                status=status_value.status.value,
                raw_status=status_value.raw_status,
                failure_message=status_value.failure_message,
            )
            return 0 if status_value.status not in {
                RunStatus.ERROR, RunStatus.TIMEOUT, RunStatus.MISSING_OUTPUT, RunStatus.SUBMIT_ERROR
            } else 1
        if args.action == "wait":
            status_value = executor.wait(
                args.kernel, timeout_seconds=args.timeout, poll_interval_seconds=args.poll_interval
            )
            _emit(
                kernel=args.kernel,
                account=account,
                status=status_value.status.value,
                raw_status=status_value.raw_status,
                failure_message=status_value.failure_message,
            )
            return 0 if status_value.status == RunStatus.SUCCESS else 1
        fetch_files = ()
        if args.config:
            fetch_files = load_kernel_config(args.config).fetch_files
        elif args.files:
            fetch_files = tuple(args.files)
        fetched = executor.fetch(args.kernel, args.destination, fetch_files=fetch_files)
        missing_files = [
            path for path in fetch_files
            if not (args.destination / Path(path.replace("\\", "/"))).is_file()
        ]
        _emit(
            kernel=args.kernel,
            account=account,
            output_returncode=fetched.command.returncode,
            log_files=[str(path) for path in fetched.log_files],
            fetched_files=[str(path) for path in fetched.fetched_files],
            missing_files=missing_files,
        )
        valid_fetch = fetched.command.returncode == 0 and fetched.log_files and not missing_files
        return 0 if valid_fetch else 1


def _accounts(args: argparse.Namespace) -> int:
    if args.action == "list":
        _emit(directory=str(accounts_dir()), accounts=account_names())
        return 0
    if args.action == "add":
        path = add_account(args.username, args.token_file)
        _emit(account=args.username, profile=str(path), connected=True)
        return 0
    if args.account:
        raise ConfigurationError("accounts quota inspects every profile; omit --account")
    executor = _executor(args)
    rows = []
    for name in account_names():
        try:
            with account_session(name):
                verify_account(name)
                remaining = gpu_quota_hours(executor.quota().stdout)
            rows.append({"account": name, "gpu_remaining_hours": remaining})
        except KaggleHarnessError as exc:
            rows.append({"account": name, "error": str(exc)})
    _emit(accounts=rows)
    return 0 if rows and all("error" not in row for row in rows) else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.resource == "accounts":
            return _accounts(args)
        executor = _executor(args)
        if args.resource == "doctor":
            if args.account == "auto":
                raise ConfigurationError("doctor requires one account name")
            context = account_session(args.account) if args.account else nullcontext()
            with context:
                if args.account:
                    verify_account(args.account)
                result = executor.doctor(check_auth=args.check_auth)
            _emit(
                ok=True,
                version=result.stdout.strip(),
                account=args.account,
                auth_checked=args.check_auth,
            )
            return 0
        if args.action in {"push", "create", "update"}:
            return _push_kernel(args, executor)
        if args.action == "run":
            return _run_kernel(args, executor)
        return _read_kernel(args, executor)
    except KaggleHarnessError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
