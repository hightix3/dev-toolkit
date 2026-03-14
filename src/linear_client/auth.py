"""
Linear GraphQL API Client — Authentication

Provides httpx-compatible Auth classes for both Personal API Keys and
OAuth 2.0 Bearer tokens.

Usage::

    import httpx
    from linear_client.auth import LinearAuth

    auth = LinearAuth("lin_api_xxxxxxxxxxxx")
    client = httpx.Client(auth=auth)
"""

from __future__ import annotations

from typing import Generator

import httpx


class LinearAuth(httpx.Auth):
    """
    httpx Auth class for the Linear GraphQL API.

    Handles both **Personal API Keys** and **OAuth 2.0 Bearer tokens**.

    - Personal API keys are passed directly as ``Authorization: <API_KEY>``.
    - OAuth tokens are passed as ``Authorization: Bearer <TOKEN>``.

    The constructor auto-detects the format: if the key already starts with
    ``Bearer `` or ``lin_api_`` it is used verbatim; otherwise it is
    treated as a raw API key and forwarded as-is (Linear docs specify
    ``Authorization: <API_KEY>`` for personal keys, not ``Bearer``).

    To force the ``Bearer`` prefix (e.g. for OAuth tokens that don't carry
    the prefix themselves), pass ``bearer=True``.

    Args:
        api_key: Personal API key or OAuth access token.
        bearer: If ``True``, always prepend ``Bearer `` to the key. Defaults
            to ``False`` for personal API keys (Linear accepts the raw key).

    Example::

        # Personal API key (most common)
        auth = LinearAuth("lin_api_xxxxxxxxxxxx")

        # OAuth token — Linear expects "Bearer <token>"
        auth = LinearAuth("my_oauth_token", bearer=True)

        # Or pass the full header value yourself
        auth = LinearAuth("Bearer my_oauth_token")
    """

    ENDPOINT = "https://api.linear.app/graphql"

    def __init__(self, api_key: str, *, bearer: bool = False) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("api_key must be a non-empty string.")
        self.api_key = api_key.strip()
        self._bearer = bearer

    # ------------------------------------------------------------------
    # httpx.Auth protocol
    # ------------------------------------------------------------------

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        """Inject the Authorization and Content-Type headers."""
        request.headers["Authorization"] = self._authorization_value
        request.headers["Content-Type"] = "application/json"
        yield request

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _authorization_value(self) -> str:
        """Return the value to set in the Authorization header."""
        key = self.api_key
        if self._bearer:
            # Caller explicitly requested Bearer prefix
            if key.lower().startswith("bearer "):
                return key  # already prefixed
            return f"Bearer {key}"
        # Linear personal API keys are sent as the raw value
        return key

    def __repr__(self) -> str:
        masked = self.api_key[:8] + "..." if len(self.api_key) > 8 else "***"
        return f"LinearAuth(api_key={masked!r})"


class LinearOAuthAuth(LinearAuth):
    """
    Convenience subclass that always uses the ``Bearer`` prefix.

    Use this when authenticating with an OAuth 2.0 access token::

        auth = LinearOAuthAuth("my_oauth_access_token")
    """

    def __init__(self, access_token: str) -> None:
        super().__init__(access_token, bearer=True)

    def __repr__(self) -> str:
        masked = self.api_key[:8] + "..." if len(self.api_key) > 8 else "***"
        return f"LinearOAuthAuth(access_token={masked!r})"
