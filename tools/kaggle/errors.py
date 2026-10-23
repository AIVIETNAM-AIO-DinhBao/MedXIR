"""Typed errors raised by the Kaggle utilities."""


class KaggleHarnessError(RuntimeError):
    """Base class for actionable harness failures."""


class ConfigurationError(KaggleHarnessError):
    """A supplied configuration is invalid."""


class KaggleCommandError(KaggleHarnessError):
    """The Kaggle CLI returned an error."""

    def __init__(self, message: str, *, returncode: int = 1, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class OutputValidationError(KaggleHarnessError):
    """A completed job did not satisfy its output contract."""


class MissingOutputError(OutputValidationError):
    """A successfully inspected remote output did not contain a required file."""
