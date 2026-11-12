# UI Acceptance Tests for SouqSmart
# These tests validate the observed issues and expected behaviours.

import pytest
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def disable_live_llm(monkeypatch):
    import app.main as main_mod
    monkeypatch.setattr(main_mod.settings, "gemini_api_key", "")


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


@pytest.mark.parametrize("message,language", [
    ("أنت مين؟", "ar"),
    ("بتقدر تساعدني في إيه؟", "ar"),
    ("اشرحلي نظرية النسبية", "ar"),
    ("Who are you?", "en"),
    ("Explain relativity", "en"),
    ("Good night", "en"),
])
def test_non_shopping_messages_use_general_conversation_intent(message, language):
    from app.services import parse_chat_intent

    intent = parse_chat_intent(message, language)
    assert intent.intent_type == "conversation"
    assert intent.language == language


def test_exact_product_title_without_search_verb_remains_a_catalog_query():
    from app.services import parse_chat_intent

    intent = parse_chat_intent("Premium Olive Oil", "en")
    assert intent.intent_type == "search"
    assert intent.catalog_query == "Premium Olive Oil"


def test_general_conversation_uses_llm_without_catalog(headers, monkeypatch):
    import app.main as main_mod
    from app.response_generator import ResponseGenerator
    from unittest.mock import AsyncMock

    captured = {}

    async def generate(self, intent, user_message, language, **kwargs):
        captured.update(intent=intent, user_message=user_message, language=language, **kwargs)
        return "I'm SouqSmart, here to help with shopping and store information."

    monkeypatch.setattr(main_mod.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(ResponseGenerator, "agenerate_general", generate)
    mock_search = AsyncMock()
    monkeypatch.setattr(main_mod.catalog, "search", mock_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "Who are you?", "language": "en"},
    )

    assert response.status_code == 200
    assert response.json()["products"] == []
    assert response.json()["reply"]["text"].startswith("I'm SouqSmart")
    assert captured["intent"].intent_type == "conversation"
    assert captured["history"] == []
    assert mock_search.await_count == 0


def test_vague_shopping_request_uses_general_generation_for_clarification(headers, monkeypatch):
    import app.main as main_mod
    from app.response_generator import ResponseGenerator
    from unittest.mock import AsyncMock

    calls = []

    async def generate(self, intent, user_message, language, **kwargs):
        calls.append((intent, user_message))
        return "What kind of gift are you looking for?"

    monkeypatch.setattr(main_mod.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(ResponseGenerator, "agenerate_general", generate)
    mock_search = AsyncMock()
    monkeypatch.setattr(main_mod.catalog, "search", mock_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "Recommend a gift for my friend", "language": "en"},
    )

    assert response.status_code == 200
    assert response.json()["reply"]["text"] == "What kind of gift are you looking for?"
    assert calls[0][0].intent_type == "clarify"
    assert mock_search.await_count == 0


def test_general_conversation_preserves_session_history(headers, monkeypatch):
    import app.main as main_mod
    from app.response_generator import ResponseGenerator

    calls = []

    async def generate(self, intent, user_message, language, **kwargs):
        calls.append((user_message, kwargs["history"]))
        return "Hello! I can help you shop."

    monkeypatch.setattr(main_mod.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(ResponseGenerator, "agenerate_general", generate)

    first = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "Hello", "language": "en"},
    )
    second = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={
            "message": "Thanks",
            "language": "en",
            "session_id": first.json()["session_id"],
        },
    )

    assert first.status_code == second.status_code == 200
    assert calls[1][0] == "Thanks"
    assert calls[1][1][-1]["assistant"] == "Hello! I can help you shop."


def test_empty_catalog_search_uses_general_generation_and_safe_fallback(headers, monkeypatch):
    import app.main as main_mod
    from app.response_generator import ResponseGenerator
    from unittest.mock import AsyncMock

    async def fail_generation(self, intent, user_message, language, **kwargs):
        raise RuntimeError("simulated quota failure")

    monkeypatch.setattr(main_mod.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(ResponseGenerator, "agenerate_general", fail_generation)
    mock_search = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(main_mod.catalog, "search", mock_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "find a rare camera model", "language": "en"},
    )

    assert response.status_code == 200
    assert response.json()["products"] == []
    assert "catalog" in response.json()["reply"]["text"].lower()
    assert mock_search.await_count == 1


def test_comparison_does_not_substitute_partial_name_matches(headers, realistic_catalog):
    response = client.post("/v1/chat/messages", headers=headers, json={
        "message": "قارن بين Wallet و Everyday Backpack",
    })
    assert response.status_code == 200
    data = response.json()
    assert [p["product_id"] for p in data["products"]] == ["91"]
    assert "منتجين على الأقل" in data["reply"]["text"]


