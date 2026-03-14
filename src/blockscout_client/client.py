"""
Blockscout API Client — Full production-ready client.

Covers all 56 endpoints from the Blockscout v2 REST API
(https://eth.blockscout.com/api/v2).

Features
--------
* Exponential backoff with jitter on 429 and 5xx responses.
* TTL-based in-memory response cache for GET requests.
* Cursor-based pagination helper (``paginate``).
* Optional Blockscout PRO API key support via :class:`~blockscout_client.auth.BlockscoutAuth`.
* Context-manager support (``with BlockscoutClient(...) as client:``).
* Full type hints and docstrings on every public method.
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, Generator, Iterator, List, Optional

import httpx

from .auth import BlockscoutAuth
from .exceptions import (
    APIError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

_Params = Optional[Dict[str, Any]]
_JSON = Any

# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


class _CacheEntry:
    __slots__ = ("data", "expires_at")

    def __init__(self, data: _JSON, ttl: float) -> None:
        self.data = data
        self.expires_at = time.monotonic() + ttl


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class BlockscoutClient:
    """Production-ready async-capable HTTP client for the Blockscout v2 API.

    Args:
        base_url: Base URL for the API.  Defaults to the Ethereum mainnet
            explorer ``https://eth.blockscout.com/api/v2``.
        api_key: Optional Blockscout PRO API key.
        timeout: HTTP timeout in seconds (default 30).
        max_retries: Maximum number of retry attempts on transient failures
            (default 5).
        cache_ttl: Time-to-live in seconds for the GET response cache
            (default 60).  Set to 0 to disable caching.

    Example::

        with BlockscoutClient() as client:
            tx = client.get_transaction(
                "0xabc123..."
            )
            print(tx["hash"])
    """

    DEFAULT_BASE_URL = "https://eth.blockscout.com/api/v2"
    _RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 5,
        cache_ttl: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = BlockscoutAuth(api_key=api_key)
        self._timeout = timeout
        self._max_retries = max_retries
        self._cache_ttl = cache_ttl
        self._cache: Dict[str, _CacheEntry] = {}
        self._client = httpx.Client(
            auth=self._auth,
            timeout=self._timeout,
            headers={"Accept": "application/json"},
        )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "BlockscoutClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    # ------------------------------------------------------------------
    # Core request helpers
    # ------------------------------------------------------------------

    def _build_url(self, path: str) -> str:
        """Construct an absolute URL from a relative API path.

        Paths that already start with ``/api/`` (Celestia / health
        endpoints) are appended directly to the scheme+host portion of
        :attr:`_base_url`; all other paths are appended to the full
        base URL (which already contains ``/api/v2``).
        """
        if path.startswith("/api/") or path.startswith("/health"):
            # Strip the /api/v2 suffix to get scheme+host
            scheme_host = self._base_url.replace("/api/v2", "")
            return scheme_host + path
        return self._base_url + path

    def _cache_key(self, url: str, params: _Params) -> str:
        return f"{url}?{sorted((params or {}).items())}"

    def _get_cached(self, key: str) -> _JSON | None:
        entry = self._cache.get(key)
        if entry and time.monotonic() < entry.expires_at:
            return entry.data
        self._cache.pop(key, None)
        return None

    def _set_cached(self, key: str, data: _JSON) -> None:
        if self._cache_ttl > 0:
            self._cache[key] = _CacheEntry(data, self._cache_ttl)

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map HTTP status codes to typed exceptions."""
        code = response.status_code
        if code < 400:
            return
        try:
            body = response.json()
            message = body.get("message") or body.get("error") or str(body)
        except Exception:
            message = response.text or f"HTTP {code}"

        if code == 400:
            raise ValidationError(message, status_code=code, response=response)
        if code == 404:
            raise NotFoundError(message, status_code=code, response=response)
        if code == 429:
            raise RateLimitError(message, status_code=code, response=response)
        if code >= 500:
            raise ServerError(message, status_code=code, response=response)
        raise APIError(message, status_code=code, response=response)

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: _Params = None,
        json: _JSON = None,
    ) -> _JSON:
        """Execute an HTTP request with exponential backoff and jitter.

        Retries are performed for status codes listed in
        :attr:`_RETRY_STATUS_CODES` (429, 500, 502, 503, 504).

        Args:
            method: HTTP verb (``"GET"``, ``"POST"``, ``"PATCH"``).
            url: Absolute URL.
            params: Optional query string parameters.
            json: Optional JSON body for POST/PATCH requests.

        Returns:
            Parsed JSON response body.

        Raises:
            RateLimitError: If the server returns 429 after all retries.
            ServerError: If the server returns 5xx after all retries.
            NotFoundError: On 404.
            ValidationError: On 400.
            APIError: For any other 4xx/5xx error.
        """
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(
                    method, url, params=params, json=json
                )
                if response.status_code in self._RETRY_STATUS_CODES and attempt < self._max_retries:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait)
                    last_exc = None
                    continue
                self._raise_for_status(response)
                return response.json()
            except (RateLimitError, ServerError) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait)
                else:
                    raise
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait)
                else:
                    raise APIError(str(exc)) from exc
        if last_exc:
            raise last_exc  # type: ignore[misc]
        raise APIError("Request failed after retries")

    # ------------------------------------------------------------------
    # Public generic methods
    # ------------------------------------------------------------------

    def get(self, path: str, params: _Params = None) -> _JSON:
        """Perform a cached GET request.

        Args:
            path: API path (e.g. ``/transactions``).
            params: Optional query parameters.

        Returns:
            Parsed JSON response.
        """
        url = self._build_url(path)
        key = self._cache_key(url, params)
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        data = self._request_with_retry("GET", url, params=params)
        self._set_cached(key, data)
        return data

    def post(self, path: str, json: _JSON = None, params: _Params = None) -> _JSON:
        """Perform a POST request (not cached).

        Args:
            path: API path.
            json: Optional JSON body.
            params: Optional query parameters.

        Returns:
            Parsed JSON response.
        """
        url = self._build_url(path)
        return self._request_with_retry("POST", url, params=params, json=json)

    def patch(self, path: str, json: _JSON = None, params: _Params = None) -> _JSON:
        """Perform a PATCH request (not cached).

        Args:
            path: API path.
            json: Optional JSON body.
            params: Optional query parameters.

        Returns:
            Parsed JSON response.
        """
        url = self._build_url(path)
        return self._request_with_retry("PATCH", url, params=params, json=json)

    # ------------------------------------------------------------------
    # Pagination helper
    # ------------------------------------------------------------------

    def paginate(
        self, path: str, params: _Params = None
    ) -> Iterator[List[_JSON]]:
        """Iterate over all pages for a cursor-paginated endpoint.

        The Blockscout API uses ``next_page_params`` as the cursor field.
        Each call to this generator yields the ``items`` list for one page.

        Args:
            path: API path (e.g. ``/transactions``).
            params: Initial query parameters.

        Yields:
            Each page's list of items.

        Example::

            for page in client.paginate("/addresses/0xabc.../transactions"):
                for tx in page:
                    print(tx["hash"])
        """
        current_params: Dict[str, Any] = dict(params or {})
        while True:
            response = self.get(path, current_params)
            items = response.get("items", [])
            if items:
                yield items
            next_page = response.get("next_page_params")
            if not next_page:
                break
            current_params = {**current_params, **next_page}

    # ------------------------------------------------------------------
    # Endpoint methods — Search
    # ------------------------------------------------------------------

    def search(self, q: Optional[str] = None) -> _JSON:
        """Search across blocks, transactions, addresses, and tokens.

        GET /search

        Args:
            q: Search query string (address, tx hash, block number, token
               name, etc.).

        Returns:
            Search results dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if q is not None:
            params["q"] = q
        return self.get("/search", params or None)

    def search_check_redirect(self, q: Optional[str] = None) -> _JSON:
        """Check whether a search query should redirect to a specific page.

        GET /search/check-redirect

        Args:
            q: Search query string.

        Returns:
            Redirect information dict.
        """
        params: Dict[str, Any] = {}
        if q is not None:
            params["q"] = q
        return self.get("/search/check-redirect", params or None)

    # ------------------------------------------------------------------
    # Endpoint methods — Transactions
    # ------------------------------------------------------------------

    def list_transactions(
        self,
        filter: Optional[str] = None,
        type: Optional[str] = None,
        method: Optional[str] = None,
    ) -> _JSON:
        """Retrieve a paginated list of recent transactions.

        GET /transactions

        Args:
            filter: Optional transaction filter (e.g. ``"pending"``).
            type: Optional transaction type filter.
            method: Optional method filter.

        Returns:
            Dict with ``items`` (list of transactions) and
            ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if filter is not None:
            params["filter"] = filter
        if type is not None:
            params["type"] = type
        if method is not None:
            params["method"] = method
        return self.get("/transactions", params or None)

    def get_transaction(self, transaction_hash: str) -> _JSON:
        """Retrieve detailed information about a single transaction.

        GET /transactions/{transaction_hash}

        Args:
            transaction_hash: 0x-prefixed transaction hash.

        Returns:
            Transaction detail dict.
        """
        return self.get(f"/transactions/{transaction_hash}")

    def get_transaction_token_transfers(
        self,
        transaction_hash: str,
        type: Optional[str] = None,
    ) -> _JSON:
        """Retrieve token transfers associated with a transaction.

        GET /transactions/{transaction_hash}/token-transfers

        Args:
            transaction_hash: 0x-prefixed transaction hash.
            type: Optional token type filter (e.g. ``"ERC-20"``).

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if type is not None:
            params["type"] = type
        return self.get(
            f"/transactions/{transaction_hash}/token-transfers",
            params or None,
        )

    def get_transaction_internal_transactions(
        self, transaction_hash: str
    ) -> _JSON:
        """Retrieve internal transactions for a given transaction.

        GET /transactions/{transaction_hash}/internal-transactions

        Args:
            transaction_hash: 0x-prefixed transaction hash.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(
            f"/transactions/{transaction_hash}/internal-transactions"
        )

    def get_transaction_logs(self, transaction_hash: str) -> _JSON:
        """Retrieve event logs emitted by a transaction.

        GET /transactions/{transaction_hash}/logs

        Args:
            transaction_hash: 0x-prefixed transaction hash.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/transactions/{transaction_hash}/logs")

    def get_transaction_raw_trace(self, transaction_hash: str) -> _JSON:
        """Retrieve the raw execution trace of a transaction.

        GET /transactions/{transaction_hash}/raw-trace

        Args:
            transaction_hash: 0x-prefixed transaction hash.

        Returns:
            List of raw trace objects.
        """
        return self.get(f"/transactions/{transaction_hash}/raw-trace")

    def get_transaction_state_changes(self, transaction_hash: str) -> _JSON:
        """Retrieve state changes produced by a transaction.

        GET /transactions/{transaction_hash}/state-changes

        Args:
            transaction_hash: 0x-prefixed transaction hash.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/transactions/{transaction_hash}/state-changes")

    def get_transaction_summary(self, transaction_hash: str) -> _JSON:
        """Retrieve a human-readable summary of a transaction.

        GET /transactions/{transaction_hash}/summary

        Args:
            transaction_hash: 0x-prefixed transaction hash.

        Returns:
            Dict containing a ``summaries`` list with plain-language
            descriptions of the transaction's actions.
        """
        return self.get(f"/transactions/{transaction_hash}/summary")

    # ------------------------------------------------------------------
    # Endpoint methods — Blocks
    # ------------------------------------------------------------------

    def list_blocks(self, type: Optional[str] = None) -> _JSON:
        """Retrieve a paginated list of blocks.

        GET /blocks

        Args:
            type: Optional block type filter (e.g. ``"uncle"``).

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if type is not None:
            params["type"] = type
        return self.get("/blocks", params or None)

    def get_block(self, block_number_or_hash: str) -> _JSON:
        """Retrieve information about a specific block.

        GET /blocks/{block_number_or_hash}

        Args:
            block_number_or_hash: Block number (decimal) or 0x-prefixed
                block hash.

        Returns:
            Block detail dict.
        """
        return self.get(f"/blocks/{block_number_or_hash}")

    def get_block_transactions(self, block_number_or_hash: str) -> _JSON:
        """Retrieve transactions included in a specific block.

        GET /blocks/{block_number_or_hash}/transactions

        Args:
            block_number_or_hash: Block number (decimal) or 0x-prefixed
                block hash.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/blocks/{block_number_or_hash}/transactions")

    def get_block_withdrawals(self, block_number_or_hash: str) -> _JSON:
        """Retrieve validator withdrawals included in a specific block.

        GET /blocks/{block_number_or_hash}/withdrawals

        Args:
            block_number_or_hash: Block number (decimal) or 0x-prefixed
                block hash.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/blocks/{block_number_or_hash}/withdrawals")

    # ------------------------------------------------------------------
    # Endpoint methods — Token transfers & internal transactions (global)
    # ------------------------------------------------------------------

    def list_token_transfers(self) -> _JSON:
        """Retrieve a paginated list of all recent token transfers.

        GET /token-transfers

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get("/token-transfers")

    def list_internal_transactions(self) -> _JSON:
        """Retrieve a paginated list of all recent internal transactions.

        GET /internal-transactions

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get("/internal-transactions")

    # ------------------------------------------------------------------
    # Endpoint methods — Main page / dashboard
    # ------------------------------------------------------------------

    def get_main_page_transactions(self) -> _JSON:
        """Retrieve the latest transactions shown on the main explorer page.

        GET /main-page/transactions

        Returns:
            List of recent transaction dicts.
        """
        return self.get("/main-page/transactions")

    def get_main_page_blocks(self) -> _JSON:
        """Retrieve the latest blocks shown on the main explorer page.

        GET /main-page/blocks

        Returns:
            List of recent block dicts.
        """
        return self.get("/main-page/blocks")

    def get_indexing_status(self) -> _JSON:
        """Retrieve the current blockchain indexing status.

        GET /main-page/indexing-status

        Returns:
            Dict with fields such as ``finished_indexing``,
            ``finished_indexing_blocks``, and
            ``indexed_blocks_ratio``.
        """
        return self.get("/main-page/indexing-status")

    # ------------------------------------------------------------------
    # Endpoint methods — Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> _JSON:
        """Retrieve global blockchain statistics counters.

        GET /stats

        Returns:
            Dict with counters such as total transactions, blocks,
            addresses, and token count.
        """
        return self.get("/stats")

    def get_transactions_chart(self) -> _JSON:
        """Retrieve historical transaction volume chart data.

        GET /stats/charts/transactions

        Returns:
            Dict with a ``chart_data`` list of daily transaction counts.
        """
        return self.get("/stats/charts/transactions")

    def get_market_chart(self) -> _JSON:
        """Retrieve market cap and price chart data.

        GET /stats/charts/market

        Returns:
            Dict with ``available_supply`` and ``chart_data`` price history.
        """
        return self.get("/stats/charts/market")

    # ------------------------------------------------------------------
    # Endpoint methods — Addresses
    # ------------------------------------------------------------------

    def list_addresses(self) -> _JSON:
        """Retrieve the native coin holders list ordered by balance.

        GET /addresses

        Returns:
            Dict with ``items`` (address list) and ``next_page_params``.
        """
        return self.get("/addresses")

    def get_address(self, address_hash: str) -> _JSON:
        """Retrieve detailed information about an address.

        GET /addresses/{address_hash}

        Args:
            address_hash: 0x-prefixed Ethereum address.

        Returns:
            Address detail dict including balance, contract flags, ENS name,
            and token holdings.
        """
        return self.get(f"/addresses/{address_hash}")

    def get_address_counters(self, address_hash: str) -> _JSON:
        """Retrieve transaction and token transfer counters for an address.

        GET /addresses/{address_hash}/counters

        Args:
            address_hash: 0x-prefixed Ethereum address.

        Returns:
            Dict with ``transactions_count``, ``token_transfers_count``,
            ``gas_usage_count``, and ``validations_count``.
        """
        return self.get(f"/addresses/{address_hash}/counters")

    def get_address_transactions(
        self,
        address_hash: str,
        filter: Optional[str] = None,
    ) -> _JSON:
        """Retrieve transactions involving a specific address.

        GET /addresses/{address_hash}/transactions

        Args:
            address_hash: 0x-prefixed Ethereum address.
            filter: Optional direction filter: ``"to"`` or ``"from"``.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if filter is not None:
            params["filter"] = filter
        return self.get(f"/addresses/{address_hash}/transactions", params or None)

    def get_address_token_transfers(
        self,
        address_hash: str,
        type: Optional[str] = None,
        filter: Optional[str] = None,
        token: Optional[str] = None,
    ) -> _JSON:
        """Retrieve token transfers involving a specific address.

        GET /addresses/{address_hash}/token-transfers

        Args:
            address_hash: 0x-prefixed Ethereum address.
            type: Optional token type filter (e.g. ``"ERC-20"``).
            filter: Optional direction filter: ``"to"`` or ``"from"``.
            token: Optional token contract address to filter by.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if type is not None:
            params["type"] = type
        if filter is not None:
            params["filter"] = filter
        if token is not None:
            params["token"] = token
        return self.get(
            f"/addresses/{address_hash}/token-transfers", params or None
        )

    def get_address_internal_transactions(
        self,
        address_hash: str,
        filter: Optional[str] = None,
    ) -> _JSON:
        """Retrieve internal transactions involving a specific address.

        GET /addresses/{address_hash}/internal-transactions

        Args:
            address_hash: 0x-prefixed Ethereum address.
            filter: Optional direction filter: ``"to"`` or ``"from"``.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if filter is not None:
            params["filter"] = filter
        return self.get(
            f"/addresses/{address_hash}/internal-transactions", params or None
        )

    def get_address_logs(self, address_hash: str) -> _JSON:
        """Retrieve event logs emitted by or relating to an address.

        GET /addresses/{address_hash}/logs

        Args:
            address_hash: 0x-prefixed Ethereum address.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/addresses/{address_hash}/logs")

    def get_address_blocks_validated(self, address_hash: str) -> _JSON:
        """Retrieve blocks validated (mined/sealed) by an address.

        GET /addresses/{address_hash}/blocks-validated

        Args:
            address_hash: 0x-prefixed Ethereum address (validator/miner).

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/addresses/{address_hash}/blocks-validated")

    def get_address_token_balances(self, address_hash: str) -> _JSON:
        """Retrieve all token balances held by an address (no pagination).

        GET /addresses/{address_hash}/token-balances

        Args:
            address_hash: 0x-prefixed Ethereum address.

        Returns:
            List of token balance dicts, each containing token metadata
            and the current balance.
        """
        return self.get(f"/addresses/{address_hash}/token-balances")

    def get_address_tokens(
        self,
        address_hash: str,
        type: Optional[str] = None,
    ) -> _JSON:
        """Retrieve token balances for an address with filtering and pagination.

        GET /addresses/{address_hash}/tokens

        Args:
            address_hash: 0x-prefixed Ethereum address.
            type: Optional token standard filter (e.g. ``"ERC-20"``,
                ``"ERC-721"``, ``"ERC-1155"``).

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if type is not None:
            params["type"] = type
        return self.get(f"/addresses/{address_hash}/tokens", params or None)

    def get_address_coin_balance_history(self, address_hash: str) -> _JSON:
        """Retrieve the native coin balance history for an address.

        GET /addresses/{address_hash}/coin-balance-history

        Args:
            address_hash: 0x-prefixed Ethereum address.

        Returns:
            Dict with ``items`` (balance snapshots) and
            ``next_page_params``.
        """
        return self.get(f"/addresses/{address_hash}/coin-balance-history")

    def get_address_coin_balance_history_by_day(
        self, address_hash: str
    ) -> _JSON:
        """Retrieve daily native coin balance history for an address.

        GET /addresses/{address_hash}/coin-balance-history-by-day

        Args:
            address_hash: 0x-prefixed Ethereum address.

        Returns:
            List of daily balance snapshots (date + balance).
        """
        return self.get(
            f"/addresses/{address_hash}/coin-balance-history-by-day"
        )

    def get_address_withdrawals(self, address_hash: str) -> _JSON:
        """Retrieve validator withdrawals credited to an address.

        GET /addresses/{address_hash}/withdrawals

        Args:
            address_hash: 0x-prefixed Ethereum address.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/addresses/{address_hash}/withdrawals")

    def get_address_nft(
        self,
        address_hash: str,
        type: Optional[str] = None,
    ) -> _JSON:
        """Retrieve NFTs owned by an address.

        GET /addresses/{address_hash}/nft

        Args:
            address_hash: 0x-prefixed Ethereum address.
            type: Optional token type filter (``"ERC-721"`` or
                ``"ERC-1155"``).

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if type is not None:
            params["type"] = type
        return self.get(f"/addresses/{address_hash}/nft", params or None)

    def get_address_nft_collections(
        self,
        address_hash: str,
        type: Optional[str] = None,
    ) -> _JSON:
        """Retrieve NFTs owned by an address, grouped by collection.

        GET /addresses/{address_hash}/nft/collections

        Args:
            address_hash: 0x-prefixed Ethereum address.
            type: Optional token type filter (``"ERC-721"`` or
                ``"ERC-1155"``).

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if type is not None:
            params["type"] = type
        return self.get(
            f"/addresses/{address_hash}/nft/collections", params or None
        )

    # ------------------------------------------------------------------
    # Endpoint methods — Tokens
    # ------------------------------------------------------------------

    def list_tokens(
        self,
        q: Optional[str] = None,
        type: Optional[str] = None,
    ) -> _JSON:
        """Retrieve a paginated list of tokens.

        GET /tokens

        Args:
            q: Optional token name/symbol search query.
            type: Optional token standard filter (e.g. ``"ERC-20"``).

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if q is not None:
            params["q"] = q
        if type is not None:
            params["type"] = type
        return self.get("/tokens", params or None)

    def get_token(self, address_hash: str) -> _JSON:
        """Retrieve detailed information about a token contract.

        GET /tokens/{address_hash}

        Args:
            address_hash: 0x-prefixed token contract address.

        Returns:
            Token detail dict including name, symbol, decimals, supply,
            and holder count.
        """
        return self.get(f"/tokens/{address_hash}")

    def get_token_transfers(self, address_hash: str) -> _JSON:
        """Retrieve transfer events for a token contract.

        GET /tokens/{address_hash}/transfers

        Args:
            address_hash: 0x-prefixed token contract address.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/tokens/{address_hash}/transfers")

    def get_token_holders(self, address_hash: str) -> _JSON:
        """Retrieve token holders for a token contract.

        GET /tokens/{address_hash}/holders

        Args:
            address_hash: 0x-prefixed token contract address.

        Returns:
            Dict with ``items`` (holder list) and ``next_page_params``.
        """
        return self.get(f"/tokens/{address_hash}/holders")

    def get_token_counters(self, address_hash: str) -> _JSON:
        """Retrieve aggregated counters for a token contract.

        GET /tokens/{address_hash}/counters

        Args:
            address_hash: 0x-prefixed token contract address.

        Returns:
            Dict with ``transfers_count`` and ``token_holders_count``.
        """
        return self.get(f"/tokens/{address_hash}/counters")

    def list_token_instances(self, address_hash: str) -> _JSON:
        """Retrieve NFT instances for an ERC-721 or ERC-1155 contract.

        GET /tokens/{address_hash}/instances

        Args:
            address_hash: 0x-prefixed NFT contract address.

        Returns:
            Dict with ``items`` (NFT instance list) and
            ``next_page_params``.
        """
        return self.get(f"/tokens/{address_hash}/instances")

    def get_token_instance(self, address_hash: str, id: int) -> _JSON:
        """Retrieve a specific NFT instance by token ID.

        GET /tokens/{address_hash}/instances/{id}

        Args:
            address_hash: 0x-prefixed NFT contract address.
            id: Token ID (integer).

        Returns:
            NFT instance detail dict including metadata and ownership.
        """
        return self.get(f"/tokens/{address_hash}/instances/{id}")

    def get_token_instance_transfers(
        self, address_hash: str, id: int
    ) -> _JSON:
        """Retrieve transfer events for a specific NFT instance.

        GET /tokens/{address_hash}/instances/{id}/transfers

        Args:
            address_hash: 0x-prefixed NFT contract address.
            id: Token ID (integer).

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/tokens/{address_hash}/instances/{id}/transfers")

    def get_token_instance_holders(
        self, address_hash: str, id: int
    ) -> _JSON:
        """Retrieve holders of a specific NFT instance (ERC-1155).

        GET /tokens/{address_hash}/instances/{id}/holders

        Args:
            address_hash: 0x-prefixed ERC-1155 contract address.
            id: Token ID (integer).

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get(f"/tokens/{address_hash}/instances/{id}/holders")

    def get_token_instance_transfers_count(
        self, address_hash: str, id: int
    ) -> _JSON:
        """Retrieve the transfer count for a specific NFT instance.

        GET /tokens/{address_hash}/instances/{id}/transfers-count

        Args:
            address_hash: 0x-prefixed NFT contract address.
            id: Token ID (integer).

        Returns:
            Dict with a ``transfers_count`` field.
        """
        return self.get(
            f"/tokens/{address_hash}/instances/{id}/transfers-count"
        )

    def refetch_token_instance_metadata(
        self,
        address_hash: str,
        id: int,
        body: Optional[Dict[str, Any]] = None,
    ) -> _JSON:
        """Trigger a metadata re-fetch for a specific NFT instance.

        PATCH /tokens/{address_hash}/instances/{id}/refetch-metadata

        Args:
            address_hash: 0x-prefixed NFT contract address.
            id: Token ID (integer).
            body: Optional request body dict.

        Returns:
            Server response dict.
        """
        return self.patch(
            f"/tokens/{address_hash}/instances/{id}/refetch-metadata",
            json=body,
        )

    # ------------------------------------------------------------------
    # Endpoint methods — Smart contracts
    # ------------------------------------------------------------------

    def list_smart_contracts(
        self,
        q: Optional[str] = None,
        filter: Optional[str] = None,
    ) -> _JSON:
        """Retrieve a paginated list of verified smart contracts.

        GET /smart-contracts

        Args:
            q: Optional name/address search query.
            filter: Optional filter string.

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        params: Dict[str, Any] = {}
        if q is not None:
            params["q"] = q
        if filter is not None:
            params["filter"] = filter
        return self.get("/smart-contracts", params or None)

    def get_smart_contracts_counters(self) -> _JSON:
        """Retrieve aggregated counters for verified smart contracts.

        GET /smart-contracts/counters

        Returns:
            Dict with ``smart_contracts`` and ``new_smart_contracts_24h``.
        """
        return self.get("/smart-contracts/counters")

    def get_smart_contract(self, address_hash: str) -> _JSON:
        """Retrieve detailed information about a verified smart contract.

        GET /smart-contracts/{address_hash}

        Args:
            address_hash: 0x-prefixed contract address.

        Returns:
            Smart contract detail dict including ABI, source code,
            compiler version, and verification status.
        """
        return self.get(f"/smart-contracts/{address_hash}")

    # ------------------------------------------------------------------
    # Endpoint methods — Withdrawals
    # ------------------------------------------------------------------

    def list_withdrawals(self) -> _JSON:
        """Retrieve a paginated list of validator withdrawals.

        GET /withdrawals

        Returns:
            Dict with ``items`` and ``next_page_params``.
        """
        return self.get("/withdrawals")

    # ------------------------------------------------------------------
    # Endpoint methods — Account abstraction (ERC-4337)
    # ------------------------------------------------------------------

    def get_account_abstraction_status(self) -> _JSON:
        """Retrieve ERC-4337 account abstraction indexing status.

        GET /proxy/account-abstraction/status

        Returns:
            Dict describing the ERC-4337 indexing state.
        """
        return self.get("/proxy/account-abstraction/status")

    # ------------------------------------------------------------------
    # Endpoint methods — Celestia
    # ------------------------------------------------------------------

    def get_celestia_blob(
        self,
        height: Optional[int] = None,
        commitment: Optional[str] = None,
        skip_data: Optional[bool] = None,
    ) -> _JSON:
        """Retrieve a Celestia blob by block height and commitment.

        GET /api/v1/celestia/blob

        Args:
            height: Celestia block height.
            commitment: Base64-encoded blob commitment.
            skip_data: If ``True``, omit blob data from the response.

        Returns:
            Blob detail dict.
        """
        params: Dict[str, Any] = {}
        if height is not None:
            params["height"] = height
        if commitment is not None:
            params["commitment"] = commitment
        if skip_data is not None:
            params["skipData"] = skip_data
        return self.get("/api/v1/celestia/blob", params or None)

    def get_celestia_l2_batch_metadata(
        self,
        height: Optional[int] = None,
        namespace: Optional[str] = None,
        commitment: Optional[str] = None,
    ) -> _JSON:
        """Retrieve L2 batch metadata for a Celestia blob.

        GET /api/v1/celestia/l2BatchMetadata

        Args:
            height: Celestia block height.
            namespace: Hex-encoded namespace.
            commitment: Base64-encoded blob commitment.

        Returns:
            L2 batch metadata dict.
        """
        params: Dict[str, Any] = {}
        if height is not None:
            params["height"] = height
        if namespace is not None:
            params["namespace"] = namespace
        if commitment is not None:
            params["commitment"] = commitment
        return self.get("/api/v1/celestia/l2BatchMetadata", params or None)

    # ------------------------------------------------------------------
    # Endpoint methods — Health
    # ------------------------------------------------------------------

    def get_health(self, service: Optional[str] = None) -> _JSON:
        """Retrieve health status for Blockscout services.

        GET /health

        Args:
            service: Optional service name to query.

        Returns:
            Health status dict.
        """
        params: Dict[str, Any] = {}
        if service is not None:
            params["service"] = service
        return self.get("/health", params or None)
