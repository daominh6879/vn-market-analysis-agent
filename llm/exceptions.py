class ProviderError(Exception):
    """Base for all LLM provider errors."""

    def __init__(self, message: str, provider: str | None = None):
        super().__init__(message)
        self.provider = provider


class ModelOverloadedError(ProviderError):
    """Provider is overloaded / rate-limited."""


class ContentBlockedError(ProviderError):
    """Request blocked by provider content policy."""


class SourceError(ProviderError):
    """Upstream provider or network source error."""
