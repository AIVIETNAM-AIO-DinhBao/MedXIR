"""Kaggle kernel batch executor and structured run workflow."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from tools.common.process import run_command
from tools.common.run_store import RunStore

from .errors import (
    ConfigurationError,
    KaggleCommandError,
    MissingOutputError,
    OutputValidationError,
)
from .metadata import write_kernel_metadata
from .models import (
    CommandResult,
    KernelConfig,
    KernelFetchResult,
    KernelStatusResult,
    KernelSubmission,
    RunMetadata,
    RunStatus,
)
from .validation import (
    load_artifact_manifest,
    observed_hardware_from_result,
    validate_expected_hardware,
    validate_outputs,
)

Runner = Callable[..., CommandResult]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_STATUS_LINE = re.compile(r'^.+? has status "(?P<status>[^"]+)"\s*$', re.MULTILINE)
_WORKER_STATUS = re.compile(r"^KernelWorkerStatus\.(?P<status>[A-Z_]+)$", re.IGNORECASE)
_FAILURE_LINE = re.compile(r'^Failure message: "(?P<message>.*)"\s*$', re.MULTILINE)
_PUSH_SUCCESS = re.compile(r"Kernel version\s+(?P<version>\d+)\s+successfully pushed\b", re.IGNORECASE)
_PUSH_URL = re.compile(r"progress at\s+(?P<url>https?://\S+)", re.IGNORECASE)
_PUSH_ERROR = re.compile(r"^Kernel push error:\s*(?P<message>.+)$", re.IGNORECASE | re.MULTILINE)
_PUSH_INVALID_INPUT = re.compile(
    r"The following are not valid (?:dataset|competition|kernel|model) sources and could not be added",
    re.IGNORECASE,
)
_WAIT_TIMEOUT_MESSAGE = (
    "Harness wait timed out; the remote kernel may still be running and was not cancelled."
)


def _is_absent_kernel_error(
    exc: Exception, *, owner: str, authenticated_username: str | None
) -> bool:
    """Recognize a missing slug without hiding a distinguishable auth failure.

    Kaggle's GetKernel endpoint returned HTTP 403 for a new private slug during
    the real acceptance run. Treat that response as absent only for the
    authenticated account's own namespace; a 403 for another owner remains an
    authorization error. Kaggle's conventional 404 remains unambiguously absent.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code == 404:
        return True
    return (
        status_code == 403
        and isinstance(authenticated_username, str)
        and owner.casefold() == authenticated_username.casefold()
    )


def _kernel_version_from_response(response: object) -> int:
    """Extract the current version from the SDK's GetKernel metadata envelope."""
    metadata = getattr(response, "metadata", None)
    version = getattr(metadata, "current_version_number", None)
    if not isinstance(version, int) or version <= 0:
        raise KaggleCommandError("Kaggle GetKernel response did not include a positive current version")
    return version


