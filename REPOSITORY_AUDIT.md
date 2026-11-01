# SouqSmart Repository Audit Report

**Repository:** `D:\AI\souq smart` (FastAPI AI shopping assistant service)
**Related integration:** NiceShop PHP storefront at `C:\xampp\htdocs\nice-shop` (NOT part of this repository)
**Audit scope:** Repository discovery, security, structure, and publication readiness
**Production deployment performed:** No

---

## 1. Project Boundaries

**In scope (SouqSmart Python repository):**

| Category | Files / directories |
|---|---|
| Python application | `app/main.py`, `app/config.py`, `app/intents.py`, `app/services.py`, `app/catalog.py`, `app/sessions.py`, `app/security.py`, `app/response_generator.py`, `app/schemas.py`, `app/__init__.py` |
| Tests | `tests/conftest.py`, `tests/test_api.py`, `tests/test_ui_acceptance.py`, `tests/test_llm_intent.py`, `tests/test_response_generator.py`, `tests/test_phase3_repairs.py` |
| Docs | `docs/api-contract.md` (existing), plus new docs created in Phase 6 |
| Config | `.env.example`, `requirements.txt` |
| Browser automation | `run_all_browser_tests.py` (requires Playwright + Edge + XAMPP + MySQL) |
| CI | `.github/workflows/tests.yml` (created in Phase 7) |

**Out of scope (NiceShop PHP — separate project, NOT copied in):**

The entire `C:\xampp\htdocs\nice-shop` tree is a separate application. Only its **integration contract** is documented here:

- `POST /internal/catalog/search` (protected by `X-Service-Key`)
- `GET /internal/catalog/products?ids=...` (protected by `X-Service-Key`)
- `POST /ajax/chat` (storefront chat proxy, CSRF + rate-limited)

PHP owns authentication, sessions, cart, checkout, prices, coupons, and customer-facing business rules. The FastAPI service is read-only and must not bypass PHP security or write to MariaDB.

---

## 2. What Belongs in the Repository

Include:
- All `app/*.py` source files
- All `tests/*.py` test files
- `requirements.txt`
- `.env.example` (placeholders only, no real credentials)
- `.gitignore`
- `README.md`
- `CONTRIBUTING.md`
- `docs/` (architecture, setup, api-reference, security, testing, deployment)
- `.github/workflows/tests.yml`
- `.editorconfig`
- `run_all_browser_tests.py` (browser acceptance driver, CI-excluded unless services available)

---

## 3. What Must Be Excluded

Excluded via `.gitignore`:
- `.env`, `.env.local`, `.env.production`, `env.*.local` — contain real credentials
- `.venv/`, `venv/` — Python virtual environment
- `__pycache__/`, `*.pyc`, `*.pyo` — Python bytecode
- `.pytest_cache/`, `.coverage`, `coverage.xml`, `htmlcov/` — test artifacts
- `dist/`, `build/`, `*.egg-info/` — build artifacts
- `.idea/`, `.vscode/` — IDE-specific settings
- `browser_acceptance_report.json`, `playwright-report/`, `test-results/` — generated browser artifacts
- `scratch/` — empty scratch directory
- `*.tmp`, `*.temp`, `~$*`, `.DS_Store`, `Thumbs.db` — OS/temp files
- Historical handoff/audit reports (local context only):
  - `AGENT_A_WORK_REPORT.md`
  - `FINAL_PHASE1_HANDOFF_READONLY_AUDIT.md`
  - `FINAL_RELEASE_AUDIT_REPORT.md`
  - `INDEPENDENT_AGENT_B_SECURITY_REMEDIATION_VERIFICATION.md`
  - `PHASE1_FINAL_INTEGRATION_REGRESSION_REPORT.md`
  - `PHASE1_INTEGRATION_VERIFICATION_REPORT.md`
  - `DOCUMENTATION_IMPLEMENTATION_CONSISTENCY_AUDIT.md`
  - `souqsmart-ai-phase0-report (1).md`
  - `souqsmart-ai-phase1-implementation-report.md`
  - `SOUQSMART_CONTEXT.md`
  - `SouqSmart – Quick Start Guide`

