"""
Linear GraphQL API Client

A production-ready Python client for the Linear GraphQL API.

Endpoint: https://api.linear.app/graphql

Features:
- Typed methods for Issues, Projects, Teams, Users, Cycles, Comments,
  Workflow States, Labels, and Attachments (35+ operations).
- Automatic exponential-backoff retry on rate-limit (429 / RATELIMITED).
- Cursor-based auto-pagination via ``paginate_all()``.
- TTL-based in-memory response cache.
- Context manager support (``with LinearClient(...) as client:``).
- Reads rate-limit headers and surfaces remaining quota.

Usage::

    from linear_client import LinearClient

    with LinearClient("lin_api_xxxxxxxxxxxx") as client:
        viewer = client.get_viewer()
        teams  = client.list_teams()
        issues = client.list_issues(team_id=teams[0]["id"])
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Iterator

import httpx

from .auth import LinearAuth
from .exceptions import (
    AuthenticationError,
    GraphQLError,
    NetworkError,
    RateLimitError,
    TimeoutError,
)

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.linear.app/graphql"

# ---------------------------------------------------------------------------
# Simple TTL cache
# ---------------------------------------------------------------------------


class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


class _TTLCache:
    """
    A minimal TTL-based LRU-like cache backed by a plain dict.

    Thread-safety is not guaranteed — this is designed for single-threaded
    or async use where GIL protection is sufficient.
    """

    def __init__(self, max_size: int = 256, ttl: float = 60.0) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.is_expired():
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self._max_size:
            # Evict oldest entry (insertion-order dict)
            oldest = next(iter(self._store))
            del self._store[oldest]
        self._store[key] = _CacheEntry(value, self._ttl)

    def invalidate(self) -> None:
        self._store.clear()


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------


class LinearClient:
    """
    A production-ready synchronous client for the Linear GraphQL API.

    Args:
        api_key: A Linear personal API key (``lin_api_…``) or an OAuth
            Bearer token.
        timeout: HTTP timeout in seconds. Default 30.
        max_retries: Number of retry attempts on ``RATELIMITED``. Default 5.
        cache_ttl: TTL in seconds for the response cache. Set to ``0`` to
            disable caching. Default 60.
        cache_max_size: Maximum number of entries in the cache. Default 256.
        http_client: Optional pre-configured ``httpx.Client``. Useful for
            testing (mock transports) or custom proxies. When provided,
            ``api_key`` and ``timeout`` are ignored for transport purposes.

    Example::

        with LinearClient("lin_api_xxxxxxxxxxxx") as client:
            viewer = client.get_viewer()
            print(viewer["name"])
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 5,
        cache_ttl: float = 60.0,
        cache_max_size: int = 256,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._http = http_client or httpx.Client(
            auth=LinearAuth(api_key),
            timeout=httpx.Timeout(timeout),
        )
        self._cache: _TTLCache | None = (
            _TTLCache(max_size=cache_max_size, ttl=cache_ttl) if cache_ttl > 0 else None
        )

        # Rate-limit header state (updated per request)
        self.requests_limit: int | None = None
        self.requests_remaining: int | None = None
        self.requests_reset: int | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "LinearClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def _cache_key(self, query: str, variables: dict[str, Any]) -> str:
        payload = json.dumps({"query": query, "variables": variables}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _update_quota(self, response: httpx.Response) -> None:
        """Parse rate-limit headers and update instance state."""
        try:
            self.requests_limit = int(response.headers.get("X-RateLimit-Requests-Limit", 0))
            self.requests_remaining = int(
                response.headers.get("X-RateLimit-Requests-Remaining", 0)
            )
            self.requests_reset = int(response.headers.get("X-RateLimit-Requests-Reset", 0))
        except (ValueError, TypeError):
            pass

    def _execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        use_cache: bool = False,
    ) -> dict[str, Any]:
        """
        Execute a GraphQL query or mutation.

        Args:
            query: GraphQL query/mutation string.
            variables: Optional variables dict.
            use_cache: If ``True`` and caching is enabled, return a cached
                response when available, and store new responses.

        Returns:
            The ``data`` field from the GraphQL response.

        Raises:
            GraphQLError: For any GraphQL-level error.
            AuthenticationError: For auth failures.
            RateLimitError: If rate-limited and retries are exhausted.
            NetworkError: For transport-level failures.
            TimeoutError: If the request times out.
        """
        variables = variables or {}

        # Cache lookup
        cache_key: str | None = None
        if use_cache and self._cache is not None:
            cache_key = self._cache_key(query, variables)
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for key %s", cache_key[:8])
                return cached

        payload = {"query": query, "variables": variables}

        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.post(_ENDPOINT, json=payload)
            except httpx.TimeoutException as exc:
                raise TimeoutError(f"Request timed out: {exc}") from exc
            except httpx.RequestError as exc:
                raise NetworkError(f"Network error: {exc}") from exc

            self._update_quota(response)

            # Handle auth errors at HTTP level
            if response.status_code == 401:
                raise AuthenticationError(
                    "Authentication failed: invalid or missing API key."
                )

            # Parse body
            try:
                body = response.json()
            except Exception as exc:
                raise NetworkError(
                    f"Invalid JSON response (HTTP {response.status_code}): {exc}"
                ) from exc

            # GraphQL errors
            errors = body.get("errors")
            if errors:
                exc = GraphQLError.from_response(errors, response=body)
                if isinstance(exc, RateLimitError):
                    if attempt < self._max_retries:
                        wait = 2**attempt  # exponential back-off: 1, 2, 4, 8, 16 s
                        logger.warning(
                            "Rate limited (attempt %d/%d). Waiting %ds.",
                            attempt + 1,
                            self._max_retries,
                            wait,
                        )
                        time.sleep(wait)
                        continue
                raise exc

            data = body.get("data") or {}

            # Store in cache
            if use_cache and self._cache is not None and cache_key is not None:
                self._cache.set(cache_key, data)

            return data

        # Exhausted retries
        raise RateLimitError("Rate limit exceeded and all retries exhausted.")

    # ==================================================================
    #  ISSUES
    # ==================================================================

    def list_issues(
        self,
        team_id: str | None = None,
        filter: dict[str, Any] | None = None,
        first: int = 50,
        after: str | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """
        Fetch a paginated list of issues.

        Args:
            team_id: Optionally filter by team ID.
            filter: Raw Linear IssueFilter object.
            first: Page size (max 250). Default 50.
            after: Pagination cursor from a previous ``pageInfo.endCursor``.
            include_archived: Include archived issues. Default False.

        Returns:
            A connection dict with ``nodes`` (list of issues) and
            ``pageInfo`` (``hasNextPage``, ``endCursor``).
        """
        query = """
        query ListIssues(
            $filter: IssueFilter
            $first: Int
            $after: String
            $includeArchived: Boolean
        ) {
            issues(
                filter: $filter
                first: $first
                after: $after
                includeArchived: $includeArchived
            ) {
                nodes {
                    id
                    identifier
                    title
                    description
                    priority
                    priorityLabel
                    estimate
                    dueDate
                    createdAt
                    updatedAt
                    archivedAt
                    canceledAt
                    completedAt
                    startedAt
                    trashed
                    url
                    branchName
                    state {
                        id
                        name
                        type
                        color
                    }
                    team {
                        id
                        name
                        key
                    }
                    assignee {
                        id
                        name
                        email
                        avatarUrl
                    }
                    creator {
                        id
                        name
                        email
                    }
                    project {
                        id
                        name
                    }
                    cycle {
                        id
                        name
                        number
                    }
                    parent {
                        id
                        identifier
                        title
                    }
                    labels {
                        nodes {
                            id
                            name
                            color
                        }
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        _filter: dict[str, Any] = filter or {}
        if team_id:
            _filter["team"] = {"id": {"eq": team_id}}

        data = self._execute(
            query,
            {
                "filter": _filter if _filter else None,
                "first": first,
                "after": after,
                "includeArchived": include_archived,
            },
        )
        return data["issues"]

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        """
        Fetch a single issue by ID or identifier.

        Args:
            issue_id: Issue UUID or human-readable identifier (e.g. ``"ENG-42"``)

        Returns:
            Issue dict with nested state, team, assignee, labels, comments,
            attachments, and relations.
        """
        query = """
        query GetIssue($id: String!) {
            issue(id: $id) {
                id
                identifier
                title
                description
                priority
                priorityLabel
                estimate
                dueDate
                createdAt
                updatedAt
                archivedAt
                canceledAt
                completedAt
                startedAt
                trashed
                url
                branchName
                sortOrder
                state {
                    id
                    name
                    type
                    color
                    description
                }
                team {
                    id
                    name
                    key
                }
                assignee {
                    id
                    name
                    email
                    avatarUrl
                }
                creator {
                    id
                    name
                    email
                }
                project {
                    id
                    name
                    state
                }
                cycle {
                    id
                    name
                    number
                    startsAt
                    endsAt
                }
                parent {
                    id
                    identifier
                    title
                }
                children {
                    nodes {
                        id
                        identifier
                        title
                        priority
                        state {
                            name
                            type
                        }
                    }
                }
                labels {
                    nodes {
                        id
                        name
                        color
                    }
                }
                comments {
                    nodes {
                        id
                        body
                        createdAt
                        updatedAt
                        user {
                            id
                            name
                            email
                        }
                    }
                }
                attachments {
                    nodes {
                        id
                        title
                        url
                        createdAt
                    }
                }
                relations {
                    nodes {
                        id
                        type
                        relatedIssue {
                            id
                            identifier
                            title
                        }
                    }
                }
            }
        }
        """
        data = self._execute(query, {"id": issue_id}, use_cache=True)
        return data["issue"]

    def create_issue(
        self,
        team_id: str,
        title: str,
        *,
        description: str | None = None,
        priority: int | None = None,
        assignee_id: str | None = None,
        label_ids: list[str] | None = None,
        state_id: str | None = None,
        project_id: str | None = None,
        cycle_id: str | None = None,
        parent_id: str | None = None,
        estimate: int | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new issue.

        Args:
            team_id: Team UUID. Required.
            title: Issue title. Required.
            description: Markdown body.
            priority: 0 (none) | 1 (urgent) | 2 (high) | 3 (medium) | 4 (low).
            assignee_id: User UUID to assign.
            label_ids: List of label UUIDs.
            state_id: Workflow state UUID.
            project_id: Project UUID.
            cycle_id: Cycle UUID.
            parent_id: Parent issue UUID (for sub-issues).
            estimate: Story point estimate.
            due_date: ISO 8601 date string (``"2024-12-31"``).

        Returns:
            Dict with ``success`` and ``issue``.
        """
        query = """
        mutation CreateIssue($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id
                    identifier
                    title
                    url
                    createdAt
                    state {
                        id
                        name
                        type
                    }
                    team {
                        id
                        name
                    }
                }
            }
        }
        """
        inp: dict[str, Any] = {"teamId": team_id, "title": title}
        if description is not None:
            inp["description"] = description
        if priority is not None:
            inp["priority"] = priority
        if assignee_id is not None:
            inp["assigneeId"] = assignee_id
        if label_ids is not None:
            inp["labelIds"] = label_ids
        if state_id is not None:
            inp["stateId"] = state_id
        if project_id is not None:
            inp["projectId"] = project_id
        if cycle_id is not None:
            inp["cycleId"] = cycle_id
        if parent_id is not None:
            inp["parentId"] = parent_id
        if estimate is not None:
            inp["estimate"] = estimate
        if due_date is not None:
            inp["dueDate"] = due_date

        data = self._execute(query, {"input": inp})
        return data["issueCreate"]

    def update_issue(self, issue_id: str, **fields: Any) -> dict[str, Any]:
        """
        Update an existing issue.

        Args:
            issue_id: Issue UUID or identifier.
            **fields: Fields to update, in camelCase. For example:
                ``title``, ``description``, ``priority``, ``assigneeId``,
                ``stateId``, ``projectId``, ``cycleId``, ``dueDate``,
                ``estimate``, ``labelIds``.

        Returns:
            Dict with ``success`` and updated ``issue``.
        """
        query = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
                success
                issue {
                    id
                    identifier
                    title
                    updatedAt
                    state {
                        id
                        name
                        type
                    }
                }
            }
        }
        """
        data = self._execute(query, {"id": issue_id, "input": fields})
        return data["issueUpdate"]

    def delete_issue(self, issue_id: str) -> dict[str, Any]:
        """
        Permanently delete an issue.

        Args:
            issue_id: Issue UUID or identifier.

        Returns:
            Dict with ``success``.
        """
        query = """
        mutation DeleteIssue($id: String!) {
            issueDelete(id: $id) {
                success
            }
        }
        """
        data = self._execute(query, {"id": issue_id})
        return data["issueDelete"]

    def archive_issue(self, issue_id: str) -> dict[str, Any]:
        """
        Archive an issue (soft delete).

        Args:
            issue_id: Issue UUID or identifier.

        Returns:
            Dict with ``success``.
        """
        query = """
        mutation ArchiveIssue($id: String!) {
            issueArchive(id: $id) {
                success
            }
        }
        """
        data = self._execute(query, {"id": issue_id})
        return data["issueArchive"]

    def search_issues(
        self,
        query_string: str,
        first: int = 50,
        after: str | None = None,
    ) -> dict[str, Any]:
        """
        Full-text search across issues.

        Args:
            query_string: Search string.
            first: Page size. Default 50.
            after: Pagination cursor.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query SearchIssues($filter: IssueFilter, $first: Int, $after: String) {
            issues(filter: $filter, first: $first, after: $after) {
                nodes {
                    id
                    identifier
                    title
                    priority
                    state {
                        name
                        type
                    }
                    team {
                        id
                        name
                    }
                    assignee {
                        id
                        name
                    }
                    createdAt
                    updatedAt
                    url
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        _filter = {"searchableContent": {"contains": query_string}}
        data = self._execute(
            query,
            {"filter": _filter, "first": first, "after": after},
        )
        return data["issues"]

    # ==================================================================
    #  ISSUE LABELS
    # ==================================================================

    def list_issue_labels(
        self,
        first: int = 50,
        after: str | None = None,
        team_id: str | None = None,
    ) -> dict[str, Any]:
        """
        List issue labels.

        Args:
            first: Page size. Default 50.
            after: Pagination cursor.
            team_id: Filter to labels belonging to a specific team.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query ListIssueLabels($filter: IssueLabelFilter, $first: Int, $after: String) {
            issueLabels(filter: $filter, first: $first, after: $after) {
                nodes {
                    id
                    name
                    color
                    description
                    team {
                        id
                        name
                    }
                    createdAt
                    updatedAt
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        _filter: dict[str, Any] = {}
        if team_id:
            _filter["team"] = {"id": {"eq": team_id}}

        data = self._execute(
            query,
            {"filter": _filter if _filter else None, "first": first, "after": after},
            use_cache=True,
        )
        return data["issueLabels"]

    def create_issue_label(
        self,
        team_id: str,
        name: str,
        color: str,
        *,
        description: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new issue label.

        Args:
            team_id: Team UUID.
            name: Label name.
            color: Hex colour string (e.g. ``"#FF5733"``).
            description: Optional description.
            parent_id: Optional parent label UUID (for nested labels).

        Returns:
            Dict with ``success`` and ``issueLabel``.
        """
        query = """
        mutation CreateIssueLabel($input: IssueLabelCreateInput!) {
            issueLabelCreate(input: $input) {
                success
                issueLabel {
                    id
                    name
                    color
                    description
                    createdAt
                    team {
                        id
                        name
                    }
                }
            }
        }
        """
        inp: dict[str, Any] = {"teamId": team_id, "name": name, "color": color}
        if description is not None:
            inp["description"] = description
        if parent_id is not None:
            inp["parentId"] = parent_id

        data = self._execute(query, {"input": inp})
        return data["issueLabelCreate"]

    def update_issue_label(
        self,
        label_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """
        Update an existing issue label.

        Args:
            label_id: Label UUID.
            **fields: Fields to update: ``name``, ``color``, ``description``.

        Returns:
            Dict with ``success`` and updated ``issueLabel``.
        """
        query = """
        mutation UpdateIssueLabel($id: String!, $input: IssueLabelUpdateInput!) {
            issueLabelUpdate(id: $id, input: $input) {
                success
                issueLabel {
                    id
                    name
                    color
                    description
                    updatedAt
                }
            }
        }
        """
        data = self._execute(query, {"id": label_id, "input": fields})
        return data["issueLabelUpdate"]

    # ==================================================================
    #  PROJECTS
    # ==================================================================

    def list_projects(
        self,
        first: int = 50,
        after: str | None = None,
        filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Fetch a paginated list of projects.

        Args:
            first: Page size. Default 50.
            after: Pagination cursor.
            filter: Raw Linear ProjectFilter object.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query ListProjects($filter: ProjectFilter, $first: Int, $after: String) {
            projects(filter: $filter, first: $first, after: $after) {
                nodes {
                    id
                    name
                    description
                    state
                    slugId
                    icon
                    color
                    priority
                    startDate
                    targetDate
                    completedAt
                    canceledAt
                    createdAt
                    updatedAt
                    url
                    lead {
                        id
                        name
                        email
                    }
                    teams {
                        nodes {
                            id
                            name
                            key
                        }
                    }
                    members {
                        nodes {
                            id
                            name
                            email
                        }
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        data = self._execute(
            query,
            {"filter": filter, "first": first, "after": after},
        )
        return data["projects"]

    def get_project(self, project_id: str) -> dict[str, Any]:
        """
        Fetch a single project by ID.

        Args:
            project_id: Project UUID.

        Returns:
            Project dict with milestones, updates, members, and teams.
        """
        query = """
        query GetProject($id: String!) {
            project(id: $id) {
                id
                name
                description
                state
                slugId
                icon
                color
                priority
                startDate
                targetDate
                completedAt
                canceledAt
                createdAt
                updatedAt
                url
                lead {
                    id
                    name
                    email
                    avatarUrl
                }
                teams {
                    nodes {
                        id
                        name
                        key
                    }
                }
                members {
                    nodes {
                        id
                        name
                        email
                    }
                }
                milestones {
                    nodes {
                        id
                        name
                        targetDate
                        sortOrder
                        createdAt
                        updatedAt
                    }
                }
                projectUpdates {
                    nodes {
                        id
                        body
                        health
                        createdAt
                        updatedAt
                        user {
                            id
                            name
                        }
                    }
                }
            }
        }
        """
        data = self._execute(query, {"id": project_id}, use_cache=True)
        return data["project"]

    def create_project(
        self,
        team_ids: list[str],
        name: str,
        *,
        description: str | None = None,
        state: str | None = None,
        lead_id: str | None = None,
        member_ids: list[str] | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        color: str | None = None,
        icon: str | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """
        Create a new project.

        Args:
            team_ids: List of team UUIDs. Required.
            name: Project name. Required.
            description: Markdown description.
            state: Project state (e.g. ``"started"``, ``"planned"``).
            lead_id: User UUID for the project lead.
            member_ids: List of user UUIDs.
            start_date: ISO 8601 date string.
            target_date: ISO 8601 date string.
            color: Hex colour string.
            icon: Emoji or icon identifier.
            priority: Priority value 0-4.

        Returns:
            Dict with ``success`` and ``project``.
        """
        query = """
        mutation CreateProject($input: ProjectCreateInput!) {
            projectCreate(input: $input) {
                success
                project {
                    id
                    name
                    state
                    url
                    createdAt
                    teams {
                        nodes {
                            id
                            name
                        }
                    }
                }
            }
        }
        """
        inp: dict[str, Any] = {"teamIds": team_ids, "name": name}
        if description is not None:
            inp["description"] = description
        if state is not None:
            inp["state"] = state
        if lead_id is not None:
            inp["leadId"] = lead_id
        if member_ids is not None:
            inp["memberIds"] = member_ids
        if start_date is not None:
            inp["startDate"] = start_date
        if target_date is not None:
            inp["targetDate"] = target_date
        if color is not None:
            inp["color"] = color
        if icon is not None:
            inp["icon"] = icon
        if priority is not None:
            inp["priority"] = priority

        data = self._execute(query, {"input": inp})
        return data["projectCreate"]

    def update_project(
        self,
        project_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """
        Update a project.

        Args:
            project_id: Project UUID.
            **fields: Fields to update in camelCase: ``name``, ``description``,
                ``state``, ``leadId``, ``targetDate``, ``startDate``, etc.

        Returns:
            Dict with ``success`` and updated ``project``.
        """
        query = """
        mutation UpdateProject($id: String!, $input: ProjectUpdateInput!) {
            projectUpdate(id: $id, input: $input) {
                success
                project {
                    id
                    name
                    state
                    updatedAt
                }
            }
        }
        """
        data = self._execute(query, {"id": project_id, "input": fields})
        return data["projectUpdate"]

    def delete_project(self, project_id: str) -> dict[str, Any]:
        """
        Delete a project.

        Args:
            project_id: Project UUID.

        Returns:
            Dict with ``success``.
        """
        query = """
        mutation DeleteProject($id: String!) {
            projectDelete(id: $id) {
                success
            }
        }
        """
        data = self._execute(query, {"id": project_id})
        return data["projectDelete"]

    def list_project_updates(
        self,
        project_id: str,
        first: int = 50,
        after: str | None = None,
    ) -> dict[str, Any]:
        """
        List status updates for a project.

        Args:
            project_id: Project UUID.
            first: Page size. Default 50.
            after: Pagination cursor.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query ListProjectUpdates($id: String!, $first: Int, $after: String) {
            project(id: $id) {
                projectUpdates(first: $first, after: $after) {
                    nodes {
                        id
                        body
                        health
                        createdAt
                        updatedAt
                        user {
                            id
                            name
                            email
                        }
                    }
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                }
            }
        }
        """
        data = self._execute(
            query,
            {"id": project_id, "first": first, "after": after},
        )
        return data["project"]["projectUpdates"]

    def create_project_update(
        self,
        project_id: str,
        body: str,
        *,
        health: str | None = None,
    ) -> dict[str, Any]:
        """
        Post a project status update.

        Args:
            project_id: Project UUID.
            body: Markdown body of the update.
            health: Optional health indicator. One of ``"onTrack"``,
                ``"atRisk"``, ``"offTrack"``.

        Returns:
            Dict with ``success`` and ``projectUpdate``.
        """
        query = """
        mutation CreateProjectUpdate($input: ProjectUpdateCreateInput!) {
            projectUpdateCreate(input: $input) {
                success
                projectUpdate {
                    id
                    body
                    health
                    createdAt
                    project {
                        id
                        name
                    }
                }
            }
        }
        """
        inp: dict[str, Any] = {"projectId": project_id, "body": body}
        if health is not None:
            inp["health"] = health

        data = self._execute(query, {"input": inp})
        return data["projectUpdateCreate"]

    # ==================================================================
    #  TEAMS
    # ==================================================================

    def list_teams(
        self,
        first: int = 50,
        after: str | None = None,
        filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Fetch a paginated list of teams for the authenticated user.

        Args:
            first: Page size. Default 50.
            after: Pagination cursor.
            filter: Raw Linear TeamFilter object.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query ListTeams($filter: TeamFilter, $first: Int, $after: String) {
            teams(filter: $filter, first: $first, after: $after) {
                nodes {
                    id
                    name
                    key
                    description
                    color
                    icon
                    private
                    timezone
                    issueCount
                    cycleCalenderUrl
                    createdAt
                    updatedAt
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        data = self._execute(
            query,
            {"filter": filter, "first": first, "after": after},
            use_cache=True,
        )
        return data["teams"]

    def get_team(self, team_id: str) -> dict[str, Any]:
        """
        Fetch a single team by ID.

        Args:
            team_id: Team UUID.

        Returns:
            Team dict with members, workflow states, and labels.
        """
        query = """
        query GetTeam($id: String!) {
            team(id: $id) {
                id
                name
                key
                description
                color
                icon
                private
                timezone
                issueCount
                createdAt
                updatedAt
                members {
                    nodes {
                        id
                        name
                        email
                        avatarUrl
                    }
                }
                states {
                    nodes {
                        id
                        name
                        type
                        color
                        position
                    }
                }
                labels {
                    nodes {
                        id
                        name
                        color
                        description
                    }
                }
            }
        }
        """
        data = self._execute(query, {"id": team_id}, use_cache=True)
        return data["team"]

    # ==================================================================
    #  USERS
    # ==================================================================

    def list_users(
        self,
        first: int = 50,
        after: str | None = None,
        filter: dict[str, Any] | None = None,
        include_disabled: bool = False,
    ) -> dict[str, Any]:
        """
        Fetch a paginated list of organisation users.

        Args:
            first: Page size. Default 50.
            after: Pagination cursor.
            filter: Raw Linear UserFilter object.
            include_disabled: Include deactivated users. Default False.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query ListUsers(
            $filter: UserFilter
            $first: Int
            $after: String
            $includeDisabled: Boolean
        ) {
            users(
                filter: $filter
                first: $first
                after: $after
                includeDisabled: $includeDisabled
            ) {
                nodes {
                    id
                    name
                    email
                    displayName
                    avatarUrl
                    active
                    admin
                    guest
                    timezone
                    createdAt
                    updatedAt
                    lastSeen
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        data = self._execute(
            query,
            {
                "filter": filter,
                "first": first,
                "after": after,
                "includeDisabled": include_disabled,
            },
            use_cache=True,
        )
        return data["users"]

    def get_user(self, user_id: str) -> dict[str, Any]:
        """
        Fetch a single user by ID.

        Args:
            user_id: User UUID.

        Returns:
            User dict with assigned issues and team memberships.
        """
        query = """
        query GetUser($id: String!) {
            user(id: $id) {
                id
                name
                email
                displayName
                avatarUrl
                active
                admin
                guest
                timezone
                createdAt
                updatedAt
                lastSeen
                assignedIssues {
                    nodes {
                        id
                        identifier
                        title
                        priority
                        state {
                            name
                            type
                        }
                    }
                }
                teamMemberships {
                    nodes {
                        team {
                            id
                            name
                            key
                        }
                        owner
                    }
                }
            }
        }
        """
        data = self._execute(query, {"id": user_id}, use_cache=True)
        return data["user"]

    def get_viewer(self) -> dict[str, Any]:
        """
        Fetch the currently authenticated user.

        Returns:
            Viewer dict (same shape as ``get_user``).
        """
        query = """
        query GetViewer {
            viewer {
                id
                name
                email
                displayName
                avatarUrl
                active
                admin
                guest
                timezone
                createdAt
                updatedAt
                lastSeen
                teamMemberships {
                    nodes {
                        team {
                            id
                            name
                            key
                        }
                        owner
                    }
                }
            }
        }
        """
        data = self._execute(query, use_cache=True)
        return data["viewer"]

    # ==================================================================
    #  CYCLES
    # ==================================================================

    def list_cycles(
        self,
        team_id: str,
        first: int = 50,
        after: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch cycles for a team.

        Args:
            team_id: Team UUID.
            first: Page size. Default 50.
            after: Pagination cursor.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query ListCycles($filter: CycleFilter, $first: Int, $after: String) {
            cycles(filter: $filter, first: $first, after: $after) {
                nodes {
                    id
                    name
                    number
                    startsAt
                    endsAt
                    completedAt
                    createdAt
                    updatedAt
                    issueCountHistory
                    completedIssueCountHistory
                    scopeHistory
                    completedScopeHistory
                    team {
                        id
                        name
                        key
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        _filter: dict[str, Any] = {"team": {"id": {"eq": team_id}}}
        data = self._execute(
            query,
            {"filter": _filter, "first": first, "after": after},
            use_cache=True,
        )
        return data["cycles"]

    def get_cycle(self, cycle_id: str) -> dict[str, Any]:
        """
        Fetch a single cycle by ID.

        Args:
            cycle_id: Cycle UUID.

        Returns:
            Cycle dict with issues.
        """
        query = """
        query GetCycle($id: String!) {
            cycle(id: $id) {
                id
                name
                number
                startsAt
                endsAt
                completedAt
                createdAt
                updatedAt
                team {
                    id
                    name
                    key
                }
                issues {
                    nodes {
                        id
                        identifier
                        title
                        priority
                        estimate
                        state {
                            id
                            name
                            type
                        }
                        assignee {
                            id
                            name
                        }
                    }
                }
            }
        }
        """
        data = self._execute(query, {"id": cycle_id}, use_cache=True)
        return data["cycle"]

    def create_cycle(
        self,
        team_id: str,
        starts_at: str,
        ends_at: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new cycle for a team.

        Args:
            team_id: Team UUID.
            starts_at: ISO 8601 datetime string for cycle start.
            ends_at: ISO 8601 datetime string for cycle end.
            name: Optional cycle name.
            description: Optional description.

        Returns:
            Dict with ``success`` and ``cycle``.
        """
        query = """
        mutation CreateCycle($input: CycleCreateInput!) {
            cycleCreate(input: $input) {
                success
                cycle {
                    id
                    name
                    number
                    startsAt
                    endsAt
                    createdAt
                    team {
                        id
                        name
                    }
                }
            }
        }
        """
        inp: dict[str, Any] = {
            "teamId": team_id,
            "startsAt": starts_at,
            "endsAt": ends_at,
        }
        if name is not None:
            inp["name"] = name
        if description is not None:
            inp["description"] = description

        data = self._execute(query, {"input": inp})
        return data["cycleCreate"]

    def update_cycle(
        self,
        cycle_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """
        Update a cycle.

        Args:
            cycle_id: Cycle UUID.
            **fields: Fields to update: ``name``, ``description``,
                ``startsAt``, ``endsAt``.

        Returns:
            Dict with ``success`` and updated ``cycle``.
        """
        query = """
        mutation UpdateCycle($id: String!, $input: CycleUpdateInput!) {
            cycleUpdate(id: $id, input: $input) {
                success
                cycle {
                    id
                    name
                    number
                    startsAt
                    endsAt
                    updatedAt
                }
            }
        }
        """
        data = self._execute(query, {"id": cycle_id, "input": fields})
        return data["cycleUpdate"]

    # ==================================================================
    #  COMMENTS
    # ==================================================================

    def list_comments(
        self,
        issue_id: str,
        first: int = 50,
        after: str | None = None,
    ) -> dict[str, Any]:
        """
        List comments on an issue.

        Args:
            issue_id: Issue UUID or identifier.
            first: Page size. Default 50.
            after: Pagination cursor.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query ListComments($id: String!, $first: Int, $after: String) {
            issue(id: $id) {
                comments(first: $first, after: $after) {
                    nodes {
                        id
                        body
                        createdAt
                        updatedAt
                        editedAt
                        user {
                            id
                            name
                            email
                            avatarUrl
                        }
                        parent {
                            id
                            body
                        }
                    }
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                }
            }
        }
        """
        data = self._execute(
            query,
            {"id": issue_id, "first": first, "after": after},
        )
        return data["issue"]["comments"]

    def create_comment(
        self,
        issue_id: str,
        body: str,
        *,
        parent_id: str | None = None,
        create_as_user: str | None = None,
        display_icon_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Post a comment on an issue.

        Args:
            issue_id: Issue UUID or identifier.
            body: Markdown comment body.
            parent_id: Optional parent comment UUID (for threads).
            create_as_user: Display name to impersonate (integration bots only).
            display_icon_url: Optional avatar URL when impersonating.

        Returns:
            Dict with ``success`` and ``comment``.
        """
        query = """
        mutation CreateComment($input: CommentCreateInput!) {
            commentCreate(input: $input) {
                success
                comment {
                    id
                    body
                    createdAt
                    issue {
                        id
                        identifier
                    }
                    user {
                        id
                        name
                    }
                }
            }
        }
        """
        inp: dict[str, Any] = {"issueId": issue_id, "body": body}
        if parent_id is not None:
            inp["parentId"] = parent_id
        if create_as_user is not None:
            inp["createAsUser"] = create_as_user
        if display_icon_url is not None:
            inp["displayIconUrl"] = display_icon_url

        data = self._execute(query, {"input": inp})
        return data["commentCreate"]

    def update_comment(
        self,
        comment_id: str,
        body: str,
    ) -> dict[str, Any]:
        """
        Edit an existing comment.

        Args:
            comment_id: Comment UUID.
            body: New Markdown body.

        Returns:
            Dict with ``success`` and updated ``comment``.
        """
        query = """
        mutation UpdateComment($id: String!, $input: CommentUpdateInput!) {
            commentUpdate(id: $id, input: $input) {
                success
                comment {
                    id
                    body
                    updatedAt
                    editedAt
                }
            }
        }
        """
        data = self._execute(query, {"id": comment_id, "input": {"body": body}})
        return data["commentUpdate"]

    def delete_comment(self, comment_id: str) -> dict[str, Any]:
        """
        Delete a comment.

        Args:
            comment_id: Comment UUID.

        Returns:
            Dict with ``success``.
        """
        query = """
        mutation DeleteComment($id: String!) {
            commentDelete(id: $id) {
                success
            }
        }
        """
        data = self._execute(query, {"id": comment_id})
        return data["commentDelete"]

    # ==================================================================
    #  WORKFLOW STATES
    # ==================================================================

    def list_workflow_states(
        self,
        team_id: str | None = None,
        first: int = 50,
        after: str | None = None,
    ) -> dict[str, Any]:
        """
        List workflow states, optionally filtered by team.

        Args:
            team_id: Filter to states for this team.
            first: Page size. Default 50.
            after: Pagination cursor.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query ListWorkflowStates($filter: WorkflowStateFilter, $first: Int, $after: String) {
            workflowStates(filter: $filter, first: $first, after: $after) {
                nodes {
                    id
                    name
                    type
                    color
                    description
                    position
                    team {
                        id
                        name
                        key
                    }
                    createdAt
                    updatedAt
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        _filter: dict[str, Any] = {}
        if team_id:
            _filter["team"] = {"id": {"eq": team_id}}

        data = self._execute(
            query,
            {"filter": _filter if _filter else None, "first": first, "after": after},
            use_cache=True,
        )
        return data["workflowStates"]

    def get_workflow_state(self, state_id: str) -> dict[str, Any]:
        """
        Fetch a single workflow state by ID.

        Args:
            state_id: Workflow state UUID.

        Returns:
            Workflow state dict.
        """
        query = """
        query GetWorkflowState($id: String!) {
            workflowState(id: $id) {
                id
                name
                type
                color
                description
                position
                team {
                    id
                    name
                    key
                }
                issues {
                    nodes {
                        id
                        identifier
                        title
                        priority
                    }
                }
                createdAt
                updatedAt
            }
        }
        """
        data = self._execute(query, {"id": state_id}, use_cache=True)
        return data["workflowState"]

    # ==================================================================
    #  ATTACHMENTS
    # ==================================================================

    def list_attachments(
        self,
        issue_id: str,
        first: int = 50,
        after: str | None = None,
    ) -> dict[str, Any]:
        """
        List attachments for an issue.

        Args:
            issue_id: Issue UUID or identifier.
            first: Page size. Default 50.
            after: Pagination cursor.

        Returns:
            Connection with ``nodes`` and ``pageInfo``.
        """
        query = """
        query ListAttachments($id: String!, $first: Int, $after: String) {
            issue(id: $id) {
                attachments(first: $first, after: $after) {
                    nodes {
                        id
                        title
                        subtitle
                        url
                        iconUrl
                        metadata
                        source
                        sourceType
                        createdAt
                        updatedAt
                        creator {
                            id
                            name
                            email
                        }
                    }
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                }
            }
        }
        """
        data = self._execute(
            query,
            {"id": issue_id, "first": first, "after": after},
            use_cache=True,
        )
        return data["issue"]["attachments"]

    def create_attachment(
        self,
        issue_id: str,
        title: str,
        url: str,
        *,
        subtitle: str | None = None,
        icon_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Create a URL attachment on an issue.

        Args:
            issue_id: Issue UUID or identifier.
            title: Attachment title.
            url: Attachment URL.
            subtitle: Optional subtitle.
            icon_url: Optional custom icon URL.
            metadata: Optional metadata dict (stored as JSON).

        Returns:
            Dict with ``success`` and ``attachment``.
        """
        query = """
        mutation CreateAttachment($input: AttachmentCreateInput!) {
            attachmentCreate(input: $input) {
                success
                attachment {
                    id
                    title
                    subtitle
                    url
                    iconUrl
                    createdAt
                    issue {
                        id
                        identifier
                    }
                }
            }
        }
        """
        inp: dict[str, Any] = {"issueId": issue_id, "title": title, "url": url}
        if subtitle is not None:
            inp["subtitle"] = subtitle
        if icon_url is not None:
            inp["iconUrl"] = icon_url
        if metadata is not None:
            inp["metadata"] = metadata

        data = self._execute(query, {"input": inp})
        return data["attachmentCreate"]

    def update_attachment(
        self,
        attachment_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """
        Update an attachment.

        Args:
            attachment_id: Attachment UUID.
            **fields: Fields to update: ``title``, ``subtitle``, ``iconUrl``,
                ``metadata``.

        Returns:
            Dict with ``success`` and updated ``attachment``.
        """
        query = """
        mutation UpdateAttachment($id: String!, $input: AttachmentUpdateInput!) {
            attachmentUpdate(id: $id, input: $input) {
                success
                attachment {
                    id
                    title
                    subtitle
                    url
                    updatedAt
                }
            }
        }
        """
        data = self._execute(query, {"id": attachment_id, "input": fields})
        return data["attachmentUpdate"]

    def delete_attachment(self, attachment_id: str) -> dict[str, Any]:
        """
        Delete an attachment.

        Args:
            attachment_id: Attachment UUID.

        Returns:
            Dict with ``success``.
        """
        query = """
        mutation DeleteAttachment($id: String!) {
            attachmentDelete(id: $id) {
                success
            }
        }
        """
        data = self._execute(query, {"id": attachment_id})
        return data["attachmentDelete"]

    # ==================================================================
    #  ISSUE RELATIONS
    # ==================================================================

    def create_issue_relation(
        self,
        issue_id: str,
        related_issue_id: str,
        relation_type: str = "relates",
    ) -> dict[str, Any]:
        """
        Create a relation between two issues.

        Args:
            issue_id: Source issue UUID.
            related_issue_id: Target issue UUID.
            relation_type: Relation type. One of ``"blocks"``,
                ``"duplicate"``, ``"relates"``. Default ``"relates"``.

        Returns:
            Dict with ``success`` and ``issueRelation``.
        """
        query = """
        mutation CreateIssueRelation($input: IssueRelationCreateInput!) {
            issueRelationCreate(input: $input) {
                success
                issueRelation {
                    id
                    type
                    issue {
                        id
                        identifier
                        title
                    }
                    relatedIssue {
                        id
                        identifier
                        title
                    }
                }
            }
        }
        """
        inp: dict[str, Any] = {
            "issueId": issue_id,
            "relatedIssueId": related_issue_id,
            "type": relation_type,
        }
        data = self._execute(query, {"input": inp})
        return data["issueRelationCreate"]

    def delete_issue_relation(self, relation_id: str) -> dict[str, Any]:
        """
        Delete an issue relation.

        Args:
            relation_id: IssueRelation UUID.

        Returns:
            Dict with ``success``.
        """
        query = """
        mutation DeleteIssueRelation($id: String!) {
            issueRelationDelete(id: $id) {
                success
            }
        }
        """
        data = self._execute(query, {"id": relation_id})
        return data["issueRelationDelete"]

    # ==================================================================
    #  ORGANIZATION / MISC
    # ==================================================================

    def get_organization(self) -> dict[str, Any]:
        """
        Fetch the current organisation's details.

        Returns:
            Organisation dict.
        """
        query = """
        query GetOrganization {
            organization {
                id
                name
                urlKey
                logoUrl
                createdAt
                updatedAt
                periodUploadVolume
                gitBranchFormat
                gitLinkbackMessagesEnabled
                gitPublicLinkbackMessagesEnabled
                roadmapEnabled
                samlEnabled
                allowedAuthServices
                userCount
                createdIssueCount
            }
        }
        """
        data = self._execute(query, use_cache=True)
        return data["organization"]

    def list_workflow_states_for_team(self, team_id: str) -> list[dict[str, Any]]:
        """
        Convenience wrapper: return a flat list of all workflow states for a
        team (auto-paginates).

        Args:
            team_id: Team UUID or key.

        Returns:
            List of workflow state dicts.
        """
        query = """
        query ListWorkflowStatesForTeam(
            $filter: WorkflowStateFilter
            $first: Int
            $after: String
        ) {
            workflowStates(filter: $filter, first: $first, after: $after) {
                nodes {
                    id
                    name
                    type
                    color
                    description
                    position
                    team {
                        id
                        name
                        key
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        return self.paginate_all(
            query,
            {
                "filter": {"team": {"id": {"eq": team_id}}},
                "first": 50,
            },
            data_key="workflowStates",
        )

    # ==================================================================
    #  CONVENIENCE / UTILITY
    # ==================================================================

    def quota(self) -> dict[str, Any]:
        """
        Return the last-known rate-limit quota for this client.

        The values are updated automatically after every request from the
        ``X-RateLimit-*`` response headers.

        Returns:
            Dict with keys ``limit``, ``remaining``, ``reset_ms``.
        """
        return {
            "limit": self.requests_limit,
            "remaining": self.requests_remaining,
            "reset_ms": self.requests_reset,
        }

    def clear_cache(self) -> None:
        """Invalidate all cached responses."""
        if self._cache is not None:
            self._cache.invalidate()

    # ==================================================================
    #  PAGINATION
    # ==================================================================

    def paginate_all(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        data_key: str,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Auto-paginate a connection query, collecting all nodes.

        The query **must** accept ``$first: Int`` and ``$after: String``
        variables and return a connection with
        ``pageInfo { hasNextPage endCursor }``.

        Args:
            query: GraphQL query string.
            variables: Initial variables dict (``first``/``after`` will be
                injected/overwritten).
            data_key: Top-level key in ``data`` that holds the connection
                (e.g. ``"issues"`` or ``"workflowStates"``).
            page_size: Items per page. Default 100.

        Returns:
            Flat list of all node dicts across all pages.
        """
        all_nodes: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            vars_page = {**variables, "first": page_size, "after": cursor}
            data = self._execute(query, vars_page)
            connection = data[data_key]
            nodes = connection.get("nodes", [])
            all_nodes.extend(nodes)

            page_info = connection.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break

        return all_nodes
