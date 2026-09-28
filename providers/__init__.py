"""Small provider-neutral notification boundary."""

from dataclasses import dataclass


class ConfigurationError(ValueError):
    """A local setting is missing or invalid; messages must not contain values."""

    def __init__(self, message: str, *, code: str = "configuration_error") -> None:
        super().__init__(message)
        self.code = code


class ProviderError(Exception):
    """Delivery failed; messages must be safe to print."""

    def __init__(self, message: str, *, code: str = "provider_error", http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class Notification:
    title: str
    message: str
    task_title_included: bool = False
