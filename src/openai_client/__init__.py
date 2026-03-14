"""
openai_client — Python client package for the OpenAI API v2.3.0.

Quickstart::

    from openai_client import OpenAIClient

    client = OpenAIClient(api_key="sk-...")

    # List available models
    models = client.models.list()

    # Create a chat completion
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello!"}],
    )

Environment variables:
    OPENAI_API_KEY    — Bearer token (required unless supplied to the client).
    OPENAI_ORG_ID     — OpenAI organization identifier (optional).
    OPENAI_PROJECT_ID — OpenAI project identifier (optional).

Package layout:
    openai_client.client      — :class:`OpenAIClient` (sync + async HTTP client)
    openai_client.auth        — :class:`OpenAIAuth` (httpx.Auth implementation)
    openai_client.exceptions  — Exception hierarchy

Version:
    This package targets **OpenAI API v2.3.0** with base URL
    ``https://api.openai.com/v1`` and covers 241 endpoints across 24 resources.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

__version__: str = "1.0.0"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

from openai_client.auth import OpenAIAuth

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

from openai_client.exceptions import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    UnprocessableEntityError,
)

# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

from openai_client.client import OpenAIClient

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # version
    "__version__",
    # auth
    "OpenAIAuth",
    # exceptions
    "APIConnectionError",
    "APITimeoutError",
    "AuthenticationError",
    "ConflictError",
    "InvalidRequestError",
    "NotFoundError",
    "OpenAIError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServerError",
    "UnprocessableEntityError",
    # client
    "OpenAIClient",
]
