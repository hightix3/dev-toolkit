# OpenAI API Python Client

Auto-generated from [OpenAI API v2.3.0](https://developers.openai.com/docs) — **241 endpoints** across **24 resources**.

---

## Installation

```bash
pip install httpx          # only hard dependency
# then copy / install this package
```

---

## Quick Start

```python
from openai_client import OpenAIClient

client = OpenAIClient(api_key="sk-...")

# List models
models = client.models.list()
for m in models["data"]:
    print(m["id"])

# Chat completion
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp["choices"][0]["message"]["content"])
```

---

## Authentication

| Method | Details |
|--------|---------|
| **API Key** | `OpenAIClient(api_key="sk-…")` or env var `OPENAI_API_KEY` |
| **Organization** | `OpenAIClient(org_id="org-…")` or env var `OPENAI_ORG_ID` |
| **Project** | `OpenAIClient(project_id="proj_…")` or env var `OPENAI_PROJECT_ID` |

---

## Resources & Endpoints

| Resource | Methods | Notes |
|----------|---------|-------|
| **Models** | `list`, `retrieve`, `delete` | – |
| **Chat Completions** | `create` | SSE streaming, JSON mode, function calling |
| **Responses** | `create`, `retrieve`, `delete`, `list_input_items` | SSE streaming |
| **Assistants** | `create`, `retrieve`, `modify`, `delete`, `list` | Beta header auto-injected |
| **Threads** | `create`, `retrieve`, `modify`, `delete` | Beta |
| **Messages** | `create`, `retrieve`, `modify`, `delete`, `list` | Beta |
| **Runs** | `create`, `retrieve`, `modify`, `cancel`, `list`, `submit_tool_outputs` | Beta |
| **Run Steps** | `retrieve`, `list` | Beta |
| **Vector Stores** | `create`, `retrieve`, `modify`, `delete`, `list` | Beta |
| **Vector Store Files** | `create`, `retrieve`, `delete`, `list` | Beta |
| **Vector Store File Batches** | `create`, `retrieve`, `cancel`, `list_files` | Beta |
| **Audio** | `create_speech`, `create_transcription`, `create_translation` | Multipart upload |
| **Images** | `generate`, `create_edit`, `create_variation` | Multipart upload |
| **Videos** | `generate` | – |
| **Embeddings** | `create` | – |
| **Files** | `upload`, `list`, `retrieve`, `retrieve_content`, `delete` | Multipart upload |
| **Fine-Tuning** | `create`, `retrieve`, `cancel`, `list`, `list_events`, `list_checkpoints` | – |
| **Batches** | `create`, `retrieve`, `cancel`, `list` | – |
| **Uploads** | `create`, `add_part`, `complete`, `cancel` | – |
| **Moderations** | `create` | – |
| **Realtime Sessions** | `create` | – |
| **Realtime Transcription Sessions** | `create` | – |
| **Organization Audit Logs** | `list` | – |
| **Organization Invites** | `create`, `retrieve`, `delete`, `list` | – |
| **Organization Users** | `retrieve`, `modify`, `delete`, `list` | – |

---

## Features

### Bearer Auth with Org / Project Support

```python
client = OpenAIClient(
    api_key="sk-...",
    org_id="org-...",         # → OpenAI-Organization header
    project_id="proj_...",   # → OpenAI-Project header
)
```

### Automatic Retry with Exponential Back-off

Retries on HTTP **429** and **5xx** responses (up to `max_retries` attempts,
default **3**).  Each retry waits `retry_base_delay * 2^attempt` seconds
(default first delay: **1 s**).

```python
client = OpenAIClient(api_key="sk-...", max_retries=5, retry_base_delay=0.5)
```

### Cursor-Based Auto-Pagination

Any list endpoint returns a lazy `Page` object that auto-fetches the next
page when iterated:

```python
for model in client.models.list_autopaged():
    print(model["id"])
```

### SSE Streaming

Pass `stream=True` to streaming-capable endpoints:

```python
for chunk in client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Count to 10"}],
    stream=True,
):
    print(chunk["choices"][0]["delta"].get("content", ""), end="", flush=True)
```

### File Uploads

```python
with open("lecture.mp3", "rb") as f:
    transcript = client.audio.create_transcription(
        file=("lecture.mp3", f, "audio/mpeg"),
        model="whisper-1",
    )
print(transcript["text"])
```

### Response Caching (TTL-based)

```python
client = OpenAIClient(api_key="sk-...", cache_ttl=300)  # cache GET responses for 5 min
models1 = client.models.list()   # network request
models2 = client.models.list()   # served from cache
```

### Async Client

```python
import asyncio
from openai_client import OpenAIClient

async def main():
    async with OpenAIClient(api_key="sk-...") as client:
        resp = await client.chat.completions.acreate(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello async!"}],
        )
        print(resp["choices"][0]["message"]["content"])

asyncio.run(main())
```

---

## API Reference

### `OpenAIClient(api_key, *, base_url, timeout, max_retries, retry_base_delay, org_id, project_id, cache_ttl, **httpx_kwargs)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `$OPENAI_API_KEY` | Bearer token |
| `base_url` | `str` | `https://api.openai.com/v1` | API base URL |
| `timeout` | `float` | `60.0` | Request timeout (seconds) |
| `max_retries` | `int` | `3` | Max retry attempts on 429/5xx |
| `retry_base_delay` | `float` | `1.0` | Base delay for exponential back-off |
| `org_id` | `str` | `$OPENAI_ORG_ID` | OpenAI-Organization header value |
| `project_id` | `str` | `$OPENAI_PROJECT_ID` | OpenAI-Project header value |
| `cache_ttl` | `float` | `0` | Seconds to cache GET responses (`0` = disabled) |

### Resource accessor attributes

| Attribute | Type |
|-----------|------|
| `client.models` | `ModelsResource` |
| `client.chat` | `ChatResource` (→ `client.chat.completions`) |
| `client.responses` | `ResponsesResource` |
| `client.assistants` | `AssistantsResource` |
| `client.threads` | `ThreadsResource` |
| `client.messages` | `MessagesResource` |
| `client.runs` | `RunsResource` |
| `client.run_steps` | `RunStepsResource` |
| `client.vector_stores` | `VectorStoresResource` |
| `client.vector_store_files` | `VectorStoreFilesResource` |
| `client.vector_store_file_batches` | `VectorStoreFileBatchesResource` |
| `client.audio` | `AudioResource` |
| `client.images` | `ImagesResource` |
| `client.videos` | `VideosResource` |
| `client.embeddings` | `EmbeddingsResource` |
| `client.files` | `FilesResource` |
| `client.fine_tuning` | `FineTuningResource` |
| `client.batches` | `BatchesResource` |
| `client.uploads` | `UploadsResource` |
| `client.moderations` | `ModerationsResource` |
| `client.realtime` | `RealtimeResource` |
| `client.realtime_transcription` | `RealtimeTranscriptionResource` |
| `client.audit_logs` | `AuditLogsResource` |
| `client.invites` | `InvitesResource` |
| `client.users` | `UsersResource` |

---

## Error Handling

```python
from openai_client.exceptions import (
    RateLimitError,
    AuthenticationError,
    NotFoundError,
    ServerError,
    OpenAIError,
)

try:
    resp = client.chat.completions.create(...)
except RateLimitError as e:
    print(f"Rate limited. Retry after {e.retry_after}s")
except AuthenticationError:
    print("Check your API key")
except ServerError as e:
    print(f"OpenAI server error: {e.status_code}")
except OpenAIError as e:
    print(f"Unexpected error: {e}")
```

| Exception | HTTP Status |
|-----------|-------------|
| `InvalidRequestError` | 400 |
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403 |
| `NotFoundError` | 404 |
| `ConflictError` | 409 |
| `UnprocessableEntityError` | 422 |
| `RateLimitError` | 429 |
| `ServerError` | 5xx |
| `APIConnectionError` | — (network) |
| `APITimeoutError` | — (timeout) |

---

## License

MIT
