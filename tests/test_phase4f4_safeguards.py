"""
Phase 4F.4 Safeguards and Retrieval Remediation Unit Tests.
Verifies Arabic normalization, clarification triggers, category compatibility,
and confidence calibration.
"""
import pytest
from app.intents import DeterministicIntentParser
from app.rag.retriever import is_candidate_compatible, evaluate_retrieval_confidence


def test_arabic_normalization_word_boundaries():
    parser = DeterministicIntentParser()

    # 1. 'هدية' must remain 'هدية' (no substring deletion of 'دي')
    intent_gift = parser.parse("عايز هدية شيك")
    assert "هدية" in intent_gift.clean_query

    # 2. 'لصديق' must remain 'لصديق' (no substring deletion of 'دي')
    intent_friend = parser.parse("عايز هدية لصديق")
    assert "لصديق" in intent_friend.clean_query

    # 3. Standalone 'دي' is stripped as demonstrative filler
    intent_watch = parser.parse("عايز الساعة دي")
    assert intent_watch.clean_query == "الساعة"

    # 4. Words with substring 'دي' or 'دول' or 'شراء' are not damaged
    intent_new = parser.parse("لابتوب جديد")
    assert intent_new.clean_query == "لابتوب جديد"

    intent_schedule = parser.parse("جدول المقاسات")
    assert intent_schedule.clean_query == "جدول المقاسات"

    # 5. Mixed Arabic and English with punctuation
    intent_mixed = parser.parse("سماعات رأس بلوتوث wireless!")
    assert "سماعات رأس بلوتوث wireless" in intent_mixed.clean_query or "wireless" in intent_mixed.clean_query


def test_clarification_behavior():
    parser = DeterministicIntentParser()

    # Ambiguous queries requiring clarification
    cases = [
        ("هدية شيك لصديق بمناسبة عيد ميلاده", True),
        ("something for my home", True),
        ("gift under 500", True),
        ("gear for travel", True),
        ("عايز حاجة حلوة", True),
        ("عايز جهاز يساعدني في اللياقة وتتبع الجري", True),
        # Specific, answerable product requests must NOT require clarification
        ("عايز ساعة ذكية", False),
        ("wireless headphones", False),
        ("classic cotton t-shirt", False),
        ("wooden study desk under 3000 EGP", False),
    ]

    for message, expected_clarify in cases:
        intent = parser.parse(message)
        assert intent.clarification_needed is expected_clarify, f"Failed for '{message}': expected {expected_clarify}, got {intent.clarification_needed}"
        if expected_clarify:
            assert intent.clarification_message is not None and len(intent.clarification_message) > 10


def test_category_compatibility_ranking():
    query = "laptop bag with padded shoulder strap and water resistance"

    # Stand in Electronics must be incompatible
    stand_payload = {"name": "Laptop Stand", "category": "Electronics"}
    assert is_candidate_compatible(query, stand_payload) is False

    # Backpacks in Accessories and Sports must be compatible
    backpack_payload1 = {"name": "Everyday Backpack", "category": "Accessories"}
    assert is_candidate_compatible(query, backpack_payload1) is True

    backpack_payload2 = {"name": "Hiking Backpack", "category": "Sports & Outdoors"}
    assert is_candidate_compatible(query, backpack_payload2) is True


def test_confidence_calibration_rejects_out_of_catalog():
    # 1. Out-of-catalog query: Samsung Galaxy Ultra smartphone
    samsung_query = "Samsung Galaxy Ultra smartphone"
    samsung_dense = [("6", 0.6218, {"name": "Smart Watch", "brand": "NovaTech", "category": "Electronics"})]
    samsung_kw = []
    assert evaluate_retrieval_confidence(samsung_query, samsung_dense, samsung_kw) is False

    # 2. Out-of-catalog query: Apple iPhone 15 Pro Max
    iphone_query = "Apple iPhone 15 Pro Max"
    iphone_dense = [("1", 0.6407, {"name": "Wireless Headphones", "brand": "NovaTech", "category": "Electronics"})]
    iphone_kw = []
    assert evaluate_retrieval_confidence(iphone_query, iphone_dense, iphone_kw) is False

    # 3. Valid in-catalog high similarity query: Wireless Headphones (0.79)
    valid_query = "Wireless Headphones"
    valid_dense = [("1", 0.7970, {"name": "Wireless Headphones", "brand": "NovaTech", "category": "Electronics"})]
    valid_kw = ["1"]
    assert evaluate_retrieval_confidence(valid_query, valid_dense, valid_kw) is True

    # 4. Valid in-catalog query with moderate similarity but keyword/title agreement
    tshirt_query = "cotton shirt for men under 400 EGP"
    tshirt_dense = [("21", 0.6354, {"name": "Classic Cotton T-Shirt", "brand": "UrbanThread", "category": "Fashion"})]
    tshirt_kw = ["21"]
    assert evaluate_retrieval_confidence(tshirt_query, tshirt_dense, tshirt_kw) is True

