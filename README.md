# SouqSmart — AI Shopping Assistant

FastAPI AI shopping assistant integrated with the NiceShop PHP storefront. It provides read-only catalog search, product comparison, hybrid RAG retrieval, and evidence-grounded response generation through protected internal PHP catalog endpoints. Gemini generates natural product and policy responses when configured; deterministic handling remains for validation, clarification, and service failures.

> **Repository scope:** This repository contains the Python/FastAPI service only. The NiceShop PHP storefront lives separately at `C:\xampp\htdocs\nice-shop` and is documented here for integration purposes, not copied into this repository.

## Main Features

- Read-only product search through the protected NiceShop catalog API (`POST /internal/catalog/search`)
- Product lookup by ID (`GET /internal/catalog/products`)
- Product comparison with derived cheapest price and price spread (same currency only)
- Hybrid product retrieval with live product, price, and availability hydration from PHP
- Approved policy retrieval from Qdrant, with a checked-in approved-document fallback
- Deterministic, template-based chat replies in English and Egyptian Arabic
- Optional Gemini intent parsing and grounded response generation with safe fallback
- Configurable memory or SQLite-backed chat sessions with TTL and turn limits
- Service-key authentication (`X-Service-Key`) on all protected endpoints
- Sanitized error responses; no stack traces or secrets leaked to clients
- Fixture catalog provider for isolated testing and development

## Screenshots

No screenshots are available yet. Add placeholder screenshots under `docs/screenshots/` if desired:

```
docs/screenshots/
  chat-widget.png        # Browser chat widget (placeholder)
  product-comparison.png # Comparison result (placeholder)
```

## Architecture

The browser never calls FastAPI directly. All traffic flows through the NiceShop PHP storefront chat proxy.

```mermaid
flowchart TD
    Browser[Browser Chat Widget]
    PHPProxy[PHP Chat Proxy /ajax/chat]
    FastAPI[FastAI Service 127.0.0.1:8000]
    Intent[Intent Parser<br/>Deterministic / Gemini]
    Catalog[Catalog Provider<br/>PHPProvider / FixtureProvider]
    PHPCatalog[Protected PHP Catalog API<br/>/internal/catalog/*]
    MariaDB[(MariaDB / MySQL<br/>niceshop)]

    Browser -->|POST /ajax/chat<br/>CSRF + rate limit| PHPProxy
    PHPProxy -->|X-Service-Key| FastAPI
    FastAPI -->|parse| Intent
    FastAPI -->|search / products| Catalog
    Catalog -->|HTTP POST/GET + X-Service-Key| PHPCatalog
    PHPCatalog -->|SQL SELECT (read-only)| MariaDB
    Catalog -->|canonical products| FastAPI
    Intent -->|generate| FastAPI
    FastAPI -->|structured reply| PHPProxy
    PHPProxy -->|sanitized JSON| Browser

    subgraph "PHP owns (not the AI service)"
    Auth[Authentication<br/>Sessions<br/>Cart<br/>Checkout<br/>Prices<br/>Coupons]
    end
    PHPProxy -.-> Auth

    subgraph "AI service is forbidden to"
    NoDB[(Direct MariaDB writes)]
    NoPrice[Set or modify prices]
    NoCart[Modify cart / checkout]
    end
```

### Responsibility Boundaries

**PHP (NiceShop) is responsible for:**
- Authentication, CSRF, and session management
- Cart, checkout, prices, coupons, and customer-facing business rules
- Protecting `/internal/*` catalog endpoints with `X-Service-Key`
- Loopback restriction on internal catalog calls
- Sanitizing upstream errors before returning to the browser

**FastAPI (SouqSmart) is responsible for:**
- Read-only catalog search and product lookup
- Intent parsing (deterministic or optional Gemini)
- Template-based or Gemini-grounded response generation
- In-memory chat sessions (TTL + turn limit), lost on restart
- Service-key authentication on `/v1/*` endpoints

**The AI service MUST NOT:**
- Connect directly to MariaDB
- Modify prices, cart, checkout, or customer data
- Bypass PHP security or authentication
- Return stack traces, secrets, or internal diagnostics to clients

## Technology Stack

| Layer | Technology |
|---|---|
| API | FastAPI 0.115+, Python 3.12+ (verified 3.13.2) |
| ASGI server | Uvicorn |
| HTTP client | httpx |
| Settings | pydantic-settings |
| Validation | Pydantic v2 |
| Tests | pytest, pytest-asyncio |
| Optional LLM | Google Gemini (generateContent) |
| Storefront | PHP / NiceShop (separate repo) |
| Database | MariaDB / MySQL (NiceShop-side only) |

