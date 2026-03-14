"""SerpAPI Python Client — auto-generated from SerpAPI documentation."""

from .client import SerpAPIClient
from .auth import SerpAPIAuth
from .exceptions import (
    SerpAPIError,
    AuthenticationError,
    RateLimitError,
    InvalidRequestError,
    NotFoundError,
    ServerError,
)

__all__ = [
    "SerpAPIClient",
    "SerpAPIAuth",
    "SerpAPIError",
    "AuthenticationError",
    "RateLimitError",
    "InvalidRequestError",
    "NotFoundError",
    "ServerError",
]
