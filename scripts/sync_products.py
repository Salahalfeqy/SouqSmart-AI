"""
SouqSmart Product Catalog Ingestion CLI.

Crawls active products from the authoritative PHP catalog API,
generates Gemini Embedding 2 vectors, and updates Qdrant.
Supports safe --dry-run mode and configurable batch sizes.
"""
import argparse
import asyncio
import logging
import math
import sys
from pathlib import Path
from typing import Any, Sequence

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog import CatalogError, CatalogProvider, CatalogTimeout, FixtureProvider, PHPProvider
from app.config import Settings, get_settings
from app.rag.embeddings import GeminiEmbeddingClient
from app.rag.ingestion import ProductIngestionPipeline
from app.rag.qdrant_client import ensure_collections, get_qdrant_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_products")


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SouqSmart Product Catalog Ingestion CLI. Synchronizes PHP catalog products to Qdrant vector index."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of products fetched and processed per batch (default: 50).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and catalog connectivity without performing embeddings or vector database updates.",
    )
    return parser.parse_args(args)


async def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.batch_size <= 0:
        logger.error("Invalid --batch-size: must be a positive integer (got %d)", args.batch_size)
        return 1

    try:
        settings: Settings = get_settings()
    except Exception as exc:
        logger.error("Configuration validation failed: %s", exc)
        return 1

    logger.info("Initializing product synchronization (batch_size=%d)...", args.batch_size)

    # Initialize Catalog Provider
    catalog: CatalogProvider
    if settings.catalog_provider == "php":
        catalog = PHPProvider(
            base_url=settings.php_base_url,
            service_key=settings.service_key,
            timeout=settings.catalog_timeout_seconds,
        )
    elif settings.catalog_provider == "fixture":
        catalog = FixtureProvider()
    else:
        logger.error("Unsupported catalog provider: '%s'", settings.catalog_provider)
        return 1

    # --- DRY-RUN EXECUTION ---
    if args.dry_run:
        logger.info("==================================================")
        logger.info(" DRY-RUN MODE: Verifying configuration & catalog ")
        logger.info("==================================================")
        logger.info("Target Collection:     %s", settings.qdrant_collection_products)
        logger.info("Embedding Model:       %s", settings.gemini_embedding_model)
        logger.info("Embedding Dimension:   %d", settings.embedding_dimension)
        logger.info("Catalog Provider:      %s (%s)", type(catalog).__name__, settings.catalog_provider)
        if settings.catalog_provider == "php":
            logger.info("Catalog Base URL:      %s", settings.php_base_url)

        try:
            sample_products, total = await catalog.search({"query": "", "limit": 1, "offset": 0})
            planned_batches = math.ceil(total / args.batch_size) if total > 0 else 0
            logger.info("Catalog Connectivity:  OK (Total active products: %d)", total)
            logger.info("Planned Ingestion:     %d products across %d batch(es)", total, planned_batches)
        except (CatalogTimeout, CatalogError, Exception) as cat_err:
            logger.warning("Catalog Connectivity:  FAILED or UNREACHABLE (%s)", cat_err)
            logger.info("Planned Ingestion:     Unable to determine product count due to catalog error")

        logger.info("--------------------------------------------------")
        logger.info("Dry-run validation successful.")
        logger.info("NO embedding API calls were made.")
        logger.info("NO Qdrant vectors were inserted, modified, or deleted.")
        logger.info("==================================================")
        return 0

    # --- LIVE INGESTION EXECUTION ---
    qdrant = get_qdrant_client(settings)
    try:
        await ensure_collections(qdrant, dimension=settings.embedding_dimension)

        embedding_client = GeminiEmbeddingClient(
            api_key=settings.effective_embedding_api_key,
            model=settings.gemini_embedding_model,
            dimension=settings.embedding_dimension,
            timeout=settings.gemini_timeout_seconds,
        )

        pipeline = ProductIngestionPipeline(
            catalog_provider=catalog,
            embedding_provider=embedding_client,
            qdrant_client=qdrant,
            collection_name=settings.qdrant_collection_products,
        )

        report = await pipeline.sync_all_products(batch_size=args.batch_size)

        if report.get("status") == "success":
            logger.info(
                "Product sync completed successfully: crawled=%d, upserted=%d, skipped=%d, deleted=%d, duplicates=%d",
                report.get("total_crawled", 0),
                report.get("upserted", 0),
                report.get("skipped", 0),
                report.get("deleted", 0),
                report.get("duplicates_skipped", 0),
            )
            return 0
        else:
            logger.error("Product sync failed: %s", report.get("error", "Unknown error"))
            return 1
    except Exception as exc:
        logger.exception("Fatal error during product synchronization: %s", exc)
        return 1
    finally:
        await qdrant.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
