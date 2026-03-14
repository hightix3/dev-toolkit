"""SerpAPI authentication handler.

SerpAPI uses an API key passed as the `api_key` query parameter.
"""

import os


class SerpAPIAuth:
    """SerpAPI key authentication.

    Usage:
        auth = SerpAPIAuth(api_key="your_key")
        # or from environment variable:
        auth = SerpAPIAuth()  # reads SERPAPI_API_KEY
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("SERPAPI_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "SerpAPI key is required. Pass api_key= or set SERPAPI_API_KEY env var."
            )
