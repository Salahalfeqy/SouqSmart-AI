import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any
import httpx

from app.config import get_settings

logger = logging.getLogger("souqsmart.embeddings")


def format_document_input(text: str, title: str | None = None) -> str:
    """Format document text with gemini-embedding-2 prefix instruction."""
    clean_text = text.strip()
    title_str = title.strip() if title and title.strip() else "none"
    return f"title: {title_str} | text: {clean_text}"


def format_query_input(query: str) -> str:
    """Format search query with gemini-embedding-2 task instruction."""
    clean_query = query.strip()
    return f"task: search result | query: {clean_query}"


class BaseEmbeddingProvider(ABC):
    @abstractmethod
    async def embed_document(self, text: str, title: str | None = None) -> list[float]:
        raise NotImplementedError

    @abstractmethod
    async def embed_documents_batch(
        self, texts: list[str], titles: list[str] | None = None
    ) -> list[list[float]]:
        raise NotImplementedError

    async def embed_documents(
        self, texts: list[str], titles: list[str] | None = None
    ) -> list[list[float]]:
        return await self.embed_documents_batch(texts, titles)

    @abstractmethod
    async def embed_query(self, query: str) -> list[float]:
        raise NotImplementedError


class GeminiEmbeddingClient(BaseEmbeddingProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dimension: int = 768,
        timeout: float = 5.0,
        max_retries: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = (api_key if api_key is not None else settings.effective_embedding_api_key).strip()
        self.model = model or settings.gemini_embedding_model
        self.dimension = dimension or settings.embedding_dimension
        self.timeout = timeout
        self.max_retries = max_retries
        self._http_client = http_client

    async def _request_with_retry(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        delay = 0.5
        for attempt in range(1, self.max_retries + 1):
            try:
                if self._http_client is not None:
                    response = await self._http_client.post(url, json=payload, headers=headers, timeout=self.timeout)
                else:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        response = await client.post(url, json=payload, headers=headers)

                if response.status_code == 429:
                    logger.warning("Gemini embedding rate limited (429), attempt %s", attempt)
                    if attempt == self.max_retries:
                        response.raise_for_status()
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue

                if response.status_code >= 400:
                    response.raise_for_status()

                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError) as err:
                logger.warning("Gemini embedding network/timeout error: %s (attempt %s)", err, attempt)
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(delay)
                delay *= 2

        raise RuntimeError("Gemini embedding retries exhausted")

    async def embed_document(self, text: str, title: str | None = None) -> list[float]:
        if not self.api_key:
            raise RuntimeError("Gemini embedding API key is not configured")
        clean_text = text.strip()
        if not clean_text:
            return [0.0] * self.dimension

        formatted_text = format_document_input(clean_text, title=title)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent"
        payload: dict[str, Any] = {
            "content": {"parts": [{"text": formatted_text[:4000]}]},
        }
        if self.dimension:
            payload["outputDimensionality"] = self.dimension

        data = await self._request_with_retry(url, payload)
        embedding_obj = data.get("embedding", {})
        values = embedding_obj.get("values", [])

        if not values:
            raise ValueError("Empty embedding values received from Gemini")

        if self.dimension and len(values) != self.dimension:
            logger.debug("Truncating or padding embedding: got %s, expected %s", len(values), self.dimension)
            if len(values) > self.dimension:
                values = values[: self.dimension]
            else:
                values = values + [0.0] * (self.dimension - len(values))

        return values

    async def embed_query(self, query: str) -> list[float]:
        if not self.api_key:
            raise RuntimeError("Gemini embedding API key is not configured")
        clean_query = query.strip()
        if not clean_query:
            return [0.0] * self.dimension

        formatted_text = format_query_input(clean_query)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent"
        payload: dict[str, Any] = {
            "content": {"parts": [{"text": formatted_text[:4000]}]},
        }
        if self.dimension:
            payload["outputDimensionality"] = self.dimension

        data = await self._request_with_retry(url, payload)
        embedding_obj = data.get("embedding", {})
        values = embedding_obj.get("values", [])

        if not values:
            raise ValueError("Empty embedding values received from Gemini")

        if self.dimension and len(values) != self.dimension:
            logger.debug("Truncating or padding embedding: got %s, expected %s", len(values), self.dimension)
            if len(values) > self.dimension:
                values = values[: self.dimension]
            else:
                values = values + [0.0] * (self.dimension - len(values))

        return values

    async def embed_documents_batch(
        self, texts: list[str], titles: list[str] | None = None
    ) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError("Gemini embedding API key is not configured")
        if not texts:
            return []

        results: list[list[float]] = []
        batch_size = 20
        titles_list = titles or [None] * len(texts)

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            batch_titles = titles_list[i : i + batch_size]

            # Try batchEmbedContents
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:batchEmbedContents"
            requests = []
            for t, title in zip(batch_texts, batch_titles):
                formatted_text = format_document_input(t, title=title)
                req: dict[str, Any] = {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": formatted_text[:4000]}]},
                }
                if self.dimension:
                    req["outputDimensionality"] = self.dimension
                requests.append(req)

            try:
                data = await self._request_with_retry(url, {"requests": requests})
                embeddings = data.get("embeddings", [])
                for emb in embeddings:
                    vals = emb.get("values", [])
                    if self.dimension and len(vals) != self.dimension:
                        vals = vals[: self.dimension] if len(vals) > self.dimension else vals + [0.0] * (self.dimension - len(vals))
                    results.append(vals)
            except Exception as exc:
                logger.warning("Batch embedding call failed, falling back to sequential calls: %s", exc)
                for t, title in zip(batch_texts, batch_titles):
                    vec = await self.embed_document(t, title)
                    results.append(vec)

        return results