Not on disk / not included:
- NiceShop PHP source (`C:\xampp\htdocs\nice-shop`)
- MariaDB/MySQL database dumps
- Real customer/order data
- Production configuration
- `database_schema_documentation.md` (NiceShop-side)

---

## 4. Security Audit Findings

No secret values are printed in this report.

### Secrets found on disk

**`D:\AI\souq smart\.env`** (local Python environment file):
- `SOUQSMART_SERVICE_KEY` — a concrete shared secret value in plaintext
- `GEMINI_API_KEY` — a concrete Gemini API key value in plaintext

**`C:\xampp\htdocs\nice-shop\.env`** (NiceShop, outside this repository boundary):
- `SOUQSMART_INTERNAL_KEY` — matching shared secret
- `SOUQSMART_SERVICE_KEY` — matching shared secret

### Git tracking status

There is **no Git repository** present in `D:\AI\souq smart` (no `.git` directory). Therefore no secret is currently tracked by Git. The `.env` file is already listed in `.gitignore` and must remain excluded before any `git init` / `git add`.

### Recommendations

1. **Do not commit `.env`.** It is already excluded by `.gitignore`.
2. **Rotate the exposed credentials.** Both `SOUQSMART_SERVICE_KEY` and `GEMINI_API_KEY` exist in plaintext local configuration and may have been exposed to backups or logs. Rotate them before publishing.
3. **Use `.env.example` only** for developer onboarding; it contains safe placeholders.
4. **Fail-closed validation** is already implemented in `app/config.py`: live PHP mode rejects missing, empty, and known-insecure service keys.
5. **PHP upstream logging** is sanitized (error code/category/request ID only; no response bodies) per the NiceShop `controllers/ajax/chat.php` review.

---

## 5. Documentation Needs

Created in Phase 6:
- `README.md` — project overview, features, setup, tests, security, known limitations
- `docs/architecture.md` — components, request lifecycle, intent parsing, fallback, Gemini, catalog, sessions, security boundaries
- `docs/setup.md` — Windows/local dev setup, Python venv, XAMPP, NiceShop, FastAPI, health, Swagger
- `docs/api-reference.md` — actual FastAPI endpoints (`GET /v1/health`, `POST /v1/products/search`, `POST /v1/products/compare`, `POST /v1/chat/messages`)
- `docs/security.md` — service-key auth, PHP protection, CSRF, rate limiting, session handling, secret management, AI service boundaries
- `docs/testing.md` — pytest commands, test structure, browser acceptance, failures
- `docs/deployment.md` — local vs staging vs production, no production-readiness claim

---

## 6. Deployment / Dependency Risks

| Risk | Severity | Notes |
|---|---|---|
| Plaintext shared secret + Gemini key in `.env` | High | Must not be committed; rotate before publishing |
| No Git repository exists | Medium | `git init` required before publishing |
| Python 3.13.2 verified runtime | Low | `requirements.txt` pins major ranges only |
| Browser acceptance tests require Edge + Playwright + XAMPP + MySQL | Medium | `run_all_browser_tests.py` is local-only, not part of CI |
| Starlette `TestClient` deprecation warning | Low | Non-failing; dependency maintenance recommended |
| Price-range filters not enforced on PHP `CatalogService.search` | Medium | Python sends `price_min`/`price_max` but PHP may not translate them (documented in integration reports) |
| No production deployment verified | — | Do not claim production readiness |

---

## 7. Verified Baseline (do not regress)

- Automated tests: **78 passed**, 1 deprecation warning, exit code 0
  - Command: `.\.venv\Scripts\python.exe -m pytest -q`
- Real-browser acceptance: **11/11 scenarios** passed (historical evidence in `browser_acceptance_report.json`)
- PHP suites (NiceShop side): 40 unit passed, 14 live HTTP passed, end-to-end passed (historical evidence, NiceShop-side only)
