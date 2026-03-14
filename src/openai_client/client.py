"""
client.py — Main OpenAI API client with all 241 endpoints.

Provides:
    * :class:`ResponseCache`  — TTL-based in-memory cache for GET responses.
    * :class:`OpenAIClient`   — Full-featured synchronous client wrapping every
                               OpenAI v1 endpoint, organised by resource group.

Usage::

    from openai_client.client import OpenAIClient

    client = OpenAIClient(api_key="sk-...")

    # Simple request
    models = client.list_models()

    # Streaming chat completion
    for chunk in client.create_chat_completion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello!"}],
        stream=True,
    ):
        print(chunk, end="", flush=True)

    # Auto-paginate assistants
    for assistant in client.auto_paginate("GET", "/assistants", params={"limit": 20}):
        print(assistant["id"])
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from pathlib import Path
from typing import Any, BinaryIO, Generator, Iterator

import httpx

from .auth import OpenAIAuth
from .exceptions import APIConnectionError, APITimeoutError, OpenAIError, RateLimitError, ServerError


# ---------------------------------------------------------------------------
# ResponseCache
# ---------------------------------------------------------------------------


class ResponseCache:
    """TTL-based in-memory cache for GET request responses.

    Keys are derived from a SHA-256 hash of the sorted query parameters
    (authentication headers are excluded so cache entries are not
    identity-specific beyond the client instance).

    Args:
        ttl: Time-to-live in seconds.  ``0`` (default) disables the cache
             entirely so every GET hits the network.

    Example::

        cache = ResponseCache(ttl=30)
        cache.set("abc123", {"data": []})
        value = cache.get("abc123")  # returns the dict within 30 s
    """

    def __init__(self, ttl: float = 0) -> None:
        self.ttl: float = ttl
        self._store: dict[str, tuple[Any, float]] = {}  # key -> (value, expires_at)

    # ------------------------------------------------------------------
    # Key construction
    # ------------------------------------------------------------------

    @staticmethod
    def build_key(url: str, params: dict[str, Any] | None = None) -> str:
        """Return a stable SHA-256 cache key for *url* + *params*.

        Args:
            url:    Full request URL (without query string).
            params: Mapping of query parameters.  Keys ``api_key`` and
                    ``Authorization`` are stripped before hashing.

        Returns:
            A 64-character hexadecimal SHA-256 digest string.
        """
        safe_params = {
            k: v
            for k, v in (params or {}).items()
            if k.lower() not in {"api_key", "authorization"}
        }
        payload = json.dumps({"url": url, "params": safe_params}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Return cached value for *key*, or ``None`` if absent/expired.

        Args:
            key: Cache key produced by :meth:`build_key`.

        Returns:
            The cached object, or ``None``.
        """
        if self.ttl <= 0:
            return None
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key* with the configured TTL.

        Args:
            key:   Cache key produced by :meth:`build_key`.
            value: JSON-serialisable response body to cache.
        """
        if self.ttl <= 0:
            return
        self._store[key] = (value, time.monotonic() + self.ttl)

    def clear(self) -> None:
        """Evict all entries from the cache."""
        self._store.clear()


# ---------------------------------------------------------------------------
# OpenAIClient
# ---------------------------------------------------------------------------


class OpenAIClient:
    """Synchronous client for the OpenAI REST API (all 241 endpoints).

    Args:
        api_key:         OpenAI API key.  Defaults to the ``OPENAI_API_KEY``
                         environment variable.
        org_id:          OpenAI organisation ID.  Defaults to ``OPENAI_ORG_ID``.
        project_id:      OpenAI project ID.  Defaults to ``OPENAI_PROJECT_ID``.
        base_url:        Root URL for API calls.
        max_retries:     Maximum number of retry attempts on 429/5xx responses.
        cache_ttl:       TTL (seconds) for the GET response cache.  ``0``
                         disables caching (default).
        timeout:         HTTP request timeout in seconds.
        default_headers: Extra headers merged into every request.

    Example::

        client = OpenAIClient(api_key="sk-...")
        with client:
            resp = client.list_models()
    """

    def __init__(
        self,
        api_key: str | None = None,
        org_id: str | None = None,
        project_id: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        max_retries: int = 3,
        cache_ttl: float = 0,
        timeout: float = 60.0,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        self._cache = ResponseCache(ttl=cache_ttl)
        self._default_headers: dict[str, str] = default_headers or {}

        self._auth = OpenAIAuth(api_key=api_key, org_id=org_id, project_id=project_id)
        self._client = httpx.Client(
            auth=self._auth,
            timeout=self.timeout,
            headers={"Content-Type": "application/json", **self._default_headers},
        )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> OpenAIClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    # ------------------------------------------------------------------
    # Core request machinery
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Execute an HTTP request with retry, caching, and error mapping.

        Args:
            method: HTTP verb (``"GET"``, ``"POST"``, etc.).
            path:   API path relative to *base_url* (e.g. ``"/models"``).
            stream: When ``True``, return the raw :class:`httpx.Response` for
                    streaming without reading the body.
            **kwargs: Passed directly to :meth:`httpx.Client.request`.
                      Common keys: ``json``, ``params``, ``data``, ``files``,
                      ``headers``, ``content``.

        Returns:
            Parsed JSON response (``dict`` or ``list``), raw bytes for binary
            endpoints, or the streaming :class:`httpx.Response` when
            ``stream=True``.

        Raises:
            :class:`~openai_client.exceptions.OpenAIError`: Any API error.
            :class:`~openai_client.exceptions.APIConnectionError`: Network failure.
            :class:`~openai_client.exceptions.APITimeoutError`: Request timeout.
        """
        url = f"{self.base_url}{path}"

        # Cache check for GET requests
        if method.upper() == "GET" and not stream:
            cache_key = ResponseCache.build_key(url, kwargs.get("params"))
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        attempt = 0
        last_exc: Exception | None = None

        while attempt <= self.max_retries:
            try:
                if stream:
                    response = self._client.request(method, url, **kwargs)
                    if response.status_code >= 400:
                        response.read()
                        raise OpenAIError.from_response(response)
                    return response

                response = self._client.request(method, url, **kwargs)

                # Successful or client error (no retry for 4xx except 429)
                if response.status_code < 400:
                    # Return raw bytes for binary content endpoints
                    content_type = response.headers.get("content-type", "")
                    if "application/json" in content_type or content_type == "":
                        try:
                            result = response.json()
                        except Exception:
                            result = response.content
                    else:
                        result = response.content

                    if method.upper() == "GET":
                        cache_key = ResponseCache.build_key(url, kwargs.get("params"))
                        self._cache.set(cache_key, result)

                    return result

                # 429 — rate limited
                if response.status_code == 429:
                    if attempt >= self.max_retries:
                        raise OpenAIError.from_response(response)
                    retry_after_raw = response.headers.get("retry-after")
                    if retry_after_raw is not None:
                        try:
                            wait = float(retry_after_raw)
                        except ValueError:
                            wait = self._backoff(attempt)
                    else:
                        # Fall back to x-ratelimit-reset-requests header
                        reset_raw = response.headers.get("x-ratelimit-reset-requests")
                        if reset_raw is not None and reset_raw.endswith("s"):
                            try:
                                wait = float(reset_raw[:-1])
                            except ValueError:
                                wait = self._backoff(attempt)
                        else:
                            wait = self._backoff(attempt)
                    time.sleep(wait)
                    attempt += 1
                    continue

                # 5xx — transient server error
                if response.status_code >= 500:
                    if attempt >= self.max_retries:
                        raise OpenAIError.from_response(response)
                    time.sleep(self._backoff(attempt))
                    attempt += 1
                    continue

                # Other 4xx — raise immediately
                raise OpenAIError.from_response(response)

            except httpx.TimeoutException as exc:
                last_exc = APITimeoutError() if attempt >= self.max_retries else exc
                if attempt >= self.max_retries:
                    raise APITimeoutError() from exc
                time.sleep(self._backoff(attempt))
                attempt += 1
                continue

            except httpx.RequestError as exc:
                raise APIConnectionError(str(exc)) from exc

        # Should not reach here, but safety net
        if last_exc is not None:
            raise last_exc
        raise APIConnectionError("Exceeded maximum retries without a response.")

    @staticmethod
    def _backoff(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
        """Return exponential backoff with full jitter.

        Args:
            attempt: Zero-indexed retry attempt number.
            base:    Base wait time in seconds.
            cap:     Maximum wait time in seconds.

        Returns:
            A randomised sleep duration in seconds.
        """
        ceiling = min(cap, base * (2 ** attempt))
        return random.uniform(0, ceiling)

    # ------------------------------------------------------------------
    # Convenience HTTP methods
    # ------------------------------------------------------------------

    def get(self, path: str, **kwargs: Any) -> Any:
        """Perform a GET request.

        Args:
            path:    API path relative to *base_url*.
            **kwargs: Passed to :meth:`_request` (e.g. ``params``).
        """
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        """Perform a POST request.

        Args:
            path:    API path relative to *base_url*.
            **kwargs: Passed to :meth:`_request` (e.g. ``json``, ``data``).
        """
        return self._request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        """Perform a PUT request.

        Args:
            path:    API path relative to *base_url*.
            **kwargs: Passed to :meth:`_request`.
        """
        return self._request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        """Perform a PATCH request.

        Args:
            path:    API path relative to *base_url*.
            **kwargs: Passed to :meth:`_request`.
        """
        return self._request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        """Perform a DELETE request.

        Args:
            path:    API path relative to *base_url*.
            **kwargs: Passed to :meth:`_request`.
        """
        return self._request("DELETE", path, **kwargs)

    # ------------------------------------------------------------------
    # Streaming helper
    # ------------------------------------------------------------------

    def _stream_request(
        self, method: str, path: str, **kwargs: Any
    ) -> Generator[str, None, None]:
        """Yield Server-Sent Event data lines from a streaming endpoint.

        The caller should pass ``json={"stream": True, ...}`` in *kwargs*.
        Each yielded string is a raw SSE ``data:`` value (not yet parsed).

        Args:
            method: HTTP verb.
            path:   API path relative to *base_url*.
            **kwargs: Passed to :meth:`_request`.

        Yields:
            Raw SSE ``data:`` payload strings (strip ``"data: "`` prefix).
        """
        response: httpx.Response = self._request(method, path, stream=True, **kwargs)
        with response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data = line[len("data: "):]
                    if data.strip() == "[DONE]":
                        return
                    yield data

    # ------------------------------------------------------------------
    # Auto-pagination
    # ------------------------------------------------------------------

    def auto_paginate(
        self,
        method: str,
        path: str,
        *,
        limit: int = 20,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """Transparently cursor-paginate a list endpoint.

        Iterates until ``has_more`` is ``False``, automatically advancing
        the ``after`` cursor using the ``last_id`` from each response page.

        Args:
            method: HTTP verb (typically ``"GET"``).
            path:   API path relative to *base_url*.
            limit:  Number of items per page.
            params: Base query parameters merged with pagination params.
            **kwargs: Extra keyword arguments forwarded to :meth:`_request`.

        Yields:
            Individual items from each page's ``data`` array.

        Example::

            for assistant in client.auto_paginate("GET", "/assistants"):
                print(assistant["id"])
        """
        query: dict[str, Any] = dict(params or {})
        query["limit"] = limit
        after: str | None = None

        while True:
            if after:
                query["after"] = after
            response = self._request(method, path, params=query, **kwargs)
            items = response.get("data", [])
            yield from items
            if not response.get("has_more", False):
                break
            after = response.get("last_id")
            if not after:
                break

    # ==================================================================
    # ===  RESOURCE METHODS  ===========================================
    # ==================================================================

    # ------------------------------------------------------------------
    # Assistants (5)
    # ------------------------------------------------------------------

    def list_assistants(
        self,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
        order: str | None = None,
    ) -> dict[str, Any]:
        """Return a paginated list of assistants.

        Args:
            limit:  Number of assistants to return (max 100).
            after:  Cursor for forward pagination (object ID).
            before: Cursor for backward pagination (object ID).
            order:  Sort order — ``"asc"`` or ``"desc"`` (default).

        Returns:
            Dict with ``data`` (list), ``has_more``, ``first_id``, ``last_id``.
        """
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before
        if order is not None:
            params["order"] = order
        return self.get("/assistants", params=params)

    def create_assistant(self, **kwargs: Any) -> dict[str, Any]:
        """Create an assistant with a model and instructions.

        Args:
            **kwargs: Request body fields such as ``model``, ``name``,
                      ``description``, ``instructions``, ``tools``,
                      ``tool_resources``, ``metadata``, ``temperature``,
                      ``top_p``, ``response_format``.

        Returns:
            The created assistant object.
        """
        return self.post("/assistants", json=kwargs)

    def get_assistant(self, assistant_id: str) -> dict[str, Any]:
        """Retrieve an assistant by ID.

        Args:
            assistant_id: The ID of the assistant to retrieve.

        Returns:
            The assistant object.
        """
        return self.get(f"/assistants/{assistant_id}")

    def update_assistant(self, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update an existing assistant.

        Args:
            assistant_id: The ID of the assistant to modify.
            **kwargs:     Fields to update: ``model``, ``name``,
                          ``description``, ``instructions``, ``tools``,
                          ``tool_resources``, ``metadata``, ``temperature``,
                          ``top_p``, ``response_format``.

        Returns:
            The updated assistant object.
        """
        return self.post(f"/assistants/{assistant_id}", json=kwargs)

    def delete_assistant(self, assistant_id: str) -> dict[str, Any]:
        """Delete an assistant.

        Args:
            assistant_id: The ID of the assistant to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/assistants/{assistant_id}")

    # ------------------------------------------------------------------
    # Audio (9)
    # ------------------------------------------------------------------

    def create_speech(
        self,
        model: str,
        input: str,
        voice: str,
        **kwargs: Any,
    ) -> bytes:
        """Generate audio from text (text-to-speech).

        Args:
            model:    TTS model ID (e.g. ``"tts-1"``, ``"tts-1-hd"``).
            input:    The text to synthesise (max 4096 characters).
            voice:    Voice name (e.g. ``"alloy"``, ``"echo"``, ``"fable"``,
                      ``"onyx"``, ``"nova"``, ``"shimmer"``).
            **kwargs: Optional fields: ``response_format`` (``"mp3"`` default),
                      ``speed`` (0.25–4.0).

        Returns:
            Raw audio bytes.
        """
        payload = {"model": model, "input": input, "voice": voice, **kwargs}
        return self.post("/audio/speech", json=payload)

    def create_transcription(
        self,
        file: str | Path | BinaryIO,
        model: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Transcribe audio into text.

        Args:
            file:     Audio file — path string, :class:`pathlib.Path`, or
                      file-like object opened in binary mode.
            model:    Whisper model ID (e.g. ``"whisper-1"``).
            **kwargs: Optional: ``language``, ``prompt``,
                      ``response_format`` (``"json"``/``"text"``/``"srt"``/
                      ``"verbose_json"``/``"vtt"``), ``temperature``,
                      ``timestamp_granularities``.

        Returns:
            Transcription object (or plain text depending on
            ``response_format``).
        """
        files, data = _prepare_file_upload(file, "file", kwargs)
        data["model"] = model
        return self.post("/audio/transcriptions", files=files, data=data)

    def create_translation(
        self,
        file: str | Path | BinaryIO,
        model: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Translate audio into English text.

        Args:
            file:     Audio file — path string, :class:`pathlib.Path`, or
                      file-like object opened in binary mode.
            model:    Whisper model ID (e.g. ``"whisper-1"``).
            **kwargs: Optional: ``prompt``, ``response_format``,
                      ``temperature``.

        Returns:
            Translation object.
        """
        files, data = _prepare_file_upload(file, "file", kwargs)
        data["model"] = model
        return self.post("/audio/translations", files=files, data=data)

    def create_voice_consent(self, **kwargs: Any) -> dict[str, Any]:
        """Create a voice consent record.

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            The created voice consent object.
        """
        return self.post("/audio/voice_consents", json=kwargs)

    def list_voice_consents(self, **kwargs: Any) -> dict[str, Any]:
        """List voice consent records.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/audio/voice_consents", params=kwargs if kwargs else None)

    def get_voice_consent(self, consent_id: str) -> dict[str, Any]:
        """Retrieve a voice consent record.

        Args:
            consent_id: The ID of the voice consent to retrieve.

        Returns:
            The voice consent object.
        """
        return self.get(f"/audio/voice_consents/{consent_id}")

    def update_voice_consent(self, consent_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update a voice consent record.

        Args:
            consent_id: The ID of the voice consent to update.
            **kwargs:   Fields to update.

        Returns:
            The updated voice consent object.
        """
        return self.post(f"/audio/voice_consents/{consent_id}", json=kwargs)

    def delete_voice_consent(self, consent_id: str) -> dict[str, Any]:
        """Delete a voice consent record.

        Args:
            consent_id: The ID of the voice consent to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/audio/voice_consents/{consent_id}")

    def create_voice(self, **kwargs: Any) -> dict[str, Any]:
        """Create a custom voice.

        Args:
            **kwargs: Request body fields required by the API.

        Returns:
            The created voice object.
        """
        return self.post("/audio/voices", json=kwargs)

    # ------------------------------------------------------------------
    # Batches (4)
    # ------------------------------------------------------------------

    def create_batch(
        self,
        input_file_id: str,
        endpoint: str,
        completion_window: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create and execute a batch of API requests.

        Args:
            input_file_id:     The ID of a previously uploaded ``.jsonl`` file.
            endpoint:          API endpoint for the batch (e.g.
                               ``"/v1/chat/completions"``).
            completion_window: Processing window — ``"24h"`` is currently the
                               only supported value.
            **kwargs:          Optional: ``metadata``.

        Returns:
            The batch object.
        """
        payload = {
            "input_file_id": input_file_id,
            "endpoint": endpoint,
            "completion_window": completion_window,
            **kwargs,
        }
        return self.post("/batches", json=payload)

    def list_batches(
        self,
        limit: int | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        """List your organisation's batches.

        Args:
            limit: Max number of batches to return.
            after: Cursor for forward pagination.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if after is not None:
            params["after"] = after
        return self.get("/batches", params=params if params else None)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        """Retrieve a batch.

        Args:
            batch_id: The ID of the batch.

        Returns:
            The batch object.
        """
        return self.get(f"/batches/{batch_id}")

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        """Cancel an in-progress batch.

        Args:
            batch_id: The ID of the batch to cancel.

        Returns:
            The batch object with updated status.
        """
        return self.post(f"/batches/{batch_id}/cancel")

    # ------------------------------------------------------------------
    # Chat (6)
    # ------------------------------------------------------------------

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any] | Generator[str, None, None]:
        """Create a chat completion.

        Args:
            model:    Model ID (e.g. ``"gpt-4o"``).
            messages: List of message dicts with ``role`` and ``content``.
            **kwargs: Optional: ``temperature``, ``top_p``, ``n``,
                      ``max_tokens``, ``max_completion_tokens``, ``stop``,
                      ``presence_penalty``, ``frequency_penalty``, ``logit_bias``,
                      ``user``, ``tools``, ``tool_choice``, ``response_format``,
                      ``seed``, ``logprobs``, ``top_logprobs``,
                      ``stream`` (:class:`bool`).

        Returns:
            Chat completion object, or a generator of SSE data strings when
            ``stream=True`` is passed in *kwargs*.
        """
        stream = kwargs.pop("stream", False)
        payload = {"model": model, "messages": messages, **kwargs}
        if stream:
            payload["stream"] = True
            return self._stream_request("POST", "/chat/completions", json=payload)
        return self.post("/chat/completions", json=payload)

    def list_chat_completions(self, **kwargs: Any) -> dict[str, Any]:
        """List stored chat completions.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``,
                      ``model``, ``metadata``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/chat/completions", params=kwargs if kwargs else None)

    def get_chat_completion(self, completion_id: str) -> dict[str, Any]:
        """Retrieve a stored chat completion.

        Args:
            completion_id: The ID of the chat completion.

        Returns:
            The chat completion object.
        """
        return self.get(f"/chat/completions/{completion_id}")

    def update_chat_completion(
        self, completion_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update a stored chat completion's metadata.

        Args:
            completion_id: The ID of the chat completion to update.
            **kwargs:      Fields to update (e.g. ``metadata``).

        Returns:
            The updated chat completion object.
        """
        return self.post(f"/chat/completions/{completion_id}", json=kwargs)

    def delete_chat_completion(self, completion_id: str) -> dict[str, Any]:
        """Delete a stored chat completion.

        Args:
            completion_id: The ID of the chat completion to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/chat/completions/{completion_id}")

    def list_chat_completion_messages(
        self, completion_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List messages for a stored chat completion.

        Args:
            completion_id: The ID of the chat completion.
            **kwargs:      Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/chat/completions/{completion_id}/messages",
            params=kwargs if kwargs else None,
        )

    # ------------------------------------------------------------------
    # Chatkit (6)
    # ------------------------------------------------------------------

    def create_chatkit_session(self, **kwargs: Any) -> dict[str, Any]:
        """Create a Chatkit session.

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            The created session object.
        """
        return self.post("/chatkit/sessions", json=kwargs)

    def cancel_chatkit_session(self, session_id: str) -> dict[str, Any]:
        """Cancel a Chatkit session.

        Args:
            session_id: The ID of the session to cancel.

        Returns:
            The updated session object.
        """
        return self.post(f"/chatkit/sessions/{session_id}/cancel")

    def list_chatkit_threads(self, **kwargs: Any) -> dict[str, Any]:
        """List Chatkit threads.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/chatkit/threads", params=kwargs if kwargs else None)

    def get_chatkit_thread(self, thread_id: str) -> dict[str, Any]:
        """Retrieve a Chatkit thread.

        Args:
            thread_id: The ID of the thread.

        Returns:
            The thread object.
        """
        return self.get(f"/chatkit/threads/{thread_id}")

    def delete_chatkit_thread(self, thread_id: str) -> dict[str, Any]:
        """Delete a Chatkit thread.

        Args:
            thread_id: The ID of the thread to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/chatkit/threads/{thread_id}")

    def list_chatkit_thread_items(
        self, thread_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List items in a Chatkit thread.

        Args:
            thread_id: The ID of the thread.
            **kwargs:  Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/chatkit/threads/{thread_id}/items",
            params=kwargs if kwargs else None,
        )

    # ------------------------------------------------------------------
    # Completions (1)
    # ------------------------------------------------------------------

    def create_completion(
        self,
        model: str,
        prompt: str | list[str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a legacy text completion.

        Args:
            model:    Model ID (e.g. ``"gpt-3.5-turbo-instruct"``).
            prompt:   Prompt string or list of strings.
            **kwargs: Optional: ``suffix``, ``max_tokens``, ``temperature``,
                      ``top_p``, ``n``, ``stream``, ``logprobs``, ``echo``,
                      ``stop``, ``presence_penalty``, ``frequency_penalty``,
                      ``best_of``, ``logit_bias``, ``user``, ``seed``.

        Returns:
            Completion object.
        """
        payload = {"model": model, "prompt": prompt, **kwargs}
        return self.post("/completions", json=payload)

    # ------------------------------------------------------------------
    # Containers (9)
    # ------------------------------------------------------------------

    def list_containers(self, **kwargs: Any) -> dict[str, Any]:
        """List containers.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/containers", params=kwargs if kwargs else None)

    def create_container(self, **kwargs: Any) -> dict[str, Any]:
        """Create a container.

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            The created container object.
        """
        return self.post("/containers", json=kwargs)

    def get_container(self, container_id: str) -> dict[str, Any]:
        """Retrieve a container.

        Args:
            container_id: The ID of the container.

        Returns:
            The container object.
        """
        return self.get(f"/containers/{container_id}")

    def delete_container(self, container_id: str) -> dict[str, Any]:
        """Delete a container.

        Args:
            container_id: The ID of the container to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/containers/{container_id}")

    def create_container_file(
        self, container_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Upload a file to a container.

        Args:
            container_id: The ID of the container.
            **kwargs:     Request body fields (e.g. ``file``, ``filename``).

        Returns:
            The created container file object.
        """
        return self.post(f"/containers/{container_id}/files", json=kwargs)

    def list_container_files(
        self, container_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List files in a container.

        Args:
            container_id: The ID of the container.
            **kwargs:     Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/containers/{container_id}/files",
            params=kwargs if kwargs else None,
        )

    def get_container_file(
        self, container_id: str, file_id: str
    ) -> dict[str, Any]:
        """Retrieve a file in a container.

        Args:
            container_id: The ID of the container.
            file_id:      The ID of the file.

        Returns:
            The container file object.
        """
        return self.get(f"/containers/{container_id}/files/{file_id}")

    def delete_container_file(
        self, container_id: str, file_id: str
    ) -> dict[str, Any]:
        """Delete a file from a container.

        Args:
            container_id: The ID of the container.
            file_id:      The ID of the file to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/containers/{container_id}/files/{file_id}")

    def get_container_file_content(
        self, container_id: str, file_id: str
    ) -> bytes:
        """Retrieve the content of a container file.

        Args:
            container_id: The ID of the container.
            file_id:      The ID of the file.

        Returns:
            Raw file bytes.
        """
        return self.get(f"/containers/{container_id}/files/{file_id}/content")

    # ------------------------------------------------------------------
    # Conversations (8)
    # ------------------------------------------------------------------

    def create_conversation(self, **kwargs: Any) -> dict[str, Any]:
        """Create a new conversation.

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            The created conversation object.
        """
        return self.post("/conversations", json=kwargs)

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Retrieve a conversation.

        Args:
            conversation_id: The ID of the conversation.

        Returns:
            The conversation object.
        """
        return self.get(f"/conversations/{conversation_id}")

    def delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Delete a conversation.

        Args:
            conversation_id: The ID of the conversation to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/conversations/{conversation_id}")

    def update_conversation(
        self, conversation_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update a conversation.

        Args:
            conversation_id: The ID of the conversation.
            **kwargs:        Fields to update (e.g. ``metadata``).

        Returns:
            The updated conversation object.
        """
        return self.post(f"/conversations/{conversation_id}", json=kwargs)

    def create_conversation_item(
        self, conversation_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Add an item to a conversation.

        Args:
            conversation_id: The ID of the conversation.
            **kwargs:        Request body fields (e.g. ``role``, ``content``).

        Returns:
            The created item object.
        """
        return self.post(f"/conversations/{conversation_id}/items", json=kwargs)

    def list_conversation_items(
        self, conversation_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List items in a conversation.

        Args:
            conversation_id: The ID of the conversation.
            **kwargs:        Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/conversations/{conversation_id}/items",
            params=kwargs if kwargs else None,
        )

    def get_conversation_item(
        self, conversation_id: str, item_id: str
    ) -> dict[str, Any]:
        """Retrieve a single item in a conversation.

        Args:
            conversation_id: The ID of the conversation.
            item_id:         The ID of the item.

        Returns:
            The conversation item object.
        """
        return self.get(f"/conversations/{conversation_id}/items/{item_id}")

    def delete_conversation_item(
        self, conversation_id: str, item_id: str
    ) -> dict[str, Any]:
        """Delete an item from a conversation.

        Args:
            conversation_id: The ID of the conversation.
            item_id:         The ID of the item to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/conversations/{conversation_id}/items/{item_id}")

    # ------------------------------------------------------------------
    # Embeddings (1)
    # ------------------------------------------------------------------

    def create_embedding(
        self,
        model: str,
        input: str | list[str] | list[int] | list[list[int]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create embeddings for the provided text.

        Args:
            model:    Embedding model ID (e.g. ``"text-embedding-3-small"``).
            input:    Text string, list of strings, or pre-tokenised integer
                      lists to embed.
            **kwargs: Optional: ``encoding_format`` (``"float"``/``"base64"``),
                      ``dimensions``, ``user``.

        Returns:
            Embedding response with ``data`` list containing vector objects.
        """
        payload = {"model": model, "input": input, **kwargs}
        return self.post("/embeddings", json=payload)

    # ------------------------------------------------------------------
    # Evals (12)
    # ------------------------------------------------------------------

    def list_evals(self, **kwargs: Any) -> dict[str, Any]:
        """List evaluations.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/evals", params=kwargs if kwargs else None)

    def create_eval(self, **kwargs: Any) -> dict[str, Any]:
        """Create a new evaluation.

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            The created eval object.
        """
        return self.post("/evals", json=kwargs)

    def get_eval(self, eval_id: str) -> dict[str, Any]:
        """Retrieve an evaluation.

        Args:
            eval_id: The ID of the evaluation.

        Returns:
            The eval object.
        """
        return self.get(f"/evals/{eval_id}")

    def update_eval(self, eval_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update an evaluation.

        Args:
            eval_id:  The ID of the evaluation.
            **kwargs: Fields to update.

        Returns:
            The updated eval object.
        """
        return self.post(f"/evals/{eval_id}", json=kwargs)

    def delete_eval(self, eval_id: str) -> dict[str, Any]:
        """Delete an evaluation.

        Args:
            eval_id: The ID of the evaluation to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/evals/{eval_id}")

    def list_eval_runs(self, eval_id: str, **kwargs: Any) -> dict[str, Any]:
        """List runs for an evaluation.

        Args:
            eval_id:  The ID of the evaluation.
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(f"/evals/{eval_id}/runs", params=kwargs if kwargs else None)

    def create_eval_run(self, eval_id: str, **kwargs: Any) -> dict[str, Any]:
        """Create a run for an evaluation.

        Args:
            eval_id:  The ID of the evaluation.
            **kwargs: Request body fields as required by the API.

        Returns:
            The created eval run object.
        """
        return self.post(f"/evals/{eval_id}/runs", json=kwargs)

    def get_eval_run(self, eval_id: str, run_id: str) -> dict[str, Any]:
        """Retrieve an eval run.

        Args:
            eval_id: The ID of the evaluation.
            run_id:  The ID of the run.

        Returns:
            The eval run object.
        """
        return self.get(f"/evals/{eval_id}/runs/{run_id}")

    def update_eval_run(
        self, eval_id: str, run_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update an eval run.

        Args:
            eval_id:  The ID of the evaluation.
            run_id:   The ID of the run.
            **kwargs: Fields to update.

        Returns:
            The updated eval run object.
        """
        return self.post(f"/evals/{eval_id}/runs/{run_id}", json=kwargs)

    def delete_eval_run(self, eval_id: str, run_id: str) -> dict[str, Any]:
        """Delete an eval run.

        Args:
            eval_id: The ID of the evaluation.
            run_id:  The ID of the run to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/evals/{eval_id}/runs/{run_id}")

    def list_eval_run_output_items(
        self, eval_id: str, run_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List output items for an eval run.

        Args:
            eval_id:  The ID of the evaluation.
            run_id:   The ID of the run.
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/evals/{eval_id}/runs/{run_id}/output_items",
            params=kwargs if kwargs else None,
        )

    def get_eval_run_output_item(
        self, eval_id: str, run_id: str, output_item_id: str
    ) -> dict[str, Any]:
        """Retrieve a single output item from an eval run.

        Args:
            eval_id:        The ID of the evaluation.
            run_id:         The ID of the run.
            output_item_id: The ID of the output item.

        Returns:
            The output item object.
        """
        return self.get(f"/evals/{eval_id}/runs/{run_id}/output_items/{output_item_id}")

    # ------------------------------------------------------------------
    # Files (5)
    # ------------------------------------------------------------------

    def list_files(
        self,
        purpose: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """List uploaded files.

        Args:
            purpose:  Filter by purpose (e.g. ``"fine-tune"``,
                      ``"assistants"``).
            **kwargs: Additional query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        params: dict[str, Any] = {}
        if purpose is not None:
            params["purpose"] = purpose
        params.update(kwargs)
        return self.get("/files", params=params if params else None)

    def upload_file(
        self,
        file: str | Path | BinaryIO,
        purpose: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Upload a file for use across API endpoints.

        Args:
            file:     File to upload — path string, :class:`pathlib.Path`, or
                      file-like object opened in binary mode.
            purpose:  Intended use (``"fine-tune"``, ``"assistants"``,
                      ``"batch"``, ``"vision"``).
            **kwargs: Additional form fields.

        Returns:
            The file object.
        """
        files, data = _prepare_file_upload(file, "file", kwargs)
        data["purpose"] = purpose
        return self.post("/files", files=files, data=data)

    def delete_file(self, file_id: str) -> dict[str, Any]:
        """Delete a file.

        Args:
            file_id: The ID of the file to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/files/{file_id}")

    def get_file(self, file_id: str) -> dict[str, Any]:
        """Retrieve file metadata.

        Args:
            file_id: The ID of the file.

        Returns:
            The file object.
        """
        return self.get(f"/files/{file_id}")

    def get_file_content(self, file_id: str) -> bytes:
        """Retrieve the content of a file.

        Args:
            file_id: The ID of the file.

        Returns:
            Raw file bytes.
        """
        return self.get(f"/files/{file_id}/content")

    # ------------------------------------------------------------------
    # Fine-tuning (13)
    # ------------------------------------------------------------------

    def create_fine_tuning_job(
        self,
        model: str,
        training_file: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a fine-tuning job.

        Args:
            model:         Base model to fine-tune (e.g. ``"gpt-4o-mini"``).
            training_file: File ID of the training data (JSONL).
            **kwargs:      Optional: ``validation_file``, ``hyperparameters``,
                           ``suffix``, ``integrations``, ``seed``,
                           ``method``.

        Returns:
            The fine-tuning job object.
        """
        payload = {"model": model, "training_file": training_file, **kwargs}
        return self.post("/fine_tuning/jobs", json=payload)

    def list_fine_tuning_jobs(
        self,
        limit: int | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        """List fine-tuning jobs.

        Args:
            limit: Max number of jobs to return.
            after: Cursor for forward pagination.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if after is not None:
            params["after"] = after
        return self.get("/fine_tuning/jobs", params=params if params else None)

    def get_fine_tuning_job(self, job_id: str) -> dict[str, Any]:
        """Retrieve a fine-tuning job.

        Args:
            job_id: The ID of the fine-tuning job.

        Returns:
            The fine-tuning job object.
        """
        return self.get(f"/fine_tuning/jobs/{job_id}")

    def cancel_fine_tuning_job(self, job_id: str) -> dict[str, Any]:
        """Cancel a fine-tuning job.

        Args:
            job_id: The ID of the fine-tuning job to cancel.

        Returns:
            The fine-tuning job object with updated status.
        """
        return self.post(f"/fine_tuning/jobs/{job_id}/cancel")

    def list_fine_tuning_checkpoints(
        self, job_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List checkpoints for a fine-tuning job.

        Args:
            job_id:   The ID of the fine-tuning job.
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/fine_tuning/jobs/{job_id}/checkpoints",
            params=kwargs if kwargs else None,
        )

    def list_fine_tuning_events(
        self, job_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List events for a fine-tuning job.

        Args:
            job_id:   The ID of the fine-tuning job.
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/fine_tuning/jobs/{job_id}/events",
            params=kwargs if kwargs else None,
        )

    def pause_fine_tuning_job(self, job_id: str) -> dict[str, Any]:
        """Pause a running fine-tuning job.

        Args:
            job_id: The ID of the fine-tuning job to pause.

        Returns:
            The fine-tuning job object with updated status.
        """
        return self.post(f"/fine_tuning/jobs/{job_id}/pause")

    def resume_fine_tuning_job(self, job_id: str) -> dict[str, Any]:
        """Resume a paused fine-tuning job.

        Args:
            job_id: The ID of the fine-tuning job to resume.

        Returns:
            The fine-tuning job object with updated status.
        """
        return self.post(f"/fine_tuning/jobs/{job_id}/resume")

    def run_grader(self, **kwargs: Any) -> dict[str, Any]:
        """Run a grader (alpha).

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            Grader run result object.
        """
        return self.post("/fine_tuning/alpha/graders/run", json=kwargs)

    def validate_grader(self, **kwargs: Any) -> dict[str, Any]:
        """Validate a grader configuration (alpha).

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            Grader validation result object.
        """
        return self.post("/fine_tuning/alpha/graders/validate", json=kwargs)

    def list_checkpoint_permissions(
        self, fine_tuned_model_checkpoint: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List permissions for a fine-tuned model checkpoint.

        Args:
            fine_tuned_model_checkpoint: The model checkpoint identifier.
            **kwargs:                    Query parameters such as ``limit``,
                                         ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/fine_tuning/checkpoints/{fine_tuned_model_checkpoint}/permissions",
            params=kwargs if kwargs else None,
        )

    def create_checkpoint_permission(
        self, fine_tuned_model_checkpoint: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Create a permission for a fine-tuned model checkpoint.

        Args:
            fine_tuned_model_checkpoint: The model checkpoint identifier.
            **kwargs:                    Request body fields (e.g. ``project_ids``).

        Returns:
            The created permission object.
        """
        return self.post(
            f"/fine_tuning/checkpoints/{fine_tuned_model_checkpoint}/permissions",
            json=kwargs,
        )

    def delete_checkpoint_permission(
        self, fine_tuned_model_checkpoint: str, permission_id: str
    ) -> dict[str, Any]:
        """Delete a permission for a fine-tuned model checkpoint.

        Args:
            fine_tuned_model_checkpoint: The model checkpoint identifier.
            permission_id:               The ID of the permission to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(
            f"/fine_tuning/checkpoints/{fine_tuned_model_checkpoint}/permissions/{permission_id}"
        )

    # ------------------------------------------------------------------
    # Images (3)
    # ------------------------------------------------------------------

    def generate_image(
        self,
        model: str,
        prompt: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate images from a text prompt.

        Args:
            model:    Image model ID (e.g. ``"dall-e-3"``).
            prompt:   Text description of the desired image.
            **kwargs: Optional: ``n``, ``size``, ``quality``,
                      ``response_format`` (``"url"``/``"b64_json"``),
                      ``style``, ``user``.

        Returns:
            Image generation response with ``data`` list of image objects.
        """
        payload = {"model": model, "prompt": prompt, **kwargs}
        return self.post("/images/generations", json=payload)

    def edit_image(
        self,
        image: str | Path | BinaryIO,
        prompt: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create an edited version of an image.

        Args:
            image:    Source image — path, :class:`pathlib.Path`, or binary
                      file-like object (PNG, < 4 MB).
            prompt:   Description of the desired edit.
            **kwargs: Optional: ``mask`` (file), ``model``, ``n``, ``size``,
                      ``response_format``, ``user``.

        Returns:
            Image edit response with ``data`` list of image objects.
        """
        files, data = _prepare_file_upload(image, "image", kwargs)
        data["prompt"] = prompt
        # Handle optional mask upload
        mask = kwargs.pop("mask", None)
        if mask is not None:
            mask_files, _ = _prepare_file_upload(mask, "mask", {})
            files.update(mask_files)
        return self.post("/images/edits", files=files, data=data)

    def create_image_variation(
        self,
        image: str | Path | BinaryIO,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a variation of a given image.

        Args:
            image:    Source image — path, :class:`pathlib.Path`, or binary
                      file-like object (PNG, < 4 MB).
            **kwargs: Optional: ``model``, ``n``, ``response_format``,
                      ``size``, ``user``.

        Returns:
            Image variation response with ``data`` list of image objects.
        """
        files, data = _prepare_file_upload(image, "image", kwargs)
        return self.post("/images/variations", files=files, data=data)

    # ------------------------------------------------------------------
    # Models (3)
    # ------------------------------------------------------------------

    def list_models(self) -> dict[str, Any]:
        """List available models.

        Returns:
            Dict with ``data`` list of model objects.
        """
        return self.get("/models")

    def get_model(self, model: str) -> dict[str, Any]:
        """Retrieve a model.

        Args:
            model: The model ID (e.g. ``"gpt-4o"``).

        Returns:
            The model object.
        """
        return self.get(f"/models/{model}")

    def delete_model(self, model: str) -> dict[str, Any]:
        """Delete a fine-tuned model.

        Args:
            model: The model ID of the fine-tuned model to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/models/{model}")

    # ------------------------------------------------------------------
    # Moderations (1)
    # ------------------------------------------------------------------

    def create_moderation(
        self,
        model: str,
        input: str | list[str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Classify text for policy compliance.

        Args:
            model:    Moderation model ID (e.g. ``"omni-moderation-latest"``).
            input:    Text string or list of strings to classify.
            **kwargs: Additional request body fields.

        Returns:
            Moderation response with ``results`` list.
        """
        payload = {"model": model, "input": input, **kwargs}
        return self.post("/moderations", json=payload)

    # ------------------------------------------------------------------
    # Organization — Admin API Keys (4)
    # ------------------------------------------------------------------

    def list_admin_api_keys(self, **kwargs: Any) -> dict[str, Any]:
        """List admin API keys.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/organization/admin_api_keys", params=kwargs if kwargs else None)

    def create_admin_api_key(self, **kwargs: Any) -> dict[str, Any]:
        """Create an admin API key.

        Args:
            **kwargs: Request body fields (e.g. ``name``).

        Returns:
            The created admin API key object.
        """
        return self.post("/organization/admin_api_keys", json=kwargs)

    def get_admin_api_key(self, key_id: str) -> dict[str, Any]:
        """Retrieve an admin API key.

        Args:
            key_id: The ID of the admin API key.

        Returns:
            The admin API key object.
        """
        return self.get(f"/organization/admin_api_keys/{key_id}")

    def delete_admin_api_key(self, key_id: str) -> dict[str, Any]:
        """Delete an admin API key.

        Args:
            key_id: The ID of the admin API key to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/admin_api_keys/{key_id}")

    # ------------------------------------------------------------------
    # Organization — Audit Logs (1)
    # ------------------------------------------------------------------

    def list_audit_logs(self, **kwargs: Any) -> dict[str, Any]:
        """List audit log events.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``,
                      ``event_types``, ``actor_ids``, ``resource_ids``,
                      ``effective_at``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/organization/audit_logs", params=kwargs if kwargs else None)

    # ------------------------------------------------------------------
    # Organization — Certificates (7)
    # ------------------------------------------------------------------

    def list_certificates(self, **kwargs: Any) -> dict[str, Any]:
        """List organisation certificates.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/organization/certificates", params=kwargs if kwargs else None)

    def create_certificate(self, **kwargs: Any) -> dict[str, Any]:
        """Upload an organisation certificate.

        Args:
            **kwargs: Request body fields (e.g. ``name``, ``certificate``).

        Returns:
            The created certificate object.
        """
        return self.post("/organization/certificates", json=kwargs)

    def get_certificate(self, certificate_id: str) -> dict[str, Any]:
        """Retrieve an organisation certificate.

        Args:
            certificate_id: The ID of the certificate.

        Returns:
            The certificate object.
        """
        return self.get(f"/organization/certificates/{certificate_id}")

    def update_certificate(
        self, certificate_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update an organisation certificate.

        Args:
            certificate_id: The ID of the certificate.
            **kwargs:       Fields to update (e.g. ``name``).

        Returns:
            The updated certificate object.
        """
        return self.post(f"/organization/certificates/{certificate_id}", json=kwargs)

    def delete_certificate(self, certificate_id: str) -> dict[str, Any]:
        """Delete an organisation certificate.

        Args:
            certificate_id: The ID of the certificate to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/certificates/{certificate_id}")

    def activate_certificate(self, certificate_id: str) -> dict[str, Any]:
        """Activate an organisation certificate.

        Args:
            certificate_id: The ID of the certificate to activate.

        Returns:
            The updated certificate object.
        """
        return self.post(f"/organization/certificates/{certificate_id}/activate")

    def deactivate_certificate(self, certificate_id: str) -> dict[str, Any]:
        """Deactivate an organisation certificate.

        Args:
            certificate_id: The ID of the certificate to deactivate.

        Returns:
            The updated certificate object.
        """
        return self.post(f"/organization/certificates/{certificate_id}/deactivate")

    # ------------------------------------------------------------------
    # Organization — Costs (1)
    # ------------------------------------------------------------------

    def get_costs(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve cost data for the organisation.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Cost aggregation response object.
        """
        return self.get("/organization/costs", params=kwargs if kwargs else None)

    # ------------------------------------------------------------------
    # Organization — Groups (4)
    # ------------------------------------------------------------------

    def list_groups(self, **kwargs: Any) -> dict[str, Any]:
        """List organisation groups.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/organization/groups", params=kwargs if kwargs else None)

    def create_group(self, **kwargs: Any) -> dict[str, Any]:
        """Create an organisation group.

        Args:
            **kwargs: Request body fields (e.g. ``name``).

        Returns:
            The created group object.
        """
        return self.post("/organization/groups", json=kwargs)

    def update_group(self, group_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update an organisation group.

        Args:
            group_id: The ID of the group.
            **kwargs: Fields to update (e.g. ``name``).

        Returns:
            The updated group object.
        """
        return self.post(f"/organization/groups/{group_id}", json=kwargs)

    def delete_group(self, group_id: str) -> dict[str, Any]:
        """Delete an organisation group.

        Args:
            group_id: The ID of the group to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/groups/{group_id}")

    # ------------------------------------------------------------------
    # Organization — Group Roles (3)
    # ------------------------------------------------------------------

    def list_group_roles(self, group_id: str, **kwargs: Any) -> dict[str, Any]:
        """List roles in an organisation group.

        Args:
            group_id: The ID of the group.
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/organization/groups/{group_id}/roles",
            params=kwargs if kwargs else None,
        )

    def create_group_role(self, group_id: str, **kwargs: Any) -> dict[str, Any]:
        """Add a role to an organisation group.

        Args:
            group_id: The ID of the group.
            **kwargs: Request body fields (e.g. ``role_id``).

        Returns:
            The created group role object.
        """
        return self.post(f"/organization/groups/{group_id}/roles", json=kwargs)

    def delete_group_role(self, group_id: str, role_id: str) -> dict[str, Any]:
        """Remove a role from an organisation group.

        Args:
            group_id: The ID of the group.
            role_id:  The ID of the role to remove.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/groups/{group_id}/roles/{role_id}")

    # ------------------------------------------------------------------
    # Organization — Group Users (3)
    # ------------------------------------------------------------------

    def list_group_users(self, group_id: str, **kwargs: Any) -> dict[str, Any]:
        """List users in an organisation group.

        Args:
            group_id: The ID of the group.
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/organization/groups/{group_id}/users",
            params=kwargs if kwargs else None,
        )

    def add_group_user(self, group_id: str, **kwargs: Any) -> dict[str, Any]:
        """Add a user to an organisation group.

        Args:
            group_id: The ID of the group.
            **kwargs: Request body fields (e.g. ``user_id``).

        Returns:
            The created group membership object.
        """
        return self.post(f"/organization/groups/{group_id}/users", json=kwargs)

    def remove_group_user(self, group_id: str, user_id: str) -> dict[str, Any]:
        """Remove a user from an organisation group.

        Args:
            group_id: The ID of the group.
            user_id:  The ID of the user to remove.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/groups/{group_id}/users/{user_id}")

    # ------------------------------------------------------------------
    # Organization — Invites (4)
    # ------------------------------------------------------------------

    def list_invites(self, **kwargs: Any) -> dict[str, Any]:
        """List pending invites for the organisation.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/organization/invites", params=kwargs if kwargs else None)

    def create_invite(self, **kwargs: Any) -> dict[str, Any]:
        """Create an invitation for a new member.

        Args:
            **kwargs: Request body fields (e.g. ``email``, ``role``).

        Returns:
            The created invite object.
        """
        return self.post("/organization/invites", json=kwargs)

    def get_invite(self, invite_id: str) -> dict[str, Any]:
        """Retrieve an invite.

        Args:
            invite_id: The ID of the invite.

        Returns:
            The invite object.
        """
        return self.get(f"/organization/invites/{invite_id}")

    def delete_invite(self, invite_id: str) -> dict[str, Any]:
        """Cancel/delete an invite.

        Args:
            invite_id: The ID of the invite to cancel.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/invites/{invite_id}")

    # ------------------------------------------------------------------
    # Organization — Projects (18)
    # ------------------------------------------------------------------

    def list_projects(self, **kwargs: Any) -> dict[str, Any]:
        """List projects in the organisation.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``,
                      ``include_archived``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/organization/projects", params=kwargs if kwargs else None)

    def create_project(self, **kwargs: Any) -> dict[str, Any]:
        """Create a new project.

        Args:
            **kwargs: Request body fields (e.g. ``name``).

        Returns:
            The created project object.
        """
        return self.post("/organization/projects", json=kwargs)

    def get_project(self, project_id: str) -> dict[str, Any]:
        """Retrieve a project.

        Args:
            project_id: The ID of the project.

        Returns:
            The project object.
        """
        return self.get(f"/organization/projects/{project_id}")

    def update_project(self, project_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Fields to update (e.g. ``name``).

        Returns:
            The updated project object.
        """
        return self.post(f"/organization/projects/{project_id}", json=kwargs)

    def archive_project(self, project_id: str) -> dict[str, Any]:
        """Archive a project.

        Args:
            project_id: The ID of the project to archive.

        Returns:
            The updated project object.
        """
        return self.post(f"/organization/projects/{project_id}/archive")

    def list_project_api_keys(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List API keys for a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/organization/projects/{project_id}/api_keys",
            params=kwargs if kwargs else None,
        )

    def get_project_api_key(
        self, project_id: str, key_id: str
    ) -> dict[str, Any]:
        """Retrieve a project API key.

        Args:
            project_id: The ID of the project.
            key_id:     The ID of the API key.

        Returns:
            The API key object.
        """
        return self.get(f"/organization/projects/{project_id}/api_keys/{key_id}")

    def delete_project_api_key(
        self, project_id: str, key_id: str
    ) -> dict[str, Any]:
        """Delete a project API key.

        Args:
            project_id: The ID of the project.
            key_id:     The ID of the API key to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/projects/{project_id}/api_keys/{key_id}")

    def activate_project_certificate(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Activate a certificate for a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Request body fields (e.g. ``certificate_id``).

        Returns:
            The updated project certificate association.
        """
        return self.post(
            f"/organization/projects/{project_id}/certificates/activate",
            json=kwargs,
        )

    def deactivate_project_certificate(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Deactivate a certificate for a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Request body fields (e.g. ``certificate_id``).

        Returns:
            The updated project certificate association.
        """
        return self.post(
            f"/organization/projects/{project_id}/certificates/deactivate",
            json=kwargs,
        )

    def list_project_groups(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List groups associated with a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/organization/projects/{project_id}/groups",
            params=kwargs if kwargs else None,
        )

    def add_project_group(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Add a group to a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Request body fields (e.g. ``group_id``).

        Returns:
            The created project-group association.
        """
        return self.post(f"/organization/projects/{project_id}/groups", json=kwargs)

    def remove_project_group(
        self, project_id: str, group_id: str
    ) -> dict[str, Any]:
        """Remove a group from a project.

        Args:
            project_id: The ID of the project.
            group_id:   The ID of the group to remove.

        Returns:
            Deletion status object.
        """
        return self.delete(
            f"/organization/projects/{project_id}/groups/{group_id}"
        )

    def list_project_rate_limits(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List rate limits for a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/organization/projects/{project_id}/rate_limits",
            params=kwargs if kwargs else None,
        )

    def update_project_rate_limit(
        self, project_id: str, rate_limit_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update a project rate limit.

        Args:
            project_id:    The ID of the project.
            rate_limit_id: The ID of the rate limit to update.
            **kwargs:      Fields to update (e.g. ``max_requests_per_1_minute``).

        Returns:
            The updated rate limit object.
        """
        return self.post(
            f"/organization/projects/{project_id}/rate_limits/{rate_limit_id}",
            json=kwargs,
        )

    def list_project_service_accounts(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List service accounts for a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/organization/projects/{project_id}/service_accounts",
            params=kwargs if kwargs else None,
        )

    def create_project_service_account(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Create a service account for a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Request body fields (e.g. ``name``).

        Returns:
            The created service account object.
        """
        return self.post(
            f"/organization/projects/{project_id}/service_accounts", json=kwargs
        )

    def get_project_service_account(
        self, project_id: str, service_account_id: str
    ) -> dict[str, Any]:
        """Retrieve a project service account.

        Args:
            project_id:         The ID of the project.
            service_account_id: The ID of the service account.

        Returns:
            The service account object.
        """
        return self.get(
            f"/organization/projects/{project_id}/service_accounts/{service_account_id}"
        )

    def delete_project_service_account(
        self, project_id: str, service_account_id: str
    ) -> dict[str, Any]:
        """Delete a project service account.

        Args:
            project_id:         The ID of the project.
            service_account_id: The ID of the service account to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(
            f"/organization/projects/{project_id}/service_accounts/{service_account_id}"
        )

    def list_project_users(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List users in a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/organization/projects/{project_id}/users",
            params=kwargs if kwargs else None,
        )

    def add_project_user(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Add a user to a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Request body fields (e.g. ``user_id``, ``role``).

        Returns:
            The created project user object.
        """
        return self.post(f"/organization/projects/{project_id}/users", json=kwargs)

    def get_project_user(
        self, project_id: str, user_id: str
    ) -> dict[str, Any]:
        """Retrieve a user's project membership.

        Args:
            project_id: The ID of the project.
            user_id:    The ID of the user.

        Returns:
            The project user object.
        """
        return self.get(f"/organization/projects/{project_id}/users/{user_id}")

    def update_project_user(
        self, project_id: str, user_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update a user's role in a project.

        Args:
            project_id: The ID of the project.
            user_id:    The ID of the user.
            **kwargs:   Fields to update (e.g. ``role``).

        Returns:
            The updated project user object.
        """
        return self.post(
            f"/organization/projects/{project_id}/users/{user_id}", json=kwargs
        )

    def remove_project_user(
        self, project_id: str, user_id: str
    ) -> dict[str, Any]:
        """Remove a user from a project.

        Args:
            project_id: The ID of the project.
            user_id:    The ID of the user to remove.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/projects/{project_id}/users/{user_id}")

    # ------------------------------------------------------------------
    # Organization — Roles (4)
    # ------------------------------------------------------------------

    def list_roles(self, **kwargs: Any) -> dict[str, Any]:
        """List organisation roles.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/organization/roles", params=kwargs if kwargs else None)

    def create_role(self, **kwargs: Any) -> dict[str, Any]:
        """Create an organisation role.

        Args:
            **kwargs: Request body fields (e.g. ``name``, ``permissions``).

        Returns:
            The created role object.
        """
        return self.post("/organization/roles", json=kwargs)

    def update_role(self, role_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update an organisation role.

        Args:
            role_id:  The ID of the role.
            **kwargs: Fields to update.

        Returns:
            The updated role object.
        """
        return self.post(f"/organization/roles/{role_id}", json=kwargs)

    def delete_role(self, role_id: str) -> dict[str, Any]:
        """Delete an organisation role.

        Args:
            role_id: The ID of the role to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/roles/{role_id}")

    # ------------------------------------------------------------------
    # Organization — Usage (9)
    # ------------------------------------------------------------------

    def get_audio_speech_usage(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve audio speech usage data.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Usage aggregation object.
        """
        return self.get("/organization/usage/audio_speeches", params=kwargs if kwargs else None)

    def get_audio_transcription_usage(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve audio transcription usage data.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Usage aggregation object.
        """
        return self.get(
            "/organization/usage/audio_transcriptions",
            params=kwargs if kwargs else None,
        )

    def get_code_interpreter_usage(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve code interpreter usage data.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Usage aggregation object.
        """
        return self.get(
            "/organization/usage/code_interpreter_sessions",
            params=kwargs if kwargs else None,
        )

    def get_completion_usage(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve completion (chat/text) usage data.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Usage aggregation object.
        """
        return self.get("/organization/usage/completions", params=kwargs if kwargs else None)

    def get_embedding_usage(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve embedding usage data.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Usage aggregation object.
        """
        return self.get("/organization/usage/embeddings", params=kwargs if kwargs else None)

    def get_image_usage(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve image generation usage data.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Usage aggregation object.
        """
        return self.get("/organization/usage/images", params=kwargs if kwargs else None)

    def get_moderation_usage(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve moderation usage data.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Usage aggregation object.
        """
        return self.get("/organization/usage/moderations", params=kwargs if kwargs else None)

    def get_vector_store_usage(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve vector store usage data.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Usage aggregation object.
        """
        return self.get(
            "/organization/usage/vector_stores", params=kwargs if kwargs else None
        )

    def get_realtime_api_usage(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve Realtime API usage data.

        Args:
            **kwargs: Query parameters such as ``start_time``, ``end_time``,
                      ``bucket_width``, ``project_ids``, ``group_by``,
                      ``limit``, ``page``.

        Returns:
            Usage aggregation object.
        """
        return self.get("/organization/usage/realtime_api", params=kwargs if kwargs else None)

    # ------------------------------------------------------------------
    # Organization — Users (8)
    # ------------------------------------------------------------------

    def list_users(self, **kwargs: Any) -> dict[str, Any]:
        """List users in the organisation.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/organization/users", params=kwargs if kwargs else None)

    def get_user(self, user_id: str) -> dict[str, Any]:
        """Retrieve an organisation user.

        Args:
            user_id: The ID of the user.

        Returns:
            The user object.
        """
        return self.get(f"/organization/users/{user_id}")

    def update_user(self, user_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update an organisation user.

        Args:
            user_id:  The ID of the user.
            **kwargs: Fields to update (e.g. ``role``).

        Returns:
            The updated user object.
        """
        return self.post(f"/organization/users/{user_id}", json=kwargs)

    def delete_user(self, user_id: str) -> dict[str, Any]:
        """Remove a user from the organisation.

        Args:
            user_id: The ID of the user to remove.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/users/{user_id}")

    def list_user_roles(self, user_id: str, **kwargs: Any) -> dict[str, Any]:
        """List roles assigned to a user.

        Args:
            user_id:  The ID of the user.
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/organization/users/{user_id}/roles",
            params=kwargs if kwargs else None,
        )

    def add_user_role(self, user_id: str, **kwargs: Any) -> dict[str, Any]:
        """Assign a role to a user.

        Args:
            user_id:  The ID of the user.
            **kwargs: Request body fields (e.g. ``role_id``).

        Returns:
            The created user-role assignment.
        """
        return self.post(f"/organization/users/{user_id}/roles", json=kwargs)

    def remove_user_role(self, user_id: str, role_id: str) -> dict[str, Any]:
        """Remove a role from a user.

        Args:
            user_id: The ID of the user.
            role_id: The ID of the role to remove.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/organization/users/{user_id}/roles/{role_id}")

    # ------------------------------------------------------------------
    # Projects — Group Roles (3)
    # ------------------------------------------------------------------

    def list_project_group_roles(
        self, project_id: str, group_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List roles for a group within a project.

        Args:
            project_id: The ID of the project.
            group_id:   The ID of the group.
            **kwargs:   Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/projects/{project_id}/groups/{group_id}/roles",
            params=kwargs if kwargs else None,
        )

    def create_project_group_role(
        self, project_id: str, group_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Add a role to a group within a project.

        Args:
            project_id: The ID of the project.
            group_id:   The ID of the group.
            **kwargs:   Request body fields (e.g. ``role_id``).

        Returns:
            The created project group role object.
        """
        return self.post(
            f"/projects/{project_id}/groups/{group_id}/roles", json=kwargs
        )

    def delete_project_group_role(
        self, project_id: str, group_id: str, role_id: str
    ) -> dict[str, Any]:
        """Remove a role from a group within a project.

        Args:
            project_id: The ID of the project.
            group_id:   The ID of the group.
            role_id:    The ID of the role to remove.

        Returns:
            Deletion status object.
        """
        return self.delete(
            f"/projects/{project_id}/groups/{group_id}/roles/{role_id}"
        )

    # ------------------------------------------------------------------
    # Projects — Roles (4)
    # ------------------------------------------------------------------

    def list_project_roles(self, project_id: str, **kwargs: Any) -> dict[str, Any]:
        """List roles in a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/projects/{project_id}/roles",
            params=kwargs if kwargs else None,
        )

    def create_project_role(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Create a role within a project.

        Args:
            project_id: The ID of the project.
            **kwargs:   Request body fields (e.g. ``name``, ``permissions``).

        Returns:
            The created project role object.
        """
        return self.post(f"/projects/{project_id}/roles", json=kwargs)

    def update_project_role(
        self, project_id: str, role_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update a role within a project.

        Args:
            project_id: The ID of the project.
            role_id:    The ID of the role.
            **kwargs:   Fields to update.

        Returns:
            The updated project role object.
        """
        return self.post(f"/projects/{project_id}/roles/{role_id}", json=kwargs)

    def delete_project_role(
        self, project_id: str, role_id: str
    ) -> dict[str, Any]:
        """Delete a role within a project.

        Args:
            project_id: The ID of the project.
            role_id:    The ID of the role to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/projects/{project_id}/roles/{role_id}")

    # ------------------------------------------------------------------
    # Projects — User Roles (3)
    # ------------------------------------------------------------------

    def list_project_user_roles(
        self, project_id: str, user_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List roles assigned to a user within a project.

        Args:
            project_id: The ID of the project.
            user_id:    The ID of the user.
            **kwargs:   Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/projects/{project_id}/users/{user_id}/roles",
            params=kwargs if kwargs else None,
        )

    def create_project_user_role(
        self, project_id: str, user_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Assign a role to a user within a project.

        Args:
            project_id: The ID of the project.
            user_id:    The ID of the user.
            **kwargs:   Request body fields (e.g. ``role_id``).

        Returns:
            The created project user role assignment.
        """
        return self.post(
            f"/projects/{project_id}/users/{user_id}/roles", json=kwargs
        )

    def delete_project_user_role(
        self, project_id: str, user_id: str, role_id: str
    ) -> dict[str, Any]:
        """Remove a role from a user within a project.

        Args:
            project_id: The ID of the project.
            user_id:    The ID of the user.
            role_id:    The ID of the role to remove.

        Returns:
            Deletion status object.
        """
        return self.delete(
            f"/projects/{project_id}/users/{user_id}/roles/{role_id}"
        )

    # ------------------------------------------------------------------
    # Realtime (8)
    # ------------------------------------------------------------------

    def create_realtime_session(self, **kwargs: Any) -> dict[str, Any]:
        """Create a Realtime API session.

        Args:
            **kwargs: Request body fields (e.g. ``model``, ``modalities``,
                      ``instructions``, ``voice``, ``input_audio_format``,
                      ``output_audio_format``, ``turn_detection``,
                      ``tools``, ``tool_choice``, ``temperature``).

        Returns:
            The created session object including a client secret.
        """
        return self.post("/realtime/sessions", json=kwargs)

    def create_transcription_session(self, **kwargs: Any) -> dict[str, Any]:
        """Create a Realtime transcription session.

        Args:
            **kwargs: Request body fields (e.g. ``model``,
                      ``input_audio_format``, ``input_audio_transcription``,
                      ``turn_detection``, ``input_audio_noise_reduction``).

        Returns:
            The created transcription session object.
        """
        return self.post("/realtime/transcription_sessions", json=kwargs)

    def create_realtime_client_secret(self, **kwargs: Any) -> dict[str, Any]:
        """Create a short-lived client secret for Realtime API.

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            Client secret object.
        """
        return self.post("/realtime/client_secrets", json=kwargs)

    def create_realtime_call(self, **kwargs: Any) -> dict[str, Any]:
        """Create an outbound Realtime phone call.

        Args:
            **kwargs: Request body fields (e.g. ``phone_number``,
                      ``session``).

        Returns:
            The created call object.
        """
        return self.post("/realtime/calls", json=kwargs)

    def accept_realtime_call(self, call_id: str, **kwargs: Any) -> dict[str, Any]:
        """Accept an inbound Realtime call.

        Args:
            call_id:  The ID of the call to accept.
            **kwargs: Request body fields as required by the API.

        Returns:
            The updated call object.
        """
        return self.post(f"/realtime/calls/{call_id}/accept", json=kwargs)

    def hangup_realtime_call(self, call_id: str, **kwargs: Any) -> dict[str, Any]:
        """Hang up a Realtime call.

        Args:
            call_id:  The ID of the call to hang up.
            **kwargs: Request body fields as required by the API.

        Returns:
            The updated call object.
        """
        return self.post(f"/realtime/calls/{call_id}/hangup", json=kwargs)

    def refer_realtime_call(self, call_id: str, **kwargs: Any) -> dict[str, Any]:
        """Transfer (refer) a Realtime call.

        Args:
            call_id:  The ID of the call to transfer.
            **kwargs: Request body fields (e.g. ``refer_to``).

        Returns:
            The updated call object.
        """
        return self.post(f"/realtime/calls/{call_id}/refer", json=kwargs)

    def reject_realtime_call(self, call_id: str, **kwargs: Any) -> dict[str, Any]:
        """Reject an inbound Realtime call.

        Args:
            call_id:  The ID of the call to reject.
            **kwargs: Request body fields as required by the API.

        Returns:
            The updated call object.
        """
        return self.post(f"/realtime/calls/{call_id}/reject", json=kwargs)

    # ------------------------------------------------------------------
    # Responses (7)
    # ------------------------------------------------------------------

    def create_response(
        self,
        model: str,
        input: str | list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any] | Generator[str, None, None]:
        """Generate a model response via the Responses API.

        Args:
            model:    Model ID (e.g. ``"gpt-4o"``).
            input:    Text string or list of input item dicts.
            **kwargs: Optional: ``instructions``, ``tools``, ``tool_choice``,
                      ``temperature``, ``max_output_tokens``,
                      ``previous_response_id``, ``reasoning``,
                      ``store`` (:class:`bool`),
                      ``stream`` (:class:`bool`).

        Returns:
            Response object, or a generator of SSE data strings when
            ``stream=True`` is passed.
        """
        stream = kwargs.pop("stream", False)
        payload = {"model": model, "input": input, **kwargs}
        if stream:
            payload["stream"] = True
            return self._stream_request("POST", "/responses", json=payload)
        return self.post("/responses", json=payload)

    def get_response(self, response_id: str, **kwargs: Any) -> dict[str, Any]:
        """Retrieve a stored response.

        Args:
            response_id: The ID of the response.
            **kwargs:    Query parameters (e.g. ``include``).

        Returns:
            The response object.
        """
        return self.get(f"/responses/{response_id}", params=kwargs if kwargs else None)

    def delete_response(self, response_id: str) -> dict[str, Any]:
        """Delete a stored response.

        Args:
            response_id: The ID of the response to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/responses/{response_id}")

    def cancel_response(self, response_id: str) -> dict[str, Any]:
        """Cancel an in-progress response generation.

        Args:
            response_id: The ID of the response to cancel.

        Returns:
            The response object with updated status.
        """
        return self.post(f"/responses/{response_id}/cancel")

    def list_response_input_items(
        self, response_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List input items for a response.

        Args:
            response_id: The ID of the response.
            **kwargs:    Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/responses/{response_id}/input_items",
            params=kwargs if kwargs else None,
        )

    def count_response_tokens(self, **kwargs: Any) -> dict[str, Any]:
        """Count the number of tokens a response request would consume.

        Args:
            **kwargs: Request body mirrors :meth:`create_response` but does
                      not actually generate a response.

        Returns:
            Token count object.
        """
        return self.post("/responses/input_tokens", json=kwargs)

    def compact_response(self, **kwargs: Any) -> dict[str, Any]:
        """Compact a conversation history into a shorter context.

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            Compacted response object.
        """
        return self.post("/responses/compact", json=kwargs)

    # ------------------------------------------------------------------
    # Skills (11)
    # ------------------------------------------------------------------

    def create_skill(self, **kwargs: Any) -> dict[str, Any]:
        """Create a skill.

        Args:
            **kwargs: Request body fields (e.g. ``name``, ``description``,
                      ``content``).

        Returns:
            The created skill object.
        """
        return self.post("/skills", json=kwargs)

    def list_skills(self, **kwargs: Any) -> dict[str, Any]:
        """List skills.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/skills", params=kwargs if kwargs else None)

    def delete_skill(self, skill_id: str) -> dict[str, Any]:
        """Delete a skill.

        Args:
            skill_id: The ID of the skill to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/skills/{skill_id}")

    def get_skill(self, skill_id: str) -> dict[str, Any]:
        """Retrieve a skill.

        Args:
            skill_id: The ID of the skill.

        Returns:
            The skill object.
        """
        return self.get(f"/skills/{skill_id}")

    def update_skill(self, skill_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update a skill.

        Args:
            skill_id: The ID of the skill.
            **kwargs: Fields to update.

        Returns:
            The updated skill object.
        """
        return self.post(f"/skills/{skill_id}", json=kwargs)

    def get_skill_content(self, skill_id: str) -> Any:
        """Retrieve the content of a skill.

        Args:
            skill_id: The ID of the skill.

        Returns:
            Skill content (bytes or dict depending on content type).
        """
        return self.get(f"/skills/{skill_id}/content")

    def create_skill_version(self, skill_id: str, **kwargs: Any) -> dict[str, Any]:
        """Create a new version of a skill.

        Args:
            skill_id: The ID of the skill.
            **kwargs: Request body fields (e.g. ``content``).

        Returns:
            The created skill version object.
        """
        return self.post(f"/skills/{skill_id}/versions", json=kwargs)

    def list_skill_versions(self, skill_id: str, **kwargs: Any) -> dict[str, Any]:
        """List versions of a skill.

        Args:
            skill_id: The ID of the skill.
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/skills/{skill_id}/versions", params=kwargs if kwargs else None
        )

    def get_skill_version(self, skill_id: str, version: str) -> dict[str, Any]:
        """Retrieve a specific version of a skill.

        Args:
            skill_id: The ID of the skill.
            version:  The version identifier.

        Returns:
            The skill version object.
        """
        return self.get(f"/skills/{skill_id}/versions/{version}")

    def delete_skill_version(self, skill_id: str, version: str) -> dict[str, Any]:
        """Delete a specific version of a skill.

        Args:
            skill_id: The ID of the skill.
            version:  The version identifier to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/skills/{skill_id}/versions/{version}")

    def get_skill_version_content(self, skill_id: str, version: str) -> Any:
        """Retrieve the content of a specific skill version.

        Args:
            skill_id: The ID of the skill.
            version:  The version identifier.

        Returns:
            Skill version content (bytes or dict depending on content type).
        """
        return self.get(f"/skills/{skill_id}/versions/{version}/content")

    # ------------------------------------------------------------------
    # Threads (18)
    # ------------------------------------------------------------------

    def create_thread(self, **kwargs: Any) -> dict[str, Any]:
        """Create a thread.

        Args:
            **kwargs: Optional: ``messages``, ``tool_resources``,
                      ``metadata``.

        Returns:
            The created thread object.
        """
        return self.post("/threads", json=kwargs)

    def create_thread_and_run(self, **kwargs: Any) -> dict[str, Any]:
        """Create a thread and run it in one request.

        Args:
            **kwargs: Request body fields including ``assistant_id``,
                      ``thread``, ``model``, ``instructions``, ``tools``,
                      ``tool_resources``, ``metadata``, ``temperature``,
                      ``top_p``, ``max_prompt_tokens``,
                      ``max_completion_tokens``, ``truncation_strategy``,
                      ``tool_choice``, ``response_format``, ``stream``.

        Returns:
            The run object (or streaming response if ``stream=True``).
        """
        return self.post("/threads/runs", json=kwargs)

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Retrieve a thread.

        Args:
            thread_id: The ID of the thread.

        Returns:
            The thread object.
        """
        return self.get(f"/threads/{thread_id}")

    def update_thread(self, thread_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update a thread.

        Args:
            thread_id: The ID of the thread.
            **kwargs:  Fields to update (e.g. ``tool_resources``,
                       ``metadata``).

        Returns:
            The updated thread object.
        """
        return self.post(f"/threads/{thread_id}", json=kwargs)

    def delete_thread(self, thread_id: str) -> dict[str, Any]:
        """Delete a thread.

        Args:
            thread_id: The ID of the thread to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/threads/{thread_id}")

    # Thread — Messages (5)

    def list_thread_messages(
        self, thread_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List messages in a thread.

        Args:
            thread_id: The ID of the thread.
            **kwargs:  Query parameters: ``limit``, ``after``, ``before``,
                       ``order``, ``run_id``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/threads/{thread_id}/messages", params=kwargs if kwargs else None
        )

    def create_thread_message(
        self, thread_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Add a message to a thread.

        Args:
            thread_id: The ID of the thread.
            **kwargs:  Request body: ``role``, ``content``, ``attachments``,
                       ``metadata``.

        Returns:
            The created message object.
        """
        return self.post(f"/threads/{thread_id}/messages", json=kwargs)

    def get_thread_message(
        self, thread_id: str, message_id: str
    ) -> dict[str, Any]:
        """Retrieve a message from a thread.

        Args:
            thread_id:  The ID of the thread.
            message_id: The ID of the message.

        Returns:
            The message object.
        """
        return self.get(f"/threads/{thread_id}/messages/{message_id}")

    def update_thread_message(
        self, thread_id: str, message_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update a message in a thread.

        Args:
            thread_id:  The ID of the thread.
            message_id: The ID of the message.
            **kwargs:   Fields to update (e.g. ``metadata``).

        Returns:
            The updated message object.
        """
        return self.post(
            f"/threads/{thread_id}/messages/{message_id}", json=kwargs
        )

    def delete_thread_message(
        self, thread_id: str, message_id: str
    ) -> dict[str, Any]:
        """Delete a message from a thread.

        Args:
            thread_id:  The ID of the thread.
            message_id: The ID of the message to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/threads/{thread_id}/messages/{message_id}")

    # Thread — Runs (6)

    def list_thread_runs(self, thread_id: str, **kwargs: Any) -> dict[str, Any]:
        """List runs for a thread.

        Args:
            thread_id: The ID of the thread.
            **kwargs:  Query parameters: ``limit``, ``after``, ``before``,
                       ``order``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/threads/{thread_id}/runs", params=kwargs if kwargs else None
        )

    def create_thread_run(
        self, thread_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Create a run for a thread.

        Args:
            thread_id: The ID of the thread.
            **kwargs:  Request body: ``assistant_id``, ``model``,
                       ``instructions``, ``tools``, ``metadata``,
                       ``temperature``, ``top_p``, ``max_prompt_tokens``,
                       ``max_completion_tokens``, ``truncation_strategy``,
                       ``tool_choice``, ``response_format``, ``stream``.

        Returns:
            The run object.
        """
        return self.post(f"/threads/{thread_id}/runs", json=kwargs)

    def get_thread_run(self, thread_id: str, run_id: str) -> dict[str, Any]:
        """Retrieve a run.

        Args:
            thread_id: The ID of the thread.
            run_id:    The ID of the run.

        Returns:
            The run object.
        """
        return self.get(f"/threads/{thread_id}/runs/{run_id}")

    def update_thread_run(
        self, thread_id: str, run_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update a run.

        Args:
            thread_id: The ID of the thread.
            run_id:    The ID of the run.
            **kwargs:  Fields to update (e.g. ``metadata``).

        Returns:
            The updated run object.
        """
        return self.post(f"/threads/{thread_id}/runs/{run_id}", json=kwargs)

    def cancel_thread_run(
        self, thread_id: str, run_id: str
    ) -> dict[str, Any]:
        """Cancel a run.

        Args:
            thread_id: The ID of the thread.
            run_id:    The ID of the run to cancel.

        Returns:
            The run object with updated status.
        """
        return self.post(f"/threads/{thread_id}/runs/{run_id}/cancel")

    def submit_thread_run_tool_outputs(
        self, thread_id: str, run_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Submit tool call outputs for a run awaiting tool responses.

        Args:
            thread_id: The ID of the thread.
            run_id:    The ID of the run.
            **kwargs:  Request body: ``tool_outputs`` (list), ``stream``.

        Returns:
            The run object.
        """
        return self.post(
            f"/threads/{thread_id}/runs/{run_id}/submit_tool_outputs", json=kwargs
        )

    # Thread — Run Steps (2)

    def list_thread_run_steps(
        self, thread_id: str, run_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List steps for a run.

        Args:
            thread_id: The ID of the thread.
            run_id:    The ID of the run.
            **kwargs:  Query parameters: ``limit``, ``after``, ``before``,
                       ``order``, ``include``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/threads/{thread_id}/runs/{run_id}/steps",
            params=kwargs if kwargs else None,
        )

    def get_thread_run_step(
        self, thread_id: str, run_id: str, step_id: str
    ) -> dict[str, Any]:
        """Retrieve a single run step.

        Args:
            thread_id: The ID of the thread.
            run_id:    The ID of the run.
            step_id:   The ID of the run step.

        Returns:
            The run step object.
        """
        return self.get(f"/threads/{thread_id}/runs/{run_id}/steps/{step_id}")

    # ------------------------------------------------------------------
    # Uploads (4)
    # ------------------------------------------------------------------

    def create_upload(
        self,
        filename: str,
        purpose: str,
        bytes: int,
        mime_type: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create an upload object for large file uploads.

        Args:
            filename:  The name of the file to upload.
            purpose:   Intended use of the file.
            bytes:     Total size of the file in bytes.
            mime_type: MIME type of the file (e.g. ``"text/jsonl"``).
            **kwargs:  Additional request body fields.

        Returns:
            The upload object.
        """
        payload = {
            "filename": filename,
            "purpose": purpose,
            "bytes": bytes,
            "mime_type": mime_type,
            **kwargs,
        }
        return self.post("/uploads", json=payload)

    def add_upload_part(self, upload_id: str, **kwargs: Any) -> dict[str, Any]:
        """Add a part to an upload.

        Typically called with ``files={"data": <binary_chunk>}`` in *kwargs*.

        Args:
            upload_id: The ID of the upload.
            **kwargs:  Multipart form fields including ``data`` (binary chunk).

        Returns:
            The upload part object.
        """
        return self.post(f"/uploads/{upload_id}/parts", **kwargs)

    def complete_upload(
        self, upload_id: str, part_ids: list[str], **kwargs: Any
    ) -> dict[str, Any]:
        """Complete an upload by assembling its parts.

        Args:
            upload_id: The ID of the upload.
            part_ids:  Ordered list of upload part IDs.
            **kwargs:  Optional: ``md5`` (hex digest for integrity check).

        Returns:
            The file object created from the upload.
        """
        payload = {"part_ids": part_ids, **kwargs}
        return self.post(f"/uploads/{upload_id}/complete", json=payload)

    def cancel_upload(self, upload_id: str) -> dict[str, Any]:
        """Cancel an upload.

        Args:
            upload_id: The ID of the upload to cancel.

        Returns:
            The upload object with updated status.
        """
        return self.post(f"/uploads/{upload_id}/cancel")

    # ------------------------------------------------------------------
    # Vector Stores (16)
    # ------------------------------------------------------------------

    def list_vector_stores(self, **kwargs: Any) -> dict[str, Any]:
        """List vector stores.

        Args:
            **kwargs: Query parameters: ``limit``, ``after``, ``before``,
                      ``order``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/vector_stores", params=kwargs if kwargs else None)

    def create_vector_store(self, **kwargs: Any) -> dict[str, Any]:
        """Create a vector store.

        Args:
            **kwargs: Request body fields (e.g. ``name``, ``file_ids``,
                      ``expires_after``, ``chunking_strategy``,
                      ``metadata``).

        Returns:
            The created vector store object.
        """
        return self.post("/vector_stores", json=kwargs)

    def get_vector_store(self, vector_store_id: str) -> dict[str, Any]:
        """Retrieve a vector store.

        Args:
            vector_store_id: The ID of the vector store.

        Returns:
            The vector store object.
        """
        return self.get(f"/vector_stores/{vector_store_id}")

    def update_vector_store(
        self, vector_store_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            **kwargs:        Fields to update (e.g. ``name``,
                             ``expires_after``, ``metadata``).

        Returns:
            The updated vector store object.
        """
        return self.post(f"/vector_stores/{vector_store_id}", json=kwargs)

    def delete_vector_store(self, vector_store_id: str) -> dict[str, Any]:
        """Delete a vector store.

        Args:
            vector_store_id: The ID of the vector store to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/vector_stores/{vector_store_id}")

    # Vector Store — Files (6)

    def list_vector_store_files(
        self, vector_store_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List files in a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            **kwargs:        Query parameters: ``limit``, ``after``,
                             ``before``, ``order``, ``filter``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/vector_stores/{vector_store_id}/files",
            params=kwargs if kwargs else None,
        )

    def create_vector_store_file(
        self, vector_store_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Attach a file to a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            **kwargs:        Request body: ``file_id``,
                             ``chunking_strategy``.

        Returns:
            The vector store file object.
        """
        return self.post(f"/vector_stores/{vector_store_id}/files", json=kwargs)

    def get_vector_store_file(
        self, vector_store_id: str, file_id: str
    ) -> dict[str, Any]:
        """Retrieve a file attached to a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            file_id:         The ID of the file.

        Returns:
            The vector store file object.
        """
        return self.get(f"/vector_stores/{vector_store_id}/files/{file_id}")

    def update_vector_store_file(
        self, vector_store_id: str, file_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update a file in a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            file_id:         The ID of the file.
            **kwargs:        Fields to update (e.g. ``attributes``).

        Returns:
            The updated vector store file object.
        """
        return self.post(
            f"/vector_stores/{vector_store_id}/files/{file_id}", json=kwargs
        )

    def delete_vector_store_file(
        self, vector_store_id: str, file_id: str
    ) -> dict[str, Any]:
        """Delete a file from a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            file_id:         The ID of the file to detach.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/vector_stores/{vector_store_id}/files/{file_id}")

    def get_vector_store_file_content(
        self, vector_store_id: str, file_id: str
    ) -> bytes:
        """Retrieve the raw content of a file in a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            file_id:         The ID of the file.

        Returns:
            Raw file bytes.
        """
        return self.get(f"/vector_stores/{vector_store_id}/files/{file_id}/content")

    # Vector Store — File Batches (4)

    def create_vector_store_file_batch(
        self, vector_store_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Create a file batch for a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            **kwargs:        Request body: ``file_ids``,
                             ``chunking_strategy``.

        Returns:
            The file batch object.
        """
        return self.post(
            f"/vector_stores/{vector_store_id}/file_batches", json=kwargs
        )

    def get_vector_store_file_batch(
        self, vector_store_id: str, batch_id: str
    ) -> dict[str, Any]:
        """Retrieve a file batch for a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            batch_id:        The ID of the file batch.

        Returns:
            The file batch object.
        """
        return self.get(f"/vector_stores/{vector_store_id}/file_batches/{batch_id}")

    def cancel_vector_store_file_batch(
        self, vector_store_id: str, batch_id: str
    ) -> dict[str, Any]:
        """Cancel a file batch operation on a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            batch_id:        The ID of the file batch to cancel.

        Returns:
            The file batch object with updated status.
        """
        return self.post(
            f"/vector_stores/{vector_store_id}/file_batches/{batch_id}/cancel"
        )

    def list_vector_store_file_batch_files(
        self, vector_store_id: str, batch_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List files in a vector store file batch.

        Args:
            vector_store_id: The ID of the vector store.
            batch_id:        The ID of the file batch.
            **kwargs:        Query parameters: ``limit``, ``after``,
                             ``before``, ``order``, ``filter``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get(
            f"/vector_stores/{vector_store_id}/file_batches/{batch_id}/files",
            params=kwargs if kwargs else None,
        )

    # Vector Store — Search (1)

    def search_vector_store(
        self, vector_store_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Search a vector store.

        Args:
            vector_store_id: The ID of the vector store.
            **kwargs:        Request body: ``query`` (string or list),
                             ``rewrite_query``, ``max_num_results``,
                             ``filters``, ``ranking_options``.

        Returns:
            Search results object with ``data`` list of file chunks.
        """
        return self.post(f"/vector_stores/{vector_store_id}/search", json=kwargs)

    # ------------------------------------------------------------------
    # Videos (10)
    # ------------------------------------------------------------------

    def create_video(self, **kwargs: Any) -> dict[str, Any]:
        """Generate a video.

        Args:
            **kwargs: Request body fields (e.g. ``model``, ``prompt``,
                      ``duration``, ``resolution``, ``quality``).

        Returns:
            The created video generation job object.
        """
        return self.post("/videos", json=kwargs)

    def list_videos(self, **kwargs: Any) -> dict[str, Any]:
        """List generated videos.

        Args:
            **kwargs: Query parameters such as ``limit``, ``after``.

        Returns:
            Dict with ``data`` list and pagination fields.
        """
        return self.get("/videos", params=kwargs if kwargs else None)

    def create_video_character(self, **kwargs: Any) -> dict[str, Any]:
        """Create a video character.

        Args:
            **kwargs: Request body fields (e.g. ``name``, ``description``,
                      ``image``).

        Returns:
            The created video character object.
        """
        return self.post("/videos/characters", json=kwargs)

    def get_video_character(self, character_id: str) -> dict[str, Any]:
        """Retrieve a video character.

        Args:
            character_id: The ID of the character.

        Returns:
            The video character object.
        """
        return self.get(f"/videos/characters/{character_id}")

    def create_video_edit(self, **kwargs: Any) -> dict[str, Any]:
        """Create a video edit.

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            The created video edit job object.
        """
        return self.post("/videos/edits", json=kwargs)

    def create_video_extension(self, **kwargs: Any) -> dict[str, Any]:
        """Create a video extension.

        Args:
            **kwargs: Request body fields as required by the API.

        Returns:
            The created video extension job object.
        """
        return self.post("/videos/extensions", json=kwargs)

    def get_video(self, video_id: str) -> dict[str, Any]:
        """Retrieve a video generation job.

        Args:
            video_id: The ID of the video.

        Returns:
            The video object.
        """
        return self.get(f"/videos/{video_id}")

    def delete_video(self, video_id: str) -> dict[str, Any]:
        """Delete a video.

        Args:
            video_id: The ID of the video to delete.

        Returns:
            Deletion status object.
        """
        return self.delete(f"/videos/{video_id}")

    def get_video_content(self, video_id: str) -> bytes:
        """Download the content of a generated video.

        Args:
            video_id: The ID of the video.

        Returns:
            Raw video bytes.
        """
        return self.get(f"/videos/{video_id}/content")

    def remix_video(self, video_id: str, **kwargs: Any) -> dict[str, Any]:
        """Create a remix of an existing video.

        Args:
            video_id: The ID of the source video.
            **kwargs: Request body fields (e.g. ``prompt``, ``strength``).

        Returns:
            The remix job object.
        """
        return self.post(f"/videos/{video_id}/remix", json=kwargs)

    # ------------------------------------------------------------------
    # __repr__
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"OpenAIClient("
            f"base_url={self.base_url!r}, "
            f"max_retries={self.max_retries!r}, "
            f"timeout={self.timeout!r}"
            f")"
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _prepare_file_upload(
    file: str | Path | BinaryIO,
    field_name: str,
    extra: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepare a file for multipart upload.

    Accepts a path string, :class:`pathlib.Path`, or an already-open binary
    file object.  Returns ``(files_dict, data_dict)`` suitable for passing
    to ``httpx`` as ``files=`` and ``data=`` respectively.

    Args:
        file:       The file to upload.
        field_name: The form field name for the file (e.g. ``"file"``,
                    ``"image"``).
        extra:      Extra keyword arguments; non-file items are moved to
                    ``data_dict``.

    Returns:
        A 2-tuple of ``(files, data)`` dicts.
    """
    data: dict[str, Any] = {}
    files: dict[str, Any] = {}

    # Separate non-file kwargs into form data
    for key, val in extra.items():
        if not hasattr(val, "read"):  # simple scalar → form data
            data[key] = val

    if isinstance(file, (str, Path)):
        path = Path(file)
        files[field_name] = (path.name, open(path, "rb"), _guess_mime(path))
    else:
        # file-like object — attempt to extract a name
        name = getattr(file, "name", field_name)
        files[field_name] = (Path(name).name, file, _guess_mime(Path(name)))

    return files, data


def _guess_mime(path: Path) -> str:
    """Return a best-effort MIME type for *path* based on its suffix.

    Args:
        path: File path whose suffix is used for MIME detection.

    Returns:
        A MIME type string.  Falls back to ``"application/octet-stream"``.
    """
    suffix_map: dict[str, str] = {
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".mpeg": "video/mpeg",
        ".mpga": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".wav": "audio/wav",
        ".webm": "video/webm",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".jsonl": "application/jsonl",
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".csv": "text/csv",
    }
    return suffix_map.get(path.suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = ["ResponseCache", "OpenAIClient"]
