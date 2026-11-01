# API Reference

Base URL (local development): `http://127.0.0.1:8000`

All request/response bodies are JSON. Unknown JSON fields are rejected (`extra="forbid"`).

## Authentication

Protected endpoints require the `X-Service-Key` request header containing the value of `SOUQSMART_SERVICE_KEY`. Verification uses constant-time comparison. Missing or invalid keys return HTTP 401 with code `unauthorized`.

`GET /v1/health` is public and requires no authentication.

## Error Envelope

All application errors use this envelope:

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

| Code | HTTP | Meaning | Retryable |
|---|---|---|---|
| `unauthorized` | 401 | Missing or invalid service key | No |
| `validation_error` | 422 | Request validation failed | No |
| `product_not_found` | 404 | One or more compared product IDs missing | No |
| `upstream_timeout` | 504 | Catalog request timed out | Yes |
| `catalog_unavailable` | 502 | Catalog unavailable | Yes |
| `internal_error` | 500 | Internal error | No |
| `http_error` | varies | Framework HTTP error | No |

## GET /v1/health

Public liveness endpoint. No authentication.

**Response (200):**

```json
{
  "status": "ok",
  "version": "1.0.0",
  "catalog_provider": "php",
  "llm": {
    "status": "disabled",
    "provider": null,
    "model": null,
    "configured": false,
    "usable": false
  }
}
```

`llm.status` is `enabled`, `unconfigured`, or `disabled` depending on `SOUQSMART_INTENT_PARSER` and whether `GEMINI_API_KEY` is set.

## POST /v1/products/search

Protected. Requires `X-Service-Key`.

**Request:**

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

| Field | Type | Constraint |
|---|---|---|
| `query` | string | 1–200 printable characters, required |
| `filters.category` | string\|null | max 100 chars |
| `filters.brand` | string\|null | max 100 chars |
| `filters.price_min` | string\|null | non-negative decimal string |
| `filters.price_max` | string\|null | non-negative decimal string |
| `filters.price_min` <= `filters.price_max` | — | if both set |
| `filters.in_stock_only` | boolean | default false |
| `sort` | string | `relevance`, `price_asc`, `price_desc`; default `relevance` |
| `limit` | integer | 1–20; default 10 |
| `offset` | integer | >= 0; default 0 |

Cross-currency price sorting is not a derived numeric operation; sorting only applies when all results share one currency.

**Response (200):**

```json
{
  "results": [ { "product": {...} } ],
  "total": 1,
  "limit": 10,
  "offset": 0,
  "search_mode": "keyword",
  "data_as_of": "2026-10-29T...Z"
}
```

**Errors:** 401, 422, 502, 504.

## POST /v1/products/compare

Protected. Requires `X-Service-Key`.

**Request:**

```json
{
  "product_ids": ["1", "2"],
  "include_summary": true,
  "language": "en"
}
```

| Field | Type | Constraint |
|---|---|---|
| `product_ids` | string[] | 2–4 unique IDs, each 1–64 chars |
| `include_summary` | boolean | default true |
| `language` | string | `en`, `ar`, `auto`; default `en` |

**Response (200):**

```json
{
  "products": [ { "product": {...} } ],
  "table": { "rows": [ { "attribute": "Price", "values": {}, "differs": false } ] },
  "derived": { "cheapest_product_id": "1", "price_spread": "500.00" },
  "summary": { "text": "...", "generated": true, "source": "template", "grounded_on": ["1","2"] },
  "data_as_of": "2026-10-29T...Z"
}
```

`cheapest_product_id` and `price_spread` are derived only when all products share one currency. For mixed currencies both are `null`.

**Errors:** 401, 404 (`product_not_found`, with `missing_ids`), 422.

## POST /v1/chat/messages

Protected. Requires `X-Service-Key`.

**Request (new session):**

```json
{
  "message": "find headphones",
  "language": "en"
}
```

**Request (continuation):**

```json
{
  "session_id": "<uuid>",
  "message": "show speaker",
  "language": "en",
  "context": { "current_product_id": null }
}
```

| Field | Type | Constraint |
|---|---|---|
| `session_id` | string\|null | UUID format if provided |
| `message` | string | 1–2000 printable characters, required |
| `language` | string | `en`, `ar`, `auto`; default `auto` |
| `context.current_product_id` | string\|null | max 64 chars |

**Response (200):**

```json
{
  "session_id": "<uuid>",
  "session_status": "created",
  "message_id": "msg_1",
  "reply": { "text": "...", "language": "en", "generated": true },
  "products": [ { "product": {...} } ],
  "ai_status": "disabled",
  "data_as_of": "2026-10-29T...Z"
}
```

`session_status` is `created`, `continued`, or `new`. `ai_status` is `disabled` (template/deterministic path).

**Errors:** 401, 422, 502, 504.

## Canonical Product Schema

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

Availability statuses: `in_stock`, `out_of_stock`, `unknown`. Responses must not expose cost price, exact stock counts, customer data, or order data.

## PHP Catalog Provider Contract (server-side)

FastAPI calls the NiceShop origin (`SOUQSMART_PHP_BASE_URL`):

- `POST {base}/internal/catalog/search` — body: `query`, `limit`, `offset`, `category`, `brand`, `availability`, `price_min`, `price_max`; header: `X-Service-Key`
- `GET {base}/internal/catalog/products?ids=1,2` — header: `X-Service-Key`

PHP search returns `{"products": [...], "total": N}`. Product lookup returns `{"products": [...]}` and missing IDs are derived server-side. Both endpoints are restricted to loopback callers by default.

## Interactive Docs

Run the service locally and open `http://127.0.0.1:8000/docs` for the Swagger/OpenAPI UI.
