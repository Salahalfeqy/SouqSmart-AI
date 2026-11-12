import hashlib
import logging
import re
from pathlib import Path
from typing import Any
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.config import get_settings
from app.database import now_utc_iso
from app.rag.embeddings import BaseEmbeddingProvider
from app.rag.schemas import KnowledgePointPayload, make_chunk_point_id

logger = logging.getLogger("souqsmart.knowledge")


# Contamination and placeholder patterns that indicate untrusted template data
CONTAMINATION_PATTERNS = [
    (re.compile(r"lorem\s+ipsum", re.IGNORECASE), "Lorem Ipsum placeholder text"),
    (re.compile(r"(vivamus\s+suscipit|curabitur\s+aliquet|nulla\s+quis\s+lorem)", re.IGNORECASE), "Latin dummy text"),
    (re.compile(r"\$\s*\d+(\.\d{2})?"), "USD currency placeholder (store operates in EGP)"),
    (re.compile(r"\+1\s*\(?\d{3}\)?\s*[\d-]{7,8}"), "Template phone number (+1 international)"),
    (re.compile(r"bootstrapmade", re.IGNORECASE), "Template theme attribution (BootstrapMade)"),
    (re.compile(r"fashionstore", re.IGNORECASE), "Clothing template attribution (FashionStore)"),
    (re.compile(r"(unworn\s+unwashed|intimate\s+items)", re.IGNORECASE), "Clothing template return policy"),
]


def check_text_contamination(text: str) -> tuple[bool, str]:
    """Inspect text for placeholder, Latin dummy, or contradictory template content."""
    for pattern, reason in CONTAMINATION_PATTERNS:
        if pattern.search(text):
            return True, reason
    return False, ""


