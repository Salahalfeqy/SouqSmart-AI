# Contributing to SouqSmart

Thank you for contributing to SouqSmart. This is a repository preparation task: the goal is a clean, secure, documented, reproducible GitHub repository that preserves the existing working application.

## Scope of Contributions

- Bug fixes and documentation improvements are welcome.
- Do not redesign the application, rewrite working modules, change business logic, or introduce unrelated features.
- Do not add new chatbot features.
- Preserve the existing verified test baseline (78 automated tests, 11 browser acceptance scenarios).

## Getting Started

### 1. Clone and set up

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env and set SOUQSMART_SERVICE_KEY to a strong random value (never commit .env)
```

### 2. Run the test suite

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Expected baseline: **78 passed**, 1 non-failing Starlette deprecation warning.

### 3. Run the service

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/health
```

## Code Style

- Follow the existing file conventions in `app/` and `tests/`.
- Python: 4-space indentation, double quotes for strings, type hints where the existing code uses them.
- Do not reformat the entire codebase without need.
- No formatter/linter is currently pinned in `requirements.txt`. Do not introduce multiple competing tools.
- `.editorconfig` defines baseline editor settings (indent style, end of line, charset, trailing whitespace).

## Tests

- All tests must pass before a change is considered complete.
- Tests use the in-memory `fixture` catalog provider; no real credentials, network, or database are required.
- Do not silently skip critical tests.
- Browser acceptance tests (`run_all_browser_tests.py`) require Edge + Playwright + XAMPP + MySQL and are run locally, not in CI.

## Secrets

- Never commit `.env`, `.env.local`, `.env.production`, or any file containing real credentials.
- `.env.example` contains safe placeholders only.
- Do not print secret values in issues, pull requests, or logs.
- Rotate `SOUQSMART_SERVICE_KEY` and `GEMINI_API_KEY` before any production use.

## Pull Requests

1. Fork or branch from the repository.
2. Make your change with the smallest possible diff.
3. Run `python -m pytest -q` and confirm the baseline passes.
4. Update documentation if your change affects behavior, endpoints, or configuration.
5. Open a pull request describing the change and its verification.

## License

No license has been selected yet. Please specify a license before publishing.
