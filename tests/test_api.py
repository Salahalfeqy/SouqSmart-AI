import pytest
from app.catalog import CatalogTimeout, PHPProvider
from app.main import catalog
from app.schemas import Availability, Price, Product
from app.sessions import MemorySessions


def test_health_is_public(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["llm"]["status"] == "disabled"


@pytest.mark.parametrize("headers", [{}, {"X-Service-Key": "wrong"}])
def test_protected_authentication(client, headers):
    response = client.post("/v1/products/search", headers=headers, json={"query": "laptop"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_validation(client, headers):
    response = client.post("/v1/products/search", headers=headers, json={"query": "", "limit": 21})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_field(client, headers):
    response = client.post("/v1/products/search", headers=headers, json={"query": "laptop", "secret_field": 1})
    assert response.status_code == 422


def test_missing_product(client, headers):
    response = client.post("/v1/products/compare", headers=headers, json={"product_ids": ["fixture-101", "missing"]})
    assert response.status_code == 404
    assert response.json()["error"]["details"]["missing_ids"] == ["missing"]


def test_deterministic_search(client, headers):
    first = client.post("/v1/products/search", headers=headers, json={"query": "laptop"})
    second = client.post("/v1/products/search", headers=headers, json={"query": "laptop"})
    assert first.status_code == 200
    assert first.json()["results"] == second.json()["results"]


def test_mixed_currency_has_no_price_derived(client, headers, monkeypatch):
    mixed = [
        Product(product_id="a", name="A", price=Price(amount="10", currency="USD"), availability=Availability(status="in_stock"), attributes={}, url="https://example.invalid/a"),
        Product(product_id="b", name="B", price=Price(amount="20", currency="EUR"), availability=Availability(status="in_stock"), attributes={}, url="https://example.invalid/b"),
    ]
    async def products(ids):
        return mixed, []
    monkeypatch.setattr(catalog, "products", products)
    response = client.post("/v1/products/compare", headers=headers, json={"product_ids": ["a", "b"]})
    assert response.status_code == 200
    assert response.json()["derived"] == {"cheapest_product_id": None, "price_spread": None}


def test_catalog_timeout(client, headers, monkeypatch):
    async def timeout(payload):
        raise CatalogTimeout()
    monkeypatch.setattr(catalog, "search", timeout)
    response = client.post("/v1/products/search", headers=headers, json={"query": "laptop"})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"


def test_session_expiry(monkeypatch):
    sessions = MemorySessions(ttl_seconds=1, turn_limit=20)
    monkeypatch.setattr("app.sessions.time.monotonic", lambda: 100.0)
    first_id, _ = sessions.get_or_create(None)
    monkeypatch.setattr("app.sessions.time.monotonic", lambda: 102.0)
    second_id, status = sessions.get_or_create(first_id)
    assert second_id != first_id
    assert status == "new"


def test_template_fallback_does_not_invent_specs(client, headers):
    response = client.post("/v1/chat/messages", headers=headers, json={"message": "find laptop"})
    assert response.status_code == 200
    assert response.json()["ai_status"] == "disabled"
    assert "Catalog results" in response.json()["reply"]["text"]


@pytest.mark.asyncio
async def test_php_provider_maps_agent_b_contract(monkeypatch):
    provider = PHPProvider("http://127.0.0.1/nice-shop", "test-key", 3)
    calls = {}

    async def request(method, url, **kwargs):
        calls.update(method=method, url=url, kwargs=kwargs)
        return {"products": [{
            "product_id": "92", "name": "Leather-Style Wallet", "brand": "BrightBasics",
            "category": "Accessories", "price": {"amount": "390.00", "currency": "EGP"},
            "availability": {"status": "in_stock"}, "attributes": {},
            "url": "http://localhost/nice-shop/product?id=92", "image_url": None,
            "updated_at": "2026-10-09 14:09:22"
        }], "total": 1}

    monkeypatch.setattr(provider, "_request", request)
    products, total = await provider.search({
        "query": "wallet", "filters": {"in_stock_only": True, "category": "Accessories"},
        "limit": 10, "offset": 0
    })
    assert total == 1
    assert products[0].product_id == "92"
    assert calls["method"] == "POST"
    assert calls["url"].endswith("/internal/catalog/search")
    assert calls["kwargs"]["json"]["availability"] == "in_stock"


def test_intent_parser_product_keyword_extraction():
    from app.services import parse_chat_intent
    intent = parse_chat_intent("Help me compare wireless headphones")
    assert intent.clean_query == "wireless headphones"
    assert intent.is_compare is True
    assert intent.language == "en"


def test_intent_parser_general_browse_arabic():
    from app.services import parse_chat_intent
    intent = parse_chat_intent("قلى ايه المنتجات اللى عندكم النهارده")
    assert intent.clean_query == ""
    assert intent.is_browse is True
    assert intent.language == "ar"


def test_intent_parser_stock_filtering():
    from app.services import parse_chat_intent
    intent = parse_chat_intent("What products are in stock right now?")
    assert intent.clean_query == ""
    assert intent.is_browse is True
    assert intent.filters.get("in_stock_only") is True


def test_intent_parser_price_currency_handling():
    from app.services import parse_chat_intent
    # USD specified: store currency is EGP, so currency limitation note must be set and price_max omitted
    intent_usd = parse_chat_intent("Can you recommend top products under $100?")
    assert intent_usd.currency_note is not None
    assert "price_max" not in intent_usd.filters
    assert intent_usd.is_browse is True

    # EGP specified: matches store currency, so price_max is safely extracted
    intent_egp = parse_chat_intent("منتجات أقل من 500 جنيه")
    assert intent_egp.filters.get("price_max") == "500.00"
    assert intent_egp.language == "ar"


def test_chat_single_product_comparison_honest_response(client, headers, monkeypatch):
    single = [
        Product(
            product_id="1",
            name="Wireless Headphones",
            price=Price(amount="1450.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="http://localhost/nice-shop/product?id=1",
        )
    ]
    async def fake_search(payload):
        return single, 1
    monkeypatch.setattr(catalog, "search", fake_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "Help me compare wireless headphones"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) == 1
    assert "At least two comparable products are required" in data["reply"]["text"]


def test_chat_arabic_general_browse(client, headers):
    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "قلى ايه المنتجات اللى عندكم النهارده"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) > 0
    assert data["reply"]["language"] == "ar"
    assert "أهلاً بك" in data["reply"]["text"]


def test_chat_stock_filter_intent(client, headers):
    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "What products are in stock right now?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) > 0
    assert all(p["availability"]["status"] == "in_stock" for p in data["products"])
    assert "currently in stock" in data["reply"]["text"]


def test_egyptian_arabic_product_search_intent():
    from app.intents import DeterministicIntentParser
    parser = DeterministicIntentParser()
    intent = parser.parse("عاوز أشتري لابتوب")
    assert intent.intent_type == "search"
    assert intent.clean_query == "لابتوب"
    assert intent.catalog_query == "laptop"
    assert intent.language == "ar"


def test_egyptian_arabic_general_browse_intent():
    from app.intents import DeterministicIntentParser
    parser = DeterministicIntentParser()
    intent = parser.parse("إيه المنتجات اللي عندكم؟")
    assert intent.intent_type == "browse"
    assert intent.clean_query == ""
    assert intent.is_browse is True
    assert intent.language == "ar"


def test_egyptian_arabic_stock_query_intent():
    from app.intents import DeterministicIntentParser
    parser = DeterministicIntentParser()
    intent = parser.parse("إيه المتاح عندكم؟")
    assert intent.intent_type == "browse"
    assert intent.clean_query == ""
    assert intent.filters.get("in_stock_only") is True
    assert intent.language == "ar"


def test_comparison_these_headphones():
    from app.intents import DeterministicIntentParser
    parser = DeterministicIntentParser()
    intent = parser.parse("Compare these headphones")
    assert intent.intent_type == "compare"
    assert intent.clean_query == "headphones"
    assert intent.is_compare is True


def test_explicit_budget_safe_egp_vs_usd():
    from decimal import Decimal
    from app.intents import DeterministicIntentParser
    parser = DeterministicIntentParser()

    # Egyptian Arabic with EGP budget
    intent_egp = parser.parse("لابتوب في حدود 3000 جنيه")
    assert intent_egp.clean_query == "لابتوب"
    assert intent_egp.catalog_query == "laptop"
    assert intent_egp.budget == Decimal("3000")
    assert intent_egp.budget_currency == "EGP"
    assert intent_egp.filters.get("price_max") == "3000.00"
    assert intent_egp.clarification_needed is False

    # English with USD budget (cannot safely apply to EGP store)
    intent_usd = parser.parse("laptops under $500")
    assert intent_usd.clean_query == "laptops"
    assert intent_usd.budget == Decimal("500")
    assert intent_usd.budget_currency == "USD"
    assert "price_max" not in intent_usd.filters
    assert intent_usd.clarification_needed is True
    assert intent_usd.currency_note is not None
    assert "EGP" in intent_usd.currency_note


def test_llm_intent_parser_interface_boundary():
    from app.intents import (
        DeterministicIntentParser,
        IntentParser,
        LLMIntentParser,
        ParsedIntent,
        get_intent_parser,
    )
    det = get_intent_parser("deterministic")
    assert isinstance(det, IntentParser)
    assert isinstance(det, DeterministicIntentParser)

    llm = get_intent_parser("llm")
    assert isinstance(llm, IntentParser)
    assert isinstance(llm, LLMIntentParser)

    # LLM parser fallback returns a valid ParsedIntent matching the contract
    res = llm.parse("find headphones")
    assert isinstance(res, ParsedIntent)
    assert res.intent_type == "search"
    assert res.product_query == "headphones"


def test_chat_endpoint_egyptian_arabic_laptop_search(client, headers):
    # In fixture provider mode, "laptop" matches Fixture Nova Laptop and Fixture Orbit Laptop
    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عاوز أشتري لابتوب"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) > 0
    assert data["reply"]["language"] == "ar"
    assert "الكتالوج" in data["reply"]["text"]


def test_chat_endpoint_stock_egyptian_arabic(client, headers):
    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "إيه المتاح عندكم؟"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) > 0
    assert data["reply"]["language"] == "ar"
    assert "المتوفرة" in data["reply"]["text"] or "المخزن" in data["reply"]["text"]


def test_chat_endpoint_insufficient_comparison_arabic(client, headers, monkeypatch):
    single = [
        Product(
            product_id="1",
            name="سماعات رأس لاسلكية",
            price=Price(amount="1450.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="http://localhost/nice-shop/product?id=1",
        )
    ]
    async def fake_search(payload):
        return single, 1
    monkeypatch.setattr(catalog, "search", fake_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "قارن هذه السماعات"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) == 1
    assert data["reply"]["language"] == "ar"
    assert "منتجين على الأقل" in data["reply"]["text"]


def test_laptop_device_accessory_discrimination(client, headers, monkeypatch):
    stand_only = [
        Product(
            product_id="9",
            name="Laptop Stand",
            brand="NovaTech",
            category="Electronics",
            price=Price(amount="760.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="http://localhost/nice-shop/product?id=9",
        )
    ]
    async def fake_search(payload):
        return stand_only, 1
    monkeypatch.setattr(catalog, "search", fake_search)

    # Arabic laptop query: Laptop Stand must NOT be returned in products as a laptop
    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عاوز أشتري لابتوب"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) == 0
    assert "لا تتوفر أجهزة لابتوب" in data["reply"]["text"]
    assert "Laptop Stand" in data["reply"]["text"]

    # Explicit request for laptop stand: should be returned
    response_stand = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عاوز حامل لابتوب"},
    )
    assert response_stand.status_code == 200
    data_stand = response_stand.json()
    assert len(data_stand["products"]) == 1
    assert data_stand["products"][0]["name"] == "Laptop Stand"


def test_usd_budget_distinguishes_unfiltered_suggestions(client, headers, monkeypatch):
    catalog_items = [
        Product(
            product_id="92",
            name="Leather-Style Wallet",
            price=Price(amount="390.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="http://localhost/nice-shop/product?id=92",
        )
    ]
    async def fake_search(payload):
        return catalog_items, 1
    monkeypatch.setattr(catalog, "search", fake_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "Can you recommend top products under $100?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "[Unfiltered catalog suggestions]" in data["reply"]["text"]
    assert "EGP" in data["reply"]["text"]
    assert "$100" in data["reply"]["text"]


def test_egp_budget_response_message(client, headers, monkeypatch):
    under_1000 = [
        Product(
            product_id="92",
            name="Leather-Style Wallet",
            price=Price(amount="390.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="http://localhost/nice-shop/product?id=92",
        )
    ]
    async def fake_search(payload):
        return under_1000, 1
    monkeypatch.setattr(catalog, "search", fake_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "منتجات أقل من 1000 جنيه"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) == 1
    assert "1000.00 EGP" in data["reply"]["text"] or "1000 EGP" in data["reply"]["text"]
