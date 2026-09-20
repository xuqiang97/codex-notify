"""Small provider-neutral notification boundary."""

from dataclasses import dataclass


class ConfigurationError(ValueError):
    """A local setting is missing or invalid; messages must not contain values."""


class ProviderError(Exception):
    """Delivery failed; messages must be safe to print."""


@dataclass(frozen=True)
class Notification:
    title: str
    message: str
