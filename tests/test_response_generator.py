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
