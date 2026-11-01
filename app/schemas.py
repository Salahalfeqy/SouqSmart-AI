from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Price(StrictModel):
    amount: str
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("amount")
    @classmethod
    def valid_decimal(cls, value: str) -> str:
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("must be a decimal string") from exc
        if not number.is_finite() or number < 0:
            raise ValueError("must be a non-negative finite decimal")
        return format(number, "f")

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("currency must be a three-letter code")
        return value.upper()


class Availability(StrictModel):
    status: Literal["in_stock", "out_of_stock", "unknown"]


class Product(StrictModel):
    product_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=300)
    brand: str | None = None
    category: str | None = None
    price: Price
    availability: Availability
    attributes: dict[str, str] = Field(default_factory=dict)
    url: str
    image_url: str | None = None
    updated_at: str | None = None


class PriceFilters(StrictModel):
    category: str | None = Field(default=None, max_length=100)
    brand: str | None = Field(default=None, max_length=100)
    price_min: str | None = None
    price_max: str | None = None
    in_stock_only: bool = False

    @field_validator("price_min", "price_max")
    @classmethod
    def non_negative_decimal(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("must be a decimal string") from exc
        if not number.is_finite() or number < 0:
            raise ValueError("must be non-negative")
        return format(number, "f")

    @model_validator(mode="after")
    def ordered_prices(self) -> "PriceFilters":
        if self.price_min is not None and self.price_max is not None:
            if Decimal(self.price_min) > Decimal(self.price_max):
                raise ValueError("price_min must not exceed price_max")
        return self


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=200)
    filters: PriceFilters = Field(default_factory=PriceFilters)
    sort: Literal["relevance", "price_asc", "price_desc"] = "relevance"
    limit: int = Field(default=10, ge=1, le=20)
    offset: int = Field(default=0, ge=0)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        value = "".join(ch for ch in value if ch.isprintable()).strip()
        if not value:
            raise ValueError("must contain searchable text")
        return value


class CompareRequest(StrictModel):
    product_ids: list[str] = Field(min_length=2, max_length=4)
    include_summary: bool = True
    language: Literal["en", "ar", "auto"] = "en"

    @field_validator("product_ids")
    @classmethod
    def valid_ids(cls, value: list[str]) -> list[str]:
        clean = [item.strip() for item in value]
        if any(not item or len(item) > 64 for item in clean):
            raise ValueError("product IDs must be between 1 and 64 characters")
        if len(set(clean)) != len(clean):
            raise ValueError("product IDs must be unique")
        return clean


class ChatContext(StrictModel):
    current_product_id: str | None = Field(default=None, max_length=64)


class ChatRequest(StrictModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)
    language: Literal["en", "ar", "auto"] = "auto"
    context: ChatContext = Field(default_factory=ChatContext)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str | None) -> str | None:
        if value is not None:
            UUID(value)
        return value

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        value = "".join(ch for ch in value if ch.isprintable()).strip()
        if not value:
            raise ValueError("message must contain text")
        return value


class ErrorEnvelope(StrictModel):
    error: dict[str, Any]
