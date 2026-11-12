import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.rag.embeddings import (
    GeminiEmbeddingClient,
    format_document_input,
    format_query_input,
)


@pytest.fixture
def embedding_client():
    return GeminiEmbeddingClient(
        api_key="test-api-key",
        model="gemini-embedding-2",
        dimension=768,
        timeout=2.0,
        max_retries=2,
    )


def test_format_query_and_document_inputs():
    """Verify gemini-embedding-2 prefix string formatting."""
    query_text = format_query_input("سماعات بلوتوث")
    assert query_text == "task: search result | query: سماعات بلوتوث"

    doc_with_title = format_document_input("محتوى الوثيقة", title="سياسة الشحن")
    assert doc_with_title == "title: سياسة الشحن | text: محتوى الوثيقة"

    doc_no_title = format_document_input("محتوى بدون عنوان", title=None)
    assert doc_no_title == "title: none | text: محتوى بدون عنوان"


@pytest.mark.anyio
async def test_embed_query_payload_format(embedding_client):
    """Verify embed_query does NOT send taskType and correctly formats the query text."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "embedding": {
            "values": [0.1] * 768
        }
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        vector = await embedding_client.embed_query("لابتوب للألعاب")

        assert len(vector) == 768
        assert vector[0] == 0.1
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]

        # CRITICAL: taskType must NOT be sent to gemini-embedding-2
        assert "taskType" not in payload
        assert "task_type" not in payload
        assert "title" not in payload
        assert payload["outputDimensionality"] == 768
        assert payload["content"]["parts"][0]["text"] == "task: search result | query: لابتوب للألعاب"


@pytest.mark.anyio
async def test_embed_document_payload_format(embedding_client):
    """Verify embed_document formats title and text into prompt without taskType."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "embedding": {
            "values": [0.25] * 768
        }
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        vector = await embedding_client.embed_document(
            text="تفاصيل شحن الطلبات إلى المحافظات",
            title="سياسة الشحن",
        )

        assert len(vector) == 768
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]

        assert "taskType" not in payload
        assert "task_type" not in payload
        assert "title" not in payload
        assert payload["outputDimensionality"] == 768
        assert payload["content"]["parts"][0]["text"] == "title: سياسة الشحن | text: تفاصيل شحن الطلبات إلى المحافظات"


@pytest.mark.anyio
async def test_embed_documents_batch_payload_format(embedding_client):
    """Verify batch embedding sends distinct 1:1 requests with document prefixes and no taskType."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "embeddings": [
            {"values": [0.2] * 768},
            {"values": [0.3] * 768},
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        vectors = await embedding_client.embed_documents(
            texts=["سماعة سوني", "ماوس لوجيتك"],
            titles=["Sony Audio", "Logitech Mouse"],
        )

        assert len(vectors) == 2
        assert len(vectors[0]) == 768
        assert len(vectors[1]) == 768
        assert vectors[0][0] == 0.2
        assert vectors[1][0] == 0.3

        payload = mock_post.call_args[1]["json"]
        assert "requests" in payload
        requests = payload["requests"]
        assert len(requests) == 2

        for i, req in enumerate(requests):
            assert "taskType" not in req
            assert "task_type" not in req
            assert "title" not in req
            assert req["outputDimensionality"] == 768

        assert requests[0]["content"]["parts"][0]["text"] == "title: Sony Audio | text: سماعة سوني"
        assert requests[1]["content"]["parts"][0]["text"] == "title: Logitech Mouse | text: ماوس لوجيتك"


@pytest.mark.anyio
async def test_embedding_retry_on_429(embedding_client):
    response_429 = MagicMock()
    response_429.status_code = 429
    response_429.text = "Rate limit"

    response_200 = MagicMock()
    response_200.status_code = 200
    response_200.json.return_value = {"embedding": {"values": [0.5] * 768}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [response_429, response_200]
        with patch("asyncio.sleep", new_callable=AsyncMock):
            vector = await embedding_client.embed_query("هاتف ذكي")

            assert len(vector) == 768
            assert mock_post.call_count == 2
