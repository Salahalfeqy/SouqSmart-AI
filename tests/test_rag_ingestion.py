import pytest
from unittest.mock import AsyncMock, MagicMock
from app.rag.ingestion import ProductIngestionPipeline, build_product_semantic_text, compute_content_hash
from app.rag.schemas import make_product_point_id
from app.schemas import Availability, Price, Product


def make_test_product(
    pid: str,
    name: str,
    price: str = "150.00",
    in_stock: bool = True,
    brand: str = "Sony",
    attrs: dict[str, str] | None = None,
) -> Product:
    return Product(
        product_id=str(pid),
        name=name,
        brand=brand,
        category="Audio",
        price=Price(amount=price, currency="EGP"),
        availability=Availability(status="in_stock" if in_stock else "out_of_stock"),
        url=f"/product/{pid}",
        attributes=attrs or {},
    )


def test_content_hash_consistency_and_detection():
    prod_a = make_test_product(
        "1",
        "Sony Headphones",
        price="150.00",
        in_stock=True,
        attrs={"color": "black", "description": "Noise cancelling headphones"},
    )
    text_a1 = build_product_semantic_text(prod_a)
    hash_a1 = compute_content_hash(text_a1)

    text_a2 = build_product_semantic_text(prod_a)
    hash_a2 = compute_content_hash(text_a2)
    assert hash_a1 == hash_a2

    # Changing volatile fields like price or stock must NOT change semantic text or content hash
    prod_a_price_change = make_test_product(
        "1",
        "Sony Headphones",
        price="999.99",
        in_stock=False,
        attrs={"color": "black", "description": "Noise cancelling headphones"},
    )
    text_price_change = build_product_semantic_text(prod_a_price_change)
    assert compute_content_hash(text_price_change) == hash_a1

    # Changing description or attributes MUST change the hash
    prod_b = make_test_product(
        "1",
        "Sony Headphones",
        price="150.00",
        in_stock=True,
        attrs={"color": "black", "description": "New description"},
    )
    text_b = build_product_semantic_text(prod_b)
    assert compute_content_hash(text_b) != hash_a1


def test_deterministic_uuid5_point_id():
    uuid_1 = make_product_point_id("42")
    uuid_2 = make_product_point_id("42")
    uuid_3 = make_product_point_id("43")
    assert uuid_1 == uuid_2
    assert uuid_1 != uuid_3


@pytest.mark.anyio
async def test_ingestion_pipeline_sync():
    mock_qdrant = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.model = "gemini-embedding-2"
    mock_embedding.dimension = 768
    mock_catalog = MagicMock()

    mock_qdrant.get_collection = AsyncMock()
    mock_qdrant.upsert = AsyncMock()
    mock_qdrant.scroll = AsyncMock(return_value=([], None))
    mock_qdrant.retrieve = AsyncMock(return_value=[])

    # Single product from search
    products_page_1 = [
        make_test_product("10", "Logitech Mouse", price="25.00", brand="Logitech")
    ]
    mock_catalog.search = AsyncMock(side_effect=[
        (products_page_1, 1),
        ([], 0),  # End of pagination
    ])
    mock_embedding.embed_document = AsyncMock(return_value=[0.1] * 768)

    pipeline = ProductIngestionPipeline(
        catalog_provider=mock_catalog,
        embedding_provider=mock_embedding,
        qdrant_client=mock_qdrant,
    )

    stats = await pipeline.sync_all_products(batch_size=10)
    assert stats["total_crawled"] == 1
    assert stats["upserted"] == 1
    assert stats["skipped"] == 0
    mock_qdrant.upsert.assert_called_once()


@pytest.mark.anyio
async def test_ingestion_midway_failure_protects_against_deletion():
    """Verify that a network failure on page 2 aborts sync and does NOT delete stale points."""
    mock_qdrant = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.embed_document = AsyncMock(return_value=[0.1] * 768)
    mock_catalog = MagicMock()

    mock_qdrant.upsert = AsyncMock()
    mock_qdrant.retrieve = AsyncMock(return_value=[])
    mock_qdrant.delete = AsyncMock()

    page_1 = [make_test_product("1", "Product 1")]
    # Page 1 succeeds, Page 2 raises connection error
    mock_catalog.search = AsyncMock(side_effect=[
        (page_1, 10),
        RuntimeError("Connection to PHP backend lost"),
    ])

    pipeline = ProductIngestionPipeline(
        catalog_provider=mock_catalog,
        embedding_provider=mock_embedding,
        qdrant_client=mock_qdrant,
    )

    stats = await pipeline.sync_all_products(batch_size=1)
    assert stats["status"] == "failed"
    assert "Connection to PHP backend lost" in stats["error"]
    assert stats["deleted"] == 0
    # Crucial assertion: delete was NEVER called
    mock_qdrant.delete.assert_not_called()


