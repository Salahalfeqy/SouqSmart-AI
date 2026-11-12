"""
SouqSmart Qdrant Collection Initialization Script.
Creates 'products_v1' and 'knowledge_chunks_v1' collections with payload indexes.
"""
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.rag.qdrant_client import get_qdrant_client, ensure_collections, check_qdrant_health

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("init_qdrant")


async def main():
    settings = get_settings()
    logger.info("Connecting to Qdrant at %s:%s (HTTPS: %s)", settings.qdrant_host, settings.qdrant_port, settings.qdrant_https)
    client = get_qdrant_client(settings)
    
    is_healthy = await check_qdrant_health(client)
    if not is_healthy:
        logger.error("Could not reach Qdrant server. Make sure Qdrant is running.")
        return

    logger.info("Initializing collections...")
    await ensure_collections(
        client,
        products_collection=settings.qdrant_collection_products,
        knowledge_collection=settings.qdrant_collection_knowledge,
        vector_size=settings.gemini_embedding_dim,
    )
    logger.info("Collections verified and initialized successfully.")
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
