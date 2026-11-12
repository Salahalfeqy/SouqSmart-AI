import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
import httpx

from app.main import app, settings
from app.intents import DeterministicIntentParser
from app.services import generate_policy_reply, parse_chat_intent
from app.rag.knowledge import KnowledgeBaseManager


# =====================================================================
# 1. Intent Routing Tests
# =====================================================================

def test_policy_intent_routing_arabic_shipping():
    parser = DeterministicIntentParser()
    intent = parser.parse("ما هي سياسة الشحن ومواعيد التوصيل؟")
    assert intent.intent_type == "shipping_policy"
    assert intent.is_policy is True
    assert intent.catalog_query == ""


def test_policy_intent_routing_english_shipping():
    parser = DeterministicIntentParser()
    intent = parser.parse("what are the shipping rates and delivery times?")
    assert intent.intent_type == "shipping_policy"
    assert intent.is_policy is True
    assert intent.catalog_query == ""


def test_policy_intent_routing_arabic_returns():
    parser = DeterministicIntentParser()
    intent = parser.parse("كيف يمكنني إرجاع منتج وما هي الشروط؟")
    assert intent.intent_type == "return_policy"
    assert intent.is_policy is True
    assert intent.catalog_query == ""


def test_policy_intent_routing_english_returns():
    parser = DeterministicIntentParser()
    intent = parser.parse("how can I return a product?")
    assert intent.intent_type == "return_policy"
    assert intent.is_policy is True
    assert intent.catalog_query == ""


def test_policy_intent_routing_arabic_payment():
    parser = DeterministicIntentParser()
    intent = parser.parse("ما هي طرق الدفع المتاحة في المتجر؟")
    assert intent.intent_type == "payment_policy"
    assert intent.is_policy is True
    assert intent.catalog_query == ""


def test_policy_intent_routing_english_payment():
    parser = DeterministicIntentParser()
    intent = parser.parse("what payment methods are accepted?")
    assert intent.intent_type == "payment_policy"
    assert intent.is_policy is True
    assert intent.catalog_query == ""


def test_policy_intent_routing_warranty_unapproved():
    parser = DeterministicIntentParser()
    intent_ar = parser.parse("ما هي سياسة الضمان للأجهزة؟")
    assert intent_ar.intent_type == "warranty_policy"
    assert intent_ar.is_policy is True
    assert intent_ar.catalog_query == ""

    intent_en = parser.parse("what is the warranty policy for electronics?")
    assert intent_en.intent_type == "warranty_policy"
    assert intent_en.is_policy is True
    assert intent_en.catalog_query == ""


def test_policy_intent_routing_store_policy():
    parser = DeterministicIntentParser()
    intent_ar = parser.parse("أين يقع مقر المتجر ومواعيد العمل؟")
    assert intent_ar.intent_type == "store_policy"
    assert intent_ar.is_policy is True

    intent_en = parser.parse("where is your store located and what are the opening hours?")
    assert intent_en.intent_type == "store_policy"
    assert intent_en.is_policy is True


def test_local_policy_fallback_loads_only_approved_documents():
    manager = object.__new__(KnowledgeBaseManager)

    shipping = manager.load_approved_policy("shipping")
    assert shipping is not None
    assert shipping["doc_id"] == "shipping"
    assert "الشحن العادي" in shipping["content_text"]
    assert manager.load_approved_policy("warranty") is None


def test_product_queries_not_misclassified_as_policy():
    parser = DeterministicIntentParser()
    
    # "شاحن" (charger) must NOT trigger shipping_policy ("شحن")
    intent_charger = parser.parse("عايز شاحن لاسلكي سريع")
    assert intent_charger.intent_type == "search"
    assert intent_charger.is_policy is False

    # "محفظة" (wallet) must NOT trigger payment_policy
    intent_wallet = parser.parse("محفظة رجالي جلد")
    assert intent_wallet.intent_type == "search"
    assert intent_wallet.is_policy is False

    # General product searches
    intent_headphones = parser.parse("wireless headphones")
    assert intent_headphones.intent_type == "search"
    assert intent_headphones.is_policy is False

    # Comparisons
    intent_compare = parser.parse("compare Wireless Headphones and Wireless Mouse")
    assert intent_compare.intent_type == "compare"
    assert intent_compare.is_policy is False

    # Ambiguous gift requests
    intent_gift = parser.parse("هدية شيك لصديق")
    assert intent_gift.intent_type == "clarify"
    assert intent_gift.is_policy is False


