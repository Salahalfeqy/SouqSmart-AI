import hashlib
import json
import logging
from typing import Any
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.catalog import CatalogProvider
from app.config import get_settings
from app.database import now_utc_iso
from app.rag.embeddings import BaseEmbeddingProvider
from app.rag.schemas import ProductPointPayload, make_product_point_id

logger = logging.getLogger("souqsmart.ingestion")


def build_product_semantic_text(product: Any) -> str:
    name = getattr(product, "name", "")
    brand = getattr(product, "brand", "") or ""
    category = getattr(product, "category", "") or ""
    attrs = getattr(product, "attributes", {}) or {}
    desc = attrs.get("description", "") or attrs.get("short_description", "")

    attr_lines = [f"{k}: {v}" for k, v in sorted(attrs.items()) if k not in ("description", "short_description")]
    attr_str = "\n".join(attr_lines)

    parts = [
        f"Product Name: {name}",
        f"Brand: {brand}" if brand else "",
        f"Category: {category}" if category else "",
        f"Description: {desc}" if desc else "",
        f"Specifications:\n{attr_str}" if attr_str else "",
    ]
    return "\n".join(p for p in parts if p.strip())


def compute_content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


class ProductIngestionPipeline:
    def __init__(
        self,
        catalog_provider: CatalogProvider,
        embedding_provider: BaseEmbeddingProvider,
        qdrant_client: AsyncQdrantClient,
        collection_name: str | None = None,
    ) -> None:
        self.catalog = catalog_provider
        self.embedder = embedding_provider
        self.qdrant = qdrant_client
        settings = get_settings()
        self.collection_name = collection_name or settings.qdrant_collection_products

    async def sync_all_products(self, batch_size: int = 50) -> dict[str, Any]:
        """Full crawl & reconciliation of active products from PHP into Qdrant."""
        logger.info("Starting catalog ingestion into collection '%s'...", self.collection_name)
        offset = 0
        total_crawled = 0
        upserted_count = 0
        skipped_count = 0
        all_active_ids: set[str] = set()
        seen_ids: set[str] = set()
        duplicate_count = 0
        expected_total: int | None = None
        crawl_clean = False
        deletion_aborted = False

        try:
            while True:
                payload = {"query": "", "limit": batch_size, "offset": offset}
                try:
                    products, total = await self.catalog.search(payload)
                except Exception as crawl_err:
                    logger.error("Catalog fetch failed at offset %d: %s. Aborting sync.", offset, crawl_err)
                    raise crawl_err

                if expected_total is None:
                    expected_total = total
                    if total > 0 and not products:
                        raise RuntimeError(f"Catalog returned empty products list on page 1 but total={total}")
                elif total != expected_total:
                    logger.warning("Catalog total changed during sync: initial %d -> current %d", expected_total, total)
                    expected_total = total

                if not products:
                    break

                for prod in products:
                    pid = str(prod.product_id)
                    if pid in seen_ids:
                        duplicate_count += 1
                        logger.warning("Duplicate product ID %s encountered at offset %d", pid, offset)
                        continue
                    seen_ids.add(pid)
                    all_active_ids.add(pid)
                    total_crawled += 1

                    semantic_text = build_product_semantic_text(prod)
                    content_hash = compute_content_hash(semantic_text)
                    point_id = make_product_point_id(pid)

                    # Check if exists in Qdrant with identical hash
                    needs_update = True
                    try:
                        existing = await self.qdrant.retrieve(
                            collection_name=self.collection_name,
                            ids=[point_id],
                            with_payload=True,
                        )
                        if existing and existing[0].payload:
                            stored_hash = existing[0].payload.get("content_hash")
                            if stored_hash == content_hash:
                                needs_update = False
                                skipped_count += 1
                    except Exception as exc:
                        logger.debug("Could not verify existing point %s: %s", point_id, exc)

                    if needs_update:
                        vector = await self.embedder.embed_document(semantic_text, title=prod.name)
                        raw_model = getattr(self.embedder, "model", "gemini-embedding-2")
                        model_name = str(raw_model) if isinstance(raw_model, str) else "gemini-embedding-2"
                        raw_dim = getattr(self.embedder, "dimension", len(vector))
                        dim_val = int(raw_dim) if isinstance(raw_dim, int) else len(vector)

                        point_payload = ProductPointPayload(
                            product_id=pid,
                            name=prod.name,
                            brand=prod.brand,
                            category=prod.category,
                            description=prod.attributes.get("description", "") or prod.attributes.get("short_description", ""),
                            attributes=prod.attributes,
                            content_hash=content_hash,
                            embedding_model=model_name,
                            embedding_dimension=dim_val,
                            sync_version=1,
                            last_synced_at=now_utc_iso(),
                            is_active=True,
                        )
                        await self.qdrant.upsert(
                            collection_name=self.collection_name,
                            points=[
                                qmodels.PointStruct(
                                    id=point_id,
                                    vector=vector,
                                    payload=point_payload.model_dump(),
                                )
                            ],
                        )
                        upserted_count += 1

                offset += len(products)
                if offset >= expected_total or len(products) < batch_size:
                    break

            # Validate that full expected catalog was crawled
            if expected_total is not None and expected_total > 0 and len(all_active_ids) < expected_total:
                raise RuntimeError(
                    f"Incomplete crawl: fetched {len(all_active_ids)} unique products out of expected {expected_total}."
                )

            crawl_clean = True

            # Deletion reconciliation: remove points that are no longer active in PHP
            deleted_count = 0
            if crawl_clean and all_active_ids:
                try:
                    # Scroll through all existing points in Qdrant
                    scroll_offset = None
                    all_qdrant_point_ids = []
                    stale_ids_to_delete = []
                    while True:
                        points, next_offset = await self.qdrant.scroll(
                            collection_name=self.collection_name,
                            offset=scroll_offset,
                            limit=100,
                            with_payload=["product_id"],
                        )
                        for pt in points:
                            all_qdrant_point_ids.append(pt.id)
                            if pt.payload and str(pt.payload.get("product_id")) not in all_active_ids:
                                stale_ids_to_delete.append(pt.id)
                        if next_offset is None:
                            break
                        scroll_offset = next_offset

                    existing_qdrant_count = len(all_qdrant_point_ids)
                    # Threshold safeguard: if active IDs dropped below 50% of Qdrant points, abort deletion
                    if existing_qdrant_count > 0 and len(all_active_ids) < 0.5 * existing_qdrant_count:
                        logger.error(
                            "Deletion safeguard triggered: active items (%d) < 50%% of Qdrant points (%d). Aborting deletion.",
                            len(all_active_ids),
                            existing_qdrant_count,
                        )
                        deletion_aborted = True
                    elif stale_ids_to_delete:
                        await self.qdrant.delete(
                            collection_name=self.collection_name,
                            points_selector=qmodels.PointIdsList(points=stale_ids_to_delete),
                        )
                        deleted_count = len(stale_ids_to_delete)
                except Exception as del_exc:
                    logger.warning("Stale points deletion check failed: %s", del_exc)

            report = {
                "status": "success",
                "total_crawled": total_crawled,
                "upserted": upserted_count,
                "skipped": skipped_count,
                "deleted": deleted_count,
                "duplicates_skipped": duplicate_count,
                "deletion_aborted": deletion_aborted,
            }
            logger.info("Catalog sync completed: %s", report)
            return report

        except Exception as err:
            logger.error("Catalog sync failed midway: %s. Stale items preserved.", err)
            return {
                "status": "failed",
                "error": str(err),
                "total_crawled": total_crawled,
                "upserted": upserted_count,
                "skipped": skipped_count,
                "deleted": 0,
            }

