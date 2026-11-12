import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.rag.knowledge import (
    clean_html_text,
    chunk_document,
    parse_policy_frontmatter,
    KnowledgeBaseManager,
)
from app.rag.context_builder import build_rag_prompt
from app.intents import ParsedIntent


def test_clean_html_text():
    raw_php_html = """
    <?php // Header view ?>
    <div class="policy-container">
        <h2>سياسة الاسترجاع</h2>
        <p>يحق للعميل استرجاع المنتج خلال <strong>30 يوماً</strong> من تاريخ الاستلام.</p>
        <script>console.log("analytics");</script>
    </div>
    """
    cleaned = clean_html_text(raw_php_html)
    assert "سياسة الاسترجاع" in cleaned
    assert "30 يوماً" in cleaned
    assert "<div" not in cleaned
    assert "<script" not in cleaned
    assert "console.log" not in cleaned
    assert "<?php" not in cleaned


def test_chunk_document():
    text = "فقرة أولى. " * 30 + "فقرة ثانية. " * 30
    chunks = chunk_document(
        doc_id="refund_policy",
        title="سياسة الاسترجاع",
        content=text,
        source="knowledge_base/policies/returns.md",
        language="ar",
        chunk_size=10,
        overlap=2,
    )
    assert len(chunks) > 1
    assert chunks[0].doc_id == "refund_policy"
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1
    assert len(chunks[0].content_text) > 0


@pytest.mark.anyio
async def test_knowledge_base_search():
    mock_qdrant = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.embed_query = AsyncMock(return_value=[0.2] * 768)

    mock_point = MagicMock()
    mock_point.payload = {
        "doc_id": "shipping_policy",
        "doc_title": "سياسة الشحن والتوصيل",
        "content_text": "الشحن العادي قيمته 10 ويستغرق من 3 إلى 7 أيام عمل.",
    }
    mock_point.score = 0.92
    mock_qdrant.search = AsyncMock(return_value=[mock_point])

    kb = KnowledgeBaseManager(embedder=mock_embedding, qdrant=mock_qdrant)
    results = await kb.search_knowledge_chunks("كام مدة وتكلفة الشحن؟", limit=1, min_score=0.5)

    assert len(results) == 1
    assert results[0]["doc_id"] == "shipping_policy"
    assert "3 إلى 7 أيام" in results[0]["content"]
    assert results[0]["score"] == 0.92


def test_cod_only_payment_policy():
    """Verify payment.md strictly enforces COD-only and rejects online cards/PayPal."""
    path = Path("knowledge_base/policies/payment.md")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    meta, body = parse_policy_frontmatter(content)

    assert meta.get("approval_status") == "approved"
    assert "الدفع عند الاستلام فقط" in body or "COD Only" in body
    # Verify it does not support credit cards, paypal or digital wallets at checkout
    assert "لا يدعم" in body
    assert "الاستلام" in body


def test_shipping_currency_uncertainty():
    """Verify shipping.md states database rate tiers (0, 10, 25) with currency uncertainty."""
    path = Path("knowledge_base/policies/shipping.md")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    meta, body = parse_policy_frontmatter(content)

    assert meta.get("approval_status") == "approved"
    # Rates and estimates from PHP database
    assert "0" in body
    assert "10" in body
    assert "25" in body
    assert "5 إلى 10" in body or "5-10" in body
    assert "3 إلى 7" in body or "3-7" in body
    assert "1 إلى 3" in body or "1-3" in body
    # Currency disclaimer
    assert "العملة" in body
    assert "غير مؤكدة" in body or "Checkout" in body


def test_manual_returns_handling():
    """Verify returns.md specifies 30-day window handled manually via support without 14-day claim."""
    path = Path("knowledge_base/policies/returns.md")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    meta, body = parse_policy_frontmatter(content)

    assert meta.get("approval_status") == "approved"
    assert "30 يوماً" in body
    assert "يدوياً" in body
    assert "خدمة عملاء" in body or "الدعم" in body
    # Must NOT present the 14-day claim as NiceShop policy
    assert "14 يوماً" not in body