## Project Structure

```
souqsmart/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI app, routes, middleware
│   ├── config.py          # pydantic-settings (SOUQSMART_* / GEMINI_*)
│   ├── intents.py         # Deterministic + Gemini intent parsers
│   ├── services.py        # Ranking, comparison, chat replies, intent parsing
│   ├── catalog.py         # CatalogProvider, FixtureProvider, PHPProvider
│   ├── sessions.py        # In-memory MemorySessions
│   ├── security.py        # X-Service-Key verification
│   ├── response_generator.py  # Gemini response generator with fallback
│   └── schemas.py         # Pydantic request/response models
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_ui_acceptance.py
│   ├── test_llm_intent.py
│   ├── test_response_generator.py
│   └── test_phase3_repairs.py
├── docs/
│   ├── architecture.md
│   ├── api-contract.md
│   ├── api-reference.md
│   ├── setup.md
│   ├── security.md
│   ├── testing.md
│   └── deployment.md
├── .github/
│   └── workflows/
│       └── tests.yml
├── run_all_browser_tests.py
├── REPOSITORY_AUDIT.md
├── .env.example
├── .gitignore
├── .editorconfig
├── CONTRIBUTING.md
├── requirements.txt
└── README.md
```

## Prerequisites

- Python 3.12+ (verified with Python 3.13.2)
- pip / venv
- For live catalog mode: NiceShop PHP storefront running under XAMPP (Apache + MariaDB)
- For browser acceptance tests: Microsoft Edge, Playwright, XAMPP Apache + MySQL

## Installation

Run from the repository root in PowerShell:

```powershell
# 1. Create a virtual environment
py -3.13 -m venv .venv

# 2. Activate it
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 4. Configure environment
Copy-Item .env.example .env
# Edit .env and set SOUQSMART_SERVICE_KEY to a strong random value.
# For live PHP mode, set the same value as SOUQSMART_INTERNAL_KEY in NiceShop.
```

## Environment Setup

Create `.env` from `.env.example`. Required variables:

| Variable | Default | Meaning |
|---|---|---|
| `SOUQSMART_SERVICE_KEY` | none | Required strong shared secret for protected endpoints and PHP catalog calls |
| `SOUQSMART_CATALOG_PROVIDER` | `php` | `php` (live NiceShop) or `fixture` (isolated fictional data) |
| `SOUQSMART_PHP_BASE_URL` | `http://127.0.0.1/nice-shop` | NiceShop origin including the `/nice-shop` path |
| `SOUQSMART_CATALOG_TIMEOUT_SECONDS` | `3` | Catalog HTTP timeout in seconds |
| `SOUQSMART_SESSION_TTL_SECONDS` | `1800` | Idle chat-session lifetime in seconds |
| `SOUQSMART_SESSION_TURN_LIMIT` | `20` | Max user/assistant turns retained per session |
| `SOUQSMART_HOST` | `127.0.0.1` | Settings default (pass `--host` explicitly to uvicorn) |
| `SOUQSMART_PORT` | `8000` | Settings default (pass `--port` explicitly to uvicorn) |
| `SOUQSMART_INTENT_PARSER` | `deterministic` | `deterministic` or `llm` (Gemini) |
| `GEMINI_API_KEY` | none | Enables Gemini generation; LLM intent parsing additionally requires `SOUQSMART_INTENT_PARSER=llm` |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Gemini model name |
| `GEMINI_TIMEOUT_SECONDS` | `3.5` | Gemini request timeout in seconds |

The `GEMINI_*` variables also accept the `SOUQSMART_GEMINI_*` prefixed aliases.

**Important:** `SOUQSMART_HOST` and `SOUQSMART_PORT` are settings defaults only. The Uvicorn startup command must supply `--host` and `--port` explicitly; FastAPI does not start Uvicorn itself.

## Running the FastAPI Service

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For development auto-reload:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Start only on loopback (`127.0.0.1`).

Verify health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/health
```

Interactive API docs (Swagger):

```
http://127.0.0.1:8000/docs
```

## Connecting the PHP Storefront

The NiceShop storefront (separate project at `C:\xampp\htdocs\nice-shop`) proxies browser chat through `POST /ajax/chat` to `http://127.0.0.1:8000/v1/chat/messages`.

