# SerpAPI Python Client

Auto-generated from [SerpAPI documentation](https://serpapi.com/search-api). Includes a standalone Python client and LangChain integration.

**22+ search engines** — with retry, caching, and auto-pagination built in.

## Installation

```bash
# Standalone client (no LangChain)
pip install httpx

# With LangChain integration
pip install httpx langchain langchain-community google-search-results
```

Then copy the `serpapi_client/` directory into your project.

## Quick Start

```python
from serpapi_client import SerpAPIClient

client = SerpAPIClient(api_key="your_key")

# Google search
results = client.google("best python frameworks 2026")
for r in results["organic_results"][:5]:
    print(r["title"], r["link"])

# YouTube search
videos = client.youtube("python tutorial")

# Walmart product search
products = client.walmart("laptop")

# Auto-paginate (get all results across multiple pages)
all_results = client.auto_paginate(engine="google", q="machine learning", max_pages=3)
```

## Authentication

SerpAPI uses an API key passed as a query parameter.

```bash
# Set environment variable (recommended)
export SERPAPI_API_KEY=your_key_here
```

Or pass directly:
```python
client = SerpAPIClient(api_key="your_key_here")
```

Get your key at [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key). Free tier: 250 searches/month.

## All Methods

### Google Engines

| Method | Engine | Description |
|--------|--------|-------------|
| `google(query)` | Google Web | Standard web search |
| `google_images(query)` | Google Images | Image search |
| `google_news(query)` | Google News | News articles |
| `google_videos(query)` | Google Videos | Video search |
| `google_shopping(query)` | Google Shopping | Product/price search |
| `google_local(query)` | Google Local | Local businesses / Maps |
| `google_patents(query)` | Google Patents | Patent search |

### Other Search Engines

| Method | Engine | Description |
|--------|--------|-------------|
| `bing(query)` | Bing | Microsoft Bing search |
| `yahoo(query)` | Yahoo | Yahoo search |
| `baidu(query)` | Baidu | Chinese search engine |
| `duckduckgo(query)` | DuckDuckGo | Privacy-focused search |
| `yandex(query)` | Yandex | Russian search engine |
| `naver(query)` | Naver | Korean search engine |
| `youtube(query)` | YouTube | Video search |

### E-commerce Engines

| Method | Engine | Description |
|--------|--------|-------------|
| `walmart(query)` | Walmart | Product search |
| `ebay(query)` | eBay | Auction/product search |
| `etsy(query)` | Etsy | Handmade/vintage search |
| `home_depot(query)` | Home Depot | Home improvement products |
| `target(query)` | Target | Retail product search |
| `lowes(query)` | Lowe's | Home improvement products |
| `bestbuy(query)` | Best Buy | Electronics search |

### App Store Engines

| Method | Engine | Description |
|--------|--------|-------------|
| `apple_app_store(query)` | Apple App Store | iOS app search |
| `google_play(query)` | Google Play | Android app search |

### Utility Methods

| Method | Description |
|--------|-------------|
| `search(query, engine)` | Generic search on any engine |
| `auto_paginate(engine, max_pages)` | Auto-paginate through results |
| `get_account()` | Check account info and usage |
| `get_locations(q)` | Search supported locations |

## Common Parameters

All search methods accept these keyword arguments:

| Parameter | Type | Description |
|-----------|------|-------------|
| `gl` | str | Country code (`"us"`, `"uk"`, `"cn"`) |
| `hl` | str | Language code (`"en"`, `"zh-CN"`) |
| `location` | str | Geographic location (`"Austin, TX"`) |
| `num` | int | Number of results (1-100) |
| `start` | int | Result offset (pagination: 0, 10, 20...) |
| `safe` | str | Adult filter (`"active"` or `"off"`) |
| `device` | str | Device type (`"desktop"`, `"mobile"`, `"tablet"`) |
| `no_cache` | bool | Force fresh results |

## Auto-Pagination

```python
# Get results across multiple pages
all_results = client.auto_paginate(
    engine="google",
    q="python",
    gl="us",
    max_pages=5,  # Fetch up to 5 pages
)
print(f"Total results: {len(all_results)}")
```

## Error Handling

```python
from serpapi_client import (
    SerpAPIError,
    AuthenticationError,
    RateLimitError,
    InvalidRequestError,
)

try:
    results = client.google("test")
except AuthenticationError:
    print("Invalid API key")
except RateLimitError:
    print("Search quota exceeded")
except InvalidRequestError as e:
    print(f"Bad request: {e.message}")
except SerpAPIError as e:
    print(f"API error [{e.status_code}]: {e.message}")
```

| Exception | When |
|-----------|------|
| `AuthenticationError` | Invalid or missing API key |
| `RateLimitError` | Monthly search quota exceeded |
| `InvalidRequestError` | Invalid parameters |
| `NotFoundError` | Endpoint not found |
| `ServerError` | SerpAPI server error (after retries) |

## Configuration

```python
client = SerpAPIClient(
    api_key="your_key",
    max_retries=5,    # Retry on 429/5xx (default: 5)
    cache_ttl=600,    # Cache results for 10 min (default: 600, 0 = off)
    timeout=30.0,     # HTTP timeout in seconds (default: 30)
)
```

## LangChain Integration

### Option 1: Built-in SerpAPIWrapper

```python
from serpapi_client.langchain_integration import create_langchain_serpapi_tool

# Simple tool using LangChain's built-in wrapper
tool = create_langchain_serpapi_tool(api_key="your_key")
result = tool.run("What is the capital of France?")

# With custom parameters (use Bing instead of Google)
tool = create_langchain_serpapi_tool(
    api_key="your_key",
    engine="bing",
    params={"gl": "us", "hl": "en"},
)
```

### Option 2: Custom Client Tool (recommended)

```python
from serpapi_client.langchain_integration import create_custom_serpapi_tool

# More control: caching, retries, all engines
tool = create_custom_serpapi_tool(
    api_key="your_key",
    engine="google",
    result_count=5,
    gl="us",
    hl="en",
)
result = tool.run("latest AI news")
```

### Option 3: Multi-Engine Toolkit

```python
from serpapi_client.langchain_integration import create_serpapi_toolkit

# Get 5 specialized tools at once
tools = create_serpapi_toolkit(api_key="your_key")
# tools = [web_search, news_search, image_search, shopping_search, youtube_search]

# Use with a LangChain agent
from langchain.agents import initialize_agent, AgentType
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4")
agent = initialize_agent(tools, llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION)
result = agent.invoke({"input": "Find me the latest news about Python 3.13"})
```

## Context Manager

```python
with SerpAPIClient() as client:
    results = client.google("test")
# Connection pool closed automatically
```
