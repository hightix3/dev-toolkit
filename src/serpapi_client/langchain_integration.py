"""LangChain integration for SerpAPI.

Provides two ways to use SerpAPI with LangChain:
1. Using the built-in LangChain SerpAPIWrapper (requires langchain-community)
2. Using our custom SerpAPIClient as a LangChain Tool (no extra deps)

Usage:
    # Option 1: Built-in LangChain wrapper
    from serpapi_client.langchain_integration import create_langchain_serpapi_tool
    tool = create_langchain_serpapi_tool()

    # Option 2: Custom client as LangChain tool
    from serpapi_client.langchain_integration import create_custom_serpapi_tool
    tool = create_custom_serpapi_tool(api_key="your_key")

    # Use with an agent
    from langchain.agents import initialize_agent, AgentType
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="gpt-4")
    agent = initialize_agent([tool], llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION)
    result = agent.invoke({"input": "What is the weather in San Francisco?"})
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Option 1: Built-in LangChain SerpAPIWrapper
# ---------------------------------------------------------------------------

def create_langchain_serpapi_tool(
    api_key: str = None,
    engine: str = "google",
    params: dict = None,
):
    """Create a LangChain tool using the built-in SerpAPIWrapper.

    Requires: pip install langchain-community google-search-results

    Args:
        api_key: SerpAPI key. Falls back to SERPAPI_API_KEY env var.
        engine: Default search engine ("google", "bing", etc.).
        params: Custom search parameters (gl, hl, etc.).

    Returns:
        A LangChain Tool instance for web search.
    """
    try:
        from langchain_community.utilities import SerpAPIWrapper
        from langchain.tools import Tool
    except ImportError:
        raise ImportError(
            "LangChain integration requires:\n"
            "  pip install langchain langchain-community google-search-results"
        )

    if api_key:
        os.environ["SERPAPI_API_KEY"] = api_key

    search_params = params or {}
    if engine != "google":
        search_params["engine"] = engine

    wrapper = SerpAPIWrapper(params=search_params) if search_params else SerpAPIWrapper()

    return Tool(
        name="web_search",
        description=(
            "Search the web for current information. "
            "Input should be a search query string. "
            "Returns relevant search results."
        ),
        func=wrapper.run,
    )


# ---------------------------------------------------------------------------
# Option 2: Custom SerpAPIClient as LangChain Tool
# ---------------------------------------------------------------------------

def create_custom_serpapi_tool(
    api_key: str = None,
    engine: str = "google",
    result_count: int = 5,
    include_snippets: bool = True,
    **default_params,
):
    """Create a LangChain tool using our custom SerpAPIClient.

    Requires: pip install langchain httpx

    This option gives you more control: caching, retries, and access to
    all 22+ search engines.

    Args:
        api_key: SerpAPI key. Falls back to SERPAPI_API_KEY env var.
        engine: Default search engine.
        result_count: Number of results to return (default 5).
        include_snippets: Include result snippets in output (default True).
        **default_params: Default search parameters (gl, hl, location, etc.).

    Returns:
        A LangChain Tool instance.
    """
    try:
        from langchain.tools import Tool
    except ImportError:
        raise ImportError("LangChain integration requires: pip install langchain")

    from .client import SerpAPIClient

    client = SerpAPIClient(api_key=api_key)

    def _search(query: str) -> str:
        """Run search and format results as text."""
        params = {**default_params}
        results = client.search(query, engine=engine, **params)

        organic = results.get("organic_results", [])[:result_count]
        if not organic:
            # Try knowledge graph or answer box
            kg = results.get("knowledge_graph", {})
            if kg:
                return kg.get("description", str(kg.get("title", "No results found")))
            answer = results.get("answer_box", {})
            if answer:
                return answer.get("answer") or answer.get("snippet", "No results found")
            return "No results found."

        formatted = []
        for i, r in enumerate(organic, 1):
            title = r.get("title", "")
            link = r.get("link", "")
            snippet = r.get("snippet", "")
            entry = f"{i}. {title}\n   {link}"
            if include_snippets and snippet:
                entry += f"\n   {snippet}"
            formatted.append(entry)

        return "\n\n".join(formatted)

    return Tool(
        name="web_search",
        description=(
            "Search the web for current information using SerpAPI. "
            "Input should be a search query string. "
            "Returns top search results with titles, URLs, and snippets."
        ),
        func=_search,
    )


# ---------------------------------------------------------------------------
# Multi-engine toolkit
# ---------------------------------------------------------------------------

def create_serpapi_toolkit(api_key: str = None, **default_params) -> list:
    """Create a set of specialized LangChain tools for different search types.

    Returns a list of tools:
    - web_search: General web search (Google)
    - news_search: Google News search
    - image_search: Google Images search
    - shopping_search: Google Shopping search
    - youtube_search: YouTube video search

    Args:
        api_key: SerpAPI key.
        **default_params: Default params applied to all searches.

    Returns:
        List of LangChain Tool instances.
    """
    try:
        from langchain.tools import Tool
    except ImportError:
        raise ImportError("LangChain integration requires: pip install langchain")

    from .client import SerpAPIClient

    client = SerpAPIClient(api_key=api_key)

    def _make_search(method, result_key: str, count: int = 5):
        def _search(query: str) -> str:
            results = method(query, **default_params)
            items = results.get(result_key, [])[:count]
            if not items:
                return "No results found."
            formatted = []
            for i, r in enumerate(items, 1):
                title = r.get("title", r.get("name", ""))
                link = r.get("link", r.get("url", ""))
                snippet = r.get("snippet", r.get("description", ""))
                entry = f"{i}. {title}"
                if link:
                    entry += f"\n   {link}"
                if snippet:
                    entry += f"\n   {snippet}"
                formatted.append(entry)
            return "\n\n".join(formatted)
        return _search

    tools = [
        Tool(
            name="web_search",
            description="Search the web for general information. Input: search query.",
            func=_make_search(client.google, "organic_results"),
        ),
        Tool(
            name="news_search",
            description="Search for recent news articles. Input: news topic.",
            func=_make_search(client.google_news, "news_results"),
        ),
        Tool(
            name="image_search",
            description="Search for images. Input: image description.",
            func=_make_search(client.google_images, "images_results"),
        ),
        Tool(
            name="shopping_search",
            description="Search for products and prices. Input: product name.",
            func=_make_search(client.google_shopping, "shopping_results"),
        ),
        Tool(
            name="youtube_search",
            description="Search for YouTube videos. Input: video topic.",
            func=_make_search(client.youtube, "video_results"),
        ),
    ]

    return tools
