"""
SouqSmart Knowledge Base Ingestion CLI.

Strictly ingests verified, human-approved Markdown policy and FAQ documents.
Rejects unapproved drafts and contaminated template content.
Supports safe --dry-run mode.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Sequence

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings, get_settings
from app.rag.embeddings import GeminiEmbeddingClient
from app.rag.knowledge import (
    KnowledgeBaseManager,
    check_text_contamination,
    chunk_document,
    parse_policy_frontmatter,
)
from app.rag.qdrant_client import ensure_collections, get_qdrant_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_knowledge")


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SouqSmart Knowledge Base Ingestion CLI. Ingests approved Markdown policy documents into Qdrant."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate Markdown documents, frontmatter, and contamination filters without performing embeddings or vector database updates.",
    )
    parser.add_argument(
        "--kb-dir",
        type=str,
        default="knowledge_base",
        help="Path to the directory containing Markdown policy and FAQ documents (default: 'knowledge_base').",
    )
    return parser.parse_args(args)


async def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        settings: Settings = get_settings()
    except Exception as exc:
        logger.error("Configuration validation failed: %s", exc)
        return 1

    kb_path = Path(args.kb_dir)
    if not kb_path.exists():
        logger.error("Knowledge base directory '%s' does not exist.", args.kb_dir)
        return 1

    markdown_files = sorted(list(kb_path.glob("**/*.md")))
    if not markdown_files:
        logger.warning("No markdown files found in '%s'.", args.kb_dir)
        return 0

    logger.info("Found %d markdown files in '%s'...", len(markdown_files), kb_path)

    # Inspect, parse, and validate all markdown files
    approved_docs: list[dict] = []
    pending_docs: list[dict] = []
    contaminated_docs: list[dict] = []

    for doc_file in markdown_files:
        raw_text = doc_file.read_text(encoding="utf-8", errors="ignore")
        metadata, body = parse_policy_frontmatter(raw_text)

        doc_id = doc_file.stem
        doc_title = metadata.get("policy_type", doc_id).replace("_", " ").title()
        approval_status = metadata.get("approval_status", "pending_approval").strip().lower()

        # Rule 1: Approval status gate
        if approval_status != "approved":
            pending_docs.append({
                "file": doc_file.name,
                "doc_id": doc_id,
                "title": doc_title,
                "status": approval_status,
            })
            continue

        # Rule 2: Contamination pattern filter
        is_bad, reason = check_text_contamination(body)
        if is_bad:
            contaminated_docs.append({
                "file": doc_file.name,
                "doc_id": doc_id,
                "title": doc_title,
                "reason": reason,
            })
            continue

        # Rule 3: Chunking
        lang = metadata.get("language", "ar")
        chunks = chunk_document(
            doc_id=doc_id,
            title=doc_title,
            content=body,
            source=doc_file.as_posix(),
            language=lang,
        )
        approved_docs.append({
            "file": doc_file.name,
            "doc_id": doc_id,
            "title": doc_title,
            "chunks": chunks,
            "chunk_count": len(chunks),
        })

    # --- DRY-RUN EXECUTION ---
    if args.dry_run:
        logger.info("==================================================")
        logger.info(" DRY-RUN MODE: Verifying Knowledge Base Documents ")
        logger.info("==================================================")
        logger.info("Target Collection:     %s", settings.qdrant_collection_knowledge)
        logger.info("Embedding Model:       %s", settings.gemini_embedding_model)
        logger.info("Embedding Dimension:   %d", settings.embedding_dimension)
        logger.info("Knowledge Directory:   %s", kb_path.resolve())
        logger.info("Total Files Scanned:   %d", len(markdown_files))
        logger.info("--------------------------------------------------")
        logger.info("DISCOVERED DOCUMENTS:")

        total_chunks = sum(d["chunk_count"] for d in approved_docs)
        for doc in approved_docs:
            logger.info("  [APPROVED]           %s ('%s') -> %d chunks planned", doc["file"], doc["title"], doc["chunk_count"])
        for doc in pending_docs:
            logger.info("  [EXCLUDED - PENDING] %s ('%s') [status='%s']", doc["file"], doc["title"], doc["status"])
        for doc in contaminated_docs:
            logger.info("  [REJECTED - CONTAM]  %s ('%s') [reason='%s']", doc["file"], doc["title"], doc["reason"])

        logger.info("--------------------------------------------------")
        logger.info(
            "Dry-run summary: Approved=%d (Total Chunks=%d), Pending/Excluded=%d, Contaminated=%d",
            len(approved_docs),
            total_chunks,
            len(pending_docs),
            len(contaminated_docs),
        )
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

        kb_manager = KnowledgeBaseManager(
            qdrant=qdrant,
            embedder=embedding_client,
            collection_name=settings.qdrant_collection_knowledge,
        )

        # 1. Clean up obsolete chunks for unapproved/pending documents
        for pending in pending_docs:
            logger.info("Purging obsolete chunks for unapproved doc '%s'...", pending["doc_id"])
            await kb_manager.delete_document_chunks(pending["doc_id"])

        # 2. Ingest approved, uncontaminated documents
        total_ingested_chunks = 0
        for doc in approved_docs:
            count = await kb_manager.replace_document_chunks(doc["doc_id"], doc["chunks"])
            total_ingested_chunks += count
            logger.info("Ingested %d chunks from approved doc '%s'", count, doc["file"])

        logger.info(
            "Knowledge sync complete: %d approved docs ingested (%d chunks), %d pending docs purged/excluded.",
            len(approved_docs),
            total_ingested_chunks,
            len(pending_docs),
        )
        return 0
    except Exception as exc:
        logger.exception("Fatal error during knowledge base synchronization: %s", exc)
        return 1
    finally:
        await qdrant.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
