from functools import lru_cache
from typing import Literal
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SOUQSMART_", env_file=".env", extra="ignore")

    service_key: str = ""
    catalog_provider: str = "php"
    php_base_url: str = "http://127.0.0.1/nice-shop"
    catalog_timeout_seconds: float = 3.0
    session_ttl_seconds: float = 1800.0
    session_turn_limit: int = 20
    host: str = "127.0.0.1"
    port: int = 8000

    session_store: Literal["database", "memory"] = "database"
    database_path: str = "data/souqsmart_state.db"
    owner_secret: str = "souqsmart_owner_secret_dev"
    allow_anonymous_dev_sessions: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "SOUQSMART_ALLOW_ANONYMOUS_DEV_SESSIONS", "ALLOW_ANONYMOUS_DEV_SESSIONS"
        ),
    )

    enable_query_rewriter: bool = True
    query_rewriter_model: str = "gemini-3.8-flash"
    query_rewriter_timeout_seconds: float = 2.5

    qdrant_host: str = "127.0.0.1"
    qdrant_port: int = 6333
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection_products: str = "products_v1"
    qdrant_collection_knowledge: str = "knowledge_chunks_v1"
    rag_hybrid_enabled: bool = True

    gemini_embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("SOUQSMART_GEMINI_EMBEDDING_API_KEY", "GEMINI_EMBEDDING_API_KEY"),
    )
    gemini_embedding_model: str = Field(
        default="gemini-embedding-2",
        validation_alias=AliasChoices("SOUQSMART_GEMINI_EMBEDDING_MODEL", "GEMINI_EMBEDDING_MODEL"),
    )
    embedding_dimension: int = 768

    intent_parser: str = Field(
        default="deterministic",
        validation_alias=AliasChoices("SOUQSMART_INTENT_PARSER", "INTENT_PARSER"),
    )
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GEMINI_API_KEY", "SOUQSMART_GEMINI_API_KEY"),
    )
    gemini_model: str = Field(
        default="gemini-3.8-flash",
        validation_alias=AliasChoices("GEMINI_MODEL", "SOUQSMART_GEMINI_MODEL"),
    )
    gemini_timeout_seconds: float = Field(
        default=3.5,
        validation_alias=AliasChoices("GEMINI_TIMEOUT_SECONDS", "SOUQSMART_GEMINI_TIMEOUT_SECONDS"),
    )

    @property
    def effective_embedding_api_key(self) -> str:
        return self.gemini_embedding_api_key.strip() or self.gemini_api_key.strip()

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if self.catalog_provider == "php":
            insecure = {
                "",
                "dev-only-change-me",
                "souqsmart_dev_internal_secret_change_in_production",
                "replace-with-a-long-random-shared-key",
            }
            if not self.service_key or self.service_key.strip() in insecure:
                raise ValueError(
                    "Insecure or missing SOUQSMART_SERVICE_KEY for live PHP provider. "
                    "Configure a strong secret via SOUQSMART_SERVICE_KEY environment variable."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
