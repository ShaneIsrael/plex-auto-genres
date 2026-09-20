"""Exception hierarchy for plex-auto-genres."""


class PagError(Exception):
    """Base class for every error raised by this application."""


class ConfigError(PagError):
    """The configuration file is missing, malformed or semantically invalid."""


class PlexConnectionError(PagError):
    """Could not reach or authenticate against the Plex server."""


class ProviderError(PagError):
    """A metadata provider failed in a way the caller may want to retry."""

    retryable = True


class ProviderAuthError(ProviderError):
    """The provider rejected our credentials. Retrying will not help."""

    retryable = False


class ProviderRateLimited(ProviderError):
    """The provider asked us to slow down."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderNotFound(ProviderError):
    """The provider has no record matching the query. Retrying will not help."""

    retryable = False