def test_missing_warranty_policy_remains_pending():
    """Verify warranty.md remains pending approval and clarifies 30-day return is not a warranty."""
    path = Path("knowledge_base/policies/warranty.md")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    meta, body = parse_policy_frontmatter(content)

    # Must remain pending_approval
    assert meta.get("approval_status") == "pending_approval"
    assert "لا توجد سياسة ضمان رسمية معتمدة" in body
    assert "نافذة الاسترجاع المحددة بـ 30 يوماً هي سياسة استرجاع فقط ولا تعتبر ضماناً" in body


def test_pending_policy_excluded_from_ingestion_selection():
    """Verify that sync logic filters out files marked pending_approval."""
    kb_path = Path("knowledge_base/policies")
    for doc in kb_path.glob("*.md"):
        content = doc.read_text(encoding="utf-8")
        meta, _ = parse_policy_frontmatter(content)
        status = meta.get("approval_status", "").lower()
        if doc.name == "warranty.md":
            assert status == "pending_approval"
        elif doc.name in ("shipping.md", "returns.md", "payment.md"):
            assert status == "approved"


@pytest.mark.anyio
async def test_replace_document_chunks_lifecycle():
    """Verify that replace_document_chunks purges old chunks before upserting new ones."""
    mock_qdrant = MagicMock()
    mock_qdrant.delete = AsyncMock()
    mock_qdrant.upsert = AsyncMock()

    mock_embedder = MagicMock()
    mock_embedder.embed_document = AsyncMock(return_value=[0.1] * 768)

    kb = KnowledgeBaseManager(embedder=mock_embedder, qdrant=mock_qdrant)

    chunks = chunk_document(
        doc_id="payment_policy",
        title="طرق الدفع",
        content="الدفع عند الاستلام فقط هو الخيار المتاح حالياً.",
        source="knowledge_base/policies/payment.md",
        language="ar",
    )

    count = await kb.replace_document_chunks("payment_policy", chunks)
    assert count == len(chunks)
    # 1. delete called to purge old obsolete chunks
    mock_qdrant.delete.assert_called_once()
    # 2. upsert called with new points
    mock_qdrant.upsert.assert_called_once()


@pytest.mark.anyio
async def test_failed_sync_never_deletes_valid_points():
    """Verify that an embedding failure aborts sync without deleting any existing points."""
    mock_qdrant = MagicMock()
    mock_qdrant.delete = AsyncMock()
    mock_qdrant.upsert = AsyncMock()

    mock_embedder = MagicMock()
    # Embedder fails with network error
    mock_embedder.embed_document = AsyncMock(side_effect=RuntimeError("Google Gemini API unreachable"))

    kb = KnowledgeBaseManager(embedder=mock_embedder, qdrant=mock_qdrant)

    chunks = chunk_document(
        doc_id="shipping_policy",
        title="الشحن",
        content="نص الشحن",
        source="knowledge_base/policies/shipping.md",
        language="ar",
    )

    with pytest.raises(RuntimeError, match="Google Gemini API unreachable"):
        await kb.replace_document_chunks("shipping_policy", chunks)

    # Crucial assertion: delete was NEVER called
    mock_qdrant.delete.assert_not_called()
    mock_qdrant.upsert.assert_not_called()


def test_missing_warranty_grounding_in_context_builder():
    """Verify build_rag_prompt explicitly instructs model not to invent warranties when absent."""
    intent = ParsedIntent(intent_type="search", clean_query="هل في ضمان للاب توب؟")
    prompt = build_rag_prompt(
        user_message="هل في ضمان للاب توب؟",
        rewritten_query="ضمان لاب توب",
        intent=intent,
        products=[],
        knowledge_chunks=[],  # No warranty policy chunks present
    )

    assert "NEVER invent product IDs, prices, stock availability, warranties" in prompt
    assert "If policy details (such as warranty" in prompt
    assert "not currently available in the verified store records" in prompt
