import logging
from typing import Any
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.config import get_settings

logger = logging.getLogger("souqsmart.qdrant")


def get_qdrant_client(settings: Any = None) -> AsyncQdrantClient:
    s = settings or get_settings()
    if s.qdrant_url:
        return AsyncQdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key)
    return AsyncQdrantClient(host=s.qdrant_host, port=s.qdrant_port, api_key=s.qdrant_api_key)


async def check_qdrant_health(client: AsyncQdrantClient | None = None) -> bool:
    c = client or get_qdrant_client()
    try:
        collections = await c.get_collections()
        return collections is not None
    except Exception as exc:
        logger.debug("Qdrant health check failed: %s", exc)
        return False


async def ensure_collections(client: AsyncQdrantClient | None = None, dimension: int | None = None) -> None:
    s = get_settings()
    c = client or get_qdrant_client(s)
    dim = dimension or s.embedding_dimension

    # 1. products collection
    prod_col = s.qdrant_collection_products
    try:
        exists = await c.collection_exists(prod_col)
        if not exists:
            logger.info("Creating Qdrant collection '%s' with dimension %s", prod_col, dim)
            await c.create_collection(
                collection_name=prod_col,
                vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
            )
            # Create payload indexes
            await c.create_payload_index(prod_col, "product_id", qmodels.PayloadSchemaType.KEYWORD)
            await c.create_payload_index(prod_col, "category", qmodels.PayloadSchemaType.KEYWORD)
            await c.create_payload_index(prod_col, "brand", qmodels.PayloadSchemaType.KEYWORD)
            await c.create_payload_index(prod_col, "is_active", qmodels.PayloadSchemaType.BOOL)
    except Exception as exc:
        logger.warning("Failed to initialize collection '%s': %s", prod_col, exc)

    # 2. knowledge_chunks collection
    know_col = s.qdrant_collection_knowledge
    try:
        exists = await c.collection_exists(know_col)
        if not exists:
            logger.info("Creating Qdrant collection '%s' with dimension %s", know_col, dim)
            await c.create_collection(
                collection_name=know_col,
                vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
            )
            # Create payload indexes
            await c.create_payload_index(know_col, "doc_id", qmodels.PayloadSchemaType.KEYWORD)
            await c.create_payload_index(know_col, "language", qmodels.PayloadSchemaType.KEYWORD)
            await c.create_payload_index(know_col, "source", qmodels.PayloadSchemaType.KEYWORD)
    except Exception as exc:
        logger.warning("Failed to initialize collection '%s': %s", know_col, exc)

