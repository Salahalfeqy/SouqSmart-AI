import pytest
from decimal import Decimal
import httpx

from app.intents import (
    DeterministicIntentParser,
    LLMIntentParser,
    ParsedIntent,
    get_intent_parser,
)
from app.main import catalog
from app.schemas import Availability, Price, Product


def test_arabic_headphone_search_with_budget(client, headers, monkeypatch):
    """
    Scenario 1: Arabic query 'عاوز سماعة في حدود 1500 جنيه'
    Must normalize 'سماعة' to 'headphones' and set price_max to 1500.00 EGP.
    Must return Wireless Headphones (ID 1, 1450 EGP) which satisfies the budget.
    """
    parser = DeterministicIntentParser()
    intent = parser.parse("عاوز سماعة في حدود 1500 جنيه")
    assert intent.intent_type == "search"
    assert intent.clean_query == "سماعة"
    assert intent.catalog_query == "headphones"
    assert intent.filters.get("price_max") == "1500.00"
    assert "price_min" not in intent.filters
    assert intent.budget == Decimal("1500")
    assert intent.budget_currency == "EGP"
    assert intent.clarification_needed is False

    # Live endpoint test with mock catalog returning Wireless Headphones
    headphones = [
        Product(
            product_id="1",
            name="Wireless Headphones",
            brand="NovaTech",
            category="Electronics",
            price=Price(amount="1450.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="http://localhost/nice-shop/product?id=1",
        )
    ]
    captured_payload = {}

    async def fake_search(payload):
        nonlocal captured_payload
        captured_payload = payload
        return headphones, 1

    monkeypatch.setattr(catalog, "search", fake_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عاوز سماعة في حدود 1500 جنيه"},
    )
    assert response.status_code == 200
    data = response.json()
    assert captured_payload.get("query") == "headphones"
    assert captured_payload.get("filters", {}).get("price_max") == "1500.00"
    assert len(data["products"]) == 1
    assert data["products"][0]["product_id"] == "1"
    assert "Wireless Headphones" in data["reply"]["text"]
    assert "1450.00 EGP" in data["reply"]["text"]


@pytest.mark.parametrize(
    "phrase",
    [
        "عاوز سماعة",
        "عاوز سماعات",
        "عاوز سماعه",
        "عاوز سماعات رأس",
        "عاوز سماعة بلوتوث",
        "عاوز سماعة لاسلكية",
        "سماعات وايرلس",
        "عاوز هيدفون",
        "عاوز ايربودز",
    ],
)
def test_arabic_headphone_synonyms_normalization(phrase):
    """
    Scenario 2: Arabic queries for headphones in various Egyptian/colloquial forms
    Must all normalize catalog_query to 'headphones'.
    """
    parser = DeterministicIntentParser()
    intent = parser.parse(phrase)
    assert intent.intent_type == "search"
    assert intent.catalog_query == "headphones"
    assert intent.language == "ar"


def test_arabic_headphone_min_price_or_more_semantics(client, headers, monkeypatch):
    """
    Scenario 3: Arabic query 'عاوز سماعة في حدود 1500 جنيه او اكتر'
    'او اكتر' must NOT be interpreted as a maximum price limit.
    Must set price_min = 1500.00, clean 'او اكتر' from clean_query, and map to 'headphones'.
    Wireless Headphones at 1450 EGP (< 1500) must NOT be returned.
    """
    parser = DeterministicIntentParser()
    intent = parser.parse("عاوز سماعة في حدود 1500 جنيه او اكتر")
    assert intent.intent_type == "search"
    assert intent.clean_query == "سماعة"
    assert intent.catalog_query == "headphones"
    assert intent.filters.get("price_min") == "1500.00"
    assert "price_max" not in intent.filters
    assert intent.budget == Decimal("1500")
    assert intent.budget_currency == "EGP"

    # Verify semantic contrast between 'under 1500' and '1500 or more'
    intent_under = parser.parse("عاوز سماعة في حدود 1500 جنيه")
    assert intent_under.filters.get("price_max") == "1500.00"
    assert "price_min" not in intent_under.filters
    assert intent.filters != intent_under.filters

    # Under 1500 or more with 1450 EGP item in catalog: catalog returns empty list
    async def fake_search(payload):
        assert payload.get("filters", {}).get("price_min") == "1500.00"
        assert "price_max" not in payload.get("filters", {})
        return [], 0

    monkeypatch.setattr(catalog, "search", fake_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "عاوز سماعة في حدود 1500 جنيه او اكتر"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) == 0
    # Must explicitly state no products available at 1500 or more
    assert "أو أكثر" in data["reply"]["text"] or "او اكتر" in data["reply"]["text"]
    assert "أقل من" not in data["reply"]["text"]


def test_english_comma_separated_number_budget(client, headers, monkeypatch):
    """
    Scenario 4: English query 'Can you recommend top products under 1,000 EGP?'
    Must correctly parse comma-separated thousands '1,000' as Decimal('1000').
    Must preserve explicit EGP currency and not ask for clarification.
    """
    parser = DeterministicIntentParser()
    intent = parser.parse("Can you recommend top products under 1,000 EGP?")
    assert intent.intent_type == "browse"
    assert intent.budget == Decimal("1000")
    assert intent.budget_currency == "EGP"
    assert intent.filters.get("price_max") == "1000.00"
    assert "price_min" not in intent.filters
    assert intent.clarification_needed is False
    assert intent.currency_note is None

    mock_items = [
        Product(
            product_id="92",
            name="Leather-Style Wallet",
            brand="BrightBasics",
            category="Accessories",
            price=Price(amount="390.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="http://localhost/nice-shop/product?id=92",
        )
    ]

    async def fake_search(payload):
        assert payload.get("filters", {}).get("price_max") == "1000.00"
        return mock_items, 1

    monkeypatch.setattr(catalog, "search", fake_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "Can you recommend top products under 1,000 EGP?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) == 1
    assert data["products"][0]["product_id"] == "92"
    assert "under 1000" in data["reply"]["text"] or "under 1000.00" in data["reply"]["text"]
    assert "Leather-Style Wallet" in data["reply"]["text"]


def test_english_show_me_products_under_1000_egp(client, headers, monkeypatch):
    """
    Scenario 5: English query 'show me products under 1000 EGP'
    Applies maximum price of 1000 EGP and returns satisfying products.
    """
    parser = DeterministicIntentParser()
    intent = parser.parse("show me products under 1000 EGP")
    assert intent.intent_type == "browse"
    assert intent.budget == Decimal("1000")
    assert intent.budget_currency == "EGP"
    assert intent.filters.get("price_max") == "1000.00"

    mock_items = [
        Product(
            product_id="2",
            name="Bluetooth Speaker",
            brand="NovaTech",
            category="Electronics",
            price=Price(amount="980.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="http://localhost/nice-shop/product?id=2",
        ),
        Product(
            product_id="3",
            name="USB-C Fast Charger",
            brand="NovaTech",
            category="Electronics",
            price=Price(amount="420.00", currency="EGP"),
            availability=Availability(status="in_stock"),
            attributes={},
            url="http://localhost/nice-shop/product?id=3",
        ),
    ]

    async def fake_search(payload):
        assert payload.get("filters", {}).get("price_max") == "1000.00"
        return mock_items, 2

    monkeypatch.setattr(catalog, "search", fake_search)

    response = client.post(
        "/v1/chat/messages",
        headers=headers,
        json={"message": "show me products under 1000 EGP"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["products"]) == 2
    for p in data["products"]:
        assert Decimal(p["price"]["amount"]) <= Decimal("1000.00")
        assert p["price"]["currency"] == "EGP"


def test_llm_parser_does_not_infer_bogus_category_for_specific_keywords():
    """
    Scenario 6: LLM intent parsing safety.
    When the user requests headphones, even if LLM infers category='Accessories',
    the parser must NOT apply the category filter when searching specific keywords,
    ensuring that products categorized under 'Electronics' are not filtered out.
    """
    json_payload = """{
        "intent_type": "search",
        "clean_query": "سماعة",
        "catalog_query": "headphone",
        "language": "ar",
        "is_compare": false,
        "is_browse": false,
        "is_stock": false,
        "category": "Accessories",
        "budget_amount": "1500",
        "budget_currency": "EGP",
        "budget_type": "max"
    }"""
    mock_resp = {
        "candidates": [
            {
                "content": {"parts": [{"text": json_payload}], "role": "model"},
                "finishReason": "STOP",
            }
        ]
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=mock_resp)))
    parser = LLMIntentParser(api_key="mock-key", http_client=client)

    intent = parser.parse("عاوز سماعة في حدود 1500 جنيه")
    assert intent.clean_query == "سماعة"
    assert intent.catalog_query == "headphones"  # Normalized to plural
    assert intent.filters.get("price_max") == "1500.00"
    # Crucial: category filter must NOT be present to avoid hiding Electronics headphones
    assert "category" not in intent.filters


def test_llm_parser_supports_min_budget_type():
    """
    Scenario 7: LLM intent parser min budget type support.
    """
    json_payload = """{
        "intent_type": "search",
        "clean_query": "سماعة",
        "catalog_query": "headphones",
        "language": "ar",
        "is_compare": false,
        "is_browse": false,
        "is_stock": false,
        "budget_amount": "1,500",
        "budget_currency": "EGP",
        "budget_type": "min"
    }"""
    mock_resp = {
        "candidates": [
            {
                "content": {"parts": [{"text": json_payload}], "role": "model"},
                "finishReason": "STOP",
            }
        ]
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=mock_resp)))
    parser = LLMIntentParser(api_key="mock-key", http_client=client)

    intent = parser.parse("عاوز سماعة في حدود 1500 جنيه او اكتر")
    assert intent.budget == Decimal("1500")
    assert intent.filters.get("price_min") == "1500.00"
    assert "price_max" not in intent.filters