PHP must expose these authenticated, read-only endpoints:

- `POST /internal/catalog/search`
- `GET /internal/catalog/products?ids=...`

Both require `X-Service-Key`. FastAPI sends its `SOUQSMART_SERVICE_KEY`; PHP reads `SOUQSMART_INTERNAL_KEY` (with `SOUQSMART_SERVICE_KEY` fallback). The configured values must match. Missing, empty, or known-insecure keys fail closed.

NiceShop must never expose `cost_price`, exact stock quantities, customer data, or order data in catalog responses.

Fixture mode (isolated tests only, no live fallback):

```
SOUQSMART_CATALOG_PROVIDER=fixture
```

Fixture IDs begin with `fixture-` and must never be presented as live inventory.

## Running Automated Tests

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Tests use `SOUQSMART_CATALOG_PROVIDER=fixture` and `SOUQSMART_SERVICE_KEY=test-service-key` (set in `tests/conftest.py`), so no real credentials or network are required.

**Verified baseline:** 78 passed, 1 Starlette `TestClient` deprecation warning, exit code 0.

## Running Browser Acceptance Tests

Browser tests require Microsoft Edge, Playwright, XAMPP Apache, and MariaDB on the local machine:

```powershell
.\.venv\Scripts\Activate.ps1
python run_all_browser_tests.py
```

This script starts XAMPP services and FastAPI if they are not already running, then drives 11 real-browser chat scenarios through `http://localhost/nice-shop/`. Results are written to `browser_acceptance_report.json`.

Browser tests are **not** part of GitHub Actions CI because they depend on local XAMPP, MySQL, Edge, and Playwright.

## API Documentation

Public: `GET /v1/health`

Protected (require `X-Service-Key`):

- `POST /v1/products/search`
- `POST /v1/products/compare`
- `POST /v1/chat/messages`

Run the service locally and open `http://127.0.0.1:8000/docs` for the interactive OpenAPI/Swagger reference. See `docs/api-reference.md` and `docs/api-contract.md` for the full contract, request/response schemas, and PHP integration examples.

## Security Notes

- All protected endpoints require `X-Service-Key` (verified with constant-time comparison).
- The live PHP provider rejects missing, empty, and known-insecure service keys at startup.
- PHP `/internal/*` endpoints are restricted to loopback callers unless explicitly enabled.
- Secrets are stored only in local, uncommitted `.env` files. Never commit `.env`.
- The AI service is read-only and must not bypass PHP security or write to MariaDB.
- Rotate `SOUQSMART_SERVICE_KEY` and `GEMINI_API_KEY` before any production use.

See `docs/security.md` for details.

## Known Limitations

- Chat sessions are stored in process memory and are lost on service restart.
- Session lifetime defaults to 30 minutes (1800s) with 20 stored turns.
- Session storage uses the configured memory or SQLite-backed session store; it does not write to NiceShop's database.
- Product and approved-policy answers use retrieved evidence; deterministic replies remain for unsupported, ambiguous, or failed-generation cases.
- No competitor scraping, coupons, checkout, or NiceShop database writes.
- Price-derived comparisons require a single comparable currency.
- No production deployment has been performed; not production-ready.
- PHP price-range filters may not be fully enforced on the NiceShop side (documented integration risk).

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Startup rejects key in PHP mode | Missing/insecure `SOUQSMART_SERVICE_KEY` | Set a strong random value in `.env` |
| Empty product results in live mode | NiceShop not running or key mismatch | Start XAMPP; ensure PHP `SOUQSMART_INTERNAL_KEY` matches |
| Health reports `llm: disabled` | `SOUQSMART_INTENT_PARSER=deterministic` | Expected; set to `llm` and add `GEMINI_API_KEY` to enable |
| 401 on protected endpoints | Wrong or missing `X-Service-Key` | Send the configured service key |
| Tests fail | `.env` overriding test env vars | Tests set env vars before import; ensure no `.env` interference |

## Contribution Guidelines

See `CONTRIBUTING.md` for how to report issues, propose changes, and run the test suite.

## Roadmap

- Harden PHP price-range filter enforcement
- Add OpenAPI examples and SDK snippets
- Optional persistent session store (Redis)
- Production secret injection (environment / secret manager)
- Controlled production-readiness review

## License Status

No license has been selected yet. Please specify a license before publishing (see `CONTRIBUTING.md`).
