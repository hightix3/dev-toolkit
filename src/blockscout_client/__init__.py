"""
blockscout_client
=================

Production-ready Python API client for the Blockscout Blockchain Explorer
REST API v2 (https://eth.blockscout.com/api/v2).

Quick start::

    from blockscout_client import BlockscoutClient

    with BlockscoutClient() as client:
        tx = client.get_transaction("0xabc123...")
        print(tx["hash"])

Blockscout PRO::

    from blockscout_client import BlockscoutClient

    with BlockscoutClient(api_key="your-pro-key") as client:
        stats = client.get_stats()

Exports
-------
- :class:`~blockscout_client.client.BlockscoutClient` — Main client class.
- :class:`~blockscout_client.auth.BlockscoutAuth` — Authentication handler.
- :class:`~blockscout_client.exceptions.APIError` — Base exception.
- :class:`~blockscout_client.exceptions.RateLimitError`
- :class:`~blockscout_client.exceptions.NotFoundError`
- :class:`~blockscout_client.exceptions.ServerError`
- :class:`~blockscout_client.exceptions.ValidationError`
"""

from .auth import BlockscoutAuth
from .client import BlockscoutClient
from .exceptions import (
    APIError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)

__all__ = [
    "BlockscoutClient",
    "BlockscoutAuth",
    "APIError",
    "RateLimitError",
    "NotFoundError",
    "ServerError",
    "ValidationError",
]

__version__ = "1.0.0"
