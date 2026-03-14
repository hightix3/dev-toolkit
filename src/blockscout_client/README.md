# blockscout_client

A production-ready Python API client for the [Blockscout Blockchain Explorer](https://eth.blockscout.com/) REST API v2.

- Covers **all 56 endpoints**
- Exponential backoff with jitter on 429 and 5xx responses
- TTL-based in-memory GET response cache
- Cursor-based pagination helper
- Optional [Blockscout PRO](https://docs.blockscout.com/) API key support
- Full type hints and docstrings

---

## Installation

```bash
pip install httpx
# Then copy the blockscout_client/ package into your project.
```

---

## Quick Start

```python
from blockscout_client import BlockscoutClient

with BlockscoutClient() as client:
    tx = client.get_transaction("0xabc123...")
    print(tx["hash"], tx["status"])
```

---

## Configuration Reference

```python
BlockscoutClient(
    base_url="https://eth.blockscout.com/api/v2",  # any Blockscout instance
    api_key=None,       # Blockscout PRO API key (optional)
    timeout=30.0,       # HTTP timeout in seconds
    max_retries=5,      # Max retry attempts on 429/5xx
    cache_ttl=60.0,     # GET response cache TTL in seconds (0 = disabled)
)
```

### Blockscout PRO API Keys

Blockscout PRO users receive higher rate limits and access to premium endpoints. Supply your key via `api_key`:

```python
client = BlockscoutClient(api_key="your-pro-api-key")
```

The key is forwarded as the `x-api-key` request header. Obtain a key from the [Blockscout PRO dashboard](https://docs.blockscout.com/).

### Pointing to a different network

```python
# Gnosis Chain
client = BlockscoutClient(base_url="https://gnosis.blockscout.com/api/v2")

# Optimism
client = BlockscoutClient(base_url="https://optimism.blockscout.com/api/v2")
```

---

## Error Handling

```python
from blockscout_client import (
    BlockscoutClient,
    APIError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)

with BlockscoutClient() as client:
    try:
        tx = client.get_transaction("0xinvalidhash")
    except NotFoundError:
        print("Transaction not found")
    except ValidationError as e:
        print(f"Bad request: {e.message}")
    except RateLimitError:
        print("Rate limited — client already retried, giving up")
    except ServerError as e:
        print(f"Server error {e.status_code}: {e.message}")
    except APIError as e:
        print(f"Unexpected API error: {e}")
```

### Exception hierarchy

```
APIError (base)
├── RateLimitError   — HTTP 429 (after all retries exhausted)
├── NotFoundError    — HTTP 404
├── ServerError      — HTTP 5xx (after all retries exhausted)
└── ValidationError  — HTTP 400
```

---

## Pagination

Endpoints that return large result sets use cursor-based pagination.
The `paginate()` helper iterates all pages automatically:

```python
with BlockscoutClient() as client:
    # Collect every transaction page for an address
    all_txs = []
    for page in client.paginate(
        f"/addresses/0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045/transactions"
    ):
        all_txs.extend(page)
    print(f"Total transactions: {len(all_txs)}")
```

You can also pass initial query parameters:

```python
for page in client.paginate("/transactions", params={"filter": "pending"}):
    for tx in page:
        print(tx["hash"])
```

Manual pagination (fetching one page at a time):

```python
with BlockscoutClient() as client:
    response = client.list_transactions()
    items = response["items"]
    next_cursor = response.get("next_page_params")

    if next_cursor:
        next_page = client.list_transactions(**next_cursor)
```

---

## All Methods

| Method | HTTP | Endpoint | Description |
|--------|------|----------|-------------|
| `search(q)` | GET | `/search` | Search blocks, txs, addresses, tokens |
| `search_check_redirect(q)` | GET | `/search/check-redirect` | Check if a search query should redirect |
| `list_transactions(filter, type, method)` | GET | `/transactions` | List recent transactions |
| `get_transaction(transaction_hash)` | GET | `/transactions/{transaction_hash}` | Get transaction info |
| `get_transaction_token_transfers(transaction_hash, type)` | GET | `/transactions/{transaction_hash}/token-transfers` | Get transaction token transfers |
| `get_transaction_internal_transactions(transaction_hash)` | GET | `/transactions/{transaction_hash}/internal-transactions` | Get transaction internal transactions |
| `get_transaction_logs(transaction_hash)` | GET | `/transactions/{transaction_hash}/logs` | Get transaction logs |
| `get_transaction_raw_trace(transaction_hash)` | GET | `/transactions/{transaction_hash}/raw-trace` | Get transaction raw trace |
| `get_transaction_state_changes(transaction_hash)` | GET | `/transactions/{transaction_hash}/state-changes` | Get transaction state changes |
| `get_transaction_summary(transaction_hash)` | GET | `/transactions/{transaction_hash}/summary` | Get human-readable transaction summary |
| `list_blocks(type)` | GET | `/blocks` | List blocks |
| `get_block(block_number_or_hash)` | GET | `/blocks/{block_number_or_hash}` | Get block info |
| `get_block_transactions(block_number_or_hash)` | GET | `/blocks/{block_number_or_hash}/transactions` | Get block transactions |
| `get_block_withdrawals(block_number_or_hash)` | GET | `/blocks/{block_number_or_hash}/withdrawals` | Get block withdrawals |
| `list_token_transfers()` | GET | `/token-transfers` | List all recent token transfers |
| `list_internal_transactions()` | GET | `/internal-transactions` | List all recent internal transactions |
| `get_main_page_transactions()` | GET | `/main-page/transactions` | Get main page transactions |
| `get_main_page_blocks()` | GET | `/main-page/blocks` | Get main page blocks |
| `get_indexing_status()` | GET | `/main-page/indexing-status` | Get indexing status |
| `get_stats()` | GET | `/stats` | Get stats counters |
| `get_transactions_chart()` | GET | `/stats/charts/transactions` | Get transactions chart |
| `get_market_chart()` | GET | `/stats/charts/market` | Get market chart |
| `list_addresses()` | GET | `/addresses` | Get native coin holders list |
| `get_address(address_hash)` | GET | `/addresses/{address_hash}` | Get address info |
| `get_address_counters(address_hash)` | GET | `/addresses/{address_hash}/counters` | Get address counters |
| `get_address_transactions(address_hash, filter)` | GET | `/addresses/{address_hash}/transactions` | Get address transactions |
| `get_address_token_transfers(address_hash, type, filter, token)` | GET | `/addresses/{address_hash}/token-transfers` | Get address token transfers |
| `get_address_internal_transactions(address_hash, filter)` | GET | `/addresses/{address_hash}/internal-transactions` | Get address internal transactions |
| `get_address_logs(address_hash)` | GET | `/addresses/{address_hash}/logs` | Get address logs |
| `get_address_blocks_validated(address_hash)` | GET | `/addresses/{address_hash}/blocks-validated` | Get blocks validated by address |
| `get_address_token_balances(address_hash)` | GET | `/addresses/{address_hash}/token-balances` | Get all token balances for address |
| `get_address_tokens(address_hash, type)` | GET | `/addresses/{address_hash}/tokens` | Token balances with filtering and pagination |
| `get_address_coin_balance_history(address_hash)` | GET | `/addresses/{address_hash}/coin-balance-history` | Get address coin balance history |
| `get_address_coin_balance_history_by_day(address_hash)` | GET | `/addresses/{address_hash}/coin-balance-history-by-day` | Get address coin balance history by day |
| `get_address_withdrawals(address_hash)` | GET | `/addresses/{address_hash}/withdrawals` | Get address withdrawals |
| `get_address_nft(address_hash, type)` | GET | `/addresses/{address_hash}/nft` | Get NFTs owned by address |
| `get_address_nft_collections(address_hash, type)` | GET | `/addresses/{address_hash}/nft/collections` | Get NFTs owned by address, grouped by collection |
| `list_tokens(q, type)` | GET | `/tokens` | Get tokens list |
| `get_token(address_hash)` | GET | `/tokens/{address_hash}` | Get token info |
| `get_token_transfers(address_hash)` | GET | `/tokens/{address_hash}/transfers` | Get token transfers |
| `get_token_holders(address_hash)` | GET | `/tokens/{address_hash}/holders` | Get token holders |
| `get_token_counters(address_hash)` | GET | `/tokens/{address_hash}/counters` | Get token counters |
| `list_token_instances(address_hash)` | GET | `/tokens/{address_hash}/instances` | Get NFT instances |
| `get_token_instance(address_hash, id)` | GET | `/tokens/{address_hash}/instances/{id}` | Get NFT instance by ID |
| `get_token_instance_transfers(address_hash, id)` | GET | `/tokens/{address_hash}/instances/{id}/transfers` | Get transfers of NFT instance |
| `get_token_instance_holders(address_hash, id)` | GET | `/tokens/{address_hash}/instances/{id}/holders` | Get NFT instance holders |
| `get_token_instance_transfers_count(address_hash, id)` | GET | `/tokens/{address_hash}/instances/{id}/transfers-count` | Get transfer count of NFT instance |
| `refetch_token_instance_metadata(address_hash, id, body)` | PATCH | `/tokens/{address_hash}/instances/{id}/refetch-metadata` | Re-fetch token instance metadata |
| `list_smart_contracts(q, filter)` | GET | `/smart-contracts` | Get verified smart contracts |
| `get_smart_contracts_counters()` | GET | `/smart-contracts/counters` | Get smart contract counters |
| `get_smart_contract(address_hash)` | GET | `/smart-contracts/{address_hash}` | Get smart contract detail |
| `list_withdrawals()` | GET | `/withdrawals` | Get validator withdrawals |
| `get_account_abstraction_status()` | GET | `/proxy/account-abstraction/status` | Get ERC-4337 indexing status |
| `get_celestia_blob(height, commitment, skip_data)` | GET | `/api/v1/celestia/blob` | Get Celestia blob |
| `get_celestia_l2_batch_metadata(height, namespace, commitment)` | GET | `/api/v1/celestia/l2BatchMetadata` | Get Celestia L2 batch metadata |
| `get_health(service)` | GET | `/health` | Get Celestia service health |

---

## Examples

### Get the latest blocks and transactions

```python
from blockscout_client import BlockscoutClient

with BlockscoutClient() as client:
    blocks = client.get_main_page_blocks()
    for block in blocks:
        print(block["height"], block["timestamp"])

    txs = client.get_main_page_transactions()
    for tx in txs[:5]:
        print(tx["hash"], tx["value"])
```

### Inspect a token

```python
with BlockscoutClient() as client:
    token = client.get_token("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")  # USDC
    print(token["name"], token["symbol"], token["total_supply"])

    # Iterate all pages of holders
    for page in client.paginate("/tokens/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/holders"):
        for holder in page:
            print(holder["address"]["hash"], holder["value"])
```

### Search

```python
with BlockscoutClient() as client:
    results = client.search(q="USDC")
    for item in results.get("items", []):
        print(item["type"], item.get("name"), item.get("address"))
```

### Verify a smart contract

```python
with BlockscoutClient() as client:
    contract = client.get_smart_contract("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
    print(contract["name"])
    print(contract["compiler_version"])
    print(contract["is_verified"])
```

### Address NFT holdings

```python
with BlockscoutClient() as client:
    vitalik = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    nfts = client.get_address_nft(vitalik)
    for nft in nfts.get("items", []):
        print(nft["token"]["name"], nft["id"])
```

---

## Context Manager vs Manual Lifecycle

```python
# Recommended: context manager (auto-closes the connection pool)
with BlockscoutClient() as client:
    stats = client.get_stats()

# Manual lifecycle
client = BlockscoutClient()
try:
    stats = client.get_stats()
finally:
    client.close()
```

---

## Retry Behaviour

The client retries on HTTP `429`, `500`, `502`, `503`, and `504` using exponential backoff with random jitter:

```
wait = 2^attempt + uniform(0, 1) seconds
```

With `max_retries=5` (default) the maximum total wait before giving up is ~63 seconds. After all retries are exhausted the appropriate exception is raised (`RateLimitError` or `ServerError`).

---

## Response Caching

GET responses are cached in memory using a TTL (default 60 s). To disable:

```python
client = BlockscoutClient(cache_ttl=0)
```

POST and PATCH requests are never cached.
