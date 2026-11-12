# Flexible Chat Requirements — Implementation Report

**Status:** Implemented with documented live-provider limitation
**Date:** 2026-11-12

## Root Cause

The intent schemas had no general-conversation intent. In deterministic mode, unrecognized messages generally defaulted to `search`; the API then tried catalog retrieval. Gemini response generation ran only when products were returned. Greetings, gratitude, and clarification requests returned deterministic text before general generation. Consequently, unmatched messages could end in a catalog “no matches” response instead of a natural conversational reply.

## Implementation

- Added a `conversation` intent for clearly non-shopping messages in deterministic and LLM intent parsing. Existing product, category, price, comparison, and policy intents retain their routes; unrecognized product-name searches remain searchable.
- Added a conversation-only Gemini response path with recent session history and no product or policy evidence. It instructs the model to clarify vague shopping requests, redirect out-of-scope questions, and avoid unsupported store claims.
- Routed greetings, gratitude, general conversation, eligible clarification requests, and searches with no results through that path when configured. Generation failures use the existing deterministic reply path.
- Added validation that rejects URLs, numeric claims, prices, and common unsupported availability, specification, and policy assertions in general replies.
- Added safe deterministic fallback text for general conversation.

## Files Changed

- `app/intents.py`
- `app/main.py`
- `app/response_generator.py`
- `app/services.py`
- `tests/test_ui_acceptance.py`
- `tests/test_llm_intent.py`
- `tests/test_response_generator.py`
- `tests/test_api.py`

Created this report; it did not previously exist.

## Routing, Context, and Preservation

- Recognized product/category requests continue through the existing catalog/RAG path; PHP remains authoritative for live catalog facts.
- Policy questions continue through the existing approved-policy RAG path.
- General responses receive the recent conversation turns but no unverified product or policy evidence. Turns continue to use existing session ownership and storage.
- No PHP source, database schema/data, Qdrant collections/indexes, embeddings, authentication, rate limiting, or API contract were changed. No dependencies were added.

## Verification

- **Full Python suite:** `.\.venv\Scripts\python.exe -m pytest tests\ -q` — **203 passed**, 0 failed, 2 warnings.
- Warnings: existing Starlette/httpx deprecation and Qdrant client/server version mismatch.
- Tests cover Arabic and English general conversation, assistant identity/capabilities, out-of-scope messages, vague shopping clarification, session history, fallback behavior, exact product-title search preservation, and general-response grounding checks.
- **Real browser:** Local NiceShop loaded successfully; the chat widget displayed a reply to “Hello.” The live Gemini call timed out, and the widget rendered the deterministic greeting fallback. This verifies widget/API fallback rendering, **not** successful LLM generation.
- FastAPI was restarted with the final code and `/v1/health` returned `ok`.

## Remaining Limitations

- Live Gemini generation remains unverified: the browser request ended in `ReadTimeout`. The deterministic fallback worked.
- General-output claim checks are defensive prompt and pattern-based validation, not a complete semantic verifier for every possible factual claim.
- In deterministic mode, explicitly recognizable social/out-of-scope phrasing is routed directly to conversation; other unfamiliar wording may first use catalog retrieval. If no products are returned, it reaches the general response path.

**Verdict: READY WITH DOCUMENTED LIMITATIONS** — automated regressions and browser fallback rendering passed; successful live LLM generation still needs verification when Gemini is available.
