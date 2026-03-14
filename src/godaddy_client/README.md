# GoDaddy Domains API — Python Client

A complete, production-ready Python client for the
[GoDaddy Domains API](https://developer.godaddy.com/doc/endpoint/domains).

- **65 endpoints** — full v1 + v2 coverage
- Automatic **retry with exponential back-off + jitter** (429 / 5xx)
- **TTL-based response cache** for GET requests
- **Pagination** helper (offset / marker)
- Typed exceptions, full type hints, and docstrings

---

## Installation

```bash
pip install httpx
# Place the godaddy_client/ package on your Python path.
```

---

## Quick Start

```python
from godaddy_client import GoDaddyClient, GoDaddyAuth

auth = GoDaddyClient(api_key="my_key", api_secret="my_secret")
with GoDaddyClient(auth=GoDaddyAuth("my_key", "my_secret")) as client:
    domains = client.list_domains(limit=25)
    print(domains)
```

---

## Authentication

Obtain credentials at **https://developer.godaddy.com/keys**.

| Environment | Base URL                         | Key prefix |
|-------------|----------------------------------|------------|
| Production  | `https://api.godaddy.com`        | Any        |
| OTE (test)  | `https://api.ote-godaddy.com`    | `test_`    |

```python
from godaddy_client import GoDaddyAuth, GoDaddyClient

# Production
auth = GoDaddyAuth(api_key="abc123", api_secret="xyz789")
client = GoDaddyClient(auth=auth)

# OTE (sandbox)
ote_client = GoDaddyClient(
    auth=GoDaddyAuth("test_abc123", "test_xyz789"),
    base_url="https://api.ote-godaddy.com",
)
```

The `GoDaddyAuth` class is an `httpx.Auth` subclass that automatically injects:

```
Authorization: sso-key {api_key}:{api_secret}
```

---

## Configuration

| Parameter     | Default                          | Description                         |
|---------------|----------------------------------|-------------------------------------|
| `auth`        | *(required)*                     | `GoDaddyAuth` instance              |
| `base_url`    | `https://api.godaddy.com`        | API base URL                        |
| `timeout`     | `30.0`                           | HTTP timeout in seconds             |
| `max_retries` | `4`                              | Max retry attempts (429 / 5xx)      |
| `cache_ttl`   | `60`                             | GET response cache TTL in seconds   |

---

## Pagination

Use `client.paginate()` to automatically iterate through all pages:

```python
# Iterate every domain in the account
for domain in client.paginate("/v1/domains", page_size=100):
    print(domain["domain"])

# With base query parameters
for domain in client.paginate(
    "/v1/domains",
    params={"statuses": ["ACTIVE"]},
    page_size=50,
):
    print(domain["domain"], domain["status"])
```

You can also manually paginate by passing `marker`:

```python
page1 = client.list_domains(limit=10)
# Use the last domain as the marker for the next page
page2 = client.list_domains(limit=10, marker="lastdomain.com")
```

---

## Error Handling

```python
from godaddy_client import (
    GoDaddyClient, GoDaddyAuth,
    APIError, AuthenticationError, RateLimitError,
    NotFoundError, ServerError, ValidationError,
)

with GoDaddyClient(auth=GoDaddyAuth("key", "secret")) as client:
    try:
        domain = client.get_domain("example.com")
    except AuthenticationError as e:
        print(f"Auth failed: {e.status_code} — {e.message}")
    except NotFoundError:
        print("Domain not found")
    except RateLimitError as e:
        print(f"Rate limit hit. Retry after: {e.retry_after}s")
    except ValidationError as e:
        print(f"Bad request: {e.message}")
    except ServerError as e:
        print(f"GoDaddy server error: {e.status_code}")
    except APIError as e:
        print(f"Unexpected error: {e}")
```

| Exception             | HTTP Status     | Description                     |
|-----------------------|-----------------|---------------------------------|
| `AuthenticationError` | 401, 403        | Invalid or missing credentials  |
| `NotFoundError`       | 404             | Resource not found              |
| `RateLimitError`      | 429             | Too many requests               |
| `ValidationError`     | 400, 422        | Invalid request payload         |
| `ServerError`         | 5xx             | GoDaddy server error            |
| `APIError`            | any             | Base exception for all errors   |

---

## All Methods

### v1 — Domains

| Method | HTTP | Endpoint | Description |
|--------|------|----------|-------------|
| `list_domains` | GET | `/v1/domains` | List domains for a Shopper |
| `get_domain_agreements` | GET | `/v1/domains/agreements` | Legal agreements for TLD |
| `check_domain_availability` | GET | `/v1/domains/available` | Check single domain availability |
| `check_domains_availability_bulk` | POST | `/v1/domains/available` | Bulk availability check |
| `validate_domain_contacts` | POST | `/v1/domains/contacts/validate` | Validate contact schema |
| `purchase_domain` | POST | `/v1/domains/purchase` | Purchase and register domain |
| `get_domain_purchase_schema` | GET | `/v1/domains/purchase/schema/{tld}` | Registration schema for TLD |
| `validate_domain_purchase` | POST | `/v1/domains/purchase/validate` | Validate purchase payload |
| `suggest_domains` | GET | `/v1/domains/suggest` | Suggest alternate domain names |
| `list_tlds` | GET | `/v1/domains/tlds` | List supported TLDs |
| `cancel_domain` | DELETE | `/v1/domains/{domain}` | Cancel a purchased domain |
| `get_domain` | GET | `/v1/domains/{domain}` | Get domain details |
| `update_domain` | PATCH | `/v1/domains/{domain}` | Update domain settings |
| `update_domain_contacts` | PATCH | `/v1/domains/{domain}/contacts` | Update domain contacts |
| `cancel_domain_privacy` | DELETE | `/v1/domains/{domain}/privacy` | Cancel domain privacy |
| `purchase_domain_privacy` | POST | `/v1/domains/{domain}/privacy/purchase` | Purchase domain privacy |
| `add_dns_records` | PATCH | `/v1/domains/{domain}/records` | Add DNS records |
| `replace_dns_records` | PUT | `/v1/domains/{domain}/records` | Replace all DNS records |
| `get_dns_records` | GET | `/v1/domains/{domain}/records/{type}/{name}` | Get DNS records by type/name |
| `replace_dns_records_by_type_name` | PUT | `/v1/domains/{domain}/records/{type}/{name}` | Replace DNS records by type+name |
| `delete_dns_records_by_type_name` | DELETE | `/v1/domains/{domain}/records/{type}/{name}` | Delete DNS records by type+name |
| `replace_dns_records_by_type` | PUT | `/v1/domains/{domain}/records/{type}` | Replace DNS records by type |
| `renew_domain` | POST | `/v1/domains/{domain}/renew` | Renew domain |
| `transfer_domain` | POST | `/v1/domains/{domain}/transfer` | Transfer domain in |
| `verify_registrant_email` | POST | `/v1/domains/{domain}/verifyRegistrantEmail` | Re-send verification email |

### v2 — Domains

| Method | HTTP | Endpoint | Description |
|--------|------|----------|-------------|
| `get_domain_v2` | GET | `/v2/customers/{customerId}/domains/{domain}` | Get domain details |
| `cancel_change_of_registrant` | DELETE | `/v2/customers/{customerId}/domains/{domain}/changeOfRegistrant` | Cancel pending COR |
| `get_change_of_registrant` | GET | `/v2/customers/{customerId}/domains/{domain}/changeOfRegistrant` | Get COR info |
| `add_dnssec_records` | PATCH | `/v2/customers/{customerId}/domains/{domain}/dnssecRecords` | Add DNSSEC records |
| `delete_dnssec_records` | DELETE | `/v2/customers/{customerId}/domains/{domain}/dnssecRecords` | Remove DNSSEC records |
| `replace_nameservers` | PUT | `/v2/customers/{customerId}/domains/{domain}/nameServers` | Replace nameservers |
| `get_privacy_email_forwarding` | GET | `/v2/customers/{customerId}/domains/{domain}/privacy/forwarding` | Get privacy forwarding |
| `update_privacy_email_forwarding` | PATCH | `/v2/customers/{customerId}/domains/{domain}/privacy/forwarding` | Update privacy forwarding |
| `redeem_domain` | POST | `/v2/customers/{customerId}/domains/{domain}/redeem` | Redeem domain from redemption |
| `renew_domain_v2` | POST | `/v2/customers/{customerId}/domains/{domain}/renew` | Renew domain |
| `transfer_domain_v2` | POST | `/v2/customers/{customerId}/domains/{domain}/transfer` | Transfer domain in |
| `get_transfer_status` | GET | `/v2/customers/{customerId}/domains/{domain}/transfer` | Get transfer status |
| `validate_domain_transfer` | POST | `/v2/customers/{customerId}/domains/{domain}/transfer/validate` | Validate transfer payload |
| `accept_transfer_in` | POST | `/v2/customers/{customerId}/domains/{domain}/transferInAccept` | Accept transfer in |
| `cancel_transfer_in` | POST | `/v2/customers/{customerId}/domains/{domain}/transferInCancel` | Cancel transfer in |
| `restart_transfer_in` | POST | `/v2/customers/{customerId}/domains/{domain}/transferInRestart` | Restart transfer in |
| `retry_transfer_in` | POST | `/v2/customers/{customerId}/domains/{domain}/transferInRetry` | Retry transfer in |
| `initiate_transfer_out` | POST | `/v2/customers/{customerId}/domains/{domain}/transferOut` | Initiate transfer out (.uk) |
| `accept_transfer_out` | POST | `/v2/customers/{customerId}/domains/{domain}/transferOutAccept` | Accept transfer out |
| `reject_transfer_out` | POST | `/v2/customers/{customerId}/domains/{domain}/transferOutReject` | Reject transfer out |
| `delete_domain_forwarding` | DELETE | `/v2/customers/{customerId}/domains/forwards/{fqdn}` | Cancel domain forwarding |
| `get_domain_forwarding` | GET | `/v2/customers/{customerId}/domains/forwards/{fqdn}` | Get domain forwarding |
| `replace_domain_forwarding` | PUT | `/v2/customers/{customerId}/domains/forwards/{fqdn}` | Replace domain forwarding |
| `create_domain_forwarding` | POST | `/v2/customers/{customerId}/domains/forwards/{fqdn}` | Create domain forwarding |
| `register_domain_v2` | POST | `/v2/customers/{customerId}/domains/register` | Register domain |
| `get_domain_register_schema` | GET | `/v2/customers/{customerId}/domains/register/schema/{tld}` | Get registration schema |
| `validate_domain_registration` | POST | `/v2/customers/{customerId}/domains/register/validate` | Validate registration |
| `regenerate_auth_code` | POST | `/v2/customers/{customerId}/domains/{domain}/regenerateAuthCode` | Regenerate auth code |
| `list_maintenances` | GET | `/v2/domains/maintenances` | List upcoming maintenances |
| `get_maintenance` | GET | `/v2/domains/maintenances/{maintenanceId}` | Get maintenance details |
| `get_api_usage` | GET | `/v2/domains/usage/{yyyymm}` | Get API usage for month |

### v2 — Actions

| Method | HTTP | Endpoint | Description |
|--------|------|----------|-------------|
| `list_domain_actions` | GET | `/v2/customers/{customerId}/domains/{domain}/actions` | List recent actions |
| `cancel_domain_action` | DELETE | `/v2/customers/{customerId}/domains/{domain}/actions/{type}` | Cancel latest action |
| `get_domain_action` | GET | `/v2/customers/{customerId}/domains/{domain}/actions/{type}` | Get action by type |

### v2 — Notifications

| Method | HTTP | Endpoint | Description |
|--------|------|----------|-------------|
| `get_next_notification` | GET | `/v2/customers/{customerId}/domains/notifications` | Get next notification |
| `get_notification_opt_ins` | GET | `/v2/customers/{customerId}/domains/notifications/optIn` | Get opted-in types |
| `opt_in_notifications` | PUT | `/v2/customers/{customerId}/domains/notifications/optIn` | Opt in to notification types |
| `get_notification_schema` | GET | `/v2/customers/{customerId}/domains/notifications/schemas/{type}` | Get notification schema |
| `acknowledge_notification` | POST | `/v2/customers/{customerId}/domains/notifications/{notificationId}/acknowledge` | Acknowledge notification |

### v2 — Contacts

| Method | HTTP | Endpoint | Description |
|--------|------|----------|-------------|
| `update_domain_contacts_v2` | PATCH | `/v2/customers/{customerId}/domains/{domain}/contacts` | Update domain contacts |

---

## Examples

### Check domain availability

```python
result = client.check_domain_availability("mycoolstartup.com")
print(result["available"], result["price"])
```

### Bulk availability check

```python
domains = ["example.com", "example.net", "example.io"]
results = client.check_domains_availability_bulk(domains, check_type="FAST")
for r in results:
    print(r["domain"], r["available"])
```

### Get and replace DNS records

```python
# Read all A records
records = client.get_dns_records("example.com", "A", "@")

# Replace apex A record
client.replace_dns_records_by_type_name(
    domain="example.com",
    record_type="A",
    name="@",
    records=[{"data": "1.2.3.4", "ttl": 600, "type": "A", "name": "@"}],
)
```

### Renew a domain (v2)

```python
client.renew_domain_v2(
    customer_id="cust_123",
    domain="example.com",
    body={"period": 2},
)
```

### Poll and acknowledge notifications

```python
while True:
    note = client.get_next_notification(customer_id="cust_123")
    if not note:
        break
    print(note["type"], note["domainName"])
    client.acknowledge_notification(
        customer_id="cust_123",
        notification_id=note["notificationId"],
    )
```

### OTE vs Production

```python
from godaddy_client import GoDaddyClient, GoDaddyAuth

# OTE (safe for testing — no real charges)
ote = GoDaddyClient(
    auth=GoDaddyAuth("test_abc", "test_xyz"),
    base_url="https://api.ote-godaddy.com",
)

# Production
prod = GoDaddyClient(
    auth=GoDaddyAuth("abc", "xyz"),
)
```

---

## Project Layout

```
godaddy_client/
├── __init__.py      # Package exports
├── auth.py          # GoDaddyAuth (httpx.Auth subclass)
├── client.py        # GoDaddyClient — 65 endpoint methods
├── exceptions.py    # Exception hierarchy
└── README.md        # This file
```

---

## License

MIT