@pytest.mark.anyio
async def test_ingestion_empty_first_page_with_nonzero_total():
    """Verify that returning empty products on page 1 when total > 0 aborts without deletions."""
    mock_qdrant = MagicMock()
    mock_embedding = MagicMock()
    mock_catalog = MagicMock()
    mock_qdrant.delete = AsyncMock()

    mock_catalog.search = AsyncMock(return_value=([], 50))

    pipeline = ProductIngestionPipeline(
        catalog_provider=mock_catalog,
        embedding_provider=mock_embedding,
        qdrant_client=mock_qdrant,
    )

    stats = await pipeline.sync_all_products(batch_size=10)
    assert stats["status"] == "failed"
    assert "empty products list" in stats["error"]
    mock_qdrant.delete.assert_not_called()


@pytest.mark.anyio
async def test_ingestion_duplicate_ids_handled_safely():
    """Verify duplicate IDs in the pagination stream are deduplicated and logged."""
    mock_qdrant = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.embed_document = AsyncMock(return_value=[0.1] * 768)
    mock_catalog = MagicMock()
    mock_qdrant.upsert = AsyncMock()
    mock_qdrant.retrieve = AsyncMock(return_value=[])
    mock_qdrant.scroll = AsyncMock(return_value=([], None))
    mock_qdrant.delete = AsyncMock()

    # Two items with the identical product ID "99"
    dup_products = [
        make_test_product("99", "Duplicated Item A"),
        make_test_product("99", "Duplicated Item B"),
    ]
    mock_catalog.search = AsyncMock(return_value=(dup_products, 1))

    pipeline = ProductIngestionPipeline(
        catalog_provider=mock_catalog,
        embedding_provider=mock_embedding,
        qdrant_client=mock_qdrant,
    )

    stats = await pipeline.sync_all_products(batch_size=10)
    assert stats["status"] == "success"
    assert stats["duplicates_skipped"] == 1
    assert stats["total_crawled"] == 1


@pytest.mark.anyio
async def test_ingestion_deletion_threshold_safeguard():
    """Verify that if active products drop below 50% of Qdrant collection, deletion is aborted."""
    mock_qdrant = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.embed_document = AsyncMock(return_value=[0.1] * 768)
    mock_catalog = MagicMock()
    mock_qdrant.upsert = AsyncMock()
    mock_qdrant.retrieve = AsyncMock(return_value=[])
    mock_qdrant.delete = AsyncMock()

    # PHP only returns 1 product
    active_products = [make_test_product("100", "Only Item Left")]
    mock_catalog.search = AsyncMock(return_value=(active_products, 1))

    # But Qdrant has 10 points (100 is < 50% of 10)
    mock_points = [
        MagicMock(id=f"pt_{i}", payload={"product_id": str(i)})
        for i in range(10)
    ]
    mock_qdrant.scroll = AsyncMock(return_value=(mock_points, None))

    pipeline = ProductIngestionPipeline(
        catalog_provider=mock_catalog,
        embedding_provider=mock_embedding,
        qdrant_client=mock_qdrant,
    )

    stats = await pipeline.sync_all_products(batch_size=10)
    assert stats["status"] == "success"
    assert stats["deletion_aborted"] is True
    assert stats["deleted"] == 0
    mock_qdrant.delete.assert_not_called()


@pytest.mark.anyio
async def test_ingestion_successful_reconciliation_deletes_stale():
    """Verify that a complete, clean sync safely deletes stale points."""
    mock_qdrant = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.embed_document = AsyncMock(return_value=[0.1] * 768)
    mock_catalog = MagicMock()
    mock_qdrant.upsert = AsyncMock()
    mock_qdrant.retrieve = AsyncMock(return_value=[])
    mock_qdrant.delete = AsyncMock()

    # PHP has 2 active products: "1" and "2"
    active_products = [
        make_test_product("1", "Product 1"),
        make_test_product("2", "Product 2"),
    ]
    mock_catalog.search = AsyncMock(return_value=(active_products, 2))

    # Qdrant has 3 points: "1", "2", and stale "999" (2 active / 3 existing = 66% > 50%)
    point_1 = MagicMock(id="pt_1", payload={"product_id": "1"})
    point_2 = MagicMock(id="pt_2", payload={"product_id": "2"})
    point_stale = MagicMock(id="pt_stale", payload={"product_id": "999"})
    mock_qdrant.scroll = AsyncMock(return_value=([point_1, point_2, point_stale], None))

    pipeline = ProductIngestionPipeline(
        catalog_provider=mock_catalog,
        embedding_provider=mock_embedding,
        qdrant_client=mock_qdrant,
    )

    stats = await pipeline.sync_all_products(batch_size=10)
    assert stats["status"] == "success"
    assert stats["deleted"] == 1
    mock_qdrant.delete.assert_called_once()
