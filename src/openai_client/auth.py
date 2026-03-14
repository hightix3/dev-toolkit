"""
auth.py — Authentication handler for the OpenAI API client.

Implements ``httpx.Auth`` so it can be plugged directly into any
``httpx.Client`` or ``httpx.AsyncClient`` instance::

    import httpx
    from openai_client.auth import OpenAIAuth

    auth = OpenAIAuth(api_key="sk-...")
    with httpx.Client(auth=auth) as client:
        resp = client.get("https://api.openai.com/v1/models")

The handler injects three headers on every outgoing request:

* ``Authorization: Bearer <api_key>``  (always)
* ``OpenAI-Organization: <org_id>``    (when *org_id* is set)
* ``OpenAI-Project: <project_id>``     (when *project_id* is set)
"""

from __future__ import annotations

import os
from typing import Generator

import httpx

__all__ = ["OpenAIAuth"]


class OpenAIAuth(httpx.Auth):
    """Bearer-token auth for the OpenAI REST API.

    Parameters
    ----------
    api_key:
        OpenAI secret key (``sk-…``).  Falls back to the
        ``OPENAI_API_KEY`` environment variable when *None*.
    org_id:
        Optional organisation identifier injected as
        ``OpenAI-Organization``.  Falls back to ``OPENAI_ORG_ID``.
    project_id:
        Optional project identifier injected as ``OpenAI-Project``.
        Falls back to ``OPENAI_PROJECT_ID``.

    Raises
    ------
    ValueError
        If no API key can be resolved from the argument or the
        environment.
    """

    #: HTTP header used to carry the bearer token.
    AUTH_HEADER: str = "Authorization"
    #: HTTP header used to carry the organisation identifier.
    ORG_HEADER: str = "OpenAI-Organization"
    #: HTTP header used to carry the project identifier.
    PROJECT_HEADER: str = "OpenAI-Project"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "No OpenAI API key provided.  Pass api_key= or set the "
                "OPENAI_API_KEY environment variable."
            )
        self._api_key: str = resolved_key
        self._org_id: str | None = org_id or os.environ.get("OPENAI_ORG_ID")
        self._project_id: str | None = project_id or os.environ.get(
            "OPENAI_PROJECT_ID"
        )

    # ------------------------------------------------------------------
    # httpx.Auth protocol
    # ------------------------------------------------------------------

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Inject authentication headers before the request is sent."""
        request.headers[self.AUTH_HEADER] = f"Bearer {self._api_key}"
        if self._org_id:
            request.headers[self.ORG_HEADER] = self._org_id
        if self._project_id:
            request.headers[self.PROJECT_HEADER] = self._project_id
        yield request

    # ------------------------------------------------------------------
    # Repr helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        masked = self._api_key[:8] + "…" if len(self._api_key) > 8 else "***"
        parts = [f"api_key={masked!r}"]
        if self._org_id:
            parts.append(f"org_id={self._org_id!r}")
        if self._project_id:
            parts.append(f"project_id={self._project_id!r}")
        return f"OpenAIAuth({', '.join(parts)})"
