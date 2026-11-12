import asyncio
import logging
import re
from typing import Any
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.catalog import CatalogProvider
from app.config import get_settings
from app.intents import AR_EN_PRODUCT_SYNONYMS
from app.rag.embeddings import BaseEmbeddingProvider
from app.rag.fusion import reciprocal_rank_fusion
from app.schemas import Product

logger = logging.getLogger("souqsmart.retriever")

BAG_QUERY_TERMS = {"bag", "backpack", "backpacks", "bags", "شنطة", "شنطه", "حقيبة", "حقيبه"}
INCOMPATIBLE_BAG_TITLES = {"stand", "holder", "charger", "mouse", "keyboard", "lamp", "حامل", "ستاند"}


def is_candidate_compatible(query: str, payload: dict[str, Any]) -> bool:
    """Lightweight category and product-type compatibility check."""
    q_tokens = set(re.findall(r"\w+", query.lower()))
    name_tokens = set(re.findall(r"\w+", (payload.get("name") or "").lower()))
    cat_lower = (payload.get("category") or "").lower()

    # If the user specifically requested a bag/backpack, exclude stands and electronics peripherals
    if q_tokens.intersection(BAG_QUERY_TERMS):
        if name_tokens.intersection(INCOMPATIBLE_BAG_TITLES):
            return False
        if cat_lower in {"electronics", "grocery", "toys & games"}:
            return False

    # If the user specifically requested a laptop/computer device, exclude non-tech categories
    laptop_terms = {"laptop", "laptops", "لابتوب", "حاسوب", "كمبيوتر"}
    if q_tokens.intersection(laptop_terms):
        if cat_lower in {"grocery", "books & stationery", "furniture", "toys & games", "beauty & personal care", "fashion"}:
            return False

    # If the query is specifically for webcams / video calls / online meetings, exclude non-tech categories
    webcam_terms = {"webcam", "كاميرا", "ويبكام", "ويب كام", "meetings", "الاجتماعات", "الفيديو"}
    if q_tokens.intersection(webcam_terms):
        if cat_lower not in {"electronics", "accessories"}:
            return False

    return True


def evaluate_retrieval_confidence(
    query: str,
    dense_results: list[tuple[str, float, dict[str, Any]]],
    keyword_ids: list[str],
) -> bool:
    """
    Calibrated confidence check for 100-product catalog.
    Separates dense cosine similarity from fused scores.
    Distinguishes valid catalog products from ungrounded / out-of-catalog hallucinations.
    """
    if not dense_results:
        return bool(keyword_ids)

    top_pid, top_score, top_payload = dense_results[0]
    top_name = (top_payload.get("name") or "").lower()
    top_brand = (top_payload.get("brand") or "").lower()
    top_cat = (top_payload.get("category") or "").lower()

    # 1. High dense similarity indicates clear semantic match
    if top_score >= 0.68:
        return True

    # 2. Agreement between top dense candidates and keyword search
    dense_top3_ids = {pid for pid, _, _ in dense_results[:3]}
    if dense_top3_ids.intersection(keyword_ids):
        return True

    # 3. Moderate similarity (>= 0.60) supported by lexical or synonym overlap in query
    if top_score >= 0.60:
        q_clean = query.lower()
        q_tokens = set(re.findall(r"\w+", q_clean))
        q_tokens = {t for t in q_tokens if len(t) > 2 and t not in {"and", "for", "the", "with", "under", "over", "from"}}
        target_tokens = set(re.findall(r"\w+", f"{top_name} {top_brand} {top_cat}"))
        if q_tokens.intersection(target_tokens):
            return True

        # Check common Arabic-to-English product synonyms
        for ar_term, en_term in AR_EN_PRODUCT_SYNONYMS.items():
            if ar_term in q_clean:
                en_syn_tokens = set(re.findall(r"\w+", en_term.lower()))
                if en_syn_tokens.intersection(target_tokens):
                    return True

    # Otherwise rejected as ungrounded / out-of-catalog
    return False


