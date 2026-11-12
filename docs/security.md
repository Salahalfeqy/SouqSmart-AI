# Security

SouqSmart follows a fail-closed, least-privilege design. The FastAPI service is read-only with respect to store data and must not bypass PHP security.

## Service-Key Authentication

All protected FastAPI endpoints (`POST /v1/products/search`, `POST /v1/products/compare`, `POST /v1/chat/messages`) require the `X-Service-Key` header. Verification uses `hmac.compare_digest` (constant-time) in `app/security.py`.

- Missing key → HTTP 401 `unauthorized`
- Wrong key → HTTP 401 `unauthorized`
- `GET /v1/health` is public

`SOUQSMART_SERVICE_KEY` has no default. In live PHP catalog mode, startup rejects missing, empty, or known-insecure values (`""`, `dev-only-change-me`, `souqsmart_dev_internal_secret_change_in_production`, `replace-with-a-long-random-shared-key`).

## PHP Internal Catalog Protection

NiceShop exposes `/internal/catalog/search` and `/internal/catalog/products` protected by the same shared secret (`SOUQSMART_INTERNAL_KEY` in PHP, must match FastAPI's `SOUQSMART_SERVICE_KEY`).

- PHP verifies the key with `hash_equals` (constant-time)
- Missing/invalid/default keys → HTTP 401
- Internal endpoints are restricted to loopback callers (`127.0.0.1`, `::1`) unless `SOUQSMART_ALLOW_EXTERNAL` is explicitly enabled
- Apache (`.htaccess`) blocks direct access to `core/`, `controllers/`, `services/`, `database/`, `documentation/`, `views/`, `config.php`, `config.local.php`, and `.env`

## CSRF Protection

The PHP chat proxy (`POST /ajax/chat`) enforces CSRF token verification before forwarding to FastAPI. Invalid or missing tokens → HTTP 403.

## Rate Limiting

The PHP chat proxy rate-limits chat to 20 requests per minute per session. Exceeding the limit → HTTP 429.

## Session Handling

- Chat sessions are stored in FastAPI process memory only
- Each session has a UUID and TTL (`SOUQSMART_SESSION_TTL_SECONDS`, default 1800s)
- Turns are capped (`SOUQSMART_SESSION_TURN_LIMIT`, default 20)
- Sessions are lost on service restart
- PHP owns storefront authentication and sessions independently

## Secret Management

- `SOUQSMART_SERVICE_KEY` and `GEMINI_API_KEY` are configured via `.env` (local, uncommitted) or environment variables
- `.env`, `.env.local`, `.env.production` are excluded by `.gitignore` and must never be committed
- `.env.example` contains safe placeholders only
- API keys are never logged; errors log only category/request ID, never keys or request content
- Rotate `SOUQSMART_SERVICE_KEY` and `GEMINI_API_KEY` before any production use

## Data Minimization

- Catalog responses omit `cost_price`, exact inventory quantities, customer data, and order data
- The PHP chat proxy sanitizes products before returning them to the browser (only `product_id`, `name`, `brand`, `category`, `price`, `availability`, `attributes`, `url`, `image_url`)
- FastAPI error responses never include stack traces or exception details
- Gemini receives the current message, recent conversation context, and retrieved evidence needed to answer the request
- Generated currency-qualified prices must match a retrieved catalog product; generated URLs are rejected

## AI Service Boundaries

The FastAPI AI service is **forbidden to**:

- Connect directly to MariaDB / MySQL
- Read or modify customer data, orders, cart, or checkout
- Set or modify prices or coupons
- Bypass PHP authentication, CSRF, rate limiting, or session management
- Write to the database (read-only catalog access via PHP endpoints only)

PHP (NiceShop) owns: authentication, sessions, cart, checkout, prices, coupons, and customer-facing business rules.

## What the AI Service Must Not Return

- Stack traces or exception details to clients
- Secrets, tokens, or credentials
- Diagnostic information about upstream failures beyond the sanitized error envelope
- Fabricated product IDs, prices, URLs, or availability claims
