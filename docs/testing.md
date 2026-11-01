# Testing

## Verified Baseline

Automated tests: **78 passed**, 1 Starlette `TestClient` deprecation warning, exit code 0.

Command:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Real-browser acceptance: **11/11 scenarios** passed (historical evidence; requires Edge + Playwright + XAMPP + MySQL).

## Test Commands

Run the full Python suite:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Run a specific test file:

```powershell
python -m pytest tests/test_api.py -q
```

Run with verbose output:

```powershell
python -m pytest -v
```

## Test Structure

| File | Purpose |
|---|---|
| `tests/conftest.py` | Sets fixture provider + test service key; provides `client` and `headers` fixtures |
| `tests/test_api.py` | Health, auth, validation, search, compare, catalog timeout, sessions, chat |
| `tests/test_ui_acceptance.py` | Chat UI acceptance (greeting, browse, multiple price, comparison, gratitude) |
| `tests/test_llm_intent.py` | Gemini intent parser with mocked transport; fallback, rate limit, prompt injection safety |
| `tests/test_response_generator.py` | Gemini response generator with mocked HTTP client |
| `tests/test_phase3_repairs.py` | Arabic headphone normalization, budget semantics, LLM category safety |

Tests use `SOUQSMART_CATALOG_PROVIDER=fixture` and `SOUQSMART_SERVICE_KEY=test-service-key`, so no real credentials or network calls are required. The fixture provider returns clearly fictional data (`fixture-*` IDs).

## Browser Acceptance Tests

`run_all_browser_tests.py` drives 11 real-browser chat scenarios through `http://localhost/nice-shop/` using Playwright and Microsoft Edge.

Requirements:
- Microsoft Edge installed
- Playwright browsers installed
- XAMPP Apache and MariaDB running
- NiceShop storefront at `http://localhost/nice-shop/`
- FastAPI running at `http://127.0.0.1:8000`

Run:

```powershell
.\.venv\Scripts\Activate.ps1
python run_all_browser_tests.py
```

Results are written to `browser_acceptance_report.json`.

Browser tests are **not** part of GitHub Actions CI because they depend on local XAMPP, MySQL, Edge, and Playwright.

## Required Services (for local tests)

None for the pytest suite (uses in-memory fixture provider).

For browser acceptance tests: XAMPP Apache + MariaDB, NiceShop storefront, FastAPI service, Microsoft Edge, Playwright.

## Test Data Assumptions

- Fixture data is fictional (`fixture-*` IDs) and never represents live inventory
- Live catalog data (NiceShop) is external to this repository and not included
- Tests do not modify the database; no INSERT/UPDATE/DELETE is performed

## Interpreting Failures

| Failure | Likely cause |
|---|---|
| 401 on protected endpoints | Test service key mismatch (conftest sets it) |
| 422 validation_error | Invalid request payload |
| 504 upstream_timeout | Catalog provider timeout (mocked in tests) |
| 502 catalog_unavailable | Catalog error (mocked in tests) |
| Empty product results | Fixture query did not match; check search term |
| Starlette deprecation warning | Non-failing; `httpx2` dependency maintenance recommended |

Do not silently skip critical tests and claim the full suite passed. A failing test must be investigated before publishing.
