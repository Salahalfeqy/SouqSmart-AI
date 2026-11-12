import inspect
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.catalog import CatalogError, CatalogTimeout, FixtureProvider, PHPProvider
from app.config import get_settings
from app.database import init_database
from app.intents import (
    ParsedIntent,
    STORE_CURRENCY,
    is_price_removal_query,
    merge_conversational_constraints,
)
from app.query_rewriter import QueryRewriter
from app.rag.embeddings import GeminiEmbeddingClient
from app.rag.knowledge import KnowledgeBaseManager
from app.rag.qdrant_client import ensure_collections, get_qdrant_client
from app.rag.retriever import HybridRetriever
from app.schemas import ChatRequest, CompareRequest, Product, SearchRequest
from app.security import verify_service_key
from app.services import (
    chat_reply,
    compare_products,
    filter_device_candidates,
    generate_policy_reply,
    is_accessory_product,
    is_accessory_query,
    is_laptop_query,
    parse_chat_intent,
    rank_products,
    template_comparison,
)
from app.sessions import MemorySessions
from app.sessions_db import DatabaseSessionStore

logger = logging.getLogger("souqsmart.api")
settings = get_settings()

if settings.catalog_provider == "fixture":
    catalog = FixtureProvider()
elif settings.catalog_provider == "php":
    catalog = PHPProvider(settings.php_base_url, settings.service_key, settings.catalog_timeout_seconds)
else:
    raise RuntimeError("SOUQSMART_CATALOG_PROVIDER must be 'php' or 'fixture'")

if settings.session_store == "database":
    sessions: Any = DatabaseSessionStore(
        settings.database_path,
        settings.session_ttl_seconds,
        settings.session_turn_limit,
        allow_dev_anonymous=(settings.allow_anonymous_dev_sessions and settings.catalog_provider != "php"),
    )
else:
    sessions = MemorySessions(settings.session_ttl_seconds, settings.session_turn_limit)

query_rewriter = QueryRewriter()
embedding_client = GeminiEmbeddingClient()
qdrant_client = get_qdrant_client(settings)
retriever = HybridRetriever(catalog, embedding_client, qdrant_client)
kb_manager = KnowledgeBaseManager(embedder=embedding_client, qdrant=qdrant_client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.session_store == "database":
        try:
            await init_database(settings.database_path)
        except Exception:
            pass
    if settings.catalog_provider == "php" and settings.rag_hybrid_enabled:
        try:
            await ensure_collections(qdrant_client, settings.embedding_dimension)
        except Exception:
            pass
    yield


app = FastAPI(title="SouqSmart AI Service", version="1.0.0", lifespan=lifespan)
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
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": getattr(request.state, "request_id", str(uuid.uuid4())),
                "retryable": retryable,
            }
        },
    )


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
    return {
        "results": results,
        "total": total,
        "limit": payload.limit,
        "offset": payload.offset,
        "search_mode": "keyword",
        "data_as_of": now_iso(),
    }


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


async def _session_get_or_create(sess: Any, session_id: str | None, owner_hash: str) -> tuple[str, str]:
    if inspect.iscoroutinefunction(sess.get_or_create):
        return await sess.get_or_create(session_id, owner_hash)
    return sess.get_or_create(session_id, owner_hash)


async def _session_add_turn(sess: Any, session_id: str, user: str, assistant: str, **kwargs) -> int:
    if inspect.iscoroutinefunction(sess.add_turn):
        return await sess.add_turn(session_id=session_id, user=user, assistant=assistant, **kwargs)
    sess.add_turn(session_id, user, assistant, **kwargs)
    return sess.get_turn_count(session_id)


async def _session_get_turn_count(sess: Any, session_id: str) -> int:
    if inspect.iscoroutinefunction(sess.get_turn_count):
        return await sess.get_turn_count(session_id)
    return sess.get_turn_count(session_id)


async def _session_get_turns(sess: Any, session_id: str, limit: int | None = None) -> list[dict]:
    if inspect.iscoroutinefunction(sess.get_turns):
        return await sess.get_turns(session_id, limit)
    return sess.get_turns(session_id, limit)