def remote_output_file_pattern(files: Sequence[str]) -> str | None:
    """Build a literal, anchored Kaggle output regex from safe relative paths."""
    normalized: list[str] = []
    for raw_path in files:
        if not isinstance(raw_path, str) or not raw_path:
            raise ConfigurationError("fetch_files entries must be non-empty strings")
        path = PurePosixPath(raw_path.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
            raise ConfigurationError(f"fetch_files entry must be a safe relative path: {raw_path}")
        normalized.append(str(path))
    if not normalized:
        return None
    return r"\A(?:" + "|".join(re.escape(path) for path in dict.fromkeys(normalized)) + r")\Z"


def kaggle_slugify(title: str) -> str:
    """Derive the lowercase ASCII slug used by Kaggle for ordinary kernel titles."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def validate_title_slug(config: KernelConfig) -> None:
    configured_slug = config.id.split("/", 1)[1]
    derived_slug = kaggle_slugify(config.title)
    if configured_slug != derived_slug:
        raise ConfigurationError(
            "kernel title/id slug mismatch: "
            f"configured slug={configured_slug!r}, derived slug={derived_slug!r}, "
            f"title={config.title!r}"
        )


def _lock_filename(kernel: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "-", kernel).strip("-.")[:80] or "kernel"
    digest = hashlib.sha256(kernel.encode("utf-8")).hexdigest()[:16]
    return f"{readable}-{digest}.lock"


@contextmanager
def kernel_lock(kernel: str, work_root: Path):
    """Fail-fast local lock for one kernel slug for the full remote lifecycle."""
    lock_dir = work_root / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / _lock_filename(kernel)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ConfigurationError(
            f"kernel {kernel!r} is already locked by another harness process: {lock_path}"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"kernel": kernel, "pid": os.getpid()}) + "\n")
        yield lock_path
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def parse_kernel_status(output: str) -> KernelStatusResult:
    match = _STATUS_LINE.search(output.strip())
    if not match:
        raise KaggleCommandError(f"unrecognized Kaggle kernel status: {output.strip() or '<empty>'}")
    raw_status = match.group("status")
    worker_status = _WORKER_STATUS.fullmatch(raw_status.strip())
    normalized_status = worker_status.group("status") if worker_status else raw_status
    status_key = normalized_status.strip().lower().replace("_", "").replace("-", "")
    statuses = {
        "complete": RunStatus.SUCCESS,
        "running": RunStatus.RUNNING,
        "queued": RunStatus.QUEUED,
        "pending": RunStatus.QUEUED,
        "error": RunStatus.ERROR,
        "failed": RunStatus.ERROR,
        "cancelacknowledged": RunStatus.ERROR,
        "canceled": RunStatus.ERROR,
        "cancelled": RunStatus.ERROR,
    }
    if status_key not in statuses:
        raise KaggleCommandError(f"unsupported Kaggle kernel status: {raw_status}")
    failure_match = _FAILURE_LINE.search(output.strip())
    return KernelStatusResult(
        status=statuses[status_key],
        raw_status=raw_status,
        failure_message=failure_match.group("message") if failure_match else None,
        raw_output=output.strip(),
    )


def parse_kernel_submission(result: CommandResult) -> KernelSubmission:
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    error_match = _PUSH_ERROR.search(output)
    if error_match:
        raise KaggleCommandError(f"Kaggle rejected kernel submission: {error_match.group('message')}")
    if _PUSH_INVALID_INPUT.search(output):
        raise KaggleCommandError(
            f"Kaggle pushed the kernel without one or more configured inputs: {output[:1000]}"
        )
    success_match = _PUSH_SUCCESS.search(output)
    if not success_match:
        detail = output[:1000] or "no CLI output"
        raise KaggleCommandError(
            f"Kaggle CLI exited successfully, but no pushed kernel version could be verified: {detail}"
        )
    url_match = _PUSH_URL.search(output)
    return KernelSubmission(
        command=result,
        version=int(success_match.group("version")),
        url=url_match.group("url").rstrip(".") if url_match else None,
    )


class KaggleKernelExecutor:
    def __init__(
        self,
        executable: str = "kaggle",
        runner: Runner = run_command,
        command_timeout_seconds: float = 600,
    ) -> None:
        self.executable = executable
        self.runner = runner
        if command_timeout_seconds <= 0:
            raise ConfigurationError("command timeout must be positive")
        self.command_timeout_seconds = command_timeout_seconds

    def _run(
        self,
        arguments: Sequence[str],
        *,
        check: bool = True,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        try:
            result = self.runner(
                [self.executable, *arguments],
                timeout_seconds=timeout_seconds or self.command_timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise KaggleCommandError(
                f"Kaggle CLI executable was not found: {self.executable}. Install it or pass --kaggle-bin."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise KaggleCommandError(
                f"Kaggle CLI command timed out after {exc.timeout} seconds: {' '.join(exc.cmd)}"
            ) from exc
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no CLI output"
            raise KaggleCommandError(
                f"Kaggle command failed ({result.returncode}): {detail}",
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result

    def doctor(self, *, check_auth: bool = False) -> CommandResult:
        version = self._run(["--version"])
        if check_auth:
            self._run(["kernels", "list", "-m", "--page-size", "1"])
        return version

    def quota(self) -> CommandResult:
        """Return the active account's GPU/TPU quota as JSON."""
        return self._run(["quota", "--format", "json"])

    def submit(self, staged_dir: Path, timeout_seconds: int) -> KernelSubmission:
        result = self._run(
            ["kernels", "push", "--path", str(staged_dir), "--timeout", str(timeout_seconds)]
        )
        return parse_kernel_submission(result)

    def kernel_current_version(self, kernel: str) -> int | None:
        """Return the latest remote version, or None only when the slug is absent.

        Kaggle's CLI exposes version-qualified operations but not the current
        version number. Its installed SDK is the CLI's own API client and exposes
        this metadata directly.
        """
        owner, slug = kernel.split("/", 1)
        authenticated_username: str | None = None
        try:
            from kaggle import KaggleApi
            from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest

            api = KaggleApi()
            api.authenticate()
            configured_username = api.config_values.get("username")
            authenticated_username = (
                configured_username if isinstance(configured_username, str) else None
            )
            request = ApiGetKernelRequest()
            request.user_name = owner
            request.kernel_slug = slug
            with api.build_kaggle_client() as client:
                response = client.kernels.kernels_api_client.get_kernel(request)
            return _kernel_version_from_response(response)
        except Exception as exc:  # Kaggle SDK exposes HTTP errors by implementation version.
            if _is_absent_kernel_error(
                exc, owner=owner, authenticated_username=authenticated_username
            ):
                return None
            raise KaggleCommandError(
                f"could not determine current Kaggle kernel version for {kernel}: {exc}"
            ) from exc

    def status(self, kernel: str, *, timeout_seconds: float | None = None) -> KernelStatusResult:
        result = self._run(["kernels", "status", kernel], timeout_seconds=timeout_seconds)
        return parse_kernel_status(f"{result.stdout}\n{result.stderr}")

    def wait(
        self,
        kernel: str,
        *,
        timeout_seconds: int,
        poll_interval_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> KernelStatusResult:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ConfigurationError("wait timeout and poll interval must be positive")
        deadline = clock() + timeout_seconds
        latest: KernelStatusResult | None = None
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                return KernelStatusResult(
                    RunStatus.TIMEOUT,
                    latest.raw_status if latest else None,
                    _WAIT_TIMEOUT_MESSAGE,
                    latest.raw_output if latest else "",
                )
            latest = self.status(kernel, timeout_seconds=min(self.command_timeout_seconds, remaining))
            if latest.status.terminal:
                return latest
            remaining = deadline - clock()
            if remaining <= 0:
                return KernelStatusResult(
                    RunStatus.TIMEOUT,
                    latest.raw_status,
                    _WAIT_TIMEOUT_MESSAGE,
                    latest.raw_output,
                )
            sleeper(min(poll_interval_seconds, remaining))

    def fetch(
        self, kernel: str, destination: Path, *, fetch_files: Sequence[str] = ()
    ) -> KernelFetchResult:
        destination.mkdir(parents=True, exist_ok=True)
        pattern = remote_output_file_pattern(fetch_files)
        arguments = ["kernels", "output", kernel, "--path", str(destination), "--force"]
        if pattern:
            arguments.extend(["--file-pattern", pattern])
        output = self._run(
            arguments, check=False
        )
        log_files = tuple(sorted(path for path in destination.rglob("*.log") if path.is_file()))
        if fetch_files:
            fetched_files = tuple(
                destination / PurePosixPath(path.replace("\\", "/"))
                for path in fetch_files
                if (destination / PurePosixPath(path.replace("\\", "/"))).is_file()
            )
        else:
            fetched_files = tuple(
                sorted(path for path in destination.rglob("*") if path.is_file() and path not in log_files)
            )
        return KernelFetchResult(
            command=output,
            log_files=log_files,
            fetched_files=fetched_files,
            requested_files=tuple(fetch_files),
        )


def stage_kernel(source_dir: Path, build_dir: Path, config: KernelConfig) -> Path:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise ConfigurationError(f"source directory does not exist: {source_dir}")
    if build_dir.exists():
        raise ConfigurationError(f"staging directory already exists: {build_dir}")
    source_code = source_dir / config.code_file
    validate_authoritative_source_path(source_dir, source_code)
    if not source_code.is_file():
        raise ConfigurationError(f"code_file does not exist in source directory: {config.code_file}")
    build_dir.mkdir(parents=True)
    staged_code = build_dir / config.code_file
    staged_code.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_code, staged_code)
    if config.parameters:
        if config.kernel_type != "script" or config.language != "python":
            raise ConfigurationError(
                "inline parameters currently require a Python script kernel; use an explicit Kaggle input otherwise"
            )
        try:
            parameters_json = json.dumps(config.parameters, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"parameters must be finite JSON-compatible values: {exc}") from exc
        original = base64.b64encode(staged_code.read_bytes()).decode("ascii")
        parameters = base64.b64encode(parameters_json.encode("utf-8")).decode("ascii")
        wrapper = (
            "# Generated by autonomous-research-harness; this file is self-contained.\n"
            "import base64 as __harness_b64, json as __harness_json, os as __harness_os\n"
            f"__harness_parameters_json = __harness_b64.b64decode('{parameters}').decode('utf-8')\n"
            "__harness_os.environ['RESEARCH_HARNESS_PARAMETERS'] = __harness_parameters_json\n"
            "HARNESS_PARAMETERS = __harness_json.loads(__harness_parameters_json)\n"
            f"__harness_source = __harness_b64.b64decode('{original}').decode('utf-8')\n"
            f"exec(compile(__harness_source, {config.code_file!r}, 'exec'), globals(), globals())\n"
        )
        staged_code.write_text(wrapper, encoding="utf-8")
    write_kernel_metadata(config, build_dir)
    return build_dir


def preflight_kernel_submission(executor: KaggleKernelExecutor, config: KernelConfig) -> None:
    """Reject mutable producer reuse and changed evaluation sources before push."""
    if config.immutable_kernel:
        existing_version = executor.kernel_current_version(config.id)
        if existing_version is not None:
            raise ConfigurationError(
                f"immutable kernel slug already exists: {config.id} "
                f"(current version {existing_version}); use a new slug"
            )
    for expectation in config.kernel_source_expectations:
        latest_version = executor.kernel_current_version(expectation.kernel)
        if latest_version is None:
            raise ConfigurationError(
                f"could not determine current version for artifact source {expectation.kernel}"
            )
        if latest_version != expectation.version:
            raise ConfigurationError(
                f"Artifact source {expectation.kernel} was recorded at version "
                f"{expectation.version}, but Kaggle currently reports version {latest_version}. "
                "Refusing evaluation because kernel_sources cannot guarantee the original artifact version."
            )


def _git_root(path: Path) -> Path | None:
    resolved = path.resolve()
    return next(
        (candidate for candidate in (resolved, *resolved.parents) if (candidate / ".git").exists()),
        None,
    )


def validate_authoritative_source_path(source_dir: Path, source_code: Path) -> None:
    """Enforce the source-root convention for repository-relative submissions.

    Legacy callers may stage an external, non-repository directory. The strict
    convention applies once the source directory is inside a Git repository.
    """
    git_root = _git_root(source_dir)
    if git_root is None:
        return
    try:
        relative_path = source_code.resolve().relative_to(git_root)
    except ValueError as exc:
        raise ConfigurationError("code_file must remain within the repository") from exc
    if not relative_path.parts or relative_path.parts[0] != "src":
        raise ConfigurationError(
            "repository authoritative code_file must be located under src/"
        )


def git_identity(source_dir: Path) -> tuple[str | None, bool | None]:
    source_dir = source_dir.resolve()
    git_root = _git_root(source_dir)
    safe_directory = ["-c", f"safe.directory={git_root.as_posix()}"] if git_root else []

    def run_git(*arguments: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", *safe_directory, "-C", str(source_dir), *arguments],
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    commit_result = run_git("rev-parse", "HEAD")
    status_result = run_git("status", "--porcelain", "--untracked-files=normal")
    commit = (
        commit_result.stdout.strip()
        if commit_result is not None and commit_result.returncode == 0
        else None
    )
    dirty = (
        bool(status_result.stdout.strip())
        if status_result is not None and status_result.returncode == 0
        else None
    )
    return commit, dirty


def git_file_tracked(source_dir: Path, source_code: Path) -> bool | None:
    """Return whether a source file is tracked, or None when Git is unavailable."""
    source_dir = source_dir.resolve()
    source_code = source_code.resolve()
    git_root = _git_root(source_dir)
    if git_root is None:
        return None
    try:
        relative_path = source_code.relative_to(git_root)
        repository_result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={git_root.as_posix()}",
                "-C",
                str(git_root),
                "rev-parse",
                "--is-inside-work-tree",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if repository_result.returncode != 0:
            return None
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={git_root.as_posix()}",
                "-C",
                str(git_root),
                "ls-files",
                "--error-unmatch",
                "--",
                relative_path.as_posix(),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.returncode == 0


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ConfigurationError(f"could not hash staged code_file {path}: {exc}") from exc
    return digest.hexdigest()


def _failure_detail(result: CommandResult, fallback: str) -> str:
    return result.stderr.strip() or result.stdout.strip() or fallback


def _combine_error(current: str | None, new: str) -> str:
    return f"{current}; {new}" if current else new


def execute_run(
    executor: KaggleKernelExecutor,
    config: KernelConfig,
    source_dir: Path,
    *,
    work_root: Path = Path(".work/kaggle"),
    runs_root: Path = Path("runs"),
    kind: str = "experiment",
    run_id: str | None = None,
    account: str | None = None,
) -> tuple[RunStatus, Path]:
    if kind not in {"experiment", "smoke"}:
        raise ConfigurationError("kind must be 'experiment' or 'smoke'")
    validate_title_slug(config)
    store = RunStore(runs_root)
    run_id = run_id or store.next_id(kind)
    commit, dirty = git_identity(source_dir)
    source_path = (source_dir.resolve() / config.code_file).resolve()
    metadata = RunMetadata(
        run_id=run_id,
        status=RunStatus.QUEUED,
        executor="kaggle",
        kernel=config.id,
        account=account,
        git_commit=commit,
        requested_hardware=config.machine_shape or ("GPU" if config.enable_gpu else None),
        expected_hardware_contains=config.expected_hardware_contains,
        internet=config.enable_internet,
        started_at=utc_now(),
        git_dirty=dirty,
        source_dir=str(source_dir.resolve()),
        source_path=str(source_path),
        config=config.as_dict(),
    )
    run_dir = store.create(run_id, kind, metadata, config.as_dict())
    build_dir = work_root / "builds" / run_id

    status = RunStatus.QUEUED
    submission_succeeded = False
    local_failures: list[str] = []
    lock_stack = ExitStack()
    remote_reached_terminal = False

    def append_log(name: str, value: str) -> None:
        try:
            store.append_log(run_dir, name, value)
        except OSError as exc:
            local_failures.append(f"could not persist {name}: {exc}")

    try:
        lock_stack.enter_context(kernel_lock(config.id, work_root))
        preflight_kernel_submission(executor, config)
        if source_path.is_file():
            metadata.source_sha256 = file_sha256(source_path)
            metadata.source_git_tracked = git_file_tracked(source_dir, source_path)
        if kind == "experiment" and metadata.source_git_tracked is False:
            raise ConfigurationError(
                f"full experiment source is not Git tracked: {config.code_file}"
            )
        stage_kernel(source_dir, build_dir, config)
        staged_code_sha256 = file_sha256(build_dir / config.code_file)
        submitted = executor.submit(build_dir, config.timeout_seconds)
        submission_succeeded = True
        metadata.submitted_code_sha256 = staged_code_sha256
        append_log("stdout.log", submitted.command.stdout)
        append_log("stderr.log", submitted.command.stderr)
        metadata.kernel_version = submitted.version
        metadata.status = RunStatus.RUNNING
        try:
            store.write_metadata(run_dir, metadata)
        except OSError as exc:
            local_failures.append(f"could not persist intermediate metadata: {exc}")

        try:
            remote_status = executor.wait(
                config.id,
                timeout_seconds=config.wait_timeout_seconds or config.timeout_seconds,
                poll_interval_seconds=config.poll_interval_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - finalization must cover runner failures
            status = RunStatus.ERROR
            metadata.error = str(exc)
        else:
            status = remote_status.status
            remote_reached_terminal = (
                remote_status.status.terminal and remote_status.status != RunStatus.TIMEOUT
            )
            metadata.remote_status = remote_status.raw_status
            metadata.remote_failure_message = remote_status.failure_message
            append_log("stdout.log", remote_status.raw_output)
            if remote_status.failure_message:
                metadata.error = remote_status.failure_message

        if remote_reached_terminal:
            try:
                fetched = fetch_with_retries(executor, config.id, run_dir / "artifacts", config.fetch_files)
            except Exception as exc:  # noqa: BLE001 - finalization covers fetch failures
                status = RunStatus.ERROR
                metadata.error = _combine_error(metadata.error, f"Kaggle fetch failed: {exc}")
            else:
                status = _process_fetched_run(
                    fetched, status, metadata, config, run_dir, append_log
                )

    except Exception as exc:  # noqa: BLE001 - guarantee a terminal record after run creation
        status = RunStatus.ERROR if submission_succeeded else RunStatus.SUBMIT_ERROR
        metadata.error = _combine_error(metadata.error, str(exc))
        append_log("stderr.log", str(exc))
    finally:
        if local_failures:
            status = RunStatus.ERROR
            metadata.error = _combine_error(metadata.error, "; ".join(local_failures))
        if status != RunStatus.SUCCESS and not metadata.error:
            metadata.error = (
                f"Kaggle run finished with status {status.value}; "
                "inspect stdout.log and stderr.log"
            )
        metadata.status = status
        metadata.finished_at = utc_now()
        try:
            try:
                store.write_metadata(run_dir, metadata)
            except OSError:
                # Retry once for transient replace/locking failures. If durable storage is
                # unavailable, propagate rather than falsely reporting a finalized run.
                store.write_metadata(run_dir, metadata)
        finally:
            lock_stack.close()
    return status, run_dir


FETCH_ATTEMPTS = 3


def fetch_with_retries(executor, kernel: str, destination: Path, fetch_files: Sequence[str],
                       attempts: int = FETCH_ATTEMPTS, pause: float = 5.0):
    """Fetch a terminal run's output, retrying a transient download failure (e.g. a connection broken
    mid-download). A fetch is accepted when the CLI exits 0, every requested file is present and a log
    arrived; the last attempt's result is returned either way, so a persistent failure is still recorded."""
    fetched = None
    for attempt in range(attempts):
        fetched = executor.fetch(kernel, destination, fetch_files=fetch_files)
        complete = (
            fetched.command.returncode == 0
            and fetched.log_files
            and all((destination / PurePosixPath(f.replace("\\", "/"))).is_file() for f in fetched.requested_files)
        )
        if complete:
            break
        if attempt + 1 < attempts:
            time.sleep(pause)
    return fetched


def _process_fetched_run(
    fetched: KernelFetchResult,
    status: RunStatus,
    metadata: RunMetadata,
    config: KernelConfig,
    run_dir: Path,
    append_log: Callable[[str, str], None],
) -> RunStatus:
    """Persist fetched evidence and validate a remotely terminal run."""
    try:
        output = fetched.command
        append_log("stderr.log", output.stderr)

        if output.returncode != 0:
            status = RunStatus.ERROR
            metadata.error = _combine_error(
                metadata.error,
                "Kaggle output fetch command failed "
                f"(exit code {output.returncode}): "
                + _failure_detail(output, "no CLI error output"),
            )
        missing_requested = [
            path
            for path in fetched.requested_files
            if not (run_dir / "artifacts" / PurePosixPath(path.replace("\\", "/"))).is_file()
        ]
        if missing_requested:
            if status == RunStatus.SUCCESS:
                status = RunStatus.MISSING_OUTPUT
            metadata.error = _combine_error(
                metadata.error,
                "Requested remote output was not found: " + ", ".join(missing_requested),
            )
        if not fetched.log_files:
            status = RunStatus.ERROR
            metadata.error = _combine_error(
                metadata.error,
                "Kaggle output download completed but contained no .log file",
            )

        for log_file in fetched.log_files:
            try:
                append_log("stdout.log", log_file.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                status = RunStatus.ERROR
                metadata.error = _combine_error(
                    metadata.error,
                    f"could not read downloaded Kaggle log {log_file}: {exc}",
                )

        if status == RunStatus.SUCCESS:
            try:
                result_data = validate_outputs(run_dir / "artifacts", config, run_dir)
            except MissingOutputError as exc:
                status = RunStatus.MISSING_OUTPUT
                metadata.error = _combine_error(metadata.error, str(exc))
            except OutputValidationError as exc:
                status = RunStatus.ERROR
                metadata.error = _combine_error(metadata.error, str(exc))
            except Exception as exc:  # noqa: BLE001 - preserve final state on local failures
                status = RunStatus.ERROR
                metadata.error = _combine_error(
                    metadata.error, f"output validation failed: {exc}"
                )
            else:
                metadata.observed_hardware = observed_hardware_from_result(result_data)
                try:
                    validate_expected_hardware(
                        config.expected_hardware_contains, metadata.observed_hardware
                    )
                except OutputValidationError as exc:
                    status = RunStatus.ERROR
                    metadata.error = _combine_error(metadata.error, str(exc))
        if status == RunStatus.SUCCESS and _declares_artifact_manifest(config):
            manifest_path = run_dir / "artifacts" / "artifact_manifest.json"
            try:
                load_artifact_manifest(manifest_path)
            except OutputValidationError as exc:
                status = RunStatus.ERROR
                metadata.error = _combine_error(metadata.error, str(exc))
            else:
                manifest_sha256 = file_sha256(manifest_path)
                metadata.remote_artifacts = {
                    "provider": "kaggle_kernel",
                    "kernel": metadata.kernel,
                    "kernel_version": metadata.kernel_version,
                    "manifest": "artifact_manifest.json",
                    "manifest_sha256": manifest_sha256,
                }
    except Exception as exc:  # noqa: BLE001 - caller finalizes all local failures
        metadata.error = _combine_error(metadata.error, f"output processing failed: {exc}")
        return RunStatus.ERROR
    return status


def _declares_artifact_manifest(config: KernelConfig) -> bool:
    return "artifact_manifest.json" in {
        value.replace("\\", "/") for value in (*config.fetch_files, *config.required_outputs)
    }
