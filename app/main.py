import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.catalog import CatalogError, CatalogTimeout, FixtureProvider, PHPProvider
from app.config import get_settings
from app.schemas import ChatRequest, CompareRequest, Product, SearchRequest
from app.security import verify_service_key
from app.services import (
    chat_reply,
    compare_products,
    is_accessory_product,
    is_accessory_query,
    is_laptop_query,
    parse_chat_intent,
    rank_products,
    template_comparison,
)
from app.sessions import MemorySessions

settings = get_settings()
if settings.catalog_provider == "fixture":
    catalog = FixtureProvider()
elif settings.catalog_provider == "php":
    catalog = PHPProvider(settings.php_base_url, settings.service_key, settings.catalog_timeout_seconds)
else:
    raise RuntimeError("SOUQSMART_CATALOG_PROVIDER must be 'php' or 'fixture'")
sessions = MemorySessions(settings.session_ttl_seconds, settings.session_turn_limit)
app = FastAPI(title="SouqSmart AI Service", version="1.0.0")
service_key_header = APIKeyHeader(name="X-Service-Key", auto_error=False)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def authorized(_: str | None = Depends(service_key_header)) -> None:
    verify_service_key(settings.service_key, _)


@app.middleware("http")
async def request_context(request: Request, call_next: Callable) -> Response:
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.monotonic()
    try:
        response = await call_next(request)
    except CatalogTimeout:
        response = error(request, 504, "upstream_timeout", "The catalog request timed out", retryable=True)
    except CatalogError:
        response = error(request, 502, "catalog_unavailable", "The catalog is unavailable", retryable=True)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Response-Time-Ms"] = str(round((time.monotonic() - started) * 1000, 2))
    return response


def error(request: Request, status: int, code: str, message: str, details: object = None, retryable: bool = False) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details, "request_id": getattr(request.state, "request_id", str(uuid.uuid4())), "retryable": retryable}})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return error(request, 401, "unauthorized", "Missing or invalid service key")
    return error(request, exc.status_code, "http_error", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(part) for part in item["loc"] if part != "body"), "issue": item["msg"]} for item in exc.errors()]
    return error(request, 422, "validation_error", "Request validation failed", details, False)


@app.exception_handler(Exception)
async def unexpected_handler(request: Request, exc: Exception):
    if isinstance(exc, CatalogTimeout):
        return error(request, 504, "upstream_timeout", "The catalog request timed out", retryable=True)
    if isinstance(exc, CatalogError):
        return error(request, 502, "catalog_unavailable", "The catalog is unavailable", retryable=True)
    return error(request, 500, "internal_error", "An internal error occurred", retryable=False)


@app.get("/v1/health")
async def health():
    is_llm_mode = settings.intent_parser == "llm"
    has_api_key = bool(settings.gemini_api_key.strip())

    if is_llm_mode and has_api_key:
        llm_status = "enabled"
    elif is_llm_mode and not has_api_key:
        llm_status = "unconfigured"
    else:
        llm_status = "disabled"

    return {
        "status": "ok",
        "version": app.version,
        "catalog_provider": settings.catalog_provider,
        "llm": {
            "status": llm_status,
            "provider": "gemini" if is_llm_mode else None,
            "model": settings.gemini_model if is_llm_mode else None,
            "configured": is_llm_mode,
            "usable": is_llm_mode and has_api_key,
        },
    }


@app.post("/v1/products/search", dependencies=[Depends(authorized)])
async def search(payload: SearchRequest, request: Request):
    provider_payload = payload.model_dump(mode="json")
    products, total = await catalog.search(provider_payload)
    results = rank_products(products, payload.query, payload.sort)
    return {"results": results, "total": total, "limit": payload.limit, "offset": payload.offset, "search_mode": "keyword", "data_as_of": now_iso()}


@app.post("/v1/products/compare", dependencies=[Depends(authorized)])
async def compare(payload: CompareRequest, request: Request):
    products, missing = await catalog.products(payload.product_ids)
    if missing:
        return error(request, 404, "product_not_found", "One or more products were not found", {"missing_ids": missing}, False)
    ordered = [next(p for p in products if p.product_id == item) for item in payload.product_ids]
    derived, table = compare_products(ordered)
    summary = None
    if payload.include_summary:
        summary = {"text": template_comparison(ordered, derived), "generated": True, "source": "template", "grounded_on": payload.product_ids}
    return {"products": ordered, "table": {"rows": table}, "derived": derived, "summary": summary, "data_as_of": now_iso()}


