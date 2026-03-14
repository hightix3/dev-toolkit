"""
linear_client — A production-ready Python client for the Linear GraphQL API.

Quick start::

    from linear_client import LinearClient

    with LinearClient("lin_api_xxxxxxxxxxxx") as client:
        me = client.get_viewer()
        print(f"Logged in as: {me['name']}")

        teams = client.list_teams()
        for team in teams["nodes"]:
            print(team["name"])

        issues = client.list_issues(team_id=teams["nodes"][0]["id"])
        for issue in issues["nodes"]:
            print(issue["identifier"], issue["title"])
"""

from .auth import LinearAuth, LinearOAuthAuth
from .client import LinearClient
from .exceptions import (
    AuthenticationError,
    GraphQLError,
    LinearError,
    NetworkError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    TimeoutError,
    ValidationError,
)

__all__ = [
    # Client
    "LinearClient",
    # Auth
    "LinearAuth",
    "LinearOAuthAuth",
    # Exceptions
    "LinearError",
    "GraphQLError",
    "AuthenticationError",
    "RateLimitError",
    "NotFoundError",
    "PermissionError",
    "ValidationError",
    "NetworkError",
    "TimeoutError",
]

__version__ = "1.0.0"
__author__ = "linear_client"
