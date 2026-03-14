"""
GoDaddy Domains API — Exception hierarchy.
"""
from __future__ import annotations

from typing import Any, Optional


class APIError(Exception):
    """Base exception for all GoDaddy API errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"status_code={self.status_code!r})"
        )


class AuthenticationError(APIError):
    """Raised when the API returns 401 Unauthorized or 403 Forbidden."""


class RateLimitError(APIError):
    """Raised when the API returns 429 Too Many Requests."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        status_code: int = 429,
        response_body: Optional[Any] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, status_code, response_body)
        self.retry_after = retry_after


class NotFoundError(APIError):
    """Raised when the API returns 404 Not Found."""


class ServerError(APIError):
    """Raised when the API returns a 5xx Server Error."""


class ValidationError(APIError):
    """Raised when the API returns 422 Unprocessable Entity or 400 Bad Request."""
