# Deployment

## Production Readiness

**No production deployment has been performed or verified.** Do not claim this project is production-ready.

## Environment Tiers

### Local Development

- FastAPI on `127.0.0.1:8000` (loopback only)
- NiceShop under local XAMPP (Apache + MariaDB)
- Secrets in uncommitted `.env` files
- `SOUQSMART_CATALOG_PROVIDER=php` or `fixture`
- `SOUQSMART_INTENT_PARSER=deterministic` (Gemini optional)

### Staging

- Same architecture as production but isolated from live customer data
- Secrets injected through environment variables or a secret manager (not `.env`)
- `SOUQSMART_CATALOG_PROVIDER=php`
- Loopback or VPC-restricted internal catalog calls
- Run the full test suite (`pytest`) and browser acceptance tests before promotion

### Production

Not yet verified. Before considering production:

1. Rotate `SOUQSMART_SERVICE_KEY` and `GEMINI_API_KEY` (existing values exist in plaintext local configuration)
2. Use an access-controlled runtime secret injection mechanism (environment variables or secret manager); never `.env`
3. Restrict PHP `/internal/*` endpoints to loopback/trusted callers only
4. Sanitize upstream error logging (status, request ID, error category only; no response bodies)
5. Verify PHP price-range filter enforcement (`price_min`, `price_max`)
6. Perform controlled production deployment and penetration testing
7. Confirm mixed-currency comparison behavior if the catalog supports multiple currencies

## Recommended Production Configuration

| Setting | Recommendation |
|---|---|
| `SOUQSMART_HOST` | `127.0.0.1` behind a reverse proxy, or bind to the private interface only |
| `SOUQSMART_PORT` | `8000` (or as orchestrated) |
| `SOUQSMART_CATALOG_PROVIDER` | `php` |
| `SOUQSMART_PHP_BASE_URL` | Internal staging/production NiceShop origin |
| `SOUQSMART_SERVICE_KEY` | Strong random value, injected at runtime, matching PHP `SOUQSMART_INTERNAL_KEY` |
| `SOUQSMART_INTENT_PARSER` | `deterministic` unless Gemini is required and key is managed securely |
| `GEMINI_API_KEY` | Injected at runtime; never in `.env` or source |
| `SOUQSMART_SESSION_TTL_SECONDS` | `1800` (consider persistent store for production) |
| `SOUQSMART_SESSION_TURN_LIMIT` | `20` |

## Deployment Risks

- Plaintext shared secret + Gemini key in local `.env` (must be rotated before production)
- No production deployment verified
- PHP price-range filters may not be fully enforced on the NiceShop side
- Chat sessions are in-memory and lost on restart (no persistent session store)
- Starlette `TestClient` deprecation warning (dependency maintenance)

## What Is NOT Claimed

- No production deployment has been performed
- No penetration testing has been performed
- No mixed-currency live comparison has been verified against real records
- The project is not certified for production use
