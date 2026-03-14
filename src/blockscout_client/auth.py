"""
Blockscout API Client — Authentication handler.

The Blockscout public API requires no authentication.  However,
Blockscout PRO users may supply an API key that is forwarded as the
``x-api-key`` request header for higher rate limits and premium endpoints.
"""

from __future__ import annotations

from typing import Generator

import httpx


class BlockscoutAuth(httpx.Auth):
    """Authentication handler for the Blockscout API.

    For the standard public API no credentials are required and this
    class acts as a no-op.  Blockscout PRO users can supply their API
    key to unlock higher rate limits and additional endpoints.

    Args:
        api_key: Optional Blockscout PRO API key.  When provided it is
            attached to every outgoing request as the ``x-api-key``
            header.  Obtain a key from https://docs.blockscout.com/

    Example::

        # Public (no auth)
        auth = BlockscoutAuth()

        # Blockscout PRO
        auth = BlockscoutAuth(api_key="your-pro-api-key")
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Inject the API key header when a key has been configured.

        This method is called by httpx for every outgoing request.

        Args:
            request: The outgoing :class:`httpx.Request` object.

        Yields:
            The (optionally modified) request.
        """
        if self.api_key:
            request.headers["x-api-key"] = self.api_key
        yield request
