import pytest
from unittest.mock import AsyncMock, MagicMock
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.retriever import HybridRetriever
from app.schemas import Availability, Price, Product


def make_test_product(
    pid: str,
    name: str,
    price: str = "100.00",
    in_stock: bool = True,
    brand: str = "Brand",
    attrs: dict[str, str] | None = None,
) -> Product:
    return Product(
        product_id=str(pid),
        name=name,
        brand=brand,
        category="Electronics",
        price=Price(amount=price, currency="EGP"),
        availability=Availability(status="in_stock" if in_stock else "out_of_stock"),
        url=f"/product/{pid}",
        attributes=attrs or {},
    )


def test_reciprocal_rank_fusion_math():
    dense = ["10", "20", "30"]
    sparse = ["20", "40", "10"]
    
    # RRF formula: 1 / (60 + rank) with rank 1-indexed
    # item 20: rank 2 in dense (1/62), rank 1 in sparse (1/61) -> ~0.01613 + 0.01639 = 0.03252
    # item 10: rank 1 in dense (1/61), rank 3 in sparse (1/63) -> ~0.01639 + 0.01587 = 0.03226
    # 20 should rank first, then 10
    fused = reciprocal_rank_fusion([dense, sparse], k=60)
    top_ids = [item_id for item_id, score in fused]
    assert top_ids[0] == "20"
    assert top_ids[1] == "10"
    assert set(top_ids) == {"10", "20", "30", "40"}


@pytest.mark.anyio
async def test_hybrid_retriever_dense_and_keyword_combination():
    mock_qdrant = MagicMock()
    mock_embedding = MagicMock()
    mock_catalog = MagicMock()

    # Mock embedding
    mock_embedding.embed_query = AsyncMock(return_value=[0.1] * 768)

    # Mock Qdrant points
    p1 = MagicMock()
    p1.payload = {"product_id": "101"}
    p2 = MagicMock()
    p2.payload = {"product_id": "102"}
    mock_qdrant.search = AsyncMock(return_value=[p1, p2])

    prod_101 = make_test_product("101", "Product 101", price="100.00")
    prod_102 = make_test_product("102", "Product 102", price="200.00")
    prod_103 = make_test_product("103", "Product 103", price="300.00")

    # Mock PHP catalog search returning (products, total)
    mock_catalog.search = AsyncMock(return_value=([prod_102, prod_103], 2))

    # Mock live product hydration
    mock_catalog.products = AsyncMock(return_value=([prod_102, prod_101], []))

    retriever = HybridRetriever(
        catalog_provider=mock_catalog,
        embedding_provider=mock_embedding,
        qdrant_client=mock_qdrant,
    )

    results, citations = await retriever.retrieve_products("لابتوب", limit=2, hybrid_enabled=True)
    assert len(results) == 2
    # Product 102 appeared in both dense and keyword, so it should rank first
    assert results[0].product_id == "102"
    assert results[1].product_id == "101"


@pytest.mark.anyio
async def test_hybrid_retriever_qdrant_failure_fallback():
    mock_qdrant = MagicMock()
    mock_embedding = MagicMock()
    mock_catalog = MagicMock()

    prod_55 = make_test_product("55", "Fallback Item", price="50.00")

    mock_embedding.embed_query = AsyncMock(side_effect=Exception("Embedding failed"))
    mock_catalog.search = AsyncMock(return_value=([prod_55], 1))
    mock_catalog.products = AsyncMock(return_value=([prod_55], []))

    retriever = HybridRetriever(
        catalog_provider=mock_catalog,
        embedding_provider=mock_embedding,
        qdrant_client=mock_qdrant,
    )

    # Should gracefully fall back to keyword-only results without throwing
    results, citations = await retriever.retrieve_products("سماعة", limit=1, hybrid_enabled=True)
    assert len(results) == 1
    assert results[0].product_id == "55"