# =====================================================================
# 2. Grounded Extractive Reply Tests
# =====================================================================

def test_grounded_shipping_reply():
    parser = DeterministicIntentParser()
    intent_ar = parser.parse("ما هي سياسة الشحن؟")
    reply_ar = generate_policy_reply(intent_ar, language="ar")
    assert "0" in reply_ar
    assert "10" in reply_ar
    assert "25" in reply_ar
    assert "5 إلى 10" in reply_ar
    assert "3 إلى 7" in reply_ar
    assert "1 إلى 3" in reply_ar
    assert "العملة" in reply_ar
    assert "Checkout" in reply_ar

    intent_en = parser.parse("what is the shipping policy?")
    reply_en = generate_policy_reply(intent_en, language="en")
    assert "Economy / Free" in reply_en
    assert "Standard Shipping" in reply_en
    assert "Express Shipping" in reply_en
    assert "Checkout" in reply_en


def test_grounded_return_reply():
    parser = DeterministicIntentParser()
    intent_ar = parser.parse("كيف يمكنني إرجاع منتج؟")
    reply_ar = generate_policy_reply(intent_ar, language="ar")
    assert "30 يوماً" in reply_ar
    assert "يدوياً" in reply_ar
    assert "خدمة عملاء" in reply_ar
    assert "14" not in reply_ar # Must not assert 14-day policy

    intent_en = parser.parse("what is the return policy?")
    reply_en = generate_policy_reply(intent_en, language="en")
    assert "30 days" in reply_en
    assert "customer service" in reply_en.lower()


def test_grounded_payment_reply():
    parser = DeterministicIntentParser()
    intent_ar = parser.parse("ما هي طرق الدفع المتاحة؟")
    reply_ar = generate_policy_reply(intent_ar, language="ar")
    assert "الدفع نقداً عند الاستلام" in reply_ar or "COD" in reply_ar
    assert "لا يدعم" in reply_ar
    assert "الائتمانية" in reply_ar

    intent_en = parser.parse("what payment methods are accepted?")
    reply_en = generate_policy_reply(intent_en, language="en")
    assert "Cash on Delivery" in reply_en
    assert "does not currently accept" in reply_en


def test_grounded_warranty_unapproved_reply():
    parser = DeterministicIntentParser()
    intent_ar = parser.parse("ما هي سياسة الضمان؟")
    reply_ar = generate_policy_reply(intent_ar, language="ar")
    assert "لا توجد سياسة ضمان رسمية معتمدة" in reply_ar
    assert "نافذة الاسترجاع المحددة بـ 30 يوماً هي سياسة استرجاع فقط ولا تعتبر ضماناً" in reply_ar
    assert "خدمة العملاء" in reply_ar

    intent_en = parser.parse("what is the warranty policy?")
    reply_en = generate_policy_reply(intent_en, language="en")
    assert "does not currently have an official approved store warranty policy" in reply_en
    assert "strictly a return policy and is not considered a warranty" in reply_en


# =====================================================================
# 3. End-to-End Chat API Knowledge Integration Tests
# =====================================================================

