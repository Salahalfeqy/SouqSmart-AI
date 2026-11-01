# Architecture

SouqSmart is a read-only FastAPI AI shopping assistant integrated into the NiceShop PHP storefront. The browser never calls FastAPI directly; all chat traffic flows through the NiceShop PHP chat proxy (`POST /ajax/chat`), which forwards authenticated requests to FastAPI.

## Component Responsibilities

| Component | Responsibility |
|---|---|
| Browser chat widget | Captures user messages and displays replies/product cards; sends via PHP proxy |
| PHP chat proxy (`/ajax/chat`) | CSRF verification, rate limiting, input validation, proxies to FastAPI with `X-Service-Key`, sanitizes upstream errors |
| FastAPI (`app/main.py`) | HTTP layer: routes, middleware, service-key auth, error handling, session orchestration |
| Intent parser (`app/intents.py`) | Parses messages into structured intent (deterministic rules, optional Gemini) |
| Services (`app/services.py`) | Product ranking, comparison, template chat replies, intent parsing entrypoint |
| Catalog provider (`app/catalog.py`) | Abstract catalog interface; `PHPProvider` calls NiceShop, `FixtureProvider` for tests |
| Sessions (`app/sessions.py`) | In-memory chat sessions with TTL and turn limit |
| Security (`app/security.py`) | `X-Service-Key` verification using constant-time comparison |
| Response generator (`app/response_generator.py`) | Optional Gemini response generation grounded on catalog facts |
| Schemas (`app/schemas.py`) | Pydantic request/response validation |
| PHP catalog API (`/internal/catalog/*`) | Read-only SQL queries against MariaDB; canonical product serialization |
| MariaDB / MySQL | Product data source of truth (NiceShop-side only) |

## Request Lifecycle (Chat)

```mermaid
sequenceDiagram
    participant Browser
    participant PHP as PHP Chat Proxy
    participant FastAPI as FastAPI Service
    participant Intent as Intent Parser
    participant Catalog as Catalog Provider
    participant PHPcat as PHP Catalog API
    participant DB as MariaDB

    Browser->>PHP: POST /ajax/chat (message, CSRF, session_id)
    PHP->>PHP: CSRF + rate limit + input validation
    PHP->>FastAPI: POST /v1/chat/messages (X-Service-Key)
    FastAPI->>Intent: parse message
    Intent-->>FastAPI: structured intent
    alt search / compare / browse
        FastAPI->>Catalog: search / products
        Catalog->>PHPcat: POST/GET /internal/catalog/* (X-Service-Key)
        PHPcat->>DB: SELECT (read-only)
        DB-->>PHPcat: rows
        PHPcat-->>Catalog: canonical products
        Catalog-->>FastAPI: products
    end
    alt Gemini enabled
        FastAPI->>Intent: generate reply (grounded on products)
        Intent-->>FastAPI: generated text
    else deterministic
        FastAPI->>Intent: template chat_reply
        Intent-->>FastAPI: template text
    end
    FastAPI-->>PHP: session_id, reply, products, ai_status
    PHP->>PHP: sanitize products, extract reply text
    PHP-->>Browser: JSON (reply, products, session_id)
```

## Intent Parsing

Two parser implementations are available behind a common `IntentParser` interface:

1. **DeterministicIntentParser** (default) — rule-based, supports English and Egyptian Arabic. No network, no API key, fully offline.
2. **LLMIntentParser** (optional, `SOUQSMART_INTENT_PARSER=llm`) — Gemini-backed. Falls back to deterministic on any failure (missing key, network error, 429, malformed output, invalid schema).

The parser never trusts product IDs, prices, or stock from LLM output. It only classifies intent and extracts search keywords/filters.

## Deterministic Fallback

Whenever Gemini is unavailable, misconfigured, or returns an error, the service falls back to deterministic template replies in `app/services.py`. The fallback:
- Never invents products, prices, or URLs
- Returns honest "no results" messages when the catalog is empty
- Distinguishes laptop devices from accessories
- Handles currency clarification (USD vs EGP store currency)
- Is used for greetings, gratitude, clarification, and browse intents without catalog calls

## Gemini Integration

Gemini is used for:
- Intent parsing (`app/intents.py` LLMIntentParser)
- Response generation (`app/response_generator.py` ResponseGenerator)

Security rules:
- API key is sent in the request header/URL only to `generativelanguage.googleapis.com`
- API keys are never logged
- User messages are truncated to 1000 characters before sending
- Generated replies are scanned and rejected if they contain prices, URLs, or fabricated product names ("Magic")
- All validation (budget, currency, stock) happens before generation; the generator never invents data
- Any exception triggers deterministic fallback

## Catalog Integration

The `CatalogProvider` interface defines `search()` and `products()`.

- **PHPProvider** calls `POST /internal/catalog/search` and `GET /internal/catalog/products?ids=...` on the NiceShop origin, forwarding `X-Service-Key`.
- **FixtureProvider** returns clearly fictional data (`fixture-*` IDs) for isolated tests and development; never used in live PHP mode.

The PHP catalog serializes canonical products that omit `cost_price`, exact inventory quantities, customer data, and order data.

## Session Handling

`MemorySessions` stores sessions in process memory:
- Each session has a UUID and a list of turns
- Idle sessions expire after `SOUQSMART_SESSION_TTL_SECONDS` (default 1800s)
- Turns are capped at `SOUQSMART_SESSION_TURN_LIMIT` (default 20)
- Sessions are lost on service restart (no persistence)

## Product Response Format

Canonical product (Pydantic `Product` model):

```json
{
  "product_id": "string (1-64)",
  "name": "string (1-300)",
  "brand": "string|null",
  "category": "string|null",
  "price": { "amount": "decimal string", "currency": "3-letter code" },
  "availability": { "status": "in_stock\|out_of_stock\|unknown" },
  "attributes": { "string": "string" },
  "url": "string",
  "image_url": "string|null",
  "updated_at": "string|null"
}
```

## Security Boundaries

- **Forbidden to access:** MariaDB directly, customer data, order data, cost prices, exact stock quantities
- **Forbidden to modify:** Prices, cart, checkout, coupons, database records
- **Must not bypass:** PHP authentication, CSRF, rate limiting, session management
- PHP owns authentication, sessions, cart, checkout, prices, coupons, and customer-facing business rules