class HybridRetriever:
    def __init__(
        self,
        catalog_provider: CatalogProvider,
        embedding_provider: BaseEmbeddingProvider,
        qdrant_client: AsyncQdrantClient,
        collection_name: str | None = None,
        k_fusion: int = 60,
    ) -> None:
        self.catalog = catalog_provider
        self.embedder = embedding_provider
        self.qdrant = qdrant_client
        settings = get_settings()
        self.collection_name = collection_name or settings.qdrant_collection_products
        self.k_fusion = k_fusion

    async def retrieve_products(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        limit: int = 5,
        hybrid_enabled: bool = True,
    ) -> tuple[list[Product], list[dict[str, Any]]]:
        filters = filters or {}
        dense_results: list[tuple[str, float, dict[str, Any]]] = []
        keyword_ids: list[str] = []
        payload_map: dict[str, dict[str, Any]] = {}

        # 1. Concurrently run Dense Semantic Search and Keyword Search
        async def fetch_dense() -> list[tuple[str, float, dict[str, Any]]]:
            if not hybrid_enabled:
                return []
            try:
                query_vector = await self.embedder.embed_query(query)
                must_conditions = [
                    qmodels.FieldCondition(key="is_active", match=qmodels.MatchValue(value=True))
                ]
                if filters.get("category"):
                    must_conditions.append(
                        qmodels.FieldCondition(key="category", match=qmodels.MatchValue(value=filters["category"]))
                    )
                if filters.get("brand"):
                    must_conditions.append(
                        qmodels.FieldCondition(key="brand", match=qmodels.MatchValue(value=filters["brand"]))
                    )

                try:
                    hits = await self.qdrant.search(
                        collection_name=self.collection_name,
                        query_vector=query_vector,
                        limit=10,
                        query_filter=qmodels.Filter(must=must_conditions),
                        with_payload=["product_id", "name", "category", "brand"],
                    )
                except (AttributeError, TypeError):
                    res = await self.qdrant.query_points(
                        collection_name=self.collection_name,
                        query=query_vector,
                        limit=10,
                        query_filter=qmodels.Filter(must=must_conditions),
                        with_payload=["product_id", "name", "category", "brand"],
                    )
                    hits = res.points
                results = []
                for h in hits:
                    if h.payload and h.payload.get("product_id"):
                        pid = str(h.payload["product_id"])
                        score = float(h.score)
                        payload_map[pid] = h.payload
                        results.append((pid, score, h.payload))
                return results
            except Exception as exc:
                logger.warning("Dense retrieval failed, continuing with keyword: %s", exc)
                return []

        async def fetch_keyword() -> list[str]:
            try:
                kw_payload = {
                    "query": query[:200],
                    "filters": filters,
                    "sort": "relevance",
                    "limit": 10,
                    "offset": 0,
                }
                products, _ = await self.catalog.search(kw_payload)
                for p in products:
                    pid = str(p.product_id)
                    if pid not in payload_map:
                        payload_map[pid] = {"name": p.name, "category": p.category, "brand": p.brand}
                return [str(p.product_id) for p in products]
            except Exception as exc:
                logger.warning("Keyword retrieval failed: %s", exc)
                return []

        dense_task = asyncio.create_task(fetch_dense())
        keyword_task = asyncio.create_task(fetch_keyword())

        dense_results, keyword_ids = await asyncio.gather(dense_task, keyword_task)

        # 2. Check Retrieval Confidence
        is_confident = evaluate_retrieval_confidence(query, dense_results, keyword_ids)
        if not is_confident:
            logger.info("Query '%s' rejected by retrieval confidence evaluation (ungrounded / out-of-catalog)", query[:50])
            return [], []

        # 3. Category/Type Compatibility Filter
        filtered_dense_ids = [
            pid for pid, score, p_payload in dense_results
            if is_candidate_compatible(query, p_payload)
        ]
        filtered_keyword_ids = [
            pid for pid in keyword_ids
            if is_candidate_compatible(query, payload_map.get(pid, {}))
        ]

        # 4. Fuse candidates using Reciprocal Rank Fusion
        if filtered_dense_ids and filtered_keyword_ids:
            fused = reciprocal_rank_fusion([filtered_dense_ids, filtered_keyword_ids], k=self.k_fusion)
            candidate_ids = [item_id for item_id, _ in fused][:limit]
        elif filtered_dense_ids:
            candidate_ids = filtered_dense_ids[:limit]
        elif filtered_keyword_ids:
            candidate_ids = filtered_keyword_ids[:limit]
        else:
            candidate_ids = []

        if not candidate_ids:
            return [], []

        # 5. Live Commercial Hydration from PHP (Authoritative prices & stock)
        hydrated_products, missing_ids = await self.catalog.products(candidate_ids)

        # Preserve candidate order
        product_map = {str(p.product_id): p for p in hydrated_products}
        ordered_products: list[Product] = []
        for cid in candidate_ids:
            if cid in product_map:
                ordered_products.append(product_map[cid])

        # Build retrieval metadata for tracing
        retrieval_metadata = [
            {
                "product_id": cid,
                "in_dense": cid in filtered_dense_ids,
                "dense_rank": filtered_dense_ids.index(cid) + 1 if cid in filtered_dense_ids else None,
                "in_keyword": cid in filtered_keyword_ids,
                "keyword_rank": filtered_keyword_ids.index(cid) + 1 if cid in filtered_keyword_ids else None,
            }
            for cid in candidate_ids
            if cid in product_map
        ]

        return ordered_products, retrieval_metadata