# =====================================================================
# TASK 4: Egyptian Colloquial Greetings and Intent Classification Tests
# =====================================================================

@pytest.mark.parametrize("msg, lang, expected_greeting", [
    ("صباح الفل", "ar", "أهلاً"),
    ("صباح القشطة", "ar", "أهلاً"),
    ("مساء القشطة", "ar", "أهلاً"),
    ("مساء الورد", "ar", "أهلاً"),
    ("أهلاً وسهلاً", "ar", "أهلاً"),
    ("Good morning", "en", "Hello"),
    ("Hey", "en", "Hello"),
])
def test_task4_greeting_only_messages(headers, realistic_catalog, msg, lang, expected_greeting):
    """Greeting-only messages must return greeting reply and zero products, without querying catalog."""
    from app.services import parse_chat_intent
    intent = parse_chat_intent(msg)
    assert intent.is_greeting is True
    assert intent.intent_type == "greeting"
    assert intent.clean_query == ""

    response = client.post("/v1/chat/messages", headers=headers, json={"message": msg})
    assert response.status_code == 200
    data = response.json()
    assert data["products"] == []
    assert data["reply"]["language"] == lang
    assert expected_greeting in data["reply"]["text"]
    assert realistic_catalog.calls == []


def test_task4_greeting_with_product_request(headers, realistic_catalog):
    """Greeting followed by a substantive product request must trigger product search."""
    from app.services import parse_chat_intent
    msg = "صباح الفل، عندكم سماعة بلوتوث؟"
    intent = parse_chat_intent(msg)
    assert intent.is_greeting is False
    assert intent.intent_type == "search"
    assert "سماعة" in intent.clean_query or intent.catalog_query == "headphones"

    response = client.post("/v1/chat/messages", headers=headers, json={"message": msg})
    assert response.status_code == 200
    data = response.json()
    assert any(c[0] == "search" for c in realistic_catalog.calls)


def test_task4_greeting_with_payment_policy(headers, realistic_catalog):
    """Greeting followed by payment policy question must route to payment_policy."""
    from app.services import parse_chat_intent
    msg = "أهلاً، عايز أعرف طرق الدفع"
    intent = parse_chat_intent(msg)
    assert intent.is_greeting is False
    assert intent.intent_type == "payment_policy"

    response = client.post("/v1/chat/messages", headers=headers, json={"message": msg})
    assert response.status_code == 200
    data = response.json()
    assert data["products"] == []
    assert ("الدفع" in data["reply"]["text"] or "دفع" in data["reply"]["text"])
    assert realistic_catalog.calls == []


def test_task4_greeting_with_shipping_policy(headers, realistic_catalog):
    """Greeting followed by shipping policy question must route to shipping_policy."""
    from app.services import parse_chat_intent
    msg = "مساء الخير، هل بتشحنوا خارج مصر؟"
    intent = parse_chat_intent(msg)
    assert intent.is_greeting is False
    assert intent.intent_type == "shipping_policy"

    response = client.post("/v1/chat/messages", headers=headers, json={"message": msg})
    assert response.status_code == 200
    data = response.json()
    assert data["products"] == []
    assert ("الشحن" in data["reply"]["text"] or "شحن" in data["reply"]["text"])
    assert realistic_catalog.calls == []


def test_task4_closing_and_existing_greeting_regression(headers, realistic_catalog):
    """Regression test: existing صباح الخير and شكراً behaviors remain intact."""
    from app.services import parse_chat_intent

    # Existing greeting
    g_intent = parse_chat_intent("صباح الخير")
    assert g_intent.is_greeting is True
    assert g_intent.intent_type == "greeting"

    r_g = client.post("/v1/chat/messages", headers=headers, json={"message": "صباح الخير"})
    assert r_g.status_code == 200
    assert r_g.json()["products"] == []
    assert "أهلاً" in r_g.json()["reply"]["text"]

    # Existing closing / gratitude
    c_intent = parse_chat_intent("شكراً")
    assert c_intent.intent_type == "gratitude"
    assert c_intent.is_greeting is False

    r_c = client.post("/v1/chat/messages", headers=headers, json={"message": "شكراً"})
    assert r_c.status_code == 200
    assert r_c.json()["products"] == []
    assert "العفو" in r_c.json()["reply"]["text"]


