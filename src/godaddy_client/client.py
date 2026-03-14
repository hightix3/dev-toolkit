"""
GoDaddy Domains API — Production-ready Python client.

Supports:
- All 65 endpoints (v1 + v2)
- Automatic retry with exponential back-off + jitter (429 / 5xx)
- TTL-based response cache for GET requests
- Offset / marker pagination helpers
- Context manager usage (``with GoDaddyClient(...) as client:``)

Quick start::

    from godaddy_client import GoDaddyClient, GoDaddyAuth

    auth = GoDaddyAuth("my_key", "my_secret")
    with GoDaddyClient(auth=auth) as client:
        domains = client.list_domains()

OTE (test) environment::

    client = GoDaddyClient(auth=auth, base_url="https://api.ote-godaddy.com")
"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, Iterator, List, Optional, Union

import httpx

from .auth import GoDaddyAuth
from .exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PRODUCTION_BASE_URL = "https://api.godaddy.com"
_OTE_BASE_URL = "https://api.ote-godaddy.com"

_DEFAULT_TIMEOUT = 30.0          # seconds
_MAX_RETRIES = 4
_BACKOFF_BASE = 0.5              # seconds
_BACKOFF_MAX = 30.0              # seconds
_CACHE_DEFAULT_TTL = 60          # seconds

_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Internal cache
# ---------------------------------------------------------------------------

class _CacheEntry:
    __slots__ = ("data", "expires_at")

    def __init__(self, data: Any, ttl: float) -> None:
        self.data = data
        self.expires_at = time.monotonic() + ttl


class _ResponseCache:
    def __init__(self) -> None:
        self._store: Dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.data

    def set(self, key: str, data: Any, ttl: float) -> None:
        self._store[key] = _CacheEntry(data, ttl)

    def invalidate(self, prefix: str = "") -> None:
        if prefix:
            keys = [k for k in self._store if k.startswith(prefix)]
        else:
            keys = list(self._store.keys())
        for k in keys:
            del self._store[k]


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class GoDaddyClient:
    """
    Full-featured client for the GoDaddy Domains API.

    Parameters
    ----------
    auth:
        A :class:`~godaddy_client.GoDaddyAuth` instance (or any ``httpx.Auth``).
    base_url:
        Override the base URL.  Use ``https://api.ote-godaddy.com`` for OTE.
    timeout:
        HTTP request timeout in seconds (default 30).
    max_retries:
        Maximum number of retry attempts on transient errors (default 4).
    cache_ttl:
        TTL in seconds for GET response cache.  Pass ``0`` to disable (default 60).
    """

    def __init__(
        self,
        auth: Union[GoDaddyAuth, httpx.Auth],
        base_url: str = _PRODUCTION_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _MAX_RETRIES,
        cache_ttl: float = _CACHE_DEFAULT_TTL,
    ) -> None:
        self._auth = auth
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._cache_ttl = cache_ttl
        self._cache = _ResponseCache()
        self._http = httpx.Client(
            auth=auth,
            base_url=self._base_url,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "GoDaddyClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _build_cache_key(self, path: str, params: Optional[Dict[str, Any]]) -> str:
        """Build a deterministic cache key from path + sorted query params."""
        if params:
            sorted_params = "&".join(
                f"{k}={v}" for k, v in sorted(params.items()) if v is not None
            )
            return f"{path}?{sorted_params}"
        return path

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        use_cache: bool = True,
    ) -> Any:
        """
        Execute an HTTP request with exponential back-off retry.

        Retries are triggered on HTTP 429 and 5xx responses.
        Jitter is applied to avoid thundering-herd issues.

        Parameters
        ----------
        method:   HTTP verb (GET, POST, PUT, PATCH, DELETE).
        path:     API path, e.g. ``/v1/domains``.
        params:   Query-string parameters (``None`` values are omitted).
        json:     Request body to serialise as JSON.
        headers:  Extra request headers.
        use_cache:
            If *True* and method is GET, check/populate the TTL cache.
        """
        # Sanitise params — remove None values
        clean_params = (
            {k: v for k, v in params.items() if v is not None}
            if params
            else None
        ) or None

        # Cache look-up (GET only)
        is_get = method.upper() == "GET"
        cache_key = self._build_cache_key(path, clean_params) if is_get else ""
        if is_get and use_cache and self._cache_ttl > 0:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt <= self._max_retries:
            try:
                response = self._http.request(
                    method,
                    path,
                    params=clean_params,
                    json=json,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                last_exc = exc
                wait = self._backoff(attempt)
                time.sleep(wait)
                attempt += 1
                continue
            except httpx.RequestError as exc:
                raise APIError(f"HTTP request failed: {exc}") from exc

            status = response.status_code

            if status in _RETRY_STATUS_CODES:
                if attempt >= self._max_retries:
                    break
                retry_after: Optional[float] = None
                if "Retry-After" in response.headers:
                    try:
                        retry_after = float(response.headers["Retry-After"])
                    except ValueError:
                        pass
                wait = retry_after if retry_after is not None else self._backoff(attempt)
                time.sleep(wait)
                attempt += 1
                continue

            # Raise domain exceptions for error status codes
            self._raise_for_status(response)

            # Parse response
            data: Any = None
            if response.content:
                try:
                    data = response.json()
                except Exception:
                    data = response.text

            # Store in cache
            if is_get and use_cache and self._cache_ttl > 0:
                self._cache.set(cache_key, data, self._cache_ttl)

            return data

        # All retries exhausted
        exc_msg = f"Max retries ({self._max_retries}) exceeded for {method} {path}"
        if last_exc:
            raise APIError(exc_msg) from last_exc
        raise APIError(exc_msg)

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Return exponential back-off delay with jitter."""
        delay = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_MAX)
        return delay + random.uniform(0, delay * 0.1)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Map HTTP error responses to typed exceptions."""
        status = response.status_code
        if status < 400:
            return

        try:
            body = response.json()
        except Exception:
            body = response.text

        message = (
            body.get("message", str(body))
            if isinstance(body, dict)
            else str(body)
        )

        if status in (401, 403):
            raise AuthenticationError(message, status_code=status, response_body=body)
        if status == 404:
            raise NotFoundError(message, status_code=status, response_body=body)
        if status == 429:
            raise RateLimitError(message, status_code=status, response_body=body)
        if status in (400, 422):
            raise ValidationError(message, status_code=status, response_body=body)
        if status >= 500:
            raise ServerError(message, status_code=status, response_body=body)
        raise APIError(message, status_code=status, response_body=body)

    # ------------------------------------------------------------------
    # Generic HTTP verbs
    # ------------------------------------------------------------------

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        use_cache: bool = True,
    ) -> Any:
        """Perform a GET request."""
        return self._request_with_retry(
            "GET", path, params=params, headers=headers, use_cache=use_cache
        )

    def post(
        self,
        path: str,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """Perform a POST request."""
        return self._request_with_retry(
            "POST", path, json=json, params=params, headers=headers
        )

    def put(
        self,
        path: str,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """Perform a PUT request."""
        return self._request_with_retry(
            "PUT", path, json=json, params=params, headers=headers
        )

    def patch(
        self,
        path: str,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """Perform a PATCH request."""
        return self._request_with_retry(
            "PATCH", path, json=json, params=params, headers=headers
        )

    def delete(
        self,
        path: str,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """Perform a DELETE request."""
        return self._request_with_retry(
            "DELETE", path, json=json, params=params, headers=headers
        )

    # ------------------------------------------------------------------
    # Pagination helper
    # ------------------------------------------------------------------

    def paginate(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        page_size: int = 100,
        headers: Optional[Dict[str, str]] = None,
    ) -> Iterator[Any]:
        """
        Iterate over all pages of a paginated GET endpoint.

        Yields each **item** (not each page).  Uses GoDaddy's ``marker``-based
        pagination automatically.

        Parameters
        ----------
        path:       API path, e.g. ``/v1/domains``.
        params:     Base query parameters.
        page_size:  Number of items per page (``limit`` parameter).
        headers:    Extra request headers.

        Example::

            for domain in client.paginate("/v1/domains"):
                print(domain["domain"])
        """
        p = dict(params or {})
        p["limit"] = page_size
        marker: Optional[str] = None

        while True:
            if marker:
                p["marker"] = marker
            data = self._request_with_retry(
                "GET", path, params=p, headers=headers, use_cache=False
            )

            # Normalise to a list
            items: List[Any] = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("data", data.get("domains", [data]))

            for item in items:
                yield item

            if not items or len(items) < page_size:
                break

            # Advance marker: use last item's marker/domain field if present
            last = items[-1]
            if isinstance(last, dict):
                marker = last.get("marker") or last.get("domain")
            else:
                break

    # ==================================================================
    # ▶  v1 ENDPOINTS
    # ==================================================================

    # ------------------------------------------------------------------
    # v1 — Domains (list / search / purchase)
    # ------------------------------------------------------------------

    def list_domains(
        self,
        statuses: Optional[List[str]] = None,
        status_groups: Optional[List[str]] = None,
        limit: Optional[int] = None,
        marker: Optional[str] = None,
        includes: Optional[List[str]] = None,
        modified_date: Optional[str] = None,
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Retrieve a list of Domains for the specified Shopper.

        ``GET /v1/domains``

        Parameters
        ----------
        statuses:
            Only include domains with these statuses.
        status_groups:
            Only include domains with these status groups.
        limit:
            Maximum number of domains to return (default set by API).
        marker:
            Pagination marker from a previous response.
        includes:
            Additional fields to include (e.g. ``["contacts", "nameServers"]``).
        modified_date:
            Only include domains modified after this ISO-8601 date-time.
        shopper_id:
            The Shopper ID (resellers only).
        """
        params: Dict[str, Any] = {
            "statuses": statuses,
            "statusGroups": status_groups,
            "limit": limit,
            "marker": marker,
            "includes": includes,
            "modifiedDate": modified_date,
        }
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "GET", "/v1/domains", params=params, headers=extra_headers or None
        )

    def get_domain_agreements(
        self,
        tlds: List[str],
        privacy: bool = False,
        for_transfer: Optional[bool] = None,
        market_id: Optional[str] = None,
    ) -> Any:
        """
        Retrieve the legal agreement(s) required to purchase the specified TLD
        and add-ons.

        ``GET /v1/domains/agreements``

        Parameters
        ----------
        tlds:
            List of TLDs whose agreements should be retrieved.
        privacy:
            Whether privacy add-on is included (default ``False``).
        for_transfer:
            Whether agreements for transfer are requested.
        market_id:
            Locale market identifier (e.g. ``"en-US"``).
        """
        params: Dict[str, Any] = {
            "tlds": tlds,
            "privacy": privacy,
            "forTransfer": for_transfer,
        }
        extra_headers: Dict[str, str] = {}
        if market_id:
            extra_headers["X-Market-Id"] = market_id
        return self._request_with_retry(
            "GET",
            "/v1/domains/agreements",
            params=params,
            headers=extra_headers or None,
        )

    def check_domain_availability(
        self,
        domain: str,
        check_type: Optional[str] = None,
        for_transfer: Optional[bool] = None,
    ) -> Any:
        """
        Determine whether or not the specified domain is available for purchase.

        ``GET /v1/domains/available``

        Parameters
        ----------
        domain:
            The domain name to check, e.g. ``"example.com"``.
        check_type:
            Type of availability check (``"FAST"`` or ``"FULL"``).
        for_transfer:
            Whether to check transfer availability.
        """
        params: Dict[str, Any] = {
            "domain": domain,
            "checkType": check_type,
            "forTransfer": for_transfer,
        }
        return self._request_with_retry("GET", "/v1/domains/available", params=params)

    def check_domains_availability_bulk(
        self,
        domains: List[str],
        check_type: Optional[str] = None,
    ) -> Any:
        """
        Determine whether or not the specified domains are available for purchase
        (bulk check).

        ``POST /v1/domains/available``

        Parameters
        ----------
        domains:
            List of domain names to check.
        check_type:
            Type of availability check (``"FAST"`` or ``"FULL"``).
        """
        params: Dict[str, Any] = {"checkType": check_type}
        return self._request_with_retry(
            "POST", "/v1/domains/available", json=domains, params=params
        )

    def validate_domain_contacts(
        self,
        body: Dict[str, Any],
        market_id: Optional[str] = None,
        private_label_id: Optional[str] = None,
    ) -> Any:
        """
        Validate the request body using the Domain Contact Validation Schema
        for specified domains.

        ``POST /v1/domains/contacts/validate``

        Parameters
        ----------
        body:
            Contact validation payload conforming to the GoDaddy schema.
        market_id:
            Market/locale for validation context.
        private_label_id:
            Private label ID for resellers.
        """
        params: Dict[str, Any] = {"marketId": market_id}
        extra_headers: Dict[str, str] = {}
        if private_label_id:
            extra_headers["X-Private-Label-Id"] = private_label_id
        return self._request_with_retry(
            "POST",
            "/v1/domains/contacts/validate",
            json=body,
            params=params,
            headers=extra_headers or None,
        )

    def purchase_domain(
        self,
        body: Dict[str, Any],
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Purchase and register the specified Domain (v1).

        ``POST /v1/domains/purchase``

        Parameters
        ----------
        body:
            Domain purchase payload (see :meth:`get_domain_purchase_schema`).
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "POST",
            "/v1/domains/purchase",
            json=body,
            headers=extra_headers or None,
        )

    def get_domain_purchase_schema(self, tld: str) -> Any:
        """
        Retrieve the schema to be submitted when registering a Domain for the
        specified TLD (v1).

        ``GET /v1/domains/purchase/schema/{tld}``

        Parameters
        ----------
        tld:
            The TLD to retrieve the schema for, e.g. ``"com"``.
        """
        return self._request_with_retry(
            "GET", f"/v1/domains/purchase/schema/{tld}"
        )

    def validate_domain_purchase(self, body: Dict[str, Any]) -> Any:
        """
        Validate the request body using the Domain Purchase Schema for the
        specified TLD (v1).

        ``POST /v1/domains/purchase/validate``

        Parameters
        ----------
        body:
            Domain purchase payload to validate.
        """
        return self._request_with_retry(
            "POST", "/v1/domains/purchase/validate", json=body
        )

    def suggest_domains(
        self,
        query: Optional[str] = None,
        country: Optional[str] = None,
        city: Optional[str] = None,
        sources: Optional[List[str]] = None,
        tlds: Optional[List[str]] = None,
        length_max: Optional[int] = None,
        length_min: Optional[int] = None,
        limit: Optional[int] = None,
        wait_ms: Optional[int] = None,
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Suggest alternate Domain names based on a seed Domain, a set of
        keywords, or the shopper's purchase history.

        ``GET /v1/domains/suggest``

        Parameters
        ----------
        query:
            Seed domain or keyword phrase.
        country:
            Country code to bias suggestions.
        city:
            City name to bias suggestions.
        sources:
            Data sources to use for suggestions.
        tlds:
            TLDs to filter suggestions.
        length_max:
            Maximum domain name length.
        length_min:
            Minimum domain name length.
        limit:
            Maximum number of suggestions to return.
        wait_ms:
            Maximum time in milliseconds to wait for suggestions.
        shopper_id:
            The Shopper ID (resellers only).
        """
        params: Dict[str, Any] = {
            "query": query,
            "country": country,
            "city": city,
            "sources": sources,
            "tlds": tlds,
            "lengthMax": length_max,
            "lengthMin": length_min,
            "limit": limit,
            "waitMs": wait_ms,
        }
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "GET",
            "/v1/domains/suggest",
            params=params,
            headers=extra_headers or None,
        )

    def list_tlds(self) -> Any:
        """
        Retrieve a list of TLDs supported and enabled for sale.

        ``GET /v1/domains/tlds``
        """
        return self._request_with_retry("GET", "/v1/domains/tlds")

    # ------------------------------------------------------------------
    # v1 — Single domain operations
    # ------------------------------------------------------------------

    def cancel_domain(self, domain: str) -> Any:
        """
        Cancel a purchased domain.

        ``DELETE /v1/domains/{domain}``

        Parameters
        ----------
        domain:
            The domain name to cancel, e.g. ``"example.com"``.
        """
        return self._request_with_retry("DELETE", f"/v1/domains/{domain}")

    def get_domain(self, domain: str, shopper_id: Optional[str] = None) -> Any:
        """
        Retrieve details for the specified Domain (v1).

        ``GET /v1/domains/{domain}``

        Parameters
        ----------
        domain:
            The domain name, e.g. ``"example.com"``.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "GET",
            f"/v1/domains/{domain}",
            headers=extra_headers or None,
        )

    def update_domain(
        self,
        domain: str,
        body: Dict[str, Any],
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Update details for the specified Domain (v1).

        ``PATCH /v1/domains/{domain}``

        Parameters
        ----------
        domain:
            The domain name, e.g. ``"example.com"``.
        body:
            Fields to update (e.g. ``renewAuto``, ``exposeWhois``).
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "PATCH",
            f"/v1/domains/{domain}",
            json=body,
            headers=extra_headers or None,
        )

    def update_domain_contacts(
        self,
        domain: str,
        contacts: Dict[str, Any],
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Update domain contacts (v1).

        ``PATCH /v1/domains/{domain}/contacts``

        Parameters
        ----------
        domain:
            The domain name.
        contacts:
            Contact data payload (``registrant``, ``admin``, ``tech``, ``billing``).
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "PATCH",
            f"/v1/domains/{domain}/contacts",
            json=contacts,
            headers=extra_headers or None,
        )

    def cancel_domain_privacy(
        self, domain: str, shopper_id: Optional[str] = None
    ) -> Any:
        """
        Submit a privacy cancellation request for the given domain.

        ``DELETE /v1/domains/{domain}/privacy``

        Parameters
        ----------
        domain:
            The domain name.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "DELETE",
            f"/v1/domains/{domain}/privacy",
            headers=extra_headers or None,
        )

    def purchase_domain_privacy(
        self,
        domain: str,
        body: Dict[str, Any],
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Purchase privacy for a specified domain.

        ``POST /v1/domains/{domain}/privacy/purchase``

        Parameters
        ----------
        domain:
            The domain name.
        body:
            Privacy purchase payload.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "POST",
            f"/v1/domains/{domain}/privacy/purchase",
            json=body,
            headers=extra_headers or None,
        )

    def add_dns_records(
        self,
        domain: str,
        records: List[Dict[str, Any]],
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Add the specified DNS Records to the specified Domain.

        ``PATCH /v1/domains/{domain}/records``

        Parameters
        ----------
        domain:
            The domain name.
        records:
            List of DNS record objects to add.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "PATCH",
            f"/v1/domains/{domain}/records",
            json=records,
            headers=extra_headers or None,
        )

    def replace_dns_records(
        self,
        domain: str,
        records: List[Dict[str, Any]],
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Replace all DNS Records for the specified Domain.

        ``PUT /v1/domains/{domain}/records``

        Parameters
        ----------
        domain:
            The domain name.
        records:
            Full replacement list of DNS record objects.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "PUT",
            f"/v1/domains/{domain}/records",
            json=records,
            headers=extra_headers or None,
        )

    def get_dns_records(
        self,
        domain: str,
        record_type: str,
        name: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Retrieve DNS Records for the specified Domain, optionally with the
        specified Type and/or Name.

        ``GET /v1/domains/{domain}/records/{type}/{name}``

        Parameters
        ----------
        domain:
            The domain name.
        record_type:
            DNS record type, e.g. ``"A"``, ``"CNAME"``, ``"MX"``.
        name:
            Record name / ``"@"`` for apex.
        offset:
            Number of records to skip.
        limit:
            Maximum number of records to return.
        shopper_id:
            The Shopper ID (resellers only).
        """
        params: Dict[str, Any] = {"offset": offset, "limit": limit}
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "GET",
            f"/v1/domains/{domain}/records/{record_type}/{name}",
            params=params,
            headers=extra_headers or None,
        )

    def replace_dns_records_by_type_name(
        self,
        domain: str,
        record_type: str,
        name: str,
        records: List[Dict[str, Any]],
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Replace DNS Records for the specified Domain with the specified Type and
        Name.

        ``PUT /v1/domains/{domain}/records/{type}/{name}``

        Parameters
        ----------
        domain:
            The domain name.
        record_type:
            DNS record type, e.g. ``"A"``.
        name:
            Record name.
        records:
            Replacement list of DNS record objects.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "PUT",
            f"/v1/domains/{domain}/records/{record_type}/{name}",
            json=records,
            headers=extra_headers or None,
        )

    def delete_dns_records_by_type_name(
        self,
        domain: str,
        record_type: str,
        name: str,
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Delete all DNS Records for the specified Domain with the specified Type
        and Name.

        ``DELETE /v1/domains/{domain}/records/{type}/{name}``

        Parameters
        ----------
        domain:
            The domain name.
        record_type:
            DNS record type, e.g. ``"A"``.
        name:
            Record name.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "DELETE",
            f"/v1/domains/{domain}/records/{record_type}/{name}",
            headers=extra_headers or None,
        )

    def replace_dns_records_by_type(
        self,
        domain: str,
        record_type: str,
        records: List[Dict[str, Any]],
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Replace all DNS Records for the specified Domain with the specified Type.

        ``PUT /v1/domains/{domain}/records/{type}``

        Parameters
        ----------
        domain:
            The domain name.
        record_type:
            DNS record type, e.g. ``"A"``.
        records:
            Replacement list of DNS record objects.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "PUT",
            f"/v1/domains/{domain}/records/{record_type}",
            json=records,
            headers=extra_headers or None,
        )

    def renew_domain(
        self,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Renew the specified Domain (v1).

        ``POST /v1/domains/{domain}/renew``

        Parameters
        ----------
        domain:
            The domain name.
        body:
            Optional renewal payload (e.g. ``{"period": 2}``).
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "POST",
            f"/v1/domains/{domain}/renew",
            json=body,
            headers=extra_headers or None,
        )

    def transfer_domain(
        self,
        domain: str,
        body: Dict[str, Any],
        shopper_id: Optional[str] = None,
    ) -> Any:
        """
        Purchase and start or restart a transfer in for the specified Domain.

        ``POST /v1/domains/{domain}/transfer``

        Parameters
        ----------
        domain:
            The domain name.
        body:
            Transfer request payload.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "POST",
            f"/v1/domains/{domain}/transfer",
            json=body,
            headers=extra_headers or None,
        )

    def verify_registrant_email(
        self, domain: str, shopper_id: Optional[str] = None
    ) -> Any:
        """
        Re-send Contact E-mail Verification for specified Domain.

        ``POST /v1/domains/{domain}/verifyRegistrantEmail``

        Parameters
        ----------
        domain:
            The domain name.
        shopper_id:
            The Shopper ID (resellers only).
        """
        extra_headers: Dict[str, str] = {}
        if shopper_id:
            extra_headers["X-Shopper-Id"] = shopper_id
        return self._request_with_retry(
            "POST",
            f"/v1/domains/{domain}/verifyRegistrantEmail",
            headers=extra_headers or None,
        )

    # ==================================================================
    # ▶  v2 ENDPOINTS
    # ==================================================================

    # ------------------------------------------------------------------
    # v2 — Single domain
    # ------------------------------------------------------------------

    def get_domain_v2(
        self,
        customer_id: str,
        domain: str,
        includes: Optional[List[str]] = None,
    ) -> Any:
        """
        Retrieve details for the specified Domain (v2).

        ``GET /v2/customers/{customerId}/domains/{domain}``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        includes:
            Additional fields to include.
        """
        params: Dict[str, Any] = {"includes": includes}
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/{domain}",
            params=params,
        )

    def cancel_change_of_registrant(
        self, customer_id: str, domain: str
    ) -> Any:
        """
        Cancel the pending Change of Registrant for the specified Domain.

        ``DELETE /v2/customers/{customerId}/domains/{domain}/changeOfRegistrant``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        """
        return self._request_with_retry(
            "DELETE",
            f"/v2/customers/{customer_id}/domains/{domain}/changeOfRegistrant",
        )

    def get_change_of_registrant(
        self, customer_id: str, domain: str
    ) -> Any:
        """
        Retrieve the Change of Registrant details for the specified Domain.

        ``GET /v2/customers/{customerId}/domains/{domain}/changeOfRegistrant``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        """
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/{domain}/changeOfRegistrant",
        )

    def add_dnssec_records(
        self,
        customer_id: str,
        domain: str,
        records: List[Dict[str, Any]],
    ) -> Any:
        """
        Add DNSSEC records to the specified Domain.

        ``PATCH /v2/customers/{customerId}/domains/{domain}/dnssecRecords``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        records:
            DNSSEC record objects to add.
        """
        return self._request_with_retry(
            "PATCH",
            f"/v2/customers/{customer_id}/domains/{domain}/dnssecRecords",
            json=records,
        )

    def delete_dnssec_records(
        self,
        customer_id: str,
        domain: str,
        records: List[Dict[str, Any]],
    ) -> Any:
        """
        Remove DNSSEC records from the specified Domain.

        ``DELETE /v2/customers/{customerId}/domains/{domain}/dnssecRecords``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        records:
            DNSSEC record objects to remove.
        """
        return self._request_with_retry(
            "DELETE",
            f"/v2/customers/{customer_id}/domains/{domain}/dnssecRecords",
            json=records,
        )

    def replace_nameservers(
        self,
        customer_id: str,
        domain: str,
        nameservers: List[str],
    ) -> Any:
        """
        Update the specified Domain's nameservers (v2).

        ``PUT /v2/customers/{customerId}/domains/{domain}/nameServers``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        nameservers:
            List of nameserver hostnames.
        """
        return self._request_with_retry(
            "PUT",
            f"/v2/customers/{customer_id}/domains/{domain}/nameServers",
            json={"nameServers": nameservers},
        )

    def get_privacy_email_forwarding(
        self, customer_id: str, domain: str
    ) -> Any:
        """
        Retrieve privacy email forwarding settings for the specified Domain.

        ``GET /v2/customers/{customerId}/domains/{domain}/privacy/forwarding``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        """
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/{domain}/privacy/forwarding",
        )

    def update_privacy_email_forwarding(
        self,
        customer_id: str,
        domain: str,
        body: Dict[str, Any],
    ) -> Any:
        """
        Update privacy email forwarding settings for the specified Domain.

        ``PATCH /v2/customers/{customerId}/domains/{domain}/privacy/forwarding``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Forwarding settings payload.
        """
        return self._request_with_retry(
            "PATCH",
            f"/v2/customers/{customer_id}/domains/{domain}/privacy/forwarding",
            json=body,
        )

    def redeem_domain(
        self,
        customer_id: str,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Redeem the specified Domain (v2) from redemption period.

        ``POST /v2/customers/{customerId}/domains/{domain}/redeem``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Optional redemption payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/redeem",
            json=body,
        )

    def renew_domain_v2(
        self,
        customer_id: str,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Renew the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/renew``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Optional renewal payload (e.g. ``{"period": 2}``).
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/renew",
            json=body,
        )

    def transfer_domain_v2(
        self,
        customer_id: str,
        domain: str,
        body: Dict[str, Any],
    ) -> Any:
        """
        Purchase and start or restart a transfer in for the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/transfer``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Transfer request payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/transfer",
            json=body,
        )

    def get_transfer_status(
        self, customer_id: str, domain: str
    ) -> Any:
        """
        Retrieve the transfer status for the specified Domain (v2).

        ``GET /v2/customers/{customerId}/domains/{domain}/transfer``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        """
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/{domain}/transfer",
        )

    def validate_domain_transfer(
        self,
        customer_id: str,
        domain: str,
        body: Dict[str, Any],
    ) -> Any:
        """
        Validate the transfer payload for the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/transfer/validate``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Transfer validation payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/transfer/validate",
            json=body,
        )

    def accept_transfer_in(
        self,
        customer_id: str,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Accept the transfer in for the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/transferInAccept``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Optional acceptance payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/transferInAccept",
            json=body,
        )

    def cancel_transfer_in(
        self,
        customer_id: str,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Cancel the transfer in for the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/transferInCancel``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Optional cancellation payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/transferInCancel",
            json=body,
        )

    def restart_transfer_in(
        self,
        customer_id: str,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Restart the transfer in for the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/transferInRestart``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Optional restart payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/transferInRestart",
            json=body,
        )

    def retry_transfer_in(
        self,
        customer_id: str,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Retry the transfer in for the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/transferInRetry``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Optional retry payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/transferInRetry",
            json=body,
        )

    def initiate_transfer_out(
        self,
        customer_id: str,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Initiate transfer out for a .uk domain.

        ``POST /v2/customers/{customerId}/domains/{domain}/transferOut``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Optional transfer-out payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/transferOut",
            json=body,
        )

    def accept_transfer_out(
        self,
        customer_id: str,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Accept the transfer out for the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/transferOutAccept``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Optional acceptance payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/transferOutAccept",
            json=body,
        )

    def reject_transfer_out(
        self,
        customer_id: str,
        domain: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Reject the transfer out for the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/transferOutReject``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Optional rejection payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/transferOutReject",
            json=body,
        )

    # ------------------------------------------------------------------
    # v2 — Domain forwarding
    # ------------------------------------------------------------------

    def delete_domain_forwarding(
        self, customer_id: str, fqdn: str
    ) -> Any:
        """
        Cancel domain forwarding for the specified FQDN.

        ``DELETE /v2/customers/{customerId}/domains/forwards/{fqdn}``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        fqdn:
            The fully qualified domain name.
        """
        return self._request_with_retry(
            "DELETE",
            f"/v2/customers/{customer_id}/domains/forwards/{fqdn}",
        )

    def get_domain_forwarding(
        self, customer_id: str, fqdn: str
    ) -> Any:
        """
        Retrieve domain forwarding rule for the specified FQDN.

        ``GET /v2/customers/{customerId}/domains/forwards/{fqdn}``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        fqdn:
            The fully qualified domain name.
        """
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/forwards/{fqdn}",
        )

    def replace_domain_forwarding(
        self,
        customer_id: str,
        fqdn: str,
        body: Dict[str, Any],
    ) -> Any:
        """
        Replace domain forwarding rule for the specified FQDN.

        ``PUT /v2/customers/{customerId}/domains/forwards/{fqdn}``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        fqdn:
            The fully qualified domain name.
        body:
            Forwarding rule payload.
        """
        return self._request_with_retry(
            "PUT",
            f"/v2/customers/{customer_id}/domains/forwards/{fqdn}",
            json=body,
        )

    def create_domain_forwarding(
        self,
        customer_id: str,
        fqdn: str,
        body: Dict[str, Any],
    ) -> Any:
        """
        Create a domain forwarding rule for the specified FQDN.

        ``POST /v2/customers/{customerId}/domains/forwards/{fqdn}``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        fqdn:
            The fully qualified domain name.
        body:
            Forwarding rule payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/forwards/{fqdn}",
            json=body,
        )

    # ------------------------------------------------------------------
    # v2 — Domain registration
    # ------------------------------------------------------------------

    def register_domain_v2(
        self,
        customer_id: str,
        body: Dict[str, Any],
    ) -> Any:
        """
        Register a domain (v2).

        ``POST /v2/customers/{customerId}/domains/register``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        body:
            Domain registration payload.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/register",
            json=body,
        )

    def get_domain_register_schema(
        self, customer_id: str, tld: str
    ) -> Any:
        """
        Retrieve the schema for domain registration for the specified TLD (v2).

        ``GET /v2/customers/{customerId}/domains/register/schema/{tld}``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        tld:
            The TLD to retrieve the schema for.
        """
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/register/schema/{tld}",
        )

    def validate_domain_registration(
        self,
        customer_id: str,
        body: Dict[str, Any],
    ) -> Any:
        """
        Validate the domain registration payload (v2).

        ``POST /v2/customers/{customerId}/domains/register/validate``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        body:
            Domain registration payload to validate.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/register/validate",
            json=body,
        )

    def regenerate_auth_code(
        self, customer_id: str, domain: str
    ) -> Any:
        """
        Regenerate the auth code for the specified Domain (v2).

        ``POST /v2/customers/{customerId}/domains/{domain}/regenerateAuthCode``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        """
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/{domain}/regenerateAuthCode",
        )

    # ------------------------------------------------------------------
    # v2 — Maintenances & Usage
    # ------------------------------------------------------------------

    def list_maintenances(
        self,
        statuses: Optional[List[str]] = None,
        modified: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> Any:
        """
        Retrieve a list of upcoming GoDaddy Domains API maintenances.

        ``GET /v2/domains/maintenances``

        Parameters
        ----------
        statuses:
            Filter by maintenance statuses.
        modified:
            ISO-8601 date-time for filtering by modification date.
        direction:
            Sort direction (``"asc"`` or ``"desc"``)).
        """
        params: Dict[str, Any] = {
            "statuses": statuses,
            "modified": modified,
            "direction": direction,
        }
        return self._request_with_retry(
            "GET", "/v2/domains/maintenances", params=params
        )

    def get_maintenance(self, maintenance_id: str) -> Any:
        """
        Retrieve details for the specified maintenance.

        ``GET /v2/domains/maintenances/{maintenanceId}``

        Parameters
        ----------
        maintenance_id:
            The maintenance identifier.
        """
        return self._request_with_retry(
            "GET", f"/v2/domains/maintenances/{maintenance_id}"
        )

    def get_api_usage(self, yyyymm: str) -> Any:
        """
        Retrieve API usage stats for the specified month.

        ``GET /v2/domains/usage/{yyyymm}``

        Parameters
        ----------
        yyyymm:
            Month in ``YYYYMM`` format, e.g. ``"202401"``.
        """
        return self._request_with_retry(
            "GET", f"/v2/domains/usage/{yyyymm}"
        )

    # ==================================================================
    # ▶  v2 — ACTIONS
    # ==================================================================

    def list_domain_actions(
        self, customer_id: str, domain: str
    ) -> Any:
        """
        Retrieve recent actions for the specified Domain.

        ``GET /v2/customers/{customerId}/domains/{domain}/actions``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        """
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/{domain}/actions",
        )

    def cancel_domain_action(
        self,
        customer_id: str,
        domain: str,
        action_type: str,
    ) -> Any:
        """
        Cancel the latest customer action for the specified Domain.

        ``DELETE /v2/customers/{customerId}/domains/{domain}/actions/{type}``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        action_type:
            The action type string (e.g. ``"TRANSFER_IN_REQUEST"``).
        """
        return self._request_with_retry(
            "DELETE",
            f"/v2/customers/{customer_id}/domains/{domain}/actions/{action_type}",
        )

    def get_domain_action(
        self,
        customer_id: str,
        domain: str,
        action_type: str,
    ) -> Any:
        """
        Retrieve the specified action for the specified Domain.

        ``GET /v2/customers/{customerId}/domains/{domain}/actions/{type}``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        action_type:
            The action type string.
        """
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/{domain}/actions/{action_type}",
        )

    # ==================================================================
    # ▶  v2 — NOTIFICATIONS
    # ==================================================================

    def get_next_notification(
        self,
        customer_id: str,
        x_request_id: Optional[str] = None,
    ) -> Any:
        """
        Retrieve the next notification for the specified customer.

        ``GET /v2/customers/{customerId}/domains/notifications``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        x_request_id:
            Optional idempotency / tracing header.
        """
        extra_headers: Dict[str, str] = {}
        if x_request_id:
            extra_headers["X-Request-Id"] = x_request_id
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/notifications",
            headers=extra_headers or None,
            use_cache=False,
        )

    def get_notification_opt_ins(
        self, customer_id: str
    ) -> Any:
        """
        Retrieve the opted-in notification types for the specified customer.

        ``GET /v2/customers/{customerId}/domains/notifications/optIn``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        """
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/notifications/optIn",
        )

    def opt_in_notifications(
        self,
        customer_id: str,
        notification_types: List[str],
    ) -> Any:
        """
        Opt in to the specified notification types for the specified customer.

        ``PUT /v2/customers/{customerId}/domains/notifications/optIn``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        notification_types:
            List of notification type strings to opt in to.
        """
        return self._request_with_retry(
            "PUT",
            f"/v2/customers/{customer_id}/domains/notifications/optIn",
            json=notification_types,
        )

    def get_notification_schema(
        self,
        customer_id: str,
        notification_type: str,
    ) -> Any:
        """
        Retrieve the schema for the specified notification type.

        ``GET /v2/customers/{customerId}/domains/notifications/schemas/{type}``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        notification_type:
            The notification type string.
        """
        return self._request_with_retry(
            "GET",
            f"/v2/customers/{customer_id}/domains/notifications/schemas/{notification_type}",
        )

    def acknowledge_notification(
        self,
        customer_id: str,
        notification_id: str,
        x_request_id: Optional[str] = None,
    ) -> Any:
        """
        Acknowledge the specified notification.

        ``POST /v2/customers/{customerId}/domains/notifications/{notificationId}/acknowledge``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        notification_id:
            The notification identifier.
        x_request_id:
            Optional idempotency / tracing header.
        """
        extra_headers: Dict[str, str] = {}
        if x_request_id:
            extra_headers["X-Request-Id"] = x_request_id
        return self._request_with_retry(
            "POST",
            f"/v2/customers/{customer_id}/domains/notifications/{notification_id}/acknowledge",
            headers=extra_headers or None,
        )

    # ==================================================================
    # ▶  v2 — CONTACTS
    # ==================================================================

    def update_domain_contacts_v2(
        self,
        customer_id: str,
        domain: str,
        body: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Any:
        """
        Update domain contacts (v2).

        ``PATCH /v2/customers/{customerId}/domains/{domain}/contacts``

        Parameters
        ----------
        customer_id:
            The customer / shopper ID.
        domain:
            The domain name.
        body:
            Contact data payload.
        request_id:
            Optional idempotency / tracing header.
        """
        extra_headers: Dict[str, str] = {}
        if request_id:
            extra_headers["X-Request-Id"] = request_id
        return self._request_with_retry(
            "PATCH",
            f"/v2/customers/{customer_id}/domains/{domain}/contacts",
            json=body,
            headers=extra_headers or None,
        )
