# SouqSmart – Quick Start Guide

## Overview
This guide explains how to get the **SouqSmart** demo up and running on a Windows machine. The project consists of two parts:
- **PHP catalog** served by Apache (XAMPP) located at `C:\xampp\htdocs\nice-shop`
- **Python FastAPI** backend (`app/`) that talks to the Gemini LLM and the PHP catalog.

Follow the steps below to set up the environment, start the services, verify the installation, and run the test suite.

---

## Prerequisites
| Requirement | How to get |
|------------|------------|
| **Windows 10/11** | Already on your machine. |
| **Python 3.11+** | Download from <https://www.python.org/downloads/> and ensure `python` is on `%PATH%`. |
| **Git** (optional) | <https://git-scm.com/downloads> |
| **XAMPP** (Apache + MariaDB) | <https://www.apachefriends.org/download.html>. Install to default location `C:\xampp`. |
| **Gemini API key** | Obtain from the Google AI Studio console and add it to the `.env` file (see below). |
| **Node.js** (for optional front‑end tooling) | Not required for the basic demo. |

## 1. Clone the repository (if not already present)
```powershell
cd D:\AI
git clone <REPO_URL> "souq smart"
cd "souq smart"
```
Replace `<REPO_URL>` with the URL you use for the project.

## 2. Set up the Python virtual environment
```powershell
# From the project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1  # PowerShell
# or
# .\.venv\Scripts\activate.bat  # cmd.exe
```
### Install dependencies
```powershell
pip install -r requirements.txt
```
The `requirements.txt` contains FastAPI, httpx, pydantic, pytest, and the Gemini SDK.

## 3. Configure environment variables
Create a file named `.env` in the project root (`D:\AI\souq smart`) with the following content:
```
# Gemini API configuration
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
# Optional: Adjust timeout (seconds)
GEMINI_TIMEOUT=10
```
Replace `YOUR_GEMINI_API_KEY` with the key you obtained earlier.
> **Note**: The backend reads this file via `python-dotenv`. Do **not** commit it to version control.

## 4. Verify the PHP catalog is reachable
1. Ensure XAMPP is installed.
2. Open the XAMPP Control Panel and **Start** *Apache* (port 80). If port 80 is already in use, you can change Apache's listening port in `httpd.conf` and adjust the URL in `app/config.py` accordingly.
3. Open a browser and navigate to:
   `http://localhost/nice-shop/catalog.json`
   You should see a JSON array of product objects (name, price, id, url, availability). This file is the source of truth for all product data.

## 5. Run the FastAPI backend
From the project root with the virtualenv activated:
```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
The server will start and log something like:
```
INFO:     Will watch for changes in these directories: ['D:\AI\souq smart']
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process using WatchFiles
INFO:     Application startup complete.
```
> **Tip**: Using `--reload` ensures that any edits made to Python files are instantly reflected without needing to manually kill and restart the server process.
Leave this terminal window open – it is the **daemon** for the backend.

## 6. Health‑check the API
Open another terminal (or reuse the same one after starting the server in a new tab) and run:
```powershell
curl http://127.0.0.1:8000/v1/health
```
You should receive a JSON response similar to:
```json
{"status":"ok","llm":{"configured":true,"model":"gemini-1.5-flash","timeout":10}}
```
If `configured` is `false`, double‑check the `.env` file and the Gemini key.

## 7. Quick manual test via `curl`
```powershell
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"منتجات أقل من 1000\"}]}"
```
The response JSON contains the `clarification_needed` flag (if the system asks for a currency) and an empty `products` array.

## 8. Run the automated test suite
```powershell
python -m pytest -q
```
You should see something like:
```
66 passed, 0 failed, 0 skipped, 0 warnings in 12.34s
```
If any test fails, the output will show the failing test name and traceback.

## 9. Stop the services
- **FastAPI**: Press `Ctrl+C` in the terminal where `uvicorn` is running.
- **Apache**: Open the XAMPP Control Panel and click **Stop** next to *Apache*.

---

## Troubleshooting
| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `Port 80 already in use` | Another web server (IIS, another Apache) is listening. | Change Apache's `Listen` directive to another port (e.g., `8080`) and update the URL in `app/config.py`. |
| `RuntimeError: Gemini not configured` | `.env` missing or `GEMINI_API_KEY` empty. | Ensure the `.env` file exists in the project root and contains a valid key. |
| `httpx.ConnectError` when calling Gemini | No internet or proxy block. | Verify network connectivity, or set `HTTP_PROXY`/`HTTPS_PROXY` env vars if required. |
| Tests failing on `ResponseGenerator` import | `app/response_generator.py` cannot find settings module. | Ensure the repository’s `settings.py` (or environment loader) is present and importable. |
| Product cards show wrong prices after clarification | Clarification path still invokes the catalog. | The bug is fixed; re‑run the manual test in step 7 to confirm empty `products`. |

---

## One‑liner recap
```powershell
cd "D:\AI\souq smart" && .\.venv\Scripts\Activate.ps1 && uvicorn app.main:app --host 127.0.0.1 --port 8000
```
This command activates the virtual environment and starts the FastAPI server in one go.

---

**Enjoy experimenting with SouqSmart!**
If you run into any issues not covered here, feel free to ask for additional help.
