"""
exceptions.py — Custom exception hierarchy for the OpenAI API client.

Parses OpenAI's standard error envelope:
    {"error": {"message": "...", "type": "...", "param": "...", "code": "..."}}

HTTP status codes map to specific exception subclasses so callers can catch
at the granularity that suits them:

    try:
        client.chat.completions.create(...)
    except RateLimitError as exc:
        time.sleep(exc.retry_after or 60)
    except AuthenticationError:
        # re-prompt for API key
    except OpenAIError as exc:
        # catch-all
        print(exc.status_code, exc.message)
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Base exception
# ---------------------------------------------------------------------------


class OpenAIError(Exception):
    """Base class for all errors raised by the OpenAI client.

    Attributes:
        status_code:  HTTP status code returned by the API, or ``None`` for
                      transport-level errors.
        message:      Human-readable description of the error.
        response:     The raw ``httpx.Response`` object, when available.
        error_code:   The ``code`` field from OpenAI's error envelope, e.g.
                      ``"invalid_api_key"``.
        error_type:   The ``type`` field from OpenAI's error envelope, e.g.
                      ``"invalid_request_error"``.
        param:        The ``param`` field from OpenAI's error envelope
                      identifying the offending request parameter, when
                      present.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response: Any | None = None,
        error_code: str | None = None,
        error_type: str | None = None,
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.status_code: int | None = status_code
        self.response: Any | None = response
        self.error_code: str | None = error_code
        self.error_type: str | None = error_type
        self.param: str | None = param

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"status_code={self.status_code!r}, "
            f"message={self.message!r}, "
            f"error_code={self.error_code!r}"
            f")"
        )

    def __str__(self) -> str:
        parts: list[str] = []
        if self.status_code is not None:
            parts.append(f"HTTP {self.status_code}")
        if self.error_type:
            parts.append(self.error_type)
        if self.error_code:
            parts.append(f"[{self.error_code}]")
        parts.append(self.message)
        return " — ".join(parts) if parts[:-1] else self.message

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_response(cls, response: Any) -> OpenAIError:
        """Build the most-specific exception from an ``httpx.Response``.

        Attempts to decode OpenAI's standard JSON error envelope.  Falls back
        to the raw response text if the body is not valid JSON or does not
        match the expected schema.

        Args:
            response: An ``httpx.Response`` instance.

        Returns:
            An :class:`OpenAIError` subclass instance appropriate for the HTTP
            status code.
        """
        status_code: int = response.status_code
        message: str = ""
        error_code: str | None = None
        error_type: str | None = None
        param: str | None = None

        try:
            body: dict[str, Any] = response.json()
            error_obj: dict[str, Any] = body.get("error", {})
            message = error_obj.get("message") or response.text or str(status_code)
            error_code = error_obj.get("code")
            error_type = error_obj.get("type")
            param = error_obj.get("param")
        except Exception:
            message = response.text or str(status_code)

        kwargs: dict[str, Any] = dict(
            status_code=status_code,
            response=response,
            error_code=error_code,
            error_type=error_type,
            param=param,
        )

        exc_class = _STATUS_CODE_MAP.get(status_code)
        if exc_class is not None:
            if exc_class is RateLimitError:
                retry_after_raw = response.headers.get("retry-after")
                retry_after: float | None = None
                if retry_after_raw is not None:
                    try:
                        retry_after = float(retry_after_raw)
                    except ValueError:
                        pass
                return RateLimitError(message, retry_after=retry_after, **kwargs)
            return exc_class(message, **kwargs)

        if 500 <= status_code < 600:
            return ServerError(message, **kwargs)

        return cls(message, **kwargs)


# ---------------------------------------------------------------------------
# HTTP 4xx errors
# ---------------------------------------------------------------------------


class AuthenticationError(OpenAIError):
    """Raised when the API returns HTTP 401 Unauthorized.

    Typically caused by a missing, revoked, or malformed API key.
    """


class PermissionDeniedError(OpenAIError):
    """Raised when the API returns HTTP 403 Forbidden.

    The authenticated identity lacks permission to perform the requested
    operation or access the requested resource.
    """


class NotFoundError(OpenAIError):
    """Raised when the API returns HTTP 404 Not Found.

    The requested resource (model, file, fine-tune job, etc.) does not exist
    or is not visible to the authenticated identity.
    """


class RateLimitError(OpenAIError):
    """Raised when the API returns HTTP 429 Too Many Requests.

    Attributes:
        retry_after: Seconds to wait before retrying, parsed from the
                     ``Retry-After`` response header.  ``None`` when the
                     header is absent or cannot be parsed.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after: float | None = retry_after


class InvalidRequestError(OpenAIError):
    """Raised when the API returns HTTP 400 Bad Request.

    Indicates that the request body or query parameters are malformed or
    contain invalid values.
    """


class ConflictError(OpenAIError):
    """Raised when the API returns HTTP 409 Conflict.

    Typically occurs when attempting to create a resource that already exists
    or when a state transition is not allowed.
    """


class UnprocessableEntityError(OpenAIError):
    """Raised when the API returns HTTP 422 Unprocessable Entity.

    The server understands the content type and syntax of the request but is
    unable to process the contained instructions.
    """


# ---------------------------------------------------------------------------
# HTTP 5xx errors
# ---------------------------------------------------------------------------


class ServerError(OpenAIError):
    """Raised for HTTP 5xx responses.

    Indicates a transient or permanent error on OpenAI's infrastructure.
    Requests that trigger a :class:`ServerError` are generally safe to retry
    with exponential back-off.
    """


# ---------------------------------------------------------------------------
# Transport / connection errors (no HTTP response)
# ---------------------------------------------------------------------------


class APIConnectionError(OpenAIError):
    """Raised when a network or transport error prevents the request from
    reaching the API.

    This exception (and its subclasses) does **not** carry an HTTP
    ``status_code`` because no response was received.  The original low-level
    exception is chained via ``__cause__``.
    """

    def __init__(
        self,
        message: str = "A connection error occurred while contacting the OpenAI API.",
        *,
        response: Any | None = None,
        error_code: str | None = None,
        error_type: str | None = None,
        param: str | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=None,
            response=response,
            error_code=error_code,
            error_type=error_type,
            param=param,
        )


class APITimeoutError(APIConnectionError):
    """Raised when the request to the OpenAI API times out.

    Subclass of :class:`APIConnectionError`.  Retrying with an exponential
    back-off strategy is usually appropriate.
    """

    def __init__(
        self,
        message: str = "The request to the OpenAI API timed out.",
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)


# ---------------------------------------------------------------------------
# Internal mapping — keep in sync with subclass definitions above
# ---------------------------------------------------------------------------

_STATUS_CODE_MAP: dict[int, type[OpenAIError]] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableEntityError,
    429: RateLimitError,
}

__all__ = [
    "OpenAIError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "RateLimitError",
    "InvalidRequestError",
    "ConflictError",
    "UnprocessableEntityError",
    "ServerError",
    "APIConnectionError",
    "APITimeoutError",
]