async def _generate_general_reply(
    message: str,
    intent: ParsedIntent,
    history: list[dict],
    *,
    catalog_search_performed: bool = False,
) -> str:
    if settings.gemini_api_key.strip():
        try:
            from app.response_generator import ResponseGenerator

            return await ResponseGenerator().agenerate_general(
                intent,
                message,
                intent.language,
                history=history,
                catalog_search_performed=catalog_search_performed,
            )
        except Exception as exc:
            logger.warning("General response generation failed (%s)", type(exc).__name__)
    return chat_reply(message, [], language=intent.language, intent=intent, accessories=[])


@app.post("/v1/chat/messages", dependencies=[Depends(authorized)])
async def chat(payload: ChatRequest, request: Request, response: Response):
    raw_owner = (
        request.headers.get("X-Owner-Hash")
        or payload.owner_hash
        or request.cookies.get("souqsmart_owner")
    )
    is_php_mode = (settings.catalog_provider == "php")
    clean_owner = (raw_owner or "").strip()
    is_invalid_owner = (not clean_owner or clean_owner.lower() == "guest")

    if is_php_mode:
        if is_invalid_owner:
            raise HTTPException(
                status_code=403,
                detail="Missing or invalid owner identity: owner_hash is required in PHP-integrated mode and cannot be 'guest'",
            )
        owner_hash = clean_owner
        is_new_anon = False
    else:
        # Standalone development / fixture mode
        if is_invalid_owner:
            if not settings.allow_anonymous_dev_sessions:
                raise HTTPException(
                    status_code=403,
                    detail="Missing or invalid owner identity: anonymous sessions disabled in current configuration",
                )
            owner_hash = f"dev_anon_{uuid.uuid4().hex}"
            is_new_anon = True
        else:
            owner_hash = clean_owner
            is_new_anon = False

    try:
        session_id, status = await _session_get_or_create(sessions, payload.session_id, owner_hash)
    except ValueError as val_err:
        raise HTTPException(status_code=403, detail=str(val_err))

    if not is_php_mode and (is_new_anon or request.cookies.get("souqsmart_owner") != owner_hash):
        response.set_cookie(key="souqsmart_owner", value=owner_hash, httponly=True, samesite="lax")
    response.headers["X-Owner-Hash"] = owner_hash
    products: list[Product] = []
    accessories: list[Product] = []
    retrieval_sources: list[dict[str, Any]] = []
    id_match = payload.context.current_product_id

    # 1. Immediate intent check for greetings and gratitude
    intent = parse_chat_intent(payload.message, payload.language)

    # 3. Query Rewriting for follow-up reference resolution (if multi-turn)
    rewritten_query_text = payload.message
    rewritten_info = None
    past_turns = await _session_get_turns(sessions, session_id, limit=3)
    if past_turns and not (intent.is_greeting or intent.intent_type == "gratitude"):
        last_turn = past_turns[-1]
        previous_constraints = last_turn.get("constraints") or {}
        last_pids = last_turn.get("retrieved_product_ids") or []
        last_products = [{"id": pid, "name": ""} for pid in last_pids]
        if settings.enable_query_rewriter:
            rewritten_info = await query_rewriter.rewrite(
                payload.message,
                history=past_turns,
                last_products=last_products,
                previous_constraints=previous_constraints,
                language=payload.language,
            )
            if rewritten_info.is_followup and rewritten_info.rewritten_query != payload.message:
                rewritten_query_text = rewritten_info.rewritten_query
                intent = parse_chat_intent(rewritten_query_text, payload.language)

        intermediate_filters = dict(previous_constraints)
        if rewritten_info and rewritten_info.merged_filters:
            intermediate_filters.update(rewritten_info.merged_filters)

        intent.filters = merge_conversational_constraints(
            previous_constraints=intermediate_filters,
            current_filters=intent.filters,
            message=payload.message,
        )

        if is_price_removal_query(payload.message) and "price_min" not in intent.filters and "price_max" not in intent.filters:
            intent.budget = None
            intent.budget_currency = None
        elif intent.filters.get("price_min"):
            try:
                intent.budget = Decimal(str(intent.filters["price_min"]))
                intent.budget_currency = STORE_CURRENCY
            except Exception:
                pass
        elif intent.filters.get("price_max"):
            try:
                intent.budget = Decimal(str(intent.filters["price_max"]))
                intent.budget_currency = STORE_CURRENCY
            except Exception:
                pass

    # Adjust browse intents where the parser left a stray filler query
    if intent.is_browse or (intent.filters.get("in_stock_only") and not intent.catalog_query.strip()):
        intent.is_browse = True
        intent.catalog_query = ""
        intent.clean_query = ""

    is_general_turn = (
        intent.is_greeting
        or intent.intent_type in {"gratitude", "conversation"}
        or (intent.clarification_needed and not intent.budget_currency)
    )
    if is_general_turn:
        reply = (
            chat_reply(payload.message, [], language=intent.language, intent=intent, accessories=[])
            if intent.currency_note
            else await _generate_general_reply(payload.message, intent, past_turns)
        )
        await _session_add_turn(
            sess=sessions,
            session_id=session_id,
            user=payload.message,
            assistant=reply,
            rewritten_query=rewritten_query_text,
            intent_type=intent.intent_type,
            constraints=intent.filters,
        )
        turn_count = await _session_get_turn_count(sessions, session_id)
        return {
            "session_id": session_id,
            "session_status": status,
            "message_id": f"msg_{turn_count}",
            "reply": {"text": reply, "language": intent.language, "generated": True},
            "products": [],
            "ai_status": "disabled",
            "data_as_of": now_iso(),
        }

    # 3.5 Policy knowledge retrieval & grounded response
    if intent.is_policy:
        knowledge_chunks: list[dict[str, Any]] = []
        policy_doc_ids = {
            "shipping_policy": "shipping",
            "return_policy": "returns",
            "payment_policy": "payment",
        }
        if settings.qdrant_collection_knowledge:
            try:
                raw_hits = await kb_manager.search(
                    query=intent.clean_query or payload.message,
                    top_k=2,
                    language=None,
                )
                for h in raw_hits:
                    score = float(h.get("score", 0.0))
                    doc_id = str(h.get("doc_id", "")).lower()
                    if score >= 0.70:
                        if intent.intent_type == "shipping_policy" and "shipping" in doc_id:
                            knowledge_chunks.append(h)
                        elif intent.intent_type == "return_policy" and "return" in doc_id:
                            knowledge_chunks.append(h)
                        elif intent.intent_type == "payment_policy" and "payment" in doc_id:
                            knowledge_chunks.append(h)
            except Exception as exc:
                logger.warning("Policy knowledge retrieval failed: %s", exc)
                knowledge_chunks = []

        if not knowledge_chunks and intent.intent_type in policy_doc_ids:
            local_policy = kb_manager.load_approved_policy(policy_doc_ids[intent.intent_type])
            if local_policy:
                knowledge_chunks = [local_policy]

        retrieval_sources = [
            {
                "doc_id": chunk.get("doc_id"),
                "doc_title": chunk.get("doc_title"),
                "section": chunk.get("section"),
                "source": chunk.get("source"),
            }
            for chunk in knowledge_chunks
        ]
        reply = None
        if knowledge_chunks and settings.gemini_api_key.strip():
            try:
                from app.response_generator import ResponseGenerator

                reply = await ResponseGenerator().agenerate(
                    intent,
                    [],
                    intent.language,
                    user_message=payload.message,
                    rewritten_query=rewritten_query_text,
                    history=past_turns,
                    knowledge_chunks=knowledge_chunks,
                )
            except Exception as exc:
                logger.warning("Policy response generation failed: %s", exc)
                reply = None

        if not reply:
            reply = generate_policy_reply(
                intent=intent,
                knowledge_chunks=knowledge_chunks,
                language=intent.language,
                user_message=payload.message,
            )

        await _session_add_turn(
            sess=sessions,
            session_id=session_id,
            user=payload.message,
            assistant=reply,
            rewritten_query=rewritten_query_text,
            intent_type=intent.intent_type,
            retrieved_product_ids=[],
            sources=retrieval_sources,
        )
        turn_count = await _session_get_turn_count(sessions, session_id)
        return {
            "session_id": session_id,
            "session_status": status,
            "message_id": f"msg_{turn_count}",
            "reply": {"text": reply, "language": intent.language, "generated": True},
            "products": [],
            "ai_status": "disabled",
            "data_as_of": now_iso(),
        }

    # 4. Product resolution
    retrieval_metadata: list[dict[str, Any]] = []
    if intent.is_compare and intent.comparison_names:
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
                        retrieval_metadata.append({
                            "product_id": str(product.product_id),
                            "source": "php_exact_name",
                        })
                    break
                offset += len(matches)
                if not matches or offset >= total:
                    break
    elif id_match:
        products, _ = await catalog.products([id_match])
        retrieval_metadata = [
            {"product_id": str(product.product_id), "source": "php_product_id"}
            for product in products
        ]
    elif intent.intent_type != "greeting" and (intent.catalog_query or intent.is_browse or intent.filters):
        provider_payload = {
            "query": intent.catalog_query[:200],
            "filters": intent.filters,
            "sort": "relevance",
            "limit": 5,
            "offset": 0,
        }

        # Use hybrid retrieval in live PHP mode when query is present and not general browse
        if settings.catalog_provider == "php" and settings.rag_hybrid_enabled and intent.catalog_query and not intent.is_browse:
            try:
                products, retrieval_metadata = await retriever.retrieve_products(
                    query=intent.catalog_query,
                    filters=intent.filters,
                    limit=5,
                    hybrid_enabled=True,
                )
                for item in retrieval_metadata:
                    item["source"] = "hybrid"
            except Exception as exc:
                logger.warning("Hybrid product retrieval failed: %s", exc)
                products, _ = await catalog.search(provider_payload)
                retrieval_metadata = [
                    {"product_id": str(product.product_id), "source": "php_catalog"}
                    for product in products
                ]
        else:
            products, _ = await catalog.search(provider_payload)
            retrieval_metadata = [
                {"product_id": str(product.product_id), "source": "php_catalog"}
                for product in products
            ]

        # Strict product-type validation for explicit device/category requests
        if is_laptop_query(intent) and not is_accessory_query(intent):
            real_products, related_accessories = filter_device_candidates(intent, products)
            if not real_products:
                accessories = related_accessories
                products = []
            else:
                products = real_products
            product_ids = {str(product.product_id) for product in products}
            retrieval_metadata = [
                item for item in retrieval_metadata
                if str(item.get("product_id")) in product_ids
            ]

    # 5. Generate response using Gemini if LLM mode is enabled, otherwise fallback
    is_multi_price = any(
        phrase in payload.message
        for phrase in ("كذا سعر", "اكتر من سعر", "أكثر من سعر", "عدة أسعار", "عده اسعار", "اسعار مختلفة", "أسعار مختلفة", "multiple price", "multiple prices")
    )
    if is_multi_price and not products:
        if intent.language == "ar":
            reply = "أفهم أنك تريد معرفة أسعار متعددة لنفس المنتج. المتجر يوفّر سعرًا واحدًا لكل منتج؛ إذا كان هناك إصدارات مختلفة أو عروض، سأتحدث عنها عندما تكون موجودة في الكتالوج."
        else:
            reply = "I understand you are asking for multiple price options for a single product. The store provides a single price per product; if there are variants or offers, I will mention them when they appear in the catalog."
    elif (
        not products
        and not intent.is_compare
        and not intent.clarification_needed
        and not intent.currency_note
        and not is_laptop_query(intent)
        and settings.gemini_api_key.strip()
    ):
        reply = await _generate_general_reply(
            payload.message,
            intent,
            past_turns,
            catalog_search_performed=True,
        )
    elif (
        products
        and settings.gemini_api_key.strip()
        and not intent.clarification_needed
        and not intent.currency_note
        and (not intent.is_compare or len(products) >= 2)
    ):
        try:
            from app.response_generator import ResponseGenerator

            reply = await ResponseGenerator().agenerate(
                intent,
                products,
                intent.language,
                user_message=payload.message,
                rewritten_query=rewritten_query_text,
                history=past_turns,
            )
        except Exception as exc:
            logger.warning("Product response generation failed: %s", exc)
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

    # 6. Record turn with RAG metadata
    retrieved_ids = [str(p.product_id) for p in products]
    referenced_ids = rewritten_info.referenced_product_ids if rewritten_info else []
    await _session_add_turn(
        sess=sessions,
        session_id=session_id,
        user=payload.message,
        assistant=reply,
        rewritten_query=rewritten_query_text,
        intent_type=intent.intent_type,
        referenced_product_ids=referenced_ids,
        retrieved_product_ids=retrieved_ids,
        constraints=intent.filters,
        sources=retrieval_sources or retrieval_metadata,
    )

    turn_count = await _session_get_turn_count(sessions, session_id)
    return {
        "session_id": session_id,
        "session_status": status,
        "message_id": f"msg_{turn_count}",
        "reply": {"text": reply, "language": intent.language, "generated": True},
        "products": products,
        "ai_status": "disabled",
        "data_as_of": now_iso(),
    }
