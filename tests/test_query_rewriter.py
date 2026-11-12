import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.query_rewriter import QueryRewriter


@pytest.fixture
def rewriter():
    return QueryRewriter(api_key="fake-test-key", model="gemini-2.5-flash", timeout=2.0)


@pytest.mark.anyio
async def test_fast_path_on_first_turn(rewriter):
    # With empty history, fast-path should immediately return original query without calling Gemini
    result = await rewriter.rewrite("عايز سماعات بلوتوث", history=[], last_products=[])
    assert result.rewritten_query == "عايز سماعات بلوتوث"
    assert result.original_message == "عايز سماعات بلوتوث"
    assert result.is_followup is False


@pytest.mark.anyio
async def test_rewriting_egyptian_arabic_second_one(rewriter):
    history = [
        {"user": "عايز لابتوبات ديل", "assistant": "لقيت لك Dell Inspiron و Dell XPS"},
    ]
    last_products = [
        {"id": "10", "name": "Dell Inspiron"},
        {"id": "20", "name": "Dell XPS"},
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": '{"rewritten_query": "Dell XPS", "is_followup": true, "referenced_product_ids": ["20"], "merged_filters": {"brand": "Dell"}, "is_compare": false, "needs_clarification": false}'
                        }
                    ]
                }
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await rewriter.rewrite("عايز التاني", history=history, last_products=last_products)

        assert result.rewritten_query == "Dell XPS"
        assert result.is_followup is True
        assert result.referenced_product_ids == ["20"]


@pytest.mark.anyio
async def test_hallucinated_product_ids_are_stripped(rewriter):
    history = [
        {"user": "سماعات سوني", "assistant": "Sony WH-1000XM4"},
    ]
    last_products = [
        {"id": "55", "name": "Sony WH-1000XM4"},
    ]

    # LLM hallucinates an untrusted ID "999" not present in displayed products
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": '{"rewritten_query": "سوني WH-1000XM4", "is_followup": true, "referenced_product_ids": ["55", "999"], "merged_filters": {}, "is_compare": false, "needs_clarification": false}'
                        }
                    ]
                }
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await rewriter.rewrite("مواصفاته ايه ده؟", history=history, last_products=last_products)

        # "999" must be stripped! Only "55" was in history.
        assert result.referenced_product_ids == ["55"]
        assert "999" not in result.referenced_product_ids


@pytest.mark.anyio
async def test_fallback_on_network_error(rewriter):
    history = [
        {"user": "سماعات", "assistant": "Sony"},
    ]
    last_products = [
        {"id": "55", "name": "Sony"},
    ]

    with patch("httpx.AsyncClient.post", side_effect=Exception("API timeout")):
        result = await rewriter.rewrite("طب والأرخص منه؟", history=history, last_products=last_products)
        # Should gracefully fall back to raw query or heuristic without crashing
        assert result.original_message == "طب والأرخص منه؟"
        assert result.rewritten_query != ""


def test_price_followup_fallback_preserves_prior_category_and_query():
    rewriter = QueryRewriter(api_key="")
    history = [
        {"user": "عايز حاجة للرياضة في حدود 1000 جنيه", "assistant": "عرضت منتجات رياضية"},
    ]
    previous_constraints = {
        "category": "Sports & Outdoors",
        "price_max": "1000.00",
    }

    result = rewriter._heuristic_fallback(
        "خلاص هاتلي حاجه بسعر اكبر عادى",
        history=history,
        last_products=[],
        previous_constraints=previous_constraints,
        language="ar",
    )

    assert result.is_followup is True
    assert result.rewritten_query == "Sports & Outdoors"
    assert result.merged_filters == {"category": "Sports & Outdoors"}


def test_price_followup_fallback_preserves_prior_product_query():
    rewriter = QueryRewriter(api_key="")
    history = [
        {"user": "عاوز سماعة في حدود 1500 جنيه", "assistant": "Wireless Headphones"},
    ]
    previous_constraints = {"price_max": "1500.00"}

    result = rewriter._heuristic_fallback(
        "السعر مش مهم",
        history=history,
        last_products=[],
        previous_constraints=previous_constraints,
        language="ar",
    )

    assert result.is_followup is True
    assert result.rewritten_query == "headphones"
    assert result.merged_filters == {}


@pytest.mark.anyio
async def test_price_followup_llm_preserves_prior_category_even_if_llm_misses_followup(rewriter):
    history = [
        {"user": "عايز حاجة للرياضة في حدود 1000 جنيه", "assistant": "عرضت منتجات رياضية"},
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": '{"rewritten_query": "خلاص هاتلي حاجه بسعر اكبر عادى", "is_followup": false, "referenced_product_ids": [], "merged_filters": {}, "is_compare": false, "needs_clarification": false}'
                        }
                    ]
                }
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await rewriter.rewrite(
            "خلاص هاتلي حاجه بسعر اكبر عادى",
            history=history,
            previous_constraints={"category": "Sports & Outdoors", "price_max": "1000.00"},
            language="ar",
        )

    assert result.is_followup is True
    assert result.rewritten_query == "Sports & Outdoors"
    assert result.merged_filters == {"category": "Sports & Outdoors"}
