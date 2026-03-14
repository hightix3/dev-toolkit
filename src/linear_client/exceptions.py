"""
Linear GraphQL API Client — Exception Hierarchy

All exceptions raised by the Linear client are subclasses of LinearError.
"""

from __future__ import annotations

from typing import Any


class LinearError(Exception):
    """Base exception for all Linear API errors."""

    def __init__(self, message: str, response: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.response = response or {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.message!r})"


class GraphQLError(LinearError):
    """
    Raised when the Linear GraphQL API returns one or more errors in the
    ``errors`` field of the response body.

    Attributes:
        errors: The raw list of GraphQL error dicts from the response.
        extensions: Optional extensions dict (may contain ``code`` field).
    """

    def __init__(
        self,
        message: str,
        errors: list[dict[str, Any]] | None = None,
        extensions: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, response=response)
        self.errors = errors or []
        self.extensions = extensions or {}

    @classmethod
    def from_response(cls, errors: list[dict[str, Any]], response: dict[str, Any] | None = None) -> "GraphQLError":
        """Construct from a GraphQL errors list, choosing the most specific subclass."""
        # Inspect the first error for a machine-readable code
        first = errors[0] if errors else {}
        extensions = first.get("extensions") or {}
        code = extensions.get("code", "")
        message = first.get("message", "GraphQL error")

        if code == "AUTHENTICATION_ERROR" or "authentication" in message.lower() or "unauthorized" in message.lower():
            return AuthenticationError(message, errors=errors, extensions=extensions, response=response)
        if code == "RATELIMITED" or "rate limit" in message.lower():
            return RateLimitError(message, errors=errors, extensions=extensions, response=response)
        if code == "ENTITY_NOT_FOUND" or "not found" in message.lower():
            return NotFoundError(message, errors=errors, extensions=extensions, response=response)
        if code == "FORBIDDEN" or "forbidden" in message.lower() or "permission" in message.lower():
            return PermissionError(message, errors=errors, extensions=extensions, response=response)
        if code == "VALIDATION_ERROR" or "validation" in message.lower():
            return ValidationError(message, errors=errors, extensions=extensions, response=response)

        return cls(message, errors=errors, extensions=extensions, response=response)


class AuthenticationError(GraphQLError):
    """
    Raised when the API key or OAuth token is missing, invalid, or expired.

    HTTP context: The API may return HTTP 401 or a GraphQL error with code
    ``AUTHENTICATION_ERROR``.
    """


class RateLimitError(GraphQLError):
    """
    Raised when the Linear API rate limit is exceeded.

    Linear enforces two independent rate limits:
    - **Request limit**: 5,000 requests/hour per user (API key) or 60/hour
      for unauthenticated requests.
    - **Complexity limit**: 3,000,000 complexity points/hour per user.

    The client automatically retries with exponential backoff when this error
    is raised, up to the configured ``max_retries``.

    Attributes:
        retry_after: Seconds to wait before retrying, parsed from response
            headers (``X-RateLimit-Requests-Reset``) when available.
    """

    def __init__(
        self,
        message: str,
        errors: list[dict[str, Any]] | None = None,
        extensions: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, errors=errors, extensions=extensions, response=response)
        self.retry_after = retry_after


class NotFoundError(GraphQLError):
    """
    Raised when the requested resource does not exist or is not accessible.

    Maps to GraphQL error code ``ENTITY_NOT_FOUND``.
    """


class PermissionError(GraphQLError):
    """
    Raised when the authenticated user lacks permission to perform the
    requested operation (e.g., deleting another user's resource).
    """


class ValidationError(GraphQLError):
    """
    Raised when the input to a mutation fails server-side validation
    (e.g., a required field is missing or a value is out of range).
    """


class NetworkError(LinearError):
    """
    Raised when a network-level error prevents the request from completing
    (e.g., DNS failure, connection reset, timeout).
    """


class TimeoutError(NetworkError):
    """Raised when the HTTP request exceeds the configured timeout."""
