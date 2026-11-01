# UI Acceptance Tests for SouqSmart
# These tests validate the observed issues and expected behaviours.

import pytest
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

# Helper to get auth headers (assumes fixtures provide a valid service key)
@pytest.fixture
def headers(monkeypatch):
    # Use the settings service key from the environment; if missing, monkeypatch a dummy.
    from app.config import get_settings
    settings = get_settings()
    if not settings.service_key:
        monkeypatch.setattr(settings, "service_key", "dummy-key")
    return {"X-Service-Key": settings.service_key}


def test_greeting_no_catalog(headers, monkeypatch):
    """A greeting like 'صباح الخير' should return a deterministic greeting reply
    and an empty products list, without invoking the catalog or Gemini.
    Even if current_product_id is provided in context (e.g. browsing product 2),
    it must never trigger catalog lookups or return product cards."""
    import app.main as main_mod
    from unittest.mock import AsyncMock

    mock_search = AsyncMock()
    mock_products = AsyncMock()
    monkeypatch.setattr(main_mod.catalog, "search", mock_search)
    monkeypatch.setattr(main_mod.catalog, "products", mock_products)

    # 1. Test standalone greeting
    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "صباح الخير"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["products"] == []
    assert data["reply"]["language"] == "ar"
    assert "أهلاً" in data["reply"]["text"] or "صباح" in data["reply"]["text"]
    assert mock_search.call_count == 0
    assert mock_products.call_count == 0

    # 2. Test greeting while viewing a product page (context.current_product_id set)
    response_with_context = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={
            "message": "صباح الخير",
            "context": {"current_product_id": "2"},
        },
    )
    assert response_with_context.status_code == 200
    data_ctx = response_with_context.json()
    assert data_ctx["products"] == []
    assert data_ctx["reply"]["language"] == "ar"
    assert "أهلاً" in data_ctx["reply"]["text"] or "صباح" in data_ctx["reply"]["text"]
    # Ensure catalog provider was NEVER called
    assert mock_search.call_count == 0
    assert mock_products.call_count == 0


def test_general_browse_arabic(headers):
    """Arabic browse requests should query the catalog and return products."""
    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "اعرضلى المنتجات المتاحه"},
    )
    assert response.status_code == 200
    data = response.json()
    # Should have one or more products
    assert isinstance(data["products"], list)
    assert len(data["products"]) > 0
    # Reply should be Arabic and mention products
    assert data["reply"]["language"] == "ar"
    assert "منتجات" in data["reply"]["text"] or "أبرز المنتجات" in data["reply"]["text"]


def test_multiple_price_request_explanation(headers):
    """Requests asking for multiple prices of a single product should get a clear explanation
    and no product results."""
    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "تقدر تعرضلى منتج منتج متوفر عندك منه كذا سعر"},
    )
    assert response.status_code == 200
    data = response.json()
    # No products should be returned
    assert data["products"] == []
    # Reply should be the deterministic explanation (Arabic)
    assert data["reply"]["language"] == "ar"
    assert "سعر" in data["reply"]["text"]
    assert "متعددة" in data["reply"]["text"]


@pytest.fixture
def realistic_catalog(monkeypatch):
    """Catalog fixture with the recorded NiceShop records and normal substring search."""
    import app.main as main_mod
    from app.catalog import FixtureProvider
    from app.schemas import Product

    class RecordingCatalog(FixtureProvider):
        def __init__(self):
            self.products_by_id = {
                product_id: Product(
                    product_id=product_id, name=name,
                    price={"amount": amount, "currency": "EGP"},
                    availability={"status": "in_stock"}, attributes={},
                    url=f"http://localhost/nice-shop/product?id={product_id}",
                )
                for product_id, name, amount in (
                    ("92", "Leather-Style Wallet", "390.00"),
                    ("91", "Everyday Backpack", "890.00"),
                )
            }
            self.calls = []

        async def search(self, payload):
            self.calls.append(("search", payload["query"]))
            return await super().search(payload)

        async def products(self, ids):
            self.calls.append(("products", ids))
            return await super().products(ids)

    provider = RecordingCatalog()
    monkeypatch.setattr(main_mod, "catalog", provider)
    return provider


def test_comparison_resolves_full_names_independently(headers, realistic_catalog):
    from app.services import parse_chat_intent

    message = "قارن بين Leather-Style Wallet و Everyday Backpack"
    intent = parse_chat_intent(message)
    assert intent.intent_type == "compare"
    assert intent.comparison_names == ["Leather-Style Wallet", "Everyday Backpack"]

    browse = client.post("/v1/chat/messages", headers=headers, json={"message": "اعرضلى المنتجات المتاحه"})
    assert browse.status_code == 200
    assert {p["product_id"] for p in browse.json()["products"]} == {"92", "91"}
    realistic_catalog.calls.clear()
    response = client.post(
        "/v1/chat/messages", headers=headers,
        json={"message": message, "session_id": browse.json()["session_id"],
              "context": {"current_product_id": "92"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["session_status"] == "continued"
    assert [p["product_id"] for p in data["products"]] == ["92", "91"]
    assert realistic_catalog.calls == [("search", "Leather-Style Wallet"), ("search", "Everyday Backpack")]
    for product in data["products"]:
        record = realistic_catalog.products_by_id[product["product_id"]]
        assert product["price"] == record.price.model_dump()
        assert record.name in data["reply"]["text"]
        assert f"{record.price.amount} {record.price.currency}" in data["reply"]["text"]


@pytest.mark.parametrize("message,language,expected", [
    ("شكراً", "ar", "العفو"), ("شكرا لك", "ar", "العفو"),
    ("تسلم", "ar", "العفو"), ("متشكر", "ar", "العفو"),
    ("Thanks", "en", "welcome"), ("Thank you", "en", "welcome"),
])
def test_gratitude_is_conversational_without_catalog(headers, realistic_catalog, monkeypatch, message, language, expected):
    from app.services import parse_chat_intent
    import app.main as main_mod

    # LLM mode must not change deterministic gratitude handling.
    monkeypatch.setattr(main_mod.settings, "intent_parser", "llm")
    assert parse_chat_intent(message).intent_type == "gratitude"
    response = client.post("/v1/chat/messages", headers=headers, json={
        "message": message, "context": {"current_product_id": "92"},
    })
    assert response.status_code == 200
    data = response.json()
    assert data["reply"]["language"] == language
    assert expected in data["reply"]["text"]
    assert data["products"] == []
    assert realistic_catalog.calls == []


def test_thanks_with_shopping_request_is_not_pure_gratitude(headers, realistic_catalog):
    from app.services import parse_chat_intent

    message = "شكرا ابحث عن محفظة"
    assert parse_chat_intent(message).intent_type != "gratitude"
    response = client.post("/v1/chat/messages", headers=headers, json={"message": message})
    assert response.status_code == 200
    assert realistic_catalog.calls


def test_comparison_does_not_substitute_partial_name_matches(headers, realistic_catalog):
    response = client.post("/v1/chat/messages", headers=headers, json={
        "message": "قارن بين Wallet و Everyday Backpack",
    })
    assert response.status_code == 200
    data = response.json()
    assert [p["product_id"] for p in data["products"]] == ["91"]
    assert "منتجين على الأقل" in data["reply"]["text"]
