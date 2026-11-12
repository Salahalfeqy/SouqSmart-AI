import uuid
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

NAMESPACE_SOUQSMART = uuid.uuid5(uuid.NAMESPACE_DNS, "souqsmart.internal")


def make_product_point_id(product_id: str | int) -> str:
    return str(uuid.uuid5(NAMESPACE_SOUQSMART, f"product_{product_id}"))


def make_chunk_point_id(doc_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(NAMESPACE_SOUQSMART, f"chunk_{doc_id}_{chunk_index}"))


class ProductPointPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    product_id: str
    name: str
    brand: str | None = None
    category: str | None = None
    description: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    embedding_model: str
    embedding_dimension: int
    sync_version: int = 1
    last_synced_at: str
    is_active: bool = True


class KnowledgePointPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    doc_id: str
    chunk_id: str
    doc_title: str
    source: str
    section: str = ""
    chunk_index: int
    language: str = "ar"
    content_text: str
    content_hash: str
    doc_version: str = "1.0.0"
    updated_at: str

