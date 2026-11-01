# API contract

The base URL for local Phase 1 is `http://127.0.0.1:8000`. The browser must not call FastAPI directly; use the NiceShop PHP proxy.

Protected endpoints require the configured `X-Service-Key`. Use `<configured-service-key>` in examples, never a real secret.

Unknown JSON fields are rejected. JSON errors use this envelope:

```json
{
  "error": {
    "code": "string",
    "message": "string",
    "details": null,
    "request_id": "string",
    "retryable": false
  }
}
```

## GET /v1/health

Public liveness endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/health
```

Representative response:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "catalog_provider": "php",
  "llm": { "status": "disabled", "provider": null }
}
```

## POST /v1/products/search

```json
{
  "query": "headphones",
  "filters": {
    "category": null,
    "brand": null,
    "price_min": "500.00",
    "price_max": "2000.00",
    "in_stock_only": true
  },
  "sort": "relevance",
  "limit": 10,
  "offset": 0
}
```

```powershell
$headers = @{ "X-Service-Key" = "<configured-service-key>" }
$body = @{ query = "headphones"; filters = @{ in_stock_only = $true; price_min = "500.00"; price_max = "2000.00" }; sort = "relevance"; limit = 10; offset = 0 } | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/products/search -Headers $headers -ContentType 'application/json' -Body $body
```

Constraints: query length is 1–200 characters; limit is 1–20; offset is non-negative; prices are non-negative decimal strings and `price_min` cannot exceed `price_max`. Supported sorts are `relevance`, `price_asc`, and `price_desc`. Cross-currency price sorting is not a derived numeric operation.

## POST /v1/products/compare

```json
{
  "product_ids": ["1", "2"],
  "include_summary": true,
  "language": "en"
}
```

```powershell
$body = @{ product_ids = @("1", "2"); include_summary = $true; language = "en" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/products/compare -Headers $headers -ContentType 'application/json' -Body $body
```

`cheapest_product_id` and `price_spread` are derived only when product currencies are comparable. For mixed currencies both are `null`. A missing ID returns HTTP 404, code `product_not_found`, and missing IDs in error details.

## POST /v1/chat/messages

New session:

```json
{
  "message": "find headphones",
  "language": "en"
}
```

Continuation, using the returned session ID:

```json
{
  "session_id": "<returned-session-id>",
  "message": "show speaker",
  "language": "en",
  "context": {}
}
```

```powershell
$body = @{ message = "find headphones"; language = "en" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/chat/messages -Headers $headers -ContentType 'application/json' -Body $body
```

Chat is deterministic and template-based, with no LLM. Structured products are authoritative facts; generated prose is not a specification source.

## Canonical product

```json
{
  "product_id": "1",
  "name": "Wireless Headphones",
  "brand": "NovaTech",
  "category": "Audio",
  "price": { "amount": "1450.00", "currency": "EGP" },
  "availability": { "status": "in_stock" },
  "attributes": { "color": "Black" },
  "url": "http://localhost/nice-shop/product?id=1",
  "image_url": null,
  "updated_at": null
}
```

Availability is `in_stock`, `out_of_stock`, or `unknown`. Responses must not expose cost, exact stock counts, customer data, or order data.

## PHP catalog provider

FastAPI calls:

- `POST http://127.0.0.1/nice-shop/internal/catalog/search`
- `GET http://127.0.0.1/nice-shop/internal/catalog/products?ids=1,2`

Both require `X-Service-Key`. Python forwards `query`, `category`, `brand`, `availability`, `price_min`, `price_max`, `limit`, and `offset` to search. PHP search returns `products` and `total`; product lookup returns canonical products and missing IDs as applicable.

Fixture data is explicitly fictional and returned only when `SOUQSMART_CATALOG_PROVIDER=fixture`. Fixture IDs begin with `fixture-` and must not be represented as live inventory.
