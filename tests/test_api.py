from decimal import Decimal
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


def test_template_fallback_does_not_invent_specs(client, headers, monkeypatch):
    monkeypatch.setattr("app.main.settings.gemini_api_key", "")
    response = client.post("/v1/chat/messages", headers=headers, json={"message": "find laptop"})
    assert response.status_code == 200
    assert response.json()["ai_status"] == "disabled"
    assert "Catalog results" in response.json()["reply"]["text"]


def test_grounded_response_generation_is_independent_of_intent_parser_mode(client, headers, monkeypatch):
    from app.response_generator import ResponseGenerator

    captured = {}

    async def fake_generate(self, intent, products, language, **context):
        captured.update(intent=intent, products=products, language=language, context=context)
        return "Here are the matching catalog products."

    monkeypatch.setattr("app.main.settings.gemini_api_key", "test-key")
    monkeypatch.setattr(ResponseGenerator, "agenerate", fake_generate)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "find laptop", "language": "en"},
    )

    assert response.status_code == 200
    assert response.json()["reply"]["text"] == "Here are the matching catalog products."
    assert captured["intent"].intent_type == "search"
    assert captured["products"]
    assert captured["context"]["user_message"] == "find laptop"
    assert captured["context"]["history"] == []


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


def test_strict_product_type_validation_out_of_catalog(client, headers, monkeypatch):
    """
    Task 1: Strict Product-Type Validation for Out-of-Catalog Product Matching.
    Ensures that when a user asks for an explicit category that does not exist in the catalog
    (e.g., laptops), semantically similar yet irrelevant items (SSD, Webcam, Reading Lamp)
    are NOT returned as matching products.
    """
    mixed_candidates = [
        Product(
            product_id="10",
            name="Portable SSD 1TB",
            brand="NovaTech",
            category="Electronics",
            price=Price(amount="3400.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={"description": "Portable SSD 1TB for everyday use."},
            url="http://localhost/nice-shop/product?id=10",
        ),
        Product(
            product_id="7",
            name="Webcam HD",
            brand="NovaTech",
            category="Electronics",
            price=Price(amount="1250.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={"description": "Webcam HD for everyday use."},
            url="http://localhost/nice-shop/product?id=7",
        ),
        Product(
            product_id="56",
            name="Reading Lamp",
            brand="PaperTrail",
            category="Books & Stationery",
            price=Price(amount="370.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={"description": "Reading Lamp for everyday use."},
            url="http://localhost/nice-shop/product?id=56",
        ),
        Product(
            product_id="9",
            name="Laptop Stand",
            brand="NovaTech",
            category="Electronics",
            price=Price(amount="760.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={"description": "Laptop Stand for everyday use."},
            url="http://localhost/nice-shop/product?id=9",
        ),
    ]

    async def fake_search(payload):
        return mixed_candidates, len(mixed_candidates)

    monkeypatch.setattr(catalog, "search", fake_search)

    # 1. User: "عايز لابتوب Dell جديد"
    # Must NOT return SSD, Webcam, Reading Lamp, or Stand as product cards
    resp_dell = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عايز لابتوب Dell جديد"},
    )
    assert resp_dell.status_code == 200
    data_dell = resp_dell.json()
    assert len(data_dell["products"]) == 0, f"Expected 0 products, got: {data_dell['products']}"
    returned_pids = [p["product_id"] for p in data_dell["products"]]
    assert "10" not in returned_pids  # Portable SSD 1TB
    assert "7" not in returned_pids   # Webcam HD
    assert "56" not in returned_pids  # Reading Lamp
    assert "لا تتوفر أجهزة لابتوب" in data_dell["reply"]["text"]
    assert "Laptop Stand" in data_dell["reply"]["text"]  # Mentions related accessory in text

    # 2. User: "عايز لابتوب" without any accessories available
    no_accessories = [mixed_candidates[0], mixed_candidates[1]]  # Only SSD and Webcam
    async def fake_search_no_acc(payload):
        return no_accessories, len(no_accessories)
    monkeypatch.setattr(catalog, "search", fake_search_no_acc)

    resp_lap = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عايز لابتوب"},
    )
    assert resp_lap.status_code == 200
    data_lap = resp_lap.json()
    assert len(data_lap["products"]) == 0
    assert "لا تتوفر أجهزة لابتوب" in data_lap["reply"]["text"]

    # 3. User: "عندك Webcam HD؟" - must still return Webcam HD
    webcam_item = [mixed_candidates[1]]
    async def fake_search_webcam(payload):
        return webcam_item, 1
    monkeypatch.setattr(catalog, "search", fake_search_webcam)

    resp_webcam = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عندك Webcam HD؟"},
    )
    assert resp_webcam.status_code == 200
    data_webcam = resp_webcam.json()
    assert len(data_webcam["products"]) == 1
    assert data_webcam["products"][0]["name"] == "Webcam HD"

    # 4. User: "عايز سماعة بلوتوث" - must still return relevant audio
    audio_items = [
        Product(
            product_id="2",
            name="Bluetooth Speaker",
            brand="NovaTech",
            category="Electronics",
            price=Price(amount="980.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={"description": "Bluetooth Speaker"},
            url="http://localhost/nice-shop/product?id=2",
        )
    ]
    async def fake_search_audio(payload):
        return audio_items, 1
    monkeypatch.setattr(catalog, "search", fake_search_audio)

    resp_audio = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عايز سماعة بلوتوث"},
    )
    assert resp_audio.status_code == 200
    data_audio = resp_audio.json()
    assert len(data_audio["products"]) == 1
    assert data_audio["products"][0]["name"] == "Bluetooth Speaker"


def test_conversational_price_numeric_comparisons(client, headers):
    """Case A: Numeric comparison extraction for min and max bounds."""
    from app.intents import DeterministicIntentParser
    parser = DeterministicIntentParser()

    # 'أكبر من 1000 جنيه' -> minimum price 1000
    intent_min = parser.parse("عايز حاجة للرياضة أكبر من 1000 جنيه")
    assert intent_min.filters.get("price_min") == "1000.00"
    assert "price_max" not in intent_min.filters
    assert intent_min.budget == Decimal("1000")

    # 'اكبر من 1000 جنيه' (without hamza) -> minimum price 1000
    intent_min_no_hamza = parser.parse("عايز حاجة للرياضة اكبر من 1000 جنيه")
    assert intent_min_no_hamza.filters.get("price_min") == "1000.00"
    assert "price_max" not in intent_min_no_hamza.filters

    # 'أقل من 1000 جنيه' -> maximum price 1000
    intent_max = parser.parse("عايز حاجة للرياضة أقل من 1000 جنيه")
    assert intent_max.filters.get("price_max") == "1000.00"
    assert "price_min" not in intent_max.filters
    assert intent_max.budget == Decimal("1000")


def test_conversational_constraint_replacement(client, headers, monkeypatch):
    """Case B: Constraint replacement when user sets conflicting price bounds."""
    from app.intents import merge_conversational_constraints

    # 1. Previous maximum 1000, followed by 'أكبر من 1500 جنيه' -> minimum 1500, old maximum cleared
    prev_max = {"price_max": "1000.00", "category": "sports"}
    curr_min = {"price_min": "1500.00"}
    merged_min = merge_conversational_constraints(prev_max, curr_min, "أكبر من 1500 جنيه")
    assert merged_min["price_min"] == "1500.00"
    assert "price_max" not in merged_min
    assert merged_min["category"] == "sports"

    # 2. Previous minimum 1000, followed by 'أقل من 800 جنيه' -> maximum 800, old minimum cleared
    prev_min = {"price_min": "1000.00", "category": "sports"}
    curr_max = {"price_max": "800.00"}
    merged_max = merge_conversational_constraints(prev_min, curr_max, "أقل من 800 جنيه")
    assert merged_max["price_max"] == "800.00"
    assert "price_min" not in merged_max
    assert merged_max["category"] == "sports"

    # Multi-turn API flow verification
    async def fake_search(payload):
        return [], 0
    monkeypatch.setattr(catalog, "search", fake_search)

    # Session turn 1: maximum 1000
    r1 = client.post("/v1/chat/messages", headers=headers, json={"message": "عايز حاجة للرياضة في حدود 1000 جنيه"})
    assert r1.status_code == 200
    sess_id = r1.json()["session_id"]

    # Session turn 2: greater than 1500 -> replaces previous maximum with minimum
    r2 = client.post("/v1/chat/messages", headers=headers, json={"session_id": sess_id, "message": "أكبر من 1500 جنيه"})
    assert r2.status_code == 200
    reply_text = r2.json()["reply"]["text"]
    assert "أقل من 1000" not in reply_text
    assert "1500" in reply_text


def test_conversational_constraint_removal(client, headers, monkeypatch):
    """Case C: Constraint removal when relaxing or dropping price restrictions."""
    from app.intents import merge_conversational_constraints

    # 1. Previous maximum 1000, followed by 'هاتلي حاجة بسعر أكبر عادي' -> old maximum cleared
    prev_max = {"price_max": "1000.00", "category": "sports"}
    merged_relaxed = merge_conversational_constraints(prev_max, {}, "هاتلي حاجة بسعر أكبر عادي")
    assert "price_max" not in merged_relaxed
    assert "price_min" not in merged_relaxed
    assert merged_relaxed["category"] == "sports"

    # 2. Previous min/max, followed by 'السعر مش مهم' -> both cleared
    prev_both = {"price_min": "500.00", "price_max": "1000.00", "brand": "Nike"}
    merged_cleared = merge_conversational_constraints(prev_both, {}, "السعر مش مهم")
    assert "price_min" not in merged_cleared
    assert "price_max" not in merged_cleared
    assert merged_cleared["brand"] == "Nike"

    # Multi-turn API flow verification
    async def fake_search(payload):
        return [], 0
    monkeypatch.setattr(catalog, "search", fake_search)

    r1 = client.post("/v1/chat/messages", headers=headers, json={"message": "سماعات في حدود 1000 جنيه"})
    assert r1.status_code == 200
    sess_id = r1.json()["session_id"]

    r2 = client.post("/v1/chat/messages", headers=headers, json={"session_id": sess_id, "message": "هاتلي حاجة بسعر أكبر عادي"})
    assert r2.status_code == 200
    r2_reply = r2.json()["reply"]["text"]
    assert "أقل من 1000" not in r2_reply


def test_conversational_constraint_preservation(client, headers):
    """Case D: Unrelated constraints (such as sports category) are preserved when price changes."""
    from app.intents import merge_conversational_constraints

    # Previous category = sports, followed by 'بسعر أكبر عادي' -> sports category remains active
    prev_constraints = {"category": "sports", "price_max": "1000.00"}
    merged = merge_conversational_constraints(prev_constraints, {}, "بسعر أكبر عادي")
    assert merged.get("category") == "sports"
    assert "price_max" not in merged
    assert "price_min" not in merged


def test_full_conversational_price_update_scenario(client, headers, monkeypatch):
    """Full 3-turn live demo reproduction scenario:
    1. 'عايز حاجة للرياضة في حدود 1000 جنيه' -> reports no products under 1000
    2. 'خلاص هاتلي حاجه بسعر اكبر عادى' -> relaxes upper bound
    3. 'عايز حاجة للرياضة اكبر من 1000 جنيه' -> correctly filters by min 1000, never says under 1000
    """
    async def fake_search(payload):
        return [], 0
    monkeypatch.setattr(catalog, "search", fake_search)

    # Turn 1
    r1 = client.post("/v1/chat/messages", headers=headers, json={"message": "عايز حاجة للرياضة في حدود 1000 جنيه"})
    assert r1.status_code == 200
    sess_id = r1.json()["session_id"]
    assert "أقل من 1000" in r1.json()["reply"]["text"] or "1000" in r1.json()["reply"]["text"]

    # Turn 2
    r2 = client.post("/v1/chat/messages", headers=headers, json={"session_id": sess_id, "message": "خلاص هاتلي حاجه بسعر اكبر عادى"})
    assert r2.status_code == 200

    # Turn 3
    r3 = client.post("/v1/chat/messages", headers=headers, json={"session_id": sess_id, "message": "عايز حاجة للرياضة اكبر من 1000 جنيه"})
    assert r3.status_code == 200
    r3_reply = r3.json()["reply"]["text"]
    # CRITICAL: Must NOT say "أقل من 1000"! Must indicate 1000 or more!
    assert "أقل من 1000" not in r3_reply
    assert "1000.00 EGP أو أكثر" in r3_reply or "1000" in r3_reply


def test_price_relaxation_reuses_previous_category_query(client, headers, monkeypatch):
    import app.main as main_mod
    from app.query_rewriter import QueryRewriter

    products = [
        Product(
            product_id="sports-under-1000",
            name="Yoga Mat",
            category="Sports & Outdoors",
            price=Price(amount="540.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="https://example.invalid/yoga-mat",
        ),
        Product(
            product_id="sports-over-1000",
            name="Adjustable Dumbbells",
            category="Sports & Outdoors",
            price=Price(amount="2300.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="https://example.invalid/adjustable-dumbbells",
        ),
    ]
    search_payloads = []

    class SportsCatalog:
        async def search(self, payload):
            search_payloads.append(payload)
            matches = list(products)
            filters = payload.get("filters", {})
            if filters.get("category"):
                matches = [p for p in matches if p.category == filters["category"]]
            if filters.get("price_min") is not None:
                matches = [p for p in matches if Decimal(p.price.amount) >= Decimal(filters["price_min"])]
            if filters.get("price_max") is not None:
                matches = [p for p in matches if Decimal(p.price.amount) <= Decimal(filters["price_max"])]
            return matches, len(matches)

        async def products(self, ids):
            matches = [p for p in products if p.product_id in ids]
            return matches, [item for item in ids if item not in {p.product_id for p in matches}]

    monkeypatch.setattr(main_mod, "catalog", SportsCatalog())
    monkeypatch.setattr(main_mod, "query_rewriter", QueryRewriter(api_key=""))

    first = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عايز حاجة للرياضة في حدود 1000 جنيه"},
    )
    assert first.status_code == 200
    assert [p["name"] for p in first.json()["products"]] == ["Yoga Mat"]

    second = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={
            "session_id": first.json()["session_id"],
            "message": "خلاص هاتلي حاجه بسعر اكبر عادى",
        },
    )
    assert second.status_code == 200
    assert {p["name"] for p in second.json()["products"]} == {"Yoga Mat", "Adjustable Dumbbells"}
    assert search_payloads[1]["query"] == ""
    assert search_payloads[1]["filters"] == {"category": "Sports & Outdoors"}