def parse_policy_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter metadata and extract body text.
    
    Expected format:
    ---
    source: store_management
    policy_type: shipping
    language: ar
    version: 1.0.0
    approval_status: approved
    ---
    Body text starts here...
    """
    metadata: dict[str, str] = {}
    body = content.strip()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1].strip()
            body = parts[2].strip()
            for line in fm_text.splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    metadata[key.strip()] = val.strip().strip("\"'")
    return metadata, body


def clean_html_text(html_text: str) -> str:
    """Strip HTML markup, script tags, styles, and normalize whitespace."""
    # Remove script and style elements
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
    # Remove PHP tags
    text = re.sub(r"<\?php.*?\?>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\?=.*?\?>", "", text, flags=re.DOTALL)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode entities & normalize whitespace
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def chunk_document(
    doc_id: str,
    title: str,
    content: str,
    source: str,
    language: str = "ar",
    chunk_size: int = 400,
    overlap: int = 50,
) -> list[KnowledgePointPayload]:
    """Split text into semantic chunks with metadata."""
    words = content.split()
    if not words:
        return []

    chunks: list[KnowledgePointPayload] = []
    idx = 0
    start = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_text = " ".join(words[start:end])
        chunk_id = f"{doc_id}#chunk_{idx}"
        content_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()

        chunks.append(
            KnowledgePointPayload(
                doc_id=doc_id,
                chunk_id=chunk_id,
                doc_title=title,
                source=source,
                section=f"Section {idx + 1}",
                chunk_index=idx,
                language=language,
                content_text=chunk_text,
                content_hash=content_hash,
                doc_version="1.0.0",
                updated_at=now_utc_iso(),
            )
        )
        idx += 1
        start += chunk_size - overlap if end < len(words) else chunk_size

    return chunks


class KnowledgeBaseManager:
    def __init__(
        self,
        embedder: BaseEmbeddingProvider | None = None,
        qdrant: AsyncQdrantClient | None = None,
        collection_name: str | None = None,
        embedding_client: BaseEmbeddingProvider | None = None,
        qdrant_client: AsyncQdrantClient | None = None,
    ) -> None:
        self.embedder = embedder or embedding_client
        self.qdrant = qdrant or qdrant_client
        if self.embedder is None or self.qdrant is None:
            raise ValueError("KnowledgeBaseManager requires both an embedding provider and a Qdrant client")
        settings = get_settings()
        self.collection_name = collection_name or settings.qdrant_collection_knowledge

    def load_approved_policy(self, doc_id: str) -> dict[str, Any] | None:
        """Load a checked-in approved policy as a retrieval fallback when Qdrant is unavailable."""
        policy_files = {
            "shipping": "shipping.md",
            "returns": "returns.md",
            "payment": "payment.md",
        }
        filename = policy_files.get(doc_id)
        if not filename:
            return None

        path = Path(__file__).resolve().parents[2] / "knowledge_base" / "policies" / filename
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read approved policy source %s: %s", path, exc)
            return None

        metadata, body = parse_policy_frontmatter(raw_text)
        if metadata.get("approval_status", "").strip().lower() != "approved":
            return None
        contaminated, reason = check_text_contamination(body)
        if contaminated:
            logger.warning("Ignoring contaminated policy source %s: %s", path, reason)
            return None

        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        return {
            "doc_id": doc_id,
            "chunk_id": f"{doc_id}#local-source",
            "doc_title": title_match.group(1).strip() if title_match else path.stem,
            "source": metadata.get("source", ""),
            "section": "Approved policy source",
            "content": body,
            "content_text": body,
            "score": 1.0,
        }

    async def delete_document_chunks(self, doc_id: str) -> int:
        """Remove all chunks associated with a specific document ID to prevent stale contradictory chunks."""
        try:
            await self.qdrant.delete(
                collection_name=self.collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id))]
                    )
                ),
            )
            logger.info("Purged existing chunks for document '%s'", doc_id)
            return 1
        except Exception as exc:
            logger.warning("Failed to delete chunks for document '%s': %s", doc_id, exc)
            return 0

    async def replace_document_chunks(
        self,
        doc_id: str,
        chunks: list[KnowledgePointPayload],
    ) -> int:
        """Embed new chunks first; only on total success delete old chunks and upsert new ones.
        
        Guarantees that embedding failure never deletes valid existing points.
        """
        if not chunks:
            return 0

        # Step 1: Pre-embed and validate all points (fails safely before any deletion)
        points: list[qmodels.PointStruct] = []
        for ch in chunks:
            is_bad, reason = check_text_contamination(ch.content_text)
            if is_bad:
                logger.warning("Skipping contaminated chunk %s: %s", ch.chunk_id, reason)
                continue
            point_id = make_chunk_point_id(ch.doc_id, ch.chunk_index)
            vector = await self.embedder.embed_document(ch.content_text, title=f"{ch.doc_title}: {ch.section}")
            points.append(
                qmodels.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=ch.model_dump(),
                )
            )

        if not points:
            return 0

        # Step 2: Delete previous chunks for this document to prevent obsolete chunks
        await self.delete_document_chunks(doc_id)

        # Step 3: Upsert the new points
        await self.qdrant.upsert(
            collection_name=self.collection_name,
            points=points,
        )
        logger.info("Replaced %d chunks for document '%s' in %s", len(points), doc_id, self.collection_name)
        return len(points)

    async def ingest_document_chunks(
        self,
        chunks: list[KnowledgePointPayload],
    ) -> int:
        """Upsert pre-chunked knowledge points into Qdrant."""
        if not chunks:
            return 0
        doc_ids = {ch.doc_id for ch in chunks}
        if len(doc_ids) == 1:
            return await self.replace_document_chunks(list(doc_ids)[0], chunks)

        points: list[qmodels.PointStruct] = []
        for ch in chunks:
            # Check for contamination before ingesting
            is_bad, reason = check_text_contamination(ch.content_text)
            if is_bad:
                logger.warning("Skipping contaminated chunk %s: %s", ch.chunk_id, reason)
                continue
            point_id = make_chunk_point_id(ch.doc_id, ch.chunk_index)
            vector = await self.embedder.embed_document(ch.content_text, title=f"{ch.doc_title}: {ch.section}")
            points.append(
                qmodels.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=ch.model_dump(),
                )
            )
        if not points:
            return 0
        await self.qdrant.upsert(
            collection_name=self.collection_name,
            points=points,
        )
        logger.info("Upserted %d chunks into %s", len(points), self.collection_name)
        return len(points)

    async def ingest_document(
        self,
        doc_id: str,
        title: str,
        raw_text_or_html: str,
        source: str,
        language: str = "ar",
    ) -> int:
        clean_text = clean_html_text(raw_text_or_html)
        is_bad, reason = check_text_contamination(clean_text)
        if is_bad:
            logger.warning("Rejected contaminated document '%s': %s", doc_id, reason)
            return 0

        chunks = chunk_document(doc_id, title, clean_text, source, language)
        if not chunks:
            return 0

        return await self.replace_document_chunks(doc_id, chunks)

    async def search(
        self,
        query: str,
        top_k: int = 3,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        query_vector = await self.embedder.embed_query(query)
        qfilter = None
        if language and language != "auto":
            qfilter = qmodels.Filter(
                must=[qmodels.FieldCondition(key="language", match=qmodels.MatchValue(value=language))]
            )

        try:
            try:
                hits = await self.qdrant.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    limit=top_k,
                    query_filter=qfilter,
                    with_payload=True,
                )
            except (AttributeError, TypeError):
                res = await self.qdrant.query_points(
                    collection_name=self.collection_name,
                    query=query_vector,
                    limit=top_k,
                    query_filter=qfilter,
                    with_payload=True,
                )
                hits = res.points
            results = []
            for h in hits:
                if h.payload:
                    text = h.payload.get("content_text", "")
                    results.append(
                        {
                            "doc_id": h.payload.get("doc_id"),
                            "chunk_id": h.payload.get("chunk_id"),
                            "doc_title": h.payload.get("doc_title"),
                            "source": h.payload.get("source"),
                            "section": h.payload.get("section"),
                            "content": text,
                            "content_text": text,
                            "score": h.score,
                        }
                    )
            return results
        except Exception as exc:
            logger.warning("Knowledge search in Qdrant failed: %s", exc)
            return []

    async def search_knowledge_chunks(
        self,
        query: str,
        limit: int = 3,
        min_score: float = 0.5,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search knowledge base and filter by minimum similarity score."""
        results = await self.search(query=query, top_k=limit, language=language)
        if min_score > 0:
            results = [r for r in results if r.get("score", 0.0) >= min_score]
        return results
