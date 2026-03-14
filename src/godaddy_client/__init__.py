"""
godaddy_client — Python API client for the GoDaddy Domains API.

Exposes the main client, auth helper, and exception classes.

Quick start::

    from godaddy_client import GoDaddyClient, GoDaddyAuth

    auth = GoDaddyAuth("my_key", "my_secret")
    with GoDaddyClient(auth=auth) as client:
        domains = client.list_domains()
        print(domains)
"""

from .auth import GoDaddyAuth
from .client import GoDaddyClient
from .exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)

__all__ = [
    "GoDaddyClient",
    "GoDaddyAuth",
    "APIError",
    "AuthenticationError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "ValidationError",
]

__version__ = "1.0.0"