@app.post("/v1/chat/messages", dependencies=[Depends(authorized)])
async def chat(payload: ChatRequest, request: Request):
    session_id, status = sessions.get_or_create(payload.session_id)
    products: list[Product] = []
    accessories: list[Product] = []
    id_match = payload.context.current_product_id

    intent = parse_chat_intent(payload.message, payload.language)

    # 1. Standalone greeting: return immediately without catalog query or Gemini generation
    if intent.is_greeting or intent.intent_type == "gratitude":
        reply = chat_reply(
            payload.message,
            [],
            language=intent.language,
            intent=intent,
            accessories=[],
        )
        sessions.add_turn(session_id, payload.message, reply)
        return {
            "session_id": session_id,
            "session_status": status,
            "message_id": f"msg_{len(sessions._sessions[session_id].turns)}",
            "reply": {"text": reply, "language": intent.language, "generated": True},
            "products": [],
            "ai_status": "disabled",
            "data_as_of": now_iso(),
        }

    # 2. Clarification needed without a specified currency: return immediately without catalog query or generic suggestions
    if intent.clarification_needed and not intent.budget_currency:
        reply = chat_reply(
            payload.message,
            [],
            language=intent.language,
            intent=intent,
            accessories=[],
        )
        sessions.add_turn(session_id, payload.message, reply)
        return {
            "session_id": session_id,
            "session_status": status,
            "message_id": f"msg_{len(sessions._sessions[session_id].turns)}",
            "reply": {"text": reply, "language": intent.language, "generated": True},
            "products": [],
            "ai_status": "disabled",
            "data_as_of": now_iso(),
        }

    # Adjust browse intents where the parser left a stray filler query but the user asked for general browsing.
    # If the request has a stock filter but no meaningful catalog query, treat it as a generic browse.
    if intent.is_browse or (intent.filters.get("in_stock_only") and not intent.catalog_query.strip()):
        intent.is_browse = True
        intent.catalog_query = ""
        intent.clean_query = ""

    if intent.is_compare and intent.comparison_names:
        # Resolve each full name independently; do not substitute partial matches.
        for name in intent.comparison_names:
            offset = 0
            while True:
                matches, total = await catalog.search({
                    "query": name, "filters": {}, "sort": "relevance", "limit": 20, "offset": offset,
                })
                exact = [p for p in matches if p.name.casefold() == name.casefold()]
                if exact:
                    product = exact[0]
                    if all(p.product_id != product.product_id for p in products):
                        products.append(product)
                    break
                offset += len(matches)
                if not matches or offset >= total:
                    break
    elif id_match:
        products, _ = await catalog.products([id_match])
    elif intent.intent_type != "greeting" and (intent.catalog_query or intent.is_browse or intent.filters):
        provider_payload = {
            "query": intent.catalog_query[:200],
            "filters": intent.filters,
            "sort": "relevance",
            "limit": 5,
            "offset": 0,
        }
        products, _ = await catalog.search(provider_payload)

        # Separate accessories from actual devices when user explicitly asks for a laptop
        if is_laptop_query(intent) and not is_accessory_query(intent):
            real_laptops = [p for p in products if not is_accessory_product(p)]
            if not real_laptops:
                accessories = [p for p in products if is_accessory_product(p)]
                products = []
            else:
                products = real_laptops

    # Generate response using Gemini if LLM mode is enabled, otherwise fallback.
    # Handle multiple-price requests even in deterministic mode
    is_multi_price = any(phrase in payload.message for phrase in ("كذا سعر", "اكتر من سعر", "أكثر من سعر", "عدة أسعار", "عده اسعار", "اسعار مختلفة", "أسعار مختلفة", "multiple price", "multiple prices"))
    if intent.is_compare:
        reply = chat_reply(payload.message, products, language=intent.language, intent=intent)
    elif is_multi_price and not products:
        if intent.language == "ar":
            reply = "أفهم أنك تريد معرفة أسعار متعددة لنفس المنتج. المتجر يوفّر سعرًا واحدًا لكل منتج؛ إذا كان هناك إصدارات مختلفة أو عروض، سأتحدث عنها عندما تكون موجودة في الكتالوج."
        else:
            reply = "I understand you are asking for multiple price options for a single product. The store provides a single price per product; if there are variants or offers, I will mention them when they appear in the catalog."
    elif settings.intent_parser == "llm" and settings.gemini_api_key.strip():
        # Special case: multiple price request for a single product.
        if is_multi_price and not products:
            if intent.language == "ar":
                reply = "أفهم أنك تريد معرفة أسعار متعددة لنفس المنتج. المتجر يوفّر سعرًا واحدًا لكل منتج؛ إذا كان هناك إصدارات مختلفة أو عروض، سأتحدث عنها عندما تكون موجودة في الكتالوج."
            else:
                reply = "I understand you are asking for multiple price options for a single product. The store provides a single price per product; if there are variants or offers, I will mention them when they appear in the catalog."
        # Bypass Gemini for clarification intents or when no factual products exist.
        elif intent.clarification_needed or (not products and not intent.is_browse):
            reply = chat_reply(
                payload.message,
                products,
                language=intent.language,
                intent=intent,
                accessories=accessories,
            )
        else:
            try:
                from app.response_generator import ResponseGenerator
                generator = ResponseGenerator()
                reply = generator.generate(intent, products, intent.language)
            except Exception:
                # On any error, fall back to deterministic reply.
                reply = chat_reply(
                    payload.message,
                    products,
                    language=intent.language,
                    intent=intent,
                    accessories=accessories,
                )
    else:

        reply = chat_reply(
            payload.message,
            products,
            language=intent.language,
            intent=intent,
            accessories=accessories,
        )
    sessions.add_turn(session_id, payload.message, reply)
    return {
        "session_id": session_id,
        "session_status": status,
        "message_id": f"msg_{len(sessions._sessions[session_id].turns)}",
        "reply": {"text": reply, "language": intent.language, "generated": True},
        "products": products,
        "ai_status": "disabled",
        "data_as_of": now_iso(),
    }