@pytest.mark.anyio
async def test_api_chat_shipping_policy():
    transport = ASGITransport(app=app)
    headers = {
        "X-Service-Key": "test-service-key",
        "X-Owner-Hash": "a" * 64,
    }
    payload = {
        "message": "ما هي سياسة الشحن ومواعيد التوصيل؟",
        "language": "ar",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/chat/messages", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["products"] == [] # ZERO false product cards
        reply_text = data["reply"]["text"]
        assert "0" in reply_text
        assert "10" in reply_text
        assert "25" in reply_text


@pytest.mark.anyio
async def test_api_chat_returns_policy():
    transport = ASGITransport(app=app)
    headers = {
        "X-Service-Key": "test-service-key",
        "X-Owner-Hash": "a" * 64,
    }
    payload = {
        "message": "How can I return a product?",
        "language": "en",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/chat/messages", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["products"] == []
        reply_text = data["reply"]["text"]
        assert "30 days" in reply_text


@pytest.mark.anyio
async def test_api_chat_payment_policy():
    transport = ASGITransport(app=app)
    headers = {
        "X-Service-Key": "test-service-key",
        "X-Owner-Hash": "a" * 64,
    }
    payload = {
        "message": "ما هي طرق الدفع المتاحة؟",
        "language": "ar",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/chat/messages", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["products"] == []
        reply_text = data["reply"]["text"]
        assert "الاستلام" in reply_text


@pytest.mark.anyio
async def test_api_chat_warranty_unapproved_safeguard():
    transport = ASGITransport(app=app)
    headers = {
        "X-Service-Key": "test-service-key",
        "X-Owner-Hash": "a" * 64,
    }
    payload = {
        "message": "ما هي سياسة الضمان للأجهزة؟",
        "language": "ar",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/chat/messages", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["products"] == []
        reply_text = data["reply"]["text"]
        assert "لا توجد سياسة ضمان رسمية معتمدة" in reply_text


@pytest.mark.anyio
async def test_api_chat_gemini_429_policy_fallback(monkeypatch):
    """Verify that when Gemini generateContent returns 429, policy path falls back cleanly."""
    transport = ASGITransport(app=app)
    headers = {
        "X-Service-Key": "test-service-key",
        "X-Owner-Hash": "a" * 64,
    }
    payload = {
        "message": "What is the return policy?",
        "language": "en",
    }

    monkeypatch.setattr(settings, "intent_parser", "llm")
    monkeypatch.setattr(settings, "gemini_api_key", "test-api-key")

    real_post = httpx.AsyncClient.post

    async def selective_post(self, url, *args, **kwargs):
        if "generativelanguage.googleapis.com" in str(url):
            return httpx.Response(status_code=429, json={"error": {"code": 429, "message": "ResourceExhausted"}})
        return await real_post(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", selective_post)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/chat/messages", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["products"] == []
        # Extractive fallback was returned
        assert "30 days" in data["reply"]["text"]


# =====================================================================
# Task 3 Regression Tests: Shipping Policy Intent Routing
# =====================================================================

def test_task3_shipping_intent_routing_coverage():
    """Verify Arabic and English shipping queries route to shipping_policy."""
    parser = DeterministicIntentParser()

    # Domestic & Egyptian colloquial
    assert parser.parse("هل الشحن متاح لكل المحافظات؟").intent_type == "shipping_policy"
    assert parser.parse("الشحن بياخد كام يوم؟").intent_type == "shipping_policy"
    assert parser.parse("بكام التوصيل؟").intent_type == "shipping_policy"
    assert parser.parse("هل فيه شحن للمحافظات؟").intent_type == "shipping_policy"
    assert parser.parse("هل عندكم شحن مجاني؟").intent_type == "shipping_policy"
    assert parser.parse("هل بتشحنوا خارج مصر؟").intent_type == "shipping_policy"
    assert parser.parse("بتوصلوا لإسكندرية؟").intent_type == "shipping_policy"

    # English
    assert parser.parse("How long does delivery take?").intent_type == "shipping_policy"
    assert parser.parse("Is shipping free?").intent_type == "shipping_policy"
    assert parser.parse("Do you ship internationally?").intent_type == "shipping_policy"
    assert parser.parse("How much is shipping?").intent_type == "shipping_policy"


def test_task3_international_shipping_grounded_reply():
    """Verify international shipping is neither falsely promised nor falsely denied as fact."""
    parser = DeterministicIntentParser()

    intent_ar = parser.parse("هل بتشحنوا خارج مصر؟")
    reply_ar = generate_policy_reply(intent_ar, language="ar", user_message="هل بتشحنوا خارج مصر؟")
    assert "غير متوفرة" not in reply_ar or "لا توضح أو تؤكد" in reply_ar
    assert "خدمة عملاء" in reply_ar
    assert "شحن دولي" in reply_ar or "خارج مصر" in reply_ar

    intent_en = parser.parse("Do you ship internationally?")
    reply_en = generate_policy_reply(intent_en, language="en", user_message="Do you ship internationally?")
    assert "does not specify or confirm" in reply_en
    assert "customer support" in reply_en


def test_task3_free_shipping_grounded_reply():
    """Verify free shipping discloses approved tier but warns conditions are unspecified."""
    parser = DeterministicIntentParser()

    intent_ar = parser.parse("هل عندكم شحن مجاني؟")
    reply_ar = generate_policy_reply(intent_ar, language="ar", user_message="هل عندكم شحن مجاني؟")
    assert "الشحن الاقتصادي / المجاني" in reply_ar
    assert "رسوم الشحن 0" in reply_ar
    assert "شروط أو حدود تطبيق الشحن المجاني" in reply_ar

    intent_en = parser.parse("Is shipping free?")
    reply_en = generate_policy_reply(intent_en, language="en", user_message="Is shipping free?")
    assert "Economy / Free Shipping" in reply_en
    assert "rate 0" in reply_en.lower()
    assert "thresholds" in reply_en.lower() or "conditions" in reply_en.lower()


@pytest.mark.anyio
async def test_api_chat_domestic_shipping_routing():
    """API test: Domestic shipping questions return policy text, zero products."""
    transport = ASGITransport(app=app)
    headers = {"X-Service-Key": "test-service-key", "X-Owner-Hash": "a" * 64}

    test_queries = [
        "هل الشحن متاح لكل المحافظات؟",
        "الشحن بياخد كام يوم؟",
        "بكام التوصيل؟",
    ]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for q in test_queries:
            resp = await client.post("/v1/chat/messages", json={"message": q, "language": "ar"}, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["products"] == [], f"Expected 0 products for '{q}', got {data['products']}"
            assert "لم يتم العثور على منتجات مطابقة في الكتالوج" not in data["reply"]["text"]
            assert "الشحن" in data["reply"]["text"]


@pytest.mark.anyio
async def test_api_chat_international_and_free_shipping():
    """API test: International and free shipping inquiries route to policy without false claims."""
    transport = ASGITransport(app=app)
    headers = {"X-Service-Key": "test-service-key", "X-Owner-Hash": "a" * 64}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. International Arabic
        r_intl_ar = await client.post("/v1/chat/messages", json={"message": "هل بتشحنوا خارج مصر؟"}, headers=headers)
        assert r_intl_ar.status_code == 200
        d_intl_ar = r_intl_ar.json()
        assert d_intl_ar["products"] == []
        assert "لم يتم العثور على منتجات مطابقة في الكتالوج" not in d_intl_ar["reply"]["text"]
        assert "خدمة عملاء" in d_intl_ar["reply"]["text"]

        # 2. Free shipping Arabic
        r_free_ar = await client.post("/v1/chat/messages", json={"message": "هل عندكم شحن مجاني؟"}, headers=headers)
        assert r_free_ar.status_code == 200
        d_free_ar = r_free_ar.json()
        assert d_free_ar["products"] == []
        assert "الشحن الاقتصادي / المجاني" in d_free_ar["reply"]["text"]

        # 3. English international
        r_intl_en = await client.post("/v1/chat/messages", json={"message": "Do you ship internationally?", "language": "en"}, headers=headers)
        assert r_intl_en.status_code == 200
        d_intl_en = r_intl_en.json()
        assert d_intl_en["products"] == []
        assert "customer support" in d_intl_en["reply"]["text"]

        # 4. English delivery time
        r_time_en = await client.post("/v1/chat/messages", json={"message": "How long does delivery take?", "language": "en"}, headers=headers)
        assert r_time_en.status_code == 200
        d_time_en = r_time_en.json()
        assert d_time_en["products"] == []
        assert "Economy / Free Shipping" in d_time_en["reply"]["text"]