def test_task5_online_meeting_usecase_retrieval(headers, monkeypatch):
    """Grounded semantic use-case retrieval:
    Online meetings / video calls queries must map to 'webcam' and retrieve Webcam HD."""
    import app.main as main_mod
    from app.services import parse_chat_intent
    from app.catalog import FixtureProvider
    from app.schemas import Product

    queries = [
        "عايز حاجة أستخدمها في الاجتماعات أونلاين",
        "محتاج حاجة تساعدني في مكالمات الفيديو",
        "محتاج حاجة لمكالمات الفيديو",
    ]
    for q in queries:
        intent = parse_chat_intent(q)
        assert intent.clarification_needed is False
        assert intent.intent_type == "search"
        assert intent.catalog_query == "webcam"

    class Task5Catalog(FixtureProvider):
        def __init__(self):
            self.products_by_id = {
                "7": Product(
                    product_id="7",
                    name="Webcam HD",
                    brand="NovaTech",
                    category="Electronics",
                    price={"amount": "750.00", "currency": "EGP"},
                    availability={"status": "in_stock"},
                    attributes={"short_description": "Webcam HD for everyday use."},
                    url="http://localhost/nice-shop/product?id=7",
                ),
                "91": Product(
                    product_id="91",
                    name="Everyday Backpack",
                    brand="UrbanGear",
                    category="Accessories",
                    price={"amount": "890.00", "currency": "EGP"},
                    availability={"status": "in_stock"},
                    attributes={"short_description": "Everyday Backpack for everyday use."},
                    url="http://localhost/nice-shop/product?id=91",
                ),
            }

    monkeypatch.setattr(main_mod, "catalog", Task5Catalog())

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عايز حاجة أستخدمها في الاجتماعات أونلاين"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) > 0
    assert data["products"][0]["name"] == "Webcam HD"
    assert "Webcam HD" in data["reply"]["text"]


def test_task5_generic_productivity_clarification(headers):
    """Generic productivity requests without a specific item must ask for clarification
    and return zero products."""
    from app.services import parse_chat_intent

    msg = "محتاج جهاز أو إكسسوار يساعدني أشتغل بكفاءة"
    intent = parse_chat_intent(msg)
    assert intent.clarification_needed is True
    assert intent.intent_type == "clarify"

    response = client.post("/v1/chat/messages", headers=headers, json={"message": msg})
    assert response.status_code == 200
    data = response.json()
    assert data["products"] == []
    reply_text = data["reply"]["text"]
    assert any(term in reply_text for term in ("الأجهزة", "إكسسوارات", "حامل لابتوب", "ماوس", "العمل"))


def test_task5_broad_everyday_clarification(headers):
    """Broad everyday use requests lacking category must ask for clarification
    and return zero products."""
    from app.services import parse_chat_intent

    msg = "عايز منتج مناسب للاستخدام اليومي"
    intent = parse_chat_intent(msg)
    assert intent.clarification_needed is True
    assert intent.intent_type == "clarify"

    response = client.post("/v1/chat/messages", headers=headers, json={"message": msg})
    assert response.status_code == 200
    data = response.json()
    assert data["products"] == []
    reply_text = data["reply"]["text"]
    assert any(term in reply_text for term in ("نوع المنتجات", "الاستخدام اليومي", "إلكترونيات"))


def test_task5_general_browsing_with_category(headers, monkeypatch):
    """Category-specific everyday browsing must cleanly browse the specified category
    without leaking unrelated products."""
    import app.main as main_mod
    from app.services import parse_chat_intent
    from app.catalog import FixtureProvider
    from app.schemas import Product

    msg = "ابحث عن منتجات إلكترونية مناسبة للاستخدام اليومي"
    intent = parse_chat_intent(msg)
    assert intent.clarification_needed is False
    assert intent.filters.get("category") == "Electronics"
    assert intent.is_browse is True

    class CategoryFilteredCatalog(FixtureProvider):
        def __init__(self):
            self.products_by_id = {
                "1": Product(
                    product_id="1", name="Wireless Headphones", category="Electronics",
                    price={"amount": "1450.00", "currency": "EGP"}, availability={"status": "in_stock"},
                    attributes={}, url="http://localhost/nice-shop/product?id=1",
                ),
                "7": Product(
                    product_id="7", name="Webcam HD", category="Electronics",
                    price={"amount": "750.00", "currency": "EGP"}, availability={"status": "in_stock"},
                    attributes={}, url="http://localhost/nice-shop/product?id=7",
                ),
                "91": Product(
                    product_id="91", name="Everyday Backpack", category="Accessories",
                    price={"amount": "890.00", "currency": "EGP"}, availability={"status": "in_stock"},
                    attributes={}, url="http://localhost/nice-shop/product?id=91",
                ),
            }

        async def search(self, payload):
            prods = list(self.products_by_id.values())
            filters = payload.get("filters", {})
            if filters.get("category"):
                prods = [p for p in prods if p.category == filters["category"]]
            return prods, len(prods)

    monkeypatch.setattr(main_mod, "catalog", CategoryFilteredCatalog())

    response = client.post("/v1/chat/messages", headers=headers, json={"message": msg})
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) == 2
    for p in data["products"]:
        assert p["category"] == "Electronics"
        assert p["name"] != "Everyday Backpack"

