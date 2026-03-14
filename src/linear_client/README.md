# linear_client

A production-ready Python client for the [Linear GraphQL API](https://linear.app/developers/graphql).

## Features

- **35+ typed methods** covering Issues, Projects, Teams, Users, Cycles, Comments, Workflow States, Labels, Attachments, and Relations
- **Automatic retry** with exponential back-off on rate-limit errors
- **Cursor-based auto-pagination** — fetch all pages in one call
- **TTL-based response cache** — avoid redundant reads
- **Context manager support** — automatic connection cleanup
- **Quota tracking** — inspect remaining rate-limit budget after each request
- **Typed exception hierarchy** — distinguish auth errors from rate limits from not-found errors
- Zero external dependencies beyond `httpx`

---

## Installation

```bash
pip install httpx
```

Copy the `linear_client/` directory into your project (no PyPI package required).

---

## Getting Your API Key

1. Open Linear and navigate to **Settings → Security & Access → API**
2. Click **Create key**, give it a name, and copy the generated key
3. Keys begin with `lin_api_`

> **OAuth tokens** (for integrations): pass `bearer=True` to `LinearAuth` or use
> `LinearOAuthAuth` directly. See [Auth setup](#auth-setup) below.

---

## Quick Start

```python
from linear_client import LinearClient

# Simplest usage
with LinearClient("lin_api_xxxxxxxxxxxx") as client:
    me = client.get_viewer()
    print(f"Authenticated as: {me['name']} ({me['email']})")

    # List all teams
    teams_page = client.list_teams()
    for team in teams_page["nodes"]:
        print(f"  Team: {team['name']} ({team['key']})")

    # List issues in the first team
    team_id = teams_page["nodes"][0]["id"]
    issues_page = client.list_issues(team_id=team_id, first=10)
    for issue in issues_page["nodes"]:
        print(f"  {issue['identifier']}: {issue['title']}")
```

---

## Auth Setup

### Personal API Key (most common)

```python
from linear_client import LinearClient, LinearAuth

# Option 1 — pass the key directly (recommended)
client = LinearClient("lin_api_xxxxxxxxxxxx")

# Option 2 — construct the Auth object yourself
auth = LinearAuth("lin_api_xxxxxxxxxxxx")
```

### OAuth 2.0 Bearer Token

```python
from linear_client import LinearClient
from linear_client.auth import LinearOAuthAuth

auth = LinearOAuthAuth("my_oauth_access_token")
# or:
auth = LinearAuth("my_oauth_access_token", bearer=True)
```

Linear expects `Authorization: Bearer <token>` for OAuth tokens and
`Authorization: <key>` for personal API keys. Both classes handle
the correct header format automatically.

---

## All Methods

### Issues

| Method | Description |
|--------|-------------|
| `list_issues(team_id, filter, first, after, include_archived)` | Paginated issue list with optional team/filter |
| `get_issue(issue_id)` | Single issue with all sub-resources |
| `create_issue(team_id, title, *, description, priority, assignee_id, label_ids, state_id, project_id, cycle_id, parent_id, estimate, due_date)` | Create a new issue |
| `update_issue(issue_id, **fields)` | Update any issue fields (camelCase) |
| `delete_issue(issue_id)` | Permanently delete an issue |
| `archive_issue(issue_id)` | Soft-archive an issue |
| `search_issues(query_string, first, after)` | Full-text search |

### Issue Labels

| Method | Description |
|--------|-------------|
| `list_issue_labels(first, after, team_id)` | List labels, optionally by team |
| `create_issue_label(team_id, name, color, *, description, parent_id)` | Create a label |
| `update_issue_label(label_id, **fields)` | Update label fields |

### Projects

| Method | Description |
|--------|-------------|
| `list_projects(first, after, filter)` | Paginated project list |
| `get_project(project_id)` | Single project with milestones and updates |
| `create_project(team_ids, name, *, description, state, lead_id, ...)` | Create a project |
| `update_project(project_id, **fields)` | Update project fields |
| `delete_project(project_id)` | Delete a project |
| `list_project_updates(project_id, first, after)` | List status updates |
| `create_project_update(project_id, body, *, health)` | Post a status update |

### Teams

| Method | Description |
|--------|-------------|
| `list_teams(first, after, filter)` | List teams for the authenticated user |
| `get_team(team_id)` | Single team with members, states, and labels |

### Users

| Method | Description |
|--------|-------------|
| `list_users(first, after, filter, include_disabled)` | List organisation users |
| `get_user(user_id)` | Single user with assignments and memberships |
| `get_viewer()` | Currently authenticated user |

### Cycles

| Method | Description |
|--------|-------------|
| `list_cycles(team_id, first, after)` | Cycles for a team |
| `get_cycle(cycle_id)` | Single cycle with issues |
| `create_cycle(team_id, starts_at, ends_at, *, name, description)` | Create a cycle |
| `update_cycle(cycle_id, **fields)` | Update cycle fields |

### Comments

| Method | Description |
|--------|-------------|
| `list_comments(issue_id, first, after)` | Comments on an issue |
| `create_comment(issue_id, body, *, parent_id, create_as_user, display_icon_url)` | Post a comment |
| `update_comment(comment_id, body)` | Edit a comment |
| `delete_comment(comment_id)` | Delete a comment |

### Workflow States

| Method | Description |
|--------|-------------|
| `list_workflow_states(team_id, first, after)` | States with optional team filter |
| `get_workflow_state(state_id)` | Single state with its issues |
| `list_workflow_states_for_team(team_id)` | All states for a team (auto-paginates) |

### Attachments

| Method | Description |
|--------|-------------|
| `list_attachments(issue_id, first, after)` | Attachments on an issue |
| `create_attachment(issue_id, title, url, *, subtitle, icon_url, metadata)` | Attach a URL |
| `update_attachment(attachment_id, **fields)` | Update attachment metadata |
| `delete_attachment(attachment_id)` | Delete an attachment |

### Issue Relations

| Method | Description |
|--------|-------------|
| `create_issue_relation(issue_id, related_issue_id, relation_type)` | Link two issues |
| `delete_issue_relation(relation_id)` | Remove a relation |

### Organisation

| Method | Description |
|--------|-------------|
| `get_organization()` | Organisation details |

### Pagination

| Method | Description |
|--------|-------------|
| `paginate_all(query, variables, *, data_key)` | Auto-fetch all pages for any query |

### Utility

| Method | Description |
|--------|-------------|
| `quota()` | Current rate-limit quota (limit, remaining, reset_ms) |
| `clear_cache()` | Invalidate the TTL cache |

---

## Pagination Examples

### Using a method's built-in page result

```python
# Fetch first page
page = client.list_issues(team_id="TEAM_ID", first=50)
issues = page["nodes"]
page_info = page["pageInfo"]

# Fetch next page
if page_info["hasNextPage"]:
    next_page = client.list_issues(
        team_id="TEAM_ID",
        first=50,
        after=page_info["endCursor"],
    )
```

### Auto-paginate all results

Use `paginate_all()` with any query that accepts `$first: Int` and
`$after: String` and returns `pageInfo { hasNextPage endCursor }`.

```python
QUERY = """
query AllIssues($filter: IssueFilter, $first: Int, $after: String) {
    issues(filter: $filter, first: $first, after: $after) {
        nodes {
            id
            identifier
            title
        }
        pageInfo {
            hasNextPage
            endCursor
        }
    }
}
"""

all_issues = client.paginate_all(
    QUERY,
    {"filter": {"team": {"id": {"eq": "TEAM_ID"}}}, "first": 100},
    data_key="issues",
)
print(f"Total issues: {len(all_issues)}")
```

### Convenience wrapper (workflow states)

```python
states = client.list_workflow_states_for_team("TEAM_ID")
# Returns a flat list — all pages fetched automatically
for state in states:
    print(state["name"], state["type"])
```

---

## Error Handling

```python
from linear_client import (
    LinearClient,
    AuthenticationError,
    RateLimitError,
    NotFoundError,
    GraphQLError,
    NetworkError,
)

try:
    with LinearClient("lin_api_xxxxxxxxxxxx") as client:
        issue = client.get_issue("INVALID_ID")

except AuthenticationError:
    print("Bad API key — check your credentials.")

except RateLimitError as exc:
    print(f"Rate limited. Retry after {exc.retry_after}s.")

except NotFoundError:
    print("Issue not found.")

except GraphQLError as exc:
    # Catch-all for any other GraphQL-level error
    print(f"GraphQL error: {exc.message}")
    for err in exc.errors:
        print("  ", err)

except NetworkError as exc:
    print(f"Network problem: {exc}")
```

### Exception Hierarchy

```
LinearError
├── GraphQLError          # Any error in the GraphQL 'errors' array
│   ├── AuthenticationError  # Invalid or missing API key
│   ├── RateLimitError       # RATELIMITED — also auto-retried
│   ├── NotFoundError        # ENTITY_NOT_FOUND
│   ├── PermissionError      # FORBIDDEN
│   └── ValidationError      # Input validation failure
└── NetworkError          # Transport / DNS / connection errors
    └── TimeoutError         # Request timed out
```

`GraphQLError.from_response()` inspects the `extensions.code` field and
automatically returns the most specific subclass.

---

## Configuration

```python
client = LinearClient(
    api_key="lin_api_xxxxxxxxxxxx",
    timeout=30.0,        # HTTP timeout in seconds (default: 30)
    max_retries=5,       # Retry attempts on RATELIMITED (default: 5)
    cache_ttl=60.0,      # Cache TTL in seconds; 0 = disable (default: 60)
    cache_max_size=256,  # Max cache entries (default: 256)
)
```

### Disable caching

```python
client = LinearClient("lin_api_...", cache_ttl=0)
```

### Custom httpx client (for testing / proxies)

```python
import httpx
from linear_client import LinearClient, LinearAuth

transport = httpx.MockTransport(...)  # or httpx.HTTPTransport(proxy=...)
http = httpx.Client(auth=LinearAuth("lin_api_..."), transport=transport)
client = LinearClient("lin_api_...", http_client=http)
```

---

## Rate Limiting

Linear enforces two independent limits ([Linear rate limiting docs](https://linear.app/developers/rate-limiting)):

| Type | Authenticated (API key) | Unauthenticated |
|------|------------------------|-----------------|
| **Requests / hour** | 5,000 per user | 60 per IP |
| **Complexity points / hour** | 3,000,000 per user | 10,000 per IP |
| **Max single-query complexity** | 10,000 | 10,000 |

When the request limit is hit, Linear returns HTTP 400 with a GraphQL error
code of `RATELIMITED`. The client automatically retries with exponential
back-off (up to `max_retries`, default 5).

### Check your remaining quota

```python
with LinearClient("lin_api_...") as client:
    client.get_viewer()        # any request updates the quota
    q = client.quota()
    print(f"Requests remaining: {q['remaining']} / {q['limit']}")
    # reset_ms is UTC epoch milliseconds
```

### Response headers

| Header | Description |
|--------|-------------|
| `X-RateLimit-Requests-Limit` | Max requests per hour |
| `X-RateLimit-Requests-Remaining` | Requests left in current window |
| `X-RateLimit-Requests-Reset` | Window reset time (UTC epoch ms) |
| `X-Complexity` | Complexity of the last query |
| `X-RateLimit-Complexity-Limit` | Max complexity points per hour |
| `X-RateLimit-Complexity-Remaining` | Complexity points remaining |

---

## GraphQL Query Customization

Every method contains its own inline GraphQL query string, making it easy
to customise fields without touching the client internals.

### Run a custom query directly

```python
MY_QUERY = """
query MyIssues($userId: ID!) {
    user(id: $userId) {
        assignedIssues(first: 20) {
            nodes {
                id
                identifier
                title
                dueDate
                priority
                state { name type }
            }
        }
    }
}
"""

data = client._execute(MY_QUERY, {"userId": "USER_ID"}, use_cache=True)
issues = data["user"]["assignedIssues"]["nodes"]
```

### Filter syntax

Linear uses a structured filter object for most list queries:

```python
# Issues assigned to a specific user, high priority only
issues = client.list_issues(
    filter={
        "assignee": {"id": {"eq": "USER_ID"}},
        "priority": {"gte": 2},            # 1=urgent, 2=high, 3=medium, 4=low
    }
)

# Issues due this week
import datetime
today = datetime.date.today().isoformat()
issues = client.list_issues(
    filter={"dueDate": {"lte": today}},
)

# Include archived issues
issues = client.list_issues(team_id="TEAM_ID", include_archived=True)
```

---

## Issue Priorities

| Value | Label |
|-------|-------|
| `0` | No priority |
| `1` | Urgent |
| `2` | High |
| `3` | Medium |
| `4` | Low |

---

## Project Health Values

| Value | Description |
|-------|-------------|
| `"onTrack"` | Project is on track |
| `"atRisk"` | Project is at risk |
| `"offTrack"` | Project is off track |

---

## Issue Relation Types

| Value | Description |
|-------|-------------|
| `"blocks"` | Source issue blocks target |
| `"duplicate"` | Source duplicates target |
| `"relates"` | General relation (default) |

---

## Examples

### Create and link issues

```python
with LinearClient("lin_api_...") as client:
    teams = client.list_teams()
    team_id = teams["nodes"][0]["id"]

    # Get workflow states for the team
    states = client.list_workflow_states_for_team(team_id)
    backlog = next(s for s in states if s["type"] == "backlog")

    # Create a parent issue
    parent = client.create_issue(
        team_id=team_id,
        title="Implement new authentication flow",
        description="## Overview\n\nRefactor auth to use OAuth2...",
        priority=2,
        state_id=backlog["id"],
    )
    parent_id = parent["issue"]["id"]

    # Create a sub-issue
    sub = client.create_issue(
        team_id=team_id,
        title="Design OAuth callback endpoint",
        parent_id=parent_id,
        priority=2,
    )

    # Add a comment
    client.create_comment(parent_id, "Tracking in sprint cycle.")
    print(f"Created: {parent['issue']['identifier']}")
```

### Bulk-fetch all issues for a team

```python
QUERY = """
query AllIssues($filter: IssueFilter, $first: Int, $after: String) {
    issues(filter: $filter, first: $first, after: $after) {
        nodes { id identifier title state { name type } }
        pageInfo { hasNextPage endCursor }
    }
}
"""
all_issues = client.paginate_all(
    QUERY,
    {"filter": {"team": {"id": {"eq": "TEAM_ID"}}}, "first": 100},
    data_key="issues",
)
print(f"Fetched {len(all_issues)} issues")
```

### Move issues to a cycle

```python
# Create a 2-week cycle
import datetime
start = datetime.datetime.utcnow().isoformat() + "Z"
end = (datetime.datetime.utcnow() + datetime.timedelta(weeks=2)).isoformat() + "Z"

cycle_result = client.create_cycle(team_id, start, end, name="Sprint 42")
cycle_id = cycle_result["cycle"]["id"]

# Assign an issue to the cycle
client.update_issue("ISSUE_UUID", cycleId=cycle_id)
```

---

## API Reference

- [Linear GraphQL API docs](https://linear.app/developers/graphql)
- [Rate limiting details](https://linear.app/developers/rate-limiting)
- [Apollo Studio schema browser](https://studio.apollographql.com/public/Linear-API/variant/current/home)
