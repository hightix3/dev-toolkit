"""
Blockscout API Client — Exception hierarchy.
"""


class APIError(Exception):
    """Base exception for all Blockscout API errors.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code returned by the server (if available).
        response: Raw httpx.Response object (if available).
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response=None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response = response

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"status_code={self.status_code!r})"
        )


class RateLimitError(APIError):
    """Raised when the server responds with HTTP 429 Too Many Requests.

    The client applies exponential backoff and will re-raise this exception
    only after all retry attempts are exhausted.
    """


class NotFoundError(APIError):
    """Raised when the server responds with HTTP 404 Not Found."""


class ServerError(APIError):
    """Raised when the server responds with a 5xx status code."""


class ValidationError(APIError):
    """Raised when the server responds with HTTP 400 Bad Request,
    indicating an invalid or malformed request parameter."""
