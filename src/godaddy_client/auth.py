"""
GoDaddy Domains API — Authentication helper.

GoDaddy uses a custom "sso-key" scheme:
    Authorization: sso-key {api_key}:{api_secret}

Obtain credentials at https://developer.godaddy.com/keys
"""
from __future__ import annotations

from typing import Generator

import httpx


class GoDaddyAuth(httpx.Auth):
    """
    HTTPX authentication flow that injects the GoDaddy ``sso-key`` header.

    Parameters
    ----------
    api_key:
        The API key (OTE keys begin with ``test_``).
    api_secret:
        The API secret paired with *api_key*.
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        if not api_key or not api_secret:
            raise ValueError("Both api_key and api_secret must be non-empty strings.")
        self.api_key = api_key
        self.api_secret = api_secret

    # ------------------------------------------------------------------
    # httpx.Auth protocol
    # ------------------------------------------------------------------

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Attach the Authorization header, then yield the request."""
        request.headers["Authorization"] = (
            f"sso-key {self.api_key}:{self.api_secret}"
        )
        yield request

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        masked_secret = self.api_secret[:4] + "****" if self.api_secret else "****"
        return f"GoDaddyAuth(api_key={self.api_key!r}, api_secret={masked_secret!r})"
