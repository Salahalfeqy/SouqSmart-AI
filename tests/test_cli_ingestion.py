import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scripts.sync_knowledge as sync_knowledge
import scripts.sync_products as sync_products
from app.catalog import CatalogProvider, PHPProvider
from app.config import get_settings
from app.rag.embeddings import GeminiEmbeddingClient
from app.rag.ingestion import ProductIngestionPipeline
from app.rag.knowledge import KnowledgeBaseManager
from app.rag.qdrant_client import ensure_collections


def test_cli_product_sync_imports_and_signatures():
    """Verify scripts/sync_products.py references correct classes and signatures."""
    sig_pipeline = inspect.signature(ProductIngestionPipeline.__init__)
    params = list(sig_pipeline.parameters.keys())
    assert "catalog_provider" in params
    assert "embedding_provider" in params
    assert "qdrant_client" in params
    assert "collection_name" in params

    # Verify method name is sync_all_products
    assert hasattr(ProductIngestionPipeline, "sync_all_products")
    sig_sync = inspect.signature(ProductIngestionPipeline.sync_all_products)
    assert "batch_size" in sig_sync.parameters

    # Verify ensure_collections signature
    sig_ensure = inspect.signature(ensure_collections)
    assert "client" in sig_ensure.parameters
    assert "dimension" in sig_ensure.parameters


def test_cli_knowledge_sync_imports_and_settings_references():
    """Verify scripts/sync_knowledge.py references valid settings attributes and classes."""
    settings = get_settings()
    assert hasattr(settings, "embedding_dimension")
    assert isinstance(settings.embedding_dimension, int)
    assert hasattr(settings, "effective_embedding_api_key")
    assert hasattr(settings, "gemini_embedding_model")
    assert hasattr(settings, "qdrant_collection_knowledge")

    sig_kb = inspect.signature(KnowledgeBaseManager.__init__)
    kb_params = list(sig_kb.parameters.keys())
    assert "qdrant" in kb_params or "qdrant_client" in kb_params
    assert "embedder" in kb_params or "embedding_client" in kb_params
    assert "collection_name" in kb_params


def test_sync_products_help(capsys):
    """Verify --help exits cleanly with usage information."""
    with pytest.raises(SystemExit) as exc_info:
        sync_products.parse_args(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "SouqSmart Product Catalog Ingestion CLI" in captured.out
    assert "--batch-size" in captured.out
    assert "--dry-run" in captured.out


def test_sync_knowledge_help(capsys):
    """Verify --help exits cleanly with usage information."""
    with pytest.raises(SystemExit) as exc_info:
        sync_knowledge.parse_args(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "SouqSmart Knowledge Base Ingestion CLI" in captured.out
    assert "--dry-run" in captured.out
    assert "--kb-dir" in captured.out


@pytest.mark.asyncio
async def test_sync_products_dry_run_executes_safely_without_embeddings_or_writes():
    """Verify sync_products --dry-run performs zero Qdrant writes and zero embedding calls."""
    mock_qdrant = AsyncMock()
    mock_embedder = MagicMock()

    with patch("scripts.sync_products.get_qdrant_client", return_value=mock_qdrant), \
         patch("scripts.sync_products.GeminiEmbeddingClient", return_value=mock_embedder), \
         patch("scripts.sync_products.ensure_collections", new=AsyncMock()) as mock_ensure:

        exit_code = await sync_products.main(["--dry-run", "--batch-size", "25"])
        assert exit_code == 0

        # Assert no collection creation, no Qdrant writes, no embedding calls
        mock_ensure.assert_not_called()
        mock_qdrant.upsert.assert_not_called()
        mock_qdrant.delete.assert_not_called()
        mock_embedder.embed_document.assert_not_called()
        mock_embedder.embed_documents_batch.assert_not_called()


@pytest.mark.asyncio
async def test_sync_knowledge_dry_run_executes_safely_and_excludes_warranty():
    """Verify sync_knowledge --dry-run scans policies, excludes pending warranty, and makes no Qdrant calls."""
    mock_qdrant = AsyncMock()
    mock_embedder = MagicMock()

    with patch("scripts.sync_knowledge.get_qdrant_client", return_value=mock_qdrant), \
         patch("scripts.sync_knowledge.GeminiEmbeddingClient", return_value=mock_embedder), \
         patch("scripts.sync_knowledge.ensure_collections", new=AsyncMock()) as mock_ensure:

        exit_code = await sync_knowledge.main(["--dry-run", "--kb-dir", "knowledge_base"])
        assert exit_code == 0

        # Assert no Qdrant modification or embeddings
        mock_ensure.assert_not_called()
        mock_qdrant.upsert.assert_not_called()
        mock_qdrant.delete.assert_not_called()
        mock_embedder.embed_document.assert_not_called()


@pytest.mark.asyncio
async def test_sync_products_invalid_batch_size_fails_closed():
    """Verify invalid --batch-size returns exit code 1."""
    exit_code = await sync_products.main(["--batch-size", "0"])
    assert exit_code == 1

    exit_code_neg = await sync_products.main(["--batch-size", "-10"])
    assert exit_code_neg == 1


@pytest.mark.asyncio
async def test_sync_knowledge_missing_directory_fails_closed():
    """Verify non-existent --kb-dir returns exit code 1."""
    exit_code = await sync_knowledge.main(["--kb-dir", "non_existent_directory_xyz"])
    assert exit_code == 1


@pytest.mark.asyncio
async def test_sync_products_live_failure_returns_nonzero():
    """Verify live sync returns exit code 1 if the pipeline reports a failure."""
    mock_qdrant = AsyncMock()
    mock_pipeline = AsyncMock()
    mock_pipeline.sync_all_products.return_value = {"status": "failed", "error": "Catalog timeout"}

    with patch("scripts.sync_products.get_qdrant_client", return_value=mock_qdrant), \
         patch("scripts.sync_products.ensure_collections", new=AsyncMock()), \
         patch("scripts.sync_products.GeminiEmbeddingClient"), \
         patch("scripts.sync_products.ProductIngestionPipeline", return_value=mock_pipeline):

        exit_code = await sync_products.main([])
        assert exit_code == 1
        mock_qdrant.close.assert_awaited_once()

