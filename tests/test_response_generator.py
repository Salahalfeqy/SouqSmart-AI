import json
import types
import pytest
import httpx
from app.response_generator import ResponseGenerator
from app.intents import ParsedIntent
from app.schemas import Product, Price, Availability

# Helper to create a simple product
def make_product(pid, name, price_amount, currency="EGP", availability="in_stock"):
    return Product(
        product_id=str(pid),
        name=name,
        brand=None,
        category=None,
        attributes={},
        price=Price(amount=str(price_amount), currency=currency),
        availability=Availability(status=availability),
        url=f"http://example.com/product/{pid}",
        image_url=None,
    )

# Mock httpx.Client to intercept Gemini calls
class MockClient:
    def __init__(self, response_text):
        self.response_text = response_text
        self.called = False
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def post(self, url, json):
        self.called = True
        # Simulate Gemini JSON structure
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "candidates": [{"content": {"parts": [{"text": self.response_text}]}}]
            },
            raise_for_status=lambda: None,
        )

def test_successful_generation(monkeypatch):
    intent = ParsedIntent(intent_type="search", clean_query="headphones", catalog_query="headphones", language="en")
    products = [make_product(1, "Wireless Headphones", "1450")]
    # Patch httpx.Client to use our mock that returns a fixed reply
    mock_client = MockClient("Here are the headphones you asked for.")
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: mock_client)
    # Ensure settings provide a dummy API key
    from app.config import Settings, get_settings
    class DummySettings(Settings):
        gemini_api_key: str = "test-key"
    monkeypatch.setattr("app.config.get_settings", lambda: DummySettings())
    gen = ResponseGenerator()
    reply = gen.generate(intent, products, "en")
    assert reply == "Here are the headphones you asked for."
    assert mock_client.called


def test_prompt_includes_original_message_history_and_verified_context(monkeypatch):
    from app.config import Settings

    class DummySettings(Settings):
        gemini_api_key: str = "test-key"

    monkeypatch.setattr("app.config.get_settings", lambda: DummySettings())
    intent = ParsedIntent(intent_type="search", clean_query="headphones", catalog_query="headphones", language="en")
    product = make_product(1, "Wireless Headphones", "1450")
    prompt = ResponseGenerator()._build_prompt(
        intent,
        [product],
        "en",
        user_message="Show me headphones for calls",
        rewritten_query="headphones for calls",
        history=[{"user": "I need something for work", "assistant": "What kind of product?"}],
    )

    assert "Show me headphones for calls" in prompt
    assert "headphones for calls" in prompt
    assert "I need something for work" in prompt
    assert "Wireless Headphones" in prompt
    assert "1450 EGP" in prompt


def test_general_prompt_uses_history_without_store_facts(monkeypatch):
    from app.config import Settings

    class DummySettings(Settings):
        gemini_api_key: str = "test-key"

    monkeypatch.setattr("app.config.get_settings", lambda: DummySettings())
    intent = ParsedIntent(intent_type="conversation", language="ar")
    prompt = ResponseGenerator()._build_general_prompt(
        intent,
        "أنت مين؟",
        "ar",
        history=[{"user": "عاوز سماعة", "assistant": "إيه نوع السماعة اللي بتدور عليها؟"}],
    )

    assert "Response language: ar" in prompt
    assert "أنت مين؟" in prompt
    assert "عاوز سماعة" in prompt
    assert "No verified product or policy evidence is provided" in prompt
    assert "Verified Product Facts" not in prompt


@pytest.mark.parametrize("reply", [
    "The headphones are currently in stock.",
    "This model has a 20-hour battery.",
    "Shipping is free on all orders.",
])
def test_general_response_rejects_unverified_catalog_or_policy_claims(reply):
    with pytest.raises(RuntimeError, match="unverified"):
        ResponseGenerator._validate_general_generated_text(reply)


def test_price_validation_allows_only_catalog_prices():
    products = [make_product(1, "Wireless Headphones", "1450")]

    assert ResponseGenerator._validate_generated_text("Wireless Headphones: 1,450 EGP", products)
    with pytest.raises(RuntimeError, match="unverified price"):
        ResponseGenerator._validate_generated_text("Wireless Headphones: 1500 EGP", products)


def test_missing_api_key(monkeypatch, caplog):
    # Force empty API key via settings monkeypatch
    from app.config import Settings, get_settings
    class EmptyKeySettings(Settings):
        gemini_api_key: str = ""
    monkeypatch.setattr("app.config.get_settings", lambda: EmptyKeySettings())
    intent = ParsedIntent(intent_type="search", clean_query="headphones", catalog_query="headphones", language="en")
    products = []
    gen = ResponseGenerator()
    with pytest.raises(RuntimeError):
        gen.generate(intent, products, "en")
    # Ensure no HTTP call was attempted
    assert "Gemini API key missing" in caplog.text

def test_generation_failure_fallback(monkeypatch):
    # Simulate a network error raising httpx.ConnectError
    class FailClient:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, url, json):
            raise httpx.ConnectError("network down")
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: FailClient())
    intent = ParsedIntent(intent_type="search", clean_query="headphones", catalog_query="headphones", language="en")
    products = []
    gen = ResponseGenerator()
    with pytest.raises(httpx.ConnectError):
        gen.generate(intent, products, "en")
