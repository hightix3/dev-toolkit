"""SerpAPI custom exceptions."""


class SerpAPIError(Exception):
    """Base exception for all SerpAPI errors."""

    def __init__(self, status_code: int, message: str, response: dict = None):
        self.status_code = status_code
        self.message = message
        self.response = response or {}
        super().__init__(f"[{status_code}] {message}")


class AuthenticationError(SerpAPIError):
    """Raised on 401 — invalid or missing API key."""
    pass


class RateLimitError(SerpAPIError):
    """Raised on 429 — search quota exceeded."""
    pass


class InvalidRequestError(SerpAPIError):
    """Raised on 400 — invalid parameters."""
    pass


class NotFoundError(SerpAPIError):
    """Raised on 404 — resource not found."""
    pass


class ServerError(SerpAPIError):
    """Raised on 5xx — SerpAPI server error after retries."""
    pass
