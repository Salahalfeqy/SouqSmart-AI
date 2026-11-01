# Setup — Local Development

This guide covers setting up the SouqSmart FastAPI service for local development on Windows.

## Prerequisites

- Python 3.12+ (verified with Python 3.13.2)
- pip and venv (included with Python)
- For live catalog mode: XAMPP (Apache + MariaDB) with the NiceShop storefront at `C:\xampp\htdocs\nice-shop`
- For browser acceptance tests: Microsoft Edge and Playwright

## Python Virtual Environment

From the repository root in PowerShell:

```powershell
# Create the virtual environment
py -3.13 -m venv .venv

# Activate it
.\.venv\Scripts\Activate.ps1

# Verify activation (prompt should show .venv)
python --version
```

## Installing Dependencies

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` installs: FastAPI, Uvicorn, pydantic-settings, httpx, pytest, pytest-asyncio.

## Environment Configuration

Copy the example file and edit it:

```powershell
Copy-Item .env.example .env
# Open .env in a text editor and set SOUQSMART_SERVICE_KEY to a strong random value.
```

Never commit `.env`. It is excluded by `.gitignore`.

For live PHP catalog mode, set the same `SOUQSMART_SERVICE_KEY` value as `SOUQSMART_INTERNAL_KEY` in NiceShop's local configuration (outside this repository).

## XAMPP (NiceShop Storefront)

1. Install and start XAMPP (Apache + MySQL/MariaDB).
2. Ensure the NiceShop storefront is deployed at `C:\xampp\htdocs\nice-shop`.
3. Configure NiceShop's `SOUQSMART_INTERNAL_KEY` to match the FastAPI `SOUQSMART_SERVICE_KEY`.
4. Confirm the internal catalog endpoints are reachable:
   - `http://localhost/nice-shop/internal/catalog/search`
   - `http://localhost/nice-shop/internal/catalog/products?ids=1`

## NiceShop Integration

The FastAPI service calls NiceShop through protected internal endpoints:

- `POST /internal/catalog/search` (body: `query`, `limit`, `offset`, `category`, `brand`, `availability`, `price_min`, `price_max`)
- `GET /internal/catalog/products?ids=1,2`

Both require the `X-Service-Key` header. PHP restricts these to loopback callers by default.

See `docs/api-contract.md` for the PHP integration contract and `README.md` for the connection steps.

## Starting FastAPI

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For auto-reload during development:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Start only on loopback (`127.0.0.1`). `SOUQSMART_HOST` and `SOUQSMART_PORT` are settings defaults only; Uvicorn bind arguments must be supplied explicitly on the command line.

## Verifying Health

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/health
```

Expected (deterministic mode, no Gemini key):

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

## Accessing Swagger

With the service running, open:

```
http://127.0.0.1:8000/docs
```

The interactive OpenAPI UI lets you call `GET /v1/health` (public) and the protected endpoints (provide `X-Service-Key`).
