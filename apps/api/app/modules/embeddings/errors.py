"""Embedding provider configuration and model load errors."""


class EmbeddingError(Exception):
    """Base class for embedding provider failures."""


class EmbeddingConfigurationError(EmbeddingError, ValueError):
    """Invalid embedding provider or settings."""


class EmbeddingModelLoadError(EmbeddingError, RuntimeError):
    """The underlying model could not be loaded or used."""

    def __init__(
        self,
        message: str,
        *,
        model_id: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.model_id = model_id
        self.__cause__ = cause